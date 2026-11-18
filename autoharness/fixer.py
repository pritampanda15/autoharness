"""The Harness² Fixer: proposes invariant & domain-specific edits, composes contrasts, and reflects lessons.

Inspired by `google-research/harness2`, optimization runs in three structured model calls:

  propose()  -> K diverse candidate edits in one call:
                - Invariant path: cross-domain reasoning, verification, or scope discipline
                - Domain/skill path: targeted procedural guidance or tool usage rules
                - Guardrail/middleware path: hard boundaries in `guardrails.md` or `plugins.json`
  compose()  -> merges winning behavioral deltas from probed candidates using measured per-case contrasts
  reflect()  -> updates the persistent Lesson Ledger & Improvement Library (`lessons/lessons.json`)

Backends: `claude-cli` (Claude Code login, no API key), `api` (Anthropic SDK), `gemini`, or `openai`.
"""

from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from .config import claude_cli

FIXER_SYSTEM = """You are a principal Harness Engineer optimizing an AI agent's surrounding software harness
(`Agent = Frozen Model + Editable Harness`).

You are shown the editable harness components (such as `system.md`, `guardrails.md`, `memory.md`,
`skills.md`, `tool_guidance.md`, `scripts/*`, `subagents.md`, or `plugins.json`), the evaluation cases
the agent failed, and evidence-backed lessons from earlier refinement sessions.

Diagnose the underlying behavioral root cause behind each failure, then propose candidate harness edits
spanning complementary strategies:
1. Invariant edits: cross-domain planning, scope discipline, read-before-edit, or response conciseness.
2. Procedural / tool-guidance edits: explicit workflows in `skills.md` or `tool_guidance.md`.
3. Guardrail / middleware edits: hard boundaries in `guardrails.md` or `plugins.json`.

Rules:
- Fix behavior in general, reusable terms. NEVER mention specific case IDs, filenames, task prompts, or
  expected literal answers from the failing cases; hidden holdout cases decide whether an edit survives.
- Preserve passing behavior: currently passing cases must continue to pass.
- Prefer surgical, concise edits to the most relevant harness component(s). Do not bloat prompts.
- Respect the lesson ledger: never repeat approaches marked `avoid`.
- Return the COMPLETE new content of each file you modify. Only use paths from the editable list."""

REFLECT_SYSTEM = """You maintain a persistent Lesson Ledger for recursive agent harness engineering.
You are given the current lessons and the record of a Propose-Probe-Compose refinement session: each
candidate edit, its diff, and its measured evaluation contrast (accepted, or rejected and why, with
before/after pass rates).

Update the lessons:
- Add a lesson only when supported by concrete evaluation evidence, citing the run IDs.
- Merge duplicates. Increment `support` when new evidence confirms a lesson; weaken or remove contradicted ones.
- Keep each lesson to one or two actionable sentences, completely general (no case IDs or expected answers).
- `polarity`: `"do"` = empirically validated harness improvement; `"avoid"` = caused regressions or failed guards.
Return the full updated list, ordered by highest utility first."""


@dataclass
class Proposal:
    diagnosis: str
    edits: dict[str, str]
    expected_effect: str
    approach: str = ""
    cost_usd: float = 0.0
    error: str | None = None


def _edits_schema(editable: list[str]) -> dict[str, Any]:
    return {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {"path": {"type": "string", "enum": editable}, "content": {"type": "string"}},
            "required": ["path", "content"],
            "additionalProperties": False,
        },
    }


def candidates_schema(editable: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "diagnosis": {"type": "string"},
            "candidates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "approach": {"type": "string", "description": "one line: strategy category (invariant / skill / guardrail) and how it differs"},
                        "edits": _edits_schema(editable),
                        "expected_effect": {"type": "string"},
                    },
                    "required": ["approach", "edits", "expected_effect"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["diagnosis", "candidates"],
        "additionalProperties": False,
    }


def compose_schema(editable: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "diagnosis": {"type": "string"},
            "edits": _edits_schema(editable),
            "expected_effect": {"type": "string"},
        },
        "required": ["diagnosis", "edits", "expected_effect"],
        "additionalProperties": False,
    }


LESSONS_SCHEMA = {
    "type": "object",
    "properties": {
        "lessons": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "polarity": {"type": "string", "enum": ["do", "avoid"]},
                    "lesson": {"type": "string"},
                    "support": {"type": "integer"},
                    "evidence_runs": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["polarity", "lesson", "support", "evidence_runs"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["lessons"],
    "additionalProperties": False,
}


def _section(tag: str, data: Any) -> str:
    body = data if isinstance(data, str) else json.dumps(data, indent=1)
    return f"<{tag}>\n{body}\n</{tag}>"


class Fixer:
    def __init__(self, cfg: dict[str, Any], client=None):
        self.cfg = cfg
        self._client = client

    # ------------------------------------------------------------------ calls

    def propose(self, files: dict[str, str], failures: list[dict], passing: list[str],
                history: list[dict], lessons: list[dict], k: int = 3) -> list[Proposal]:
        parts = [
            _section("editable_harness_files", "\n".join(f'<file path="{p}">\n{c}\n</file>' for p, c in files.items())),
            _section("failing_cases", failures),
            f"<passing_cases>{len(passing)} cases currently pass and must keep passing</passing_cases>",
        ]
        if lessons:
            parts.append(_section("lessons_from_earlier_attempts", lessons))
        if history:
            parts.append(_section("rejected_attempts_this_session", history))
        parts.append(
            f"Give one shared root-cause diagnosis, then exactly {k} candidate edits taking genuinely "
            "DIFFERENT Harness² strategies (e.g., Candidate 1: invariant reasoning/communication rule; "
            "Candidate 2: procedural skill or tool-guidance adjustment; Candidate 3: guardrail/scope constraint) "
            "so their behavioral contrasts can be probed in parallel."
        )
        data, cost, err = self._call("\n\n".join(parts), candidates_schema(list(files)), FIXER_SYSTEM)
        if err:
            return [Proposal("", {}, "", cost_usd=cost, error=err)]
        out = []
        for i, c in enumerate(data.get("candidates", [])[:k]):
            edits = {e["path"]: e["content"] for e in c.get("edits", []) if e.get("path") in files}
            out.append(
                Proposal(
                    data.get("diagnosis", ""),
                    edits,
                    c.get("expected_effect", ""),
                    approach=c.get("approach", f"candidate {i + 1}"),
                    cost_usd=cost if i == 0 else 0.0,
                )
            )
        return out or [Proposal("", {}, "", cost_usd=cost, error="fixer returned no candidates")]

    def compose(self, files: dict[str, str], failures: list[dict], probes: list[dict],
                lessons: list[dict]) -> Proposal:
        parts = [
            _section("editable_harness_files", "\n".join(f'<file path="{p}">\n{c}\n</file>' for p, c in files.items())),
            _section("failing_cases", failures),
            _section("probed_candidates", probes),
        ]
        if lessons:
            parts.append(_section("lessons_from_earlier_attempts", lessons))
        parts.append(
            "Each probed candidate was executed on the full evaluation suite. Perform contrastive synthesis: "
            "identify which edit resolved which case and which triggered regressions. Compose ONE unified "
            "harness edit that retains empirically validated improvements and drops regressive changes."
        )
        data, cost, err = self._call("\n\n".join(parts), compose_schema(list(files)), FIXER_SYSTEM)
        if err:
            return Proposal("", {}, "", approach="composed", cost_usd=cost, error=err)
        edits = {e["path"]: e["content"] for e in data.get("edits", []) if e.get("path") in files}
        return Proposal(
            data.get("diagnosis", ""),
            edits,
            data.get("expected_effect", ""),
            approach="composed from probes",
            cost_usd=cost,
        )

    def reflect(self, lessons: list[dict], session: list[dict]) -> tuple[list[dict] | None, float]:
        prompt = "\n\n".join([
            _section("current_lessons", lessons),
            _section("fix_session_record", session),
            "Return the updated lessons.",
        ])
        data, cost, err = self._call(prompt, LESSONS_SCHEMA, REFLECT_SYSTEM)
        return (None if err else data.get("lessons", [])), cost

    # ------------------------------------------------------------------ backends

    def _call(self, prompt: str, schema: dict, system: str) -> tuple[dict, float, str | None]:
        backend = self.cfg.get("backend", "claude-cli")
        if self._client is None and backend == "claude-cli":
            return self._cli(prompt, schema, system)
        if self._client is None and backend == "gemini":
            return self._gemini(prompt, schema, system)
        if self._client is None and backend == "openai":
            return self._openai(prompt, schema, system)
        return self._api(prompt, schema, system)

    def _cli(self, prompt: str, schema: dict, system: str) -> tuple[dict, float, str | None]:
        cmd = [
            claude_cli(self.cfg.get("cli")), "-p", "--output-format", "json", "--json-schema", json.dumps(schema),
            "--tools", "", "--strict-mcp-config", "--setting-sources", "", "--system-prompt", system,
            "--no-session-persistence", "--model", self.cfg["model"], "--effort", self.cfg.get("effort", "high"),
        ]
        try:
            r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=self.cfg.get("timeout_s", 900))
        except subprocess.TimeoutExpired:
            return {}, 0.0, "fixer timed out"
        try:
            out = json.loads(r.stdout)
        except json.JSONDecodeError:
            return {}, 0.0, f"fixer cli exit {r.returncode}: {(r.stderr or r.stdout)[-400:]}"
        cost = float(out.get("total_cost_usd") or 0.0)
        data = out.get("structured_output")
        if out.get("is_error") or not isinstance(data, dict):
            return {}, cost, f"fixer failed: {out.get('subtype')} {str(out.get('result'))[:300]}"
        return data, cost, None

    def _api(self, prompt: str, schema: dict, system: str) -> tuple[dict, float, str | None]:
        import anthropic

        client = self._client or anthropic.Anthropic()
        try:
            with client.messages.stream(
                model=self.cfg["model"],
                max_tokens=64000,
                system=system,
                output_config={
                    "effort": self.cfg.get("effort", "high"),
                    "format": {"type": "json_schema", "schema": schema},
                },
                messages=[{"role": "user", "content": prompt}],
            ) as stream:
                resp = stream.get_final_message()
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as e:
            return {}, 0.0, f"{type(e).__name__}: {e}"
        cost = (resp.usage.input_tokens * 4 + resp.usage.output_tokens * 20) / 1e6
        if resp.stop_reason in ("refusal", "max_tokens"):
            return {}, cost, f"fixer stopped: {resp.stop_reason}"
        text = next((b.text for b in resp.content if b.type == "text"), "")
        try:
            return json.loads(text), cost, None
        except json.JSONDecodeError:
            return {}, cost, "fixer returned invalid JSON"

    def _gemini(self, prompt: str, schema: dict, system: str) -> tuple[dict, float, str | None]:
        api_key = self.cfg.get("api_key") or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            return {}, 0.0, "missing GEMINI_API_KEY / GOOGLE_API_KEY"
        model = self.cfg.get("model", "gemini-2.5-pro")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt + "\n\nRespond strictly as JSON matching schema:\n" + json.dumps(schema)}]}],
            "generationConfig": {"responseMimeType": "application/json"},
        }
        try:
            req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=180) as resp:
                raw = json.loads(resp.read().decode())
            cand = (raw.get("candidates") or [{}])[0]
            text = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts") or [])
            return json.loads(text), 0.0, None
        except Exception as e:
            return {}, 0.0, f"Gemini fixer error: {type(e).__name__}: {e}"

    def _openai(self, prompt: str, schema: dict, system: str) -> tuple[dict, float, str | None]:
        api_key = self.cfg.get("api_key") or os.environ.get("OPENAI_API_KEY") or "local"
        base_url = (self.cfg.get("base_url") or os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
        payload = {
            "model": self.cfg.get("model", "gpt-4.1"),
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "response_format": {"type": "json_schema", "json_schema": {"name": "autoharness_fixer", "strict": True, "schema": schema}},
        }
        try:
            req = urllib.request.Request(
                f"{base_url}/chat/completions",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
            )
            with urllib.request.urlopen(req, timeout=180) as resp:
                raw = json.loads(resp.read().decode())
            text = ((raw.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            return json.loads(text), 0.0, None
        except Exception as e:
            return {}, 0.0, f"OpenAI fixer error: {type(e).__name__}: {e}"
