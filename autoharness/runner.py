"""Runs every case x trial, grades it, and writes the run to disk."""

from __future__ import annotations

import json
import statistics
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from .agents import Agent
from .cases import Case
from .graders import TrialResult, grade
from .graders.judge import Judge
from .sandbox import Sandbox
from .store import Store
from .trace import Trace

# $ per 1M tokens (input, output). Used only for the cost estimate in reports.
PRICES = {
    "opus": (4.0, 20.0),
    "sonnet": (2.0, 10.0),
    "haiku": (0.10, 0.50),
    "claude-fable-5-2": (8.0, 40.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
}


def judge_trusted(store: Store, cal_cfg: dict[str, Any], judge_cfg: dict[str, Any]) -> tuple[bool, str]:
    cal = store.calibration()
    if not cal:
        return False, "judge never calibrated - judge-decided trials go to human review until it is"
    if cal.get("judge_model") != judge_cfg["model"]:
        return False, f"calibration was for {cal.get('judge_model')}, judge is now {judge_cfg['model']}"
    if cal["n"] < cal_cfg["min_labels"]:
        return False, f"only {cal['n']} human labels (need {cal_cfg['min_labels']})"
    if cal["agreement"] < cal_cfg["min_agreement"]:
        return False, f"judge agrees with humans {cal['agreement']:.0%} (< {cal_cfg['min_agreement']:.0%})"
    return True, f"judge agrees with humans {cal['agreement']:.0%} on {cal['n']} labels"


def _artifact_paths(case: Case) -> list[str]:
    paths: list[str] = []
    for c in case.checks:
        paths += c.get("include_files", [])
        paths += c.get("snapshot", [])
    return sorted(set(paths))


def run_trial(case: Case, trial: int, agent: Agent, judge: Judge | None, cfg, trusted: bool,
              keep: bool) -> tuple[Trace, TrialResult]:
    with Sandbox(case, trial, keep=keep) as sb:
        try:
            trace = agent.run(case, sb, trial)
        except Exception as e:  # an adapter crash is a failed trial, not a failed suite
            trace = Trace(case_id=case.id, trial=trial, workdir=str(sb.dir),
                          error=f"agent crashed: {type(e).__name__}: {e}\n{traceback.format_exc(limit=3)}")
        for p in _artifact_paths(case):
            try:
                trace.artifacts[p] = sb.resolve(p).read_text()
            except Exception:
                pass
        result = grade(case, trace, sb, judge, cfg["judge"], judge_trusted=trusted)
    return trace, result


def run_suite(cfg, cases: list[Case], agent: Agent, judge: Judge | None, store: Store, *,
              trials: int | None = None, workers: int | None = None, label: str = "",
              harness_fp: str = "", cases_fp: str = "", keep: bool = False, quiet: bool = False) -> dict[str, Any]:
    run_id, run_dir = store.new_run(label)
    trusted, trust_msg = judge_trusted(store, cfg["calibration"], cfg["judge"])
    jobs = [(c, t) for c in cases for t in range(1, (c.trials or trials or cfg["trials"]) + 1)]
    meta = {
        "run_id": run_id, "started": time.strftime("%Y-%m-%d %H:%M:%S"), "harness_fingerprint": harness_fp, "cases_fingerprint": cases_fp,
        "agent": {k: v for k, v in cfg["agent"].items()}, "judge": cfg["judge"], "judge_trusted": trusted,
        "judge_trust_reason": trust_msg, "n_cases": len(cases), "n_trials": len(jobs),
    }
    store.write_json(run_dir / "meta.json", meta)
    if not quiet:
        print(f"run {run_id}: {len(cases)} cases, {len(jobs)} trials  [{trust_msg}]", file=sys.stderr)

    lock = threading.Lock()
    results: list[TrialResult] = []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers or cfg["workers"]) as pool, \
            open(run_dir / "results.jsonl", "w") as out:
        futs = {pool.submit(run_trial, c, t, agent, judge, cfg, trusted, keep): (c, t) for c, t in jobs}
        for fut in as_completed(futs):
            trace, res = fut.result()
            with lock:
                store.save_trace(run_dir, trace)
                out.write(json.dumps(res.to_dict(), default=str) + "\n")
                out.flush()
                results.append(res)
                if not quiet:
                    mark = "PASS" if res.passed else "FAIL"
                    flag = " [review]" if res.needs_review else ""
                    print(f"  {mark} {res.case_id} t{res.trial}  score={res.score:.2f} "
                          f"turns={res.turns} tok={res.tokens}{flag}", file=sys.stderr)

    summary = summarise(run_id, cases, results, cfg, meta, time.time() - t0)
    store.write_json(run_dir / "summary.json", summary)
    return summary


def summarise(run_id: str, cases: list[Case], results: list[TrialResult], cfg, meta, wall: float) -> dict[str, Any]:
    by_case: dict[str, list[TrialResult]] = {}
    for r in results:
        by_case.setdefault(r.case_id, []).append(r)
    case_rows = {}
    for c in cases:
        rs = sorted(by_case.get(c.id, []), key=lambda r: r.trial)
        if not rs:
            continue
        failed_checks: dict[str, int] = {}
        for r in rs:
            for ch in r.checks:
                if not ch.passed and not ch.skipped:
                    failed_checks[ch.name] = failed_checks.get(ch.name, 0) + 1
        case_rows[c.id] = {
            "pass_rate": round(sum(r.passed for r in rs) / len(rs), 4),
            "mean_score": round(statistics.mean(r.score for r in rs), 4),
            "trials": len(rs),
            "mean_turns": round(statistics.mean(r.turns for r in rs), 2),
            "mean_tokens": round(statistics.mean(r.tokens for r in rs)),
            "mean_duration_s": round(statistics.mean(r.duration_s for r in rs), 2),
            "needs_review": sum(r.needs_review for r in rs),
            "errors": [r.error for r in rs if r.error],
            "failed_checks": failed_checks,
            "tags": c.tags, "split": c.split, "description": c.description,
        }
    rates = [row["pass_rate"] for row in case_rows.values()]
    in_tok = sum(r.input_tokens for r in results)
    out_tok = sum(r.output_tokens for r in results)
    pin, pout = PRICES.get(cfg["agent"].get("model", ""), (0.0, 0.0))
    reported = sum(r.agent_cost_usd for r in results)
    agent_cost = reported if reported else (in_tok * pin + out_tok * pout) / 1e6
    return {
        "run_id": run_id,
        "meta": meta,
        "pass_rate": round(statistics.mean(rates), 4) if rates else 0.0,
        # pass^k: share of cases that passed on EVERY trial - the reliability number
        "all_trials_pass_rate": round(sum(r == 1.0 for r in rates) / len(rates), 4) if rates else 0.0,
        "mean_score": round(statistics.mean(row["mean_score"] for row in case_rows.values()), 4) if rates else 0.0,
        "n_trials": len(results),
        "review_queue": sum(r.needs_review for r in results),
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "est_agent_cost_usd": round(agent_cost, 4),
        "judge_cost_usd": round(sum(r.judge_cost_usd for r in results), 4),
        "wall_time_s": round(wall, 1),
        "cases": case_rows,
    }
