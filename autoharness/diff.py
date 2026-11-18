"""Compare a run against the baseline and decide whether the change is safe to ship."""

from __future__ import annotations

from typing import Any


def compare(base: dict[str, Any], cur: dict[str, Any], gate: dict[str, Any]) -> dict[str, Any]:
    rows = []
    regressions, improvements = [], []
    for cid in sorted(set(base["cases"]) | set(cur["cases"])):
        b, c = base["cases"].get(cid), cur["cases"].get(cid)
        if b is None:
            rows.append({"case": cid, "status": "new", "base": None, "cur": c["pass_rate"], "delta": None})
            continue
        if c is None:
            rows.append({"case": cid, "status": "removed", "base": b["pass_rate"], "cur": None, "delta": None})
            continue
        d = round(c["pass_rate"] - b["pass_rate"], 4)
        tok_delta = (c["mean_tokens"] - b["mean_tokens"]) / b["mean_tokens"] if b["mean_tokens"] else 0.0
        status = "same"
        if d <= -gate["max_case_drop"]:
            status = "regressed"
            regressions.append(cid)
        elif d >= gate["max_case_drop"]:
            status = "improved"
            improvements.append(cid)
        rows.append({"case": cid, "status": status, "base": b["pass_rate"], "cur": c["pass_rate"],
                     "delta": d, "token_delta_pct": round(tok_delta * 100, 1),
                     "turn_delta": round(c["mean_turns"] - b["mean_turns"], 2)})
    suite_delta = round(cur["pass_rate"] - base["pass_rate"], 4)
    tok_b = base["input_tokens"] + base["output_tokens"]
    tok_c = cur["input_tokens"] + cur["output_tokens"]
    reasons = []
    if suite_delta < -gate["max_pass_rate_drop"]:
        reasons.append(f"suite pass rate dropped {suite_delta:+.1%}")
    if regressions:
        reasons.append(f"{len(regressions)} case(s) regressed: {', '.join(regressions)}")
    return {
        "baseline": base["run_id"], "current": cur["run_id"],
        "suite_delta": suite_delta,
        "score_delta": round(cur["mean_score"] - base["mean_score"], 4),
        "token_delta_pct": round((tok_c - tok_b) / tok_b * 100, 1) if tok_b else 0.0,
        "regressions": regressions, "improvements": improvements, "rows": rows,
        "gate_passed": not reasons, "gate_reasons": reasons,
    }


def format_text(cmp: dict[str, Any]) -> str:
    lines = [f"baseline {cmp['baseline']}  ->  current {cmp['current']}",
             f"pass rate {cmp['suite_delta']:+.1%}   score {cmp['score_delta']:+.3f}   tokens {cmp['token_delta_pct']:+.1f}%"]
    for r in cmp["rows"]:
        if r["status"] == "same":
            continue
        base = "-" if r["base"] is None else f"{r['base']:.0%}"
        cur = "-" if r["cur"] is None else f"{r['cur']:.0%}"
        lines.append(f"  {r['status']:>9}  {r['case']}: {base} -> {cur}")
    lines.append("GATE: " + ("PASS" if cmp["gate_passed"] else "FAIL - " + "; ".join(cmp["gate_reasons"])))
    return "\n".join(lines)
