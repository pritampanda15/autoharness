"""Layered grading: deterministic -> trace -> LLM judge.

Cheap, certain checks run first. The judge only runs (and only costs money) when
the cheap layers could not already decide the trial.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from ..cases import Case
from ..sandbox import Sandbox
from ..trace import Trace
from . import deterministic, trace_checks
from .autonomous import verify_evidence_grounding
from .judge import Judge

LAYER_OF: dict[str, str] = {
    **{k: "deterministic" for k in deterministic.CHECKS},
    **{k: "trace" for k in trace_checks.CHECKS},
    "judge": "judge",
}


@dataclass
class CheckResult:
    name: str
    type: str
    layer: str
    passed: bool
    score: float
    detail: str = ""
    required: bool = True
    weight: float = 1.0
    skipped: bool = False
    confidence: float | None = None
    votes: list[dict[str, Any]] = field(default_factory=list)
    needs_review: bool = False
    cost_usd: float = 0.0


@dataclass
class TrialResult:
    case_id: str
    trial: int
    passed: bool
    score: float
    checks: list[CheckResult]
    needs_review: bool
    review_reasons: list[str]
    error: str | None
    turns: int
    tokens: int
    input_tokens: int
    output_tokens: int
    duration_s: float
    agent_cost_usd: float = 0.0
    judge_cost_usd: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def grade(
    case: Case,
    trace: Trace,
    sandbox: Sandbox | None,
    judge: Judge | None,
    judge_cfg: dict[str, Any],
    judge_trusted: bool = True,
) -> TrialResult:
    results: list[CheckResult] = []
    judge_specs = []

    for i, spec in enumerate(case.checks):
        ctype = spec.get("type")
        name = spec.get("name") or f"{ctype}#{i}"
        layer = LAYER_OF.get(ctype)
        if layer is None:
            results.append(CheckResult(name, str(ctype), "config", False, 0.0, f"unknown check type {ctype!r}"))
            continue
        if layer == "judge":
            judge_specs.append((name, spec))
            continue
        fn = deterministic.CHECKS.get(ctype) or trace_checks.CHECKS[ctype]
        try:
            ok, detail = fn(spec, trace, sandbox)
        except Exception as e:
            ok, detail = False, f"check crashed: {type(e).__name__}: {e}"
        results.append(
            CheckResult(name, ctype, layer, ok, 1.0 if ok else 0.0, detail,
                        spec.get("required", True), float(spec.get("weight", 1.0)))
        )

    cheap_failed = any(r.required and not r.passed for r in results)
    for name, spec in judge_specs:
        req, w = spec.get("required", True), float(spec.get("weight", 1.0))
        if cheap_failed and judge_cfg.get("skip_if_deterministic_fails", True):
            results.append(CheckResult(name, "judge", "judge", False, 0.0,
                                       "skipped: a cheaper required check already failed", req, w, skipped=True))
            continue
        if judge is None:  # --no-judge: grade on the cheap layers only
            results.append(CheckResult(name, "judge", "judge", False, 0.0, "judge disabled for this run", req, w,
                                       skipped=True))
            continue
        v = judge.evaluate(case, trace, spec)
        results.append(CheckResult(name, "judge", "judge", v.passed, v.score, v.reasoning, req, w,
                                   confidence=v.confidence, votes=v.votes, needs_review=v.uncertain,
                                   cost_usd=v.cost_usd))

    counted = [r for r in results if not r.skipped]
    passed = all(r.passed for r in counted if r.required) and not any(
        r.layer == "config" for r in results
    )
    total_w = sum(r.weight for r in counted) or 1.0
    score = sum(r.score * r.weight for r in counted) / total_w

    reasons = []
    judged = [r for r in counted if r.layer == "judge"]
    autonomous_mode = bool(judge_cfg.get("autonomous", False))
    if not cheap_failed:  # only bother a human when the judge is what decided the trial
        for r in judged:
            if r.needs_review:
                reasons.append(f"{r.name}: judge uncertain (confidence={r.confidence}, votes split or low)")
        if judged and not judge_trusted and not any(r.needs_review for r in judged):
            if autonomous_mode:
                all_grounded = all(
                    verify_evidence_grounding(
                        case, trace, sandbox, r.votes, float(judge_cfg.get("min_confidence", 0.7))
                    ).grounded
                    for r in judged
                )
                if not all_grounded:
                    reasons.append("judge not calibrated and autonomous grounding check was inconclusive")
            else:
                reasons.append("judge not calibrated / below agreement threshold")

    return TrialResult(
        case_id=case.id, trial=trace.trial, passed=passed, score=round(score, 4), checks=results,
        needs_review=bool(reasons), review_reasons=reasons, error=trace.error, turns=trace.turns,
        tokens=trace.total_tokens, input_tokens=trace.input_tokens, output_tokens=trace.output_tokens,
        duration_s=trace.duration_s, agent_cost_usd=round(trace.cost_usd, 5),
        judge_cost_usd=round(sum(r.cost_usd for r in results), 5),
    )
