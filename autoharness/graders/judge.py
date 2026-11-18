"""Layer 3: LLM-as-judge with self-consistency and an explicit uncertainty signal.

The judge never has the last word on its own: each check is sampled N times,
and a split vote or low self-reported confidence marks the trial for human
review. Calibration (see calibrate.py) decides whether the judge is trusted
at all.
"""

from __future__ import annotations

import json
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from ..cases import Case
from ..config import claude_cli
from ..trace import Trace

JUDGE_SYSTEM = """You are a strict, impartial evaluator of an AI agent's work.
You are given the task the agent received, a record of what it did, and a rubric.
Grade ONLY against the rubric. Do not reward effort, politeness, or length.
If the record does not contain evidence that a rubric item was met, treat it as not met.
Report your real confidence: use a low value when the evidence is ambiguous or the
rubric is open to interpretation, so a human can review the case."""

VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "criteria": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "criterion": {"type": "string"},
                    "met": {"type": "boolean"},
                    "evidence": {"type": "string"},
                },
                "required": ["criterion", "met", "evidence"],
                "additionalProperties": False,
            },
        },
        "reasoning": {"type": "string"},
        "verdict": {"type": "string", "enum": ["pass", "fail"]},
        "score": {"type": "number", "description": "0.0-1.0 quality against the rubric"},
        "confidence": {"type": "number", "description": "0.0-1.0 how sure you are of the verdict"},
    },
    "required": ["criteria", "reasoning", "verdict", "score", "confidence"],
    "additionalProperties": False,
}


@dataclass
class JudgeVerdict:
    passed: bool
    score: float
    confidence: float
    reasoning: str
    uncertain: bool
    votes: list[dict[str, Any]] = field(default_factory=list)
    cost_usd: float = 0.0


class Judge:
    def __init__(self, cfg: dict[str, Any], client=None):
        self.cfg = cfg
        self._client = client

    @property
    def client(self):
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic()
        return self._client

    def build_prompt(self, case: Case, trace: Trace, spec: dict[str, Any]) -> str:
        parts = [f"<task>\n{case.prompt}\n</task>"]
        if spec.get("reference"):
            parts.append(f"<reference_answer>\n{spec['reference']}\n</reference_answer>")
        parts.append(f"<agent_record>\n{trace.transcript_text()}\n</agent_record>")
        for path in spec.get("include_files", []):
            content = trace.artifacts.get(path, "(file missing after the run)")
            parts.append(f'<file path="{path}">\n{content[:20000]}\n</file>')
        if trace.error:
            parts.append(f"<run_error>{trace.error}</run_error>")
        parts.append(f"<rubric>\n{spec['rubric']}\n</rubric>")
        parts.append("Evaluate each rubric item, then give the overall verdict. "
                     "Verdict is 'pass' only if every required rubric item is met.")
        return "\n\n".join(parts)

    def _one(self, prompt: str) -> dict[str, Any]:
        # An injected client (tests, custom transports) always wins over the configured backend.
        backend = self.cfg.get("backend", "api")
        if self._client is None and backend == "claude-cli":
            return self._one_cli(prompt)
        if self._client is None and backend in (
            "codex-cli", "gemini", "gemini-cli", "ollama", "vllm", "lmstudio", "openrouter", "opensource", "openai", "auto"
        ):
            from ..providers import complete_structured

            data, cost, err = complete_structured(backend, self.cfg, prompt, VERDICT_SCHEMA, JUDGE_SYSTEM)
            if err or not isinstance(data, dict):
                return {"error": err or "judge returned empty output", "_cost": cost}
            data["_cost"] = cost
            return data
        import anthropic

        try:
            resp = self.client.messages.create(
                model=self.cfg["model"],
                max_tokens=8000,
                system=JUDGE_SYSTEM,
                output_config={"effort": self.cfg.get("effort", "high"),
                               "format": {"type": "json_schema", "schema": VERDICT_SCHEMA}},
                messages=[{"role": "user", "content": prompt}],
            )
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as e:
            return {"error": f"{type(e).__name__}: {e}"}
        if resp.stop_reason == "refusal":
            return {"error": "judge refused"}
        if resp.stop_reason == "max_tokens":
            return {"error": "judge output truncated"}
        text = next((b.text for b in resp.content if b.type == "text"), "")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"error": "judge returned invalid JSON"}

    def _one_cli(self, prompt: str) -> dict[str, Any]:
        """Judge through the local Claude Code login (no API key needed).

        Stripped down so the judge sees only the evaluation: no tools, no MCP, no
        settings/CLAUDE.md, our own system prompt, and no saved session (so eval
        runs never show up in the transcripts the miner reads).
        """
        cmd = [
            claude_cli(self.cfg.get("cli")), "-p", "--output-format", "json",
            "--json-schema", json.dumps(VERDICT_SCHEMA), "--tools", "", "--strict-mcp-config",
            "--setting-sources", "", "--system-prompt", JUDGE_SYSTEM, "--no-session-persistence",
            "--model", self.cfg["model"],
        ]
        if self.cfg.get("effort"):
            cmd += ["--effort", self.cfg["effort"]]
        try:
            r = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                               timeout=self.cfg.get("timeout_s", 300))
        except subprocess.TimeoutExpired:
            return {"error": "judge timed out"}
        try:
            out = json.loads(r.stdout)
        except json.JSONDecodeError:
            return {"error": f"claude cli exit {r.returncode}: {(r.stderr or r.stdout)[-500:]}"}
        verdict = out.get("structured_output")
        if out.get("is_error") or not isinstance(verdict, dict):
            return {"error": f"claude cli: {out.get('subtype')} {str(out.get('result'))[:300]}",
                    "_cost": out.get("total_cost_usd", 0.0)}
        verdict["_cost"] = out.get("total_cost_usd", 0.0)
        return verdict

    def evaluate(self, case: Case, trace: Trace, spec: dict[str, Any]) -> JudgeVerdict:
        prompt = self.build_prompt(case, trace, spec)
        n = int(spec.get("samples", self.cfg.get("samples", 3)))
        votes = [self._one(prompt) for _ in range(n)]
        return aggregate(votes, float(self.cfg.get("min_confidence", 0.7)))


def aggregate(votes: list[dict[str, Any]], min_confidence: float) -> JudgeVerdict:
    cost = round(sum(float(v.get("_cost") or 0.0) for v in votes), 5)
    good = [v for v in votes if "error" not in v]
    if not good:
        errs = "; ".join(v.get("error", "?") for v in votes)
        return JudgeVerdict(False, 0.0, 0.0, f"judge failed: {errs}", True, votes, cost)
    tally = Counter(v["verdict"] for v in good)
    verdict, count = tally.most_common(1)[0]
    if len(tally) > 1 and tally["pass"] == tally["fail"]:
        verdict = "fail"  # ties go to fail; the human queue will settle it
    agreement = count / len(good)
    self_conf = sum(min(max(v["confidence"], 0.0), 1.0) for v in good) / len(good)
    confidence = round(agreement * self_conf, 3)
    score = round(sum(min(max(v["score"], 0.0), 1.0) for v in good) / len(good), 3)
    majority = next(v for v in good if v["verdict"] == verdict)
    uncertain = agreement < 1.0 or confidence < min_confidence or len(good) < len(votes)
    reasoning = majority["reasoning"]
    if len(tally) > 1:
        reasoning = f"[split vote {dict(tally)}] " + reasoning
    return JudgeVerdict(verdict == "pass", score, confidence, reasoning, uncertain, votes, cost)
