"""The closed loop: change -> run -> (if failing) fix -> verify -> keep or revert.

    watch()    polls the harness files; on a change it calls cycle()
    cycle()    runs the suite and gates it against the baseline; if dev cases fail
               and autofix is on, hands over to autofix()
    autofix()  asks a fixer model for an edit to the *harness files only*, re-runs
               the whole suite (including holdout cases the fixer never sees), and
               keeps the edit only if it is strictly better with no regressions.
               Otherwise the files are restored byte-for-byte.

Guardrails against a fixer that "games" the eval:
  - it can only write files listed in fixer.editable (never cases, checks, config)
  - holdout cases are hidden from it but count in the accept decision
  - edits that paste case ids / expected answers into the harness are rejected
  - token-cost and file-growth limits, a $ budget, and a max attempt count
  - a harness+cases state that was already tried and could not be fixed is not retried
"""

from __future__ import annotations

import copy
import difflib
import json
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .agents import build_agent
from .cases import Case, load_cases
from .config import Config
from .diff import compare
from .fixer import Fixer, Proposal
from .graders.judge import Judge
from .lessons import LessonStore
from .runner import run_suite
from .store import Store, fingerprint
from .tree import HarnessTree


def say(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------- fingerprints & running


def editable_rels(cfg: Config) -> list[str]:
    """Expand `fixer.editable` entries: single files stay as-is; Harness² directories expand to their components."""
    out: list[str] = []
    for rel in cfg["fixer"]["editable"]:
        p = cfg.path(rel)
        if p.exists() and p.is_dir():
            out.extend(HarnessTree.load(p).editable_paths(rel_to=cfg.root))
        else:
            out.append(rel)
    return list(dict.fromkeys(out))


def editable_files(cfg: Config) -> list[Path]:
    return [cfg.path(p) for p in editable_rels(cfg)]


def harness_fp(cfg: Config) -> str:
    parts = [json.dumps(cfg["agent"], sort_keys=True)]
    files = list(editable_files(cfg))
    spf = cfg["agent"].get("system_prompt_file")
    if spf:
        sp_path = cfg.path(spf)
        if sp_path.is_dir():
            parts.append(HarnessTree.load(sp_path).fingerprint())
        elif sp_path not in files:
            files.append(sp_path)
    for f in sorted(set(files)):
        parts.append(f.read_text() if (f.exists() and f.is_file()) else "")
    return fingerprint(*parts)


def case_files(cfg: Config) -> list[Path]:
    out: list[Path] = []
    for p in cfg["cases"]:
        p = cfg.path(p)
        out += sorted(p.rglob("*.y*ml")) if p.is_dir() else [p]
    return out


def cases_fp(cfg: Config) -> str:
    return fingerprint(*[f.read_text() for f in case_files(cfg)])


def run_once(cfg: Config, store: Store, label: str, judge: Judge | None = None,
             agent_factory: Callable | None = None, quiet: bool = False) -> dict[str, Any]:
    cases = load_cases([cfg.path(p) for p in cfg["cases"]])
    agent = (agent_factory or (lambda c: build_agent(c["agent"], c.root)))(cfg)
    if judge is None:
        judge = Judge(cfg["judge"])
    summary = run_suite(cfg, cases, agent, judge, store, label=label, harness_fp=harness_fp(cfg),
                        cases_fp=cases_fp(cfg), quiet=quiet)
    if not store.baseline():
        store.set_baseline(summary["run_id"])
    return summary


def split_rate(summary: dict[str, Any], split: str) -> float | None:
    rows = [r["pass_rate"] for r in summary["cases"].values() if r.get("split", "dev") == split]
    return sum(rows) / len(rows) if rows else None


def run_cost(summary: dict[str, Any]) -> float:
    return float(summary.get("est_agent_cost_usd", 0)) + float(summary.get("judge_cost_usd", 0))


# ---------------------------------------------------------------------------- failure digest for the fixer


def failure_digest(store: Store, summary: dict[str, Any], cases: dict[str, Case]) -> list[dict[str, Any]]:
    """Everything the fixer needs to know about the failing DEV cases. Holdout is never included."""
    run_dir = store.run_dir(summary["run_id"])
    results = store.load_results(run_dir)
    out = []
    for cid, row in summary["cases"].items():
        if row.get("split", "dev") != "dev" or row["pass_rate"] >= 1.0 or cid not in cases:
            continue
        failing = [r for r in results if r["case_id"] == cid and not r["passed"]]
        if not failing:
            continue
        r = failing[0]
        trace = store.load_trace(run_dir, cid, r["trial"])
        out.append({
            "case": cid,
            "task": cases[cid].prompt,
            "pass_rate": row["pass_rate"],
            "failed_checks": [{"check": c["name"], "detail": c["detail"][:500]}
                              for c in r["checks"] if not c["passed"] and not c["skipped"]],
            "agent_actions": [f"{tc.name}({json.dumps(tc.input)[:160]})" + (" -> ERROR" if tc.is_error else "")
                              for tc in trace.tool_calls[:25]],
            "agent_final_reply": trace.final_output[:1500],
            "run_error": trace.error,
        })
    return out


# ---------------------------------------------------------------------------- guards & decision


def leaked_terms(cases: list[Case], old: str, new: str) -> list[str]:
    """Case-specific strings the edit newly introduced (case ids, expected answers). Overfitting signal."""
    terms: set[str] = set()
    for c in cases:
        terms.add(c.id)
        for chk in c.checks:
            if chk.get("type") == "output_contains":
                terms.add(str(chk["text"]))
            for key in ("pattern", "text"):
                for tok in re.findall(r"[A-Za-z_][A-Za-z0-9_]{5,}|\d{3,}", str(chk.get(key, ""))):
                    terms.add(tok)
        for fname in c.files:
            terms.add(fname)
    common = {"python", "pytest", "return", "import", "should", "string", "number"}
    return sorted(t for t in terms if t.lower() not in common and len(t) >= 4
                  and t in new and t not in old)


def static_guard(cfg: Config, all_cases: list[Case], files: dict[str, str], prop: Proposal) -> list[str]:
    """Cheap checks before spending money on a run."""
    if prop.error:
        return [prop.error]
    if not prop.edits:
        return ["no edits proposed"]
    reasons = []
    for path, new in prop.edits.items():
        old = files[path]
        if new == old:
            reasons.append(f"{path} unchanged")
        if len(new) > len(old) * (1 + cfg["fixer"]["max_growth_pct"] / 100) + 1000:
            reasons.append(f"{path} grew from {len(old)} to {len(new)} chars")
        leaks = leaked_terms(all_cases, old, new)
        if leaks:
            reasons.append(f"{path} mentions case-specific terms {leaks} (overfitting)")
    return reasons


def decide(base: dict[str, Any], cand: dict[str, Any], cfg: Config) -> tuple[bool, list[str], dict[str, Any]]:
    cmp = compare(base, cand, cfg["gate"])
    reasons = list(cmp["gate_reasons"])
    dev_b, dev_c = split_rate(base, "dev") or 0.0, split_rate(cand, "dev") or 0.0
    ho_b, ho_c = split_rate(base, "holdout"), split_rate(cand, "holdout")
    improved = dev_c > dev_b + 1e-9 or (dev_c >= dev_b and cand["mean_score"] > base["mean_score"] + 0.01)
    if not improved:
        reasons.append(f"no improvement on dev cases ({dev_b:.0%} -> {dev_c:.0%})")
    if ho_b is not None and ho_c is not None and ho_c < ho_b - 1e-9:
        reasons.append(f"hidden holdout cases got worse ({ho_b:.0%} -> {ho_c:.0%}): likely overfitting")
    if cmp["token_delta_pct"] > cfg["fixer"]["max_token_increase_pct"]:
        reasons.append(f"agent became {cmp['token_delta_pct']:+.0f}% more expensive")
    return not reasons, reasons, {**cmp, "dev": (dev_b, dev_c), "holdout": (ho_b, ho_c)}


def rank_key(summary: dict[str, Any]) -> tuple:
    """Best candidate: most dev passes, then holdout, then score, then cheapest."""
    return (split_rate(summary, "dev") or 0.0, split_rate(summary, "holdout") or 0.0,
            summary["mean_score"], -(summary["input_tokens"] + summary["output_tokens"]))


def unified(path: str, old: str, new: str) -> str:
    return "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), f"a/{path}", f"b/{path}"))


# ---------------------------------------------------------------------------- isolated probes


SKIP_COPY = {".git", "__pycache__", ".venv", "venv", "node_modules", ".pytest_cache", ".mypy_cache"}


def _workspace(cfg: Config, edits: dict[str, str]) -> tuple[Config, Path]:
    """A private copy of everything the run reads, with the candidate's edits applied.
    Candidates can then be evaluated in parallel without touching the real files."""
    tmp = Path(tempfile.mkdtemp(prefix="autoharness-probe-"))
    rels = set(cfg["fixer"]["editable"]) | set(cfg["cases"]) | set(cfg["fixer"].get("workspace_include", []))
    if cfg["agent"].get("system_prompt_file"):
        rels.add(cfg["agent"]["system_prompt_file"])
    for rel in rels:
        src = cfg.path(rel)
        if not src.exists() or Path(rel).is_absolute():
            continue
        dst = tmp / rel
        if src.is_dir():
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns(*SKIP_COPY), dirs_exist_ok=True)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    for rel, content in edits.items():
        (tmp / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp / rel).write_text(content)
    return Config(raw=copy.deepcopy(cfg.raw), root=tmp), tmp


def probe(cfg: Config, store: Store, prop: Proposal, label: str, judge, agent_factory) -> dict[str, Any]:
    pcfg, tmp = _workspace(cfg, prop.edits)
    try:
        return run_once(pcfg, store, label, judge=judge, agent_factory=agent_factory, quiet=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def case_outcomes(base: dict[str, Any], cand: dict[str, Any], failing: list[str]) -> dict[str, Any]:
    """Per-case contrast used by compose(): what this candidate fixed and what it broke."""
    fixed = [c for c in failing if cand["cases"].get(c, {}).get("pass_rate", 0) > base["cases"][c]["pass_rate"]]
    broke = [c for c, r in cand["cases"].items() if c in base["cases"]
             and r.get("split", "dev") == "dev" and r["pass_rate"] < base["cases"][c]["pass_rate"]]
    return {"fixed_or_improved": fixed, "made_worse": broke}


# ---------------------------------------------------------------------------- the fix loop


@dataclass
class FixOutcome:
    status: str  # clean | fixed | partially_fixed | gave_up | skipped | error
    accepted: list[dict[str, Any]] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)
    final_run: str | None = None
    cost_usd: float = 0.0
    lessons: int = 0
    message: str = ""


def _attempts_file(store: Store) -> Path:
    return store.root / "fix_attempts.json"


def _state_key(cfg: Config) -> str:
    return harness_fp(cfg) + ":" + cases_fp(cfg)


def lesson_store(cfg: Config) -> LessonStore:
    return LessonStore(cfg.path(cfg["fixer"]["lessons_file"]), cfg["fixer"]["max_lessons"])


def autofix(cfg: Config, store: Store, base: dict[str, Any], *, fixer: Fixer | None = None,
            judge: Judge | None = None, agent_factory: Callable | None = None) -> FixOutcome:
    """Propose K candidates -> probe each in isolation (in parallel) -> optionally compose the best
    parts -> keep the best candidate that passes every guard, or revert. Repeat up to max_iters,
    then reflect what was learned into the lessons store."""
    fcfg = cfg["fixer"]
    ed_rels = editable_rels(cfg)
    if not ed_rels:
        return FixOutcome("error", message="fixer.editable is empty: nothing the fixer is allowed to change")
    all_cases = load_cases([cfg.path(p) for p in cfg["cases"]])
    cases = {c.id: c for c in all_cases}
    failures = failure_digest(store, base, cases)
    if not failures:
        return FixOutcome("clean", final_run=base["run_id"], message="no failing dev cases")

    tried = json.loads(_attempts_file(store).read_text()) if _attempts_file(store).exists() else {}
    if _state_key(cfg) in tried:
        return FixOutcome("skipped", final_run=base["run_id"],
                          message="this exact harness + cases was already tried and could not be fixed; "
                                  "change something or run `autoharness fix --retry`")

    fixer = fixer or Fixer(fcfg)
    lstore = lesson_store(cfg)
    lessons = lstore.load()
    k = max(1, int(fcfg["candidates"]))
    spent = 0.0
    out = FixOutcome("gave_up")
    history: list[dict[str, Any]] = []
    session: list[dict[str, Any]] = []  # everything that happened, for the reflector
    start_fail = len(failures)

    for attempt in range(1, fcfg["max_iters"] + 1):
        if spent >= fcfg["max_usd_per_trigger"]:
            out.message = f"budget ${fcfg['max_usd_per_trigger']} reached"
            break
        files = {p: cfg.path(p).read_text() for p in ed_rels}
        passing = [cid for cid, r in base["cases"].items() if r["pass_rate"] == 1.0 and r.get("split") == "dev"]
        failing_ids = [f["case"] for f in failures]
        say(f"attempt {attempt}: {len(failures)} failing dev case(s): {', '.join(failing_ids)} - "
            f"asking for {k} candidate(s), {len(lessons)} lesson(s) in context")

        # 1. PROPOSE: K diverse candidates in one call
        props = fixer.propose(files, failures, passing, history, lessons, k=k)
        spent += sum(p.cost_usd for p in props)
        if len(props) == 1 and props[0].error and not props[0].edits:
            say(f"  fixer unavailable: {props[0].error}")
            out.status = "error"
            out.message = props[0].error
            break
        if props and props[0].diagnosis:
            say(f"  diagnosis: {props[0].diagnosis[:300]}")

        # 2. Static guards (free) before any run
        runnable: list[tuple[int, Proposal]] = []
        for i, p in enumerate(props, 1):
            reasons = static_guard(cfg, all_cases, files, p)
            diff = "\n".join(unified(path, files[path], c) for path, c in p.edits.items())
            if reasons:
                say(f"  candidate {i} ({p.approach[:60]}): rejected before running - {'; '.join(reasons)}")
                rec = {"attempt": attempt, "candidate": i, "approach": p.approach, "diagnosis": p.diagnosis,
                       "diff": diff[:3000], "reasons": reasons, "accepted": False, "run": None}
                out.rejected.append(rec)
                session.append(rec)
                _log(store, "REJECTED (STATIC GUARD)", rec, {"dev": (split_rate(base, "dev"), None), "holdout": (split_rate(base, "holdout"), None), "token_delta_pct": 0.0})
                history.append({k2: rec[k2] for k2 in ("approach", "diff", "reasons")})
            else:
                runnable.append((i, p))

        # 3. PROBE: run every surviving candidate on the full suite, each in its own workspace
        probes: list[dict[str, Any]] = []
        if runnable:
            with ThreadPoolExecutor(max_workers=max(1, int(fcfg["parallel"]))) as pool:
                futs = {pool.submit(probe, cfg, store, p, f"fix{attempt}c{i}", judge, agent_factory): (i, p)
                        for i, p in runnable}
                for fut in as_completed(futs):
                    i, p = futs[fut]
                    try:
                        cand = fut.result()
                    except Exception as e:
                        say(f"  candidate {i}: probe crashed: {e}")
                        continue
                    spent += run_cost(cand)
                    probes.append(_judge_probe(cfg, base, cand, p, i, files, failing_ids))

        # 4. COMPOSE: merge what worked across candidates, using the measured per-case contrast
        if fcfg["compose"] and len(probes) >= 2 and spent < fcfg["max_usd_per_trigger"]:
            best_single = max((pr for pr in probes if pr["ok"]), key=lambda pr: rank_key(pr["summary"]), default=None)
            covers_all = best_single and not failure_digest(store, best_single["summary"], cases)
            if not covers_all:
                say("  composing a merged candidate from the probe results")
                comp = fixer.compose(files, failures, [_probe_view(pr) for pr in probes], lessons)
                spent += comp.cost_usd
                reasons = static_guard(cfg, all_cases, files, comp)
                if reasons:
                    say(f"  composed candidate rejected before running - {'; '.join(reasons)}")
                    session.append({"attempt": attempt, "candidate": "composed", "approach": comp.approach,
                                    "reasons": reasons, "accepted": False, "run": None})
                else:
                    cand = probe(cfg, store, comp, f"fix{attempt}compose", judge, agent_factory)
                    spent += run_cost(cand)
                    probes.append(_judge_probe(cfg, base, cand, comp, "composed", files, failing_ids))

        for pr in probes:
            mark = "ok" if pr["ok"] else "rejected: " + "; ".join(pr["reasons"])
            say(f"  candidate {pr['candidate']} ({pr['approach'][:50]}): dev {_fmt(pr['dev'][0])} -> {_fmt(pr['dev'][1])}, "
                f"holdout {_fmt(pr['holdout'][0])} -> {_fmt(pr['holdout'][1])} - {mark}")

        # 5. SELECT the best accepted candidate and apply it to the real files
        winners = [pr for pr in probes if pr["ok"]]
        if winners:
            win = max(winners, key=lambda pr: rank_key(pr["summary"]))
            for path, content in win["edits"].items():
                cfg.path(path).write_text(content)
            store.set_baseline(win["summary"]["run_id"])
            entry = {"attempt": attempt, "run": win["summary"]["run_id"], "candidate": win["candidate"],
                     "approach": win["approach"], "diagnosis": win["diagnosis"], "diff": win["diff"],
                     "dev": win["dev"], "holdout": win["holdout"], "accepted": True}
            out.accepted.append(entry)
            _log(store, "ACCEPTED", entry, win["cmp"])
            say(f"  KEPT candidate {win['candidate']} - {win['summary']['run_id']} is the new baseline")
            base = win["summary"]
            failures = failure_digest(store, base, cases)
            history = []
        for pr in probes:
            if winners and pr is win:
                session.append({**entry})
                continue
            rec = {"attempt": attempt, "candidate": pr["candidate"], "approach": pr["approach"],
                   "diagnosis": pr["diagnosis"], "diff": pr["diff"][:3000], "run": pr["summary"]["run_id"],
                   "reasons": pr["reasons"] or ["not the best candidate"], "dev": pr["dev"],
                   "holdout": pr["holdout"], "accepted": False, **pr["outcomes"]}
            out.rejected.append(rec)
            session.append(rec)
            if not pr["ok"]:
                _log(store, "REJECTED", rec, pr["cmp"])
                history.append({k2: rec[k2] for k2 in ("approach", "diff", "reasons", "fixed_or_improved", "made_worse")})
        if not winners:
            say("  no candidate passed every guard - real files left unchanged")
        if not failures:
            out.status = "fixed"
            break

    # 6. REFLECT: turn this session's evidence into lessons for next time
    if session and fcfg["reflect"]:
        new, cost = fixer.reflect(lessons, session)
        spent += cost
        if new is None:
            new = LessonStore.fallback(lessons, session)
        lstore.save(new)
        out.lessons = len(new)
        say(f"lessons updated: {len(new)} in {lstore.path}")

    out.cost_usd = round(spent, 4)
    out.final_run = base["run_id"]
    if out.status != "fixed":
        remaining = len(failures)
        out.status = "partially_fixed" if remaining < start_fail else "gave_up"
        if out.status == "gave_up":
            tried[_state_key(cfg)] = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "run": base["run_id"]}
            _attempts_file(store).write_text(json.dumps(tried, indent=1))
        out.message = out.message or f"{remaining} dev case(s) still failing after {attempt} attempt(s)"
    return out


def _judge_probe(cfg, base, cand, prop: Proposal, idx, files, failing_ids) -> dict[str, Any]:
    ok, reasons, cmp = decide(base, cand, cfg)
    return {"candidate": idx, "approach": prop.approach, "diagnosis": prop.diagnosis, "edits": prop.edits,
            "diff": "\n".join(unified(p, files[p], c) for p, c in prop.edits.items()),
            "summary": cand, "ok": ok, "reasons": reasons, "cmp": cmp, "dev": cmp["dev"], "holdout": cmp["holdout"],
            "outcomes": case_outcomes(base, cand, failing_ids)}


def _probe_view(pr: dict[str, Any]) -> dict[str, Any]:
    """What compose() gets to see about a probe: the edit and its measured effect, no holdout detail."""
    return {"candidate": pr["candidate"], "approach": pr["approach"], "diff": pr["diff"][:4000],
            "dev_pass_rate": pr["dev"], **pr["outcomes"],
            "rejected_because": [r for r in pr["reasons"] if "holdout" not in r] or
                                (["hurt hidden cases"] if pr["reasons"] else [])}


def _fmt(x):
    return "n/a" if x is None else f"{x:.0%}"


def _log(store: Store, verdict: str, entry: dict[str, Any], cmp: dict[str, Any]) -> None:
    lines = [f"\n## {time.strftime('%Y-%m-%d %H:%M:%S')} - {verdict} (attempt {entry['attempt']}, "
             f"candidate {entry.get('candidate')}, run {entry.get('run')})\n"]
    if entry.get("approach"):
        lines.append(f"**Approach:** {entry['approach']}\n")
    if entry.get("diagnosis"):
        lines.append(f"**Diagnosis:** {entry['diagnosis']}\n")
    if entry.get("reasons"):
        lines.append("**Rejected because:** " + "; ".join(entry["reasons"]) + "\n")
    lines.append(f"dev {_fmt(cmp['dev'][0])} -> {_fmt(cmp['dev'][1])} · holdout {_fmt(cmp['holdout'][0])} -> "
                 f"{_fmt(cmp['holdout'][1])} · tokens {cmp['token_delta_pct']:+.0f}%\n")
    lines.append("```diff\n" + entry.get("diff", "") + "\n```\n")
    with open(store.root / "fixlog.md", "a") as f:
        f.write("".join(lines))


# ---------------------------------------------------------------------------- one trigger, and the watcher


def cycle(cfg: Config, store: Store, *, autofix_on: bool = True, label: str = "watch",
          fixer: Fixer | None = None, judge: Judge | None = None,
          agent_factory: Callable | None = None) -> dict[str, Any]:
    """One full trigger: heal code_paths (if configured), run + gate vs baseline, then fix harness if needed."""
    heal_out = None
    code_paths = cfg.raw.get("code_paths") or []
    hcfg = cfg.raw.get("healer") or {}
    if code_paths and hcfg.get("enabled", True):
        from .healer import heal_codebase

        say(f"scanning code_paths ({', '.join(str(p) for p in code_paths)}) for health & self-healing")
        heal_out = heal_codebase(
            cfg,
            store,
            code_paths,
            agent_factory=agent_factory,
            run_harness_cycle=False,
            sync_memory=bool(hcfg.get("sync_memory", True)),
            dry_run=not autofix_on,
        )

    base_id = store.baseline()
    say("change detected - running the suite")
    summary = run_once(cfg, store, label, judge=judge, agent_factory=agent_factory, quiet=True)
    spent = run_cost(summary) + (heal_out.cost_usd if heal_out else 0.0)
    gate_msg = "first run (now the baseline)"
    if base_id and base_id != summary["run_id"]:
        ok, reasons, cmp = True, [], compare(store.load_summary(base_id), summary, cfg["gate"])
        gate_msg = "gate PASS" if cmp["gate_passed"] else "gate FAIL: " + "; ".join(cmp["gate_reasons"])
        if cmp["gate_passed"] and summary["pass_rate"] >= store.load_summary(base_id)["pass_rate"]:
            store.set_baseline(summary["run_id"])  # a clean, at-least-as-good run moves the baseline forward
    say(f"run {summary['run_id']}: pass {summary['pass_rate']:.0%} · {gate_msg} · review queue {summary['review_queue']}")

    result: dict[str, Any] = {"run": summary["run_id"], "gate": gate_msg, "cost_usd": spent, "fix": None, "heal": heal_out}
    dev_failing = [c for c, r in summary["cases"].items() if r.get("split", "dev") == "dev" and r["pass_rate"] < 1]
    if dev_failing and autofix_on:
        fx = autofix(cfg, store, summary, fixer=fixer, judge=judge, agent_factory=agent_factory)
        result["fix"] = fx
        result["cost_usd"] += fx.cost_usd
        say(f"autofix: {fx.status} - {fx.message or ''} ({len(fx.accepted)} kept, {len(fx.rejected)} rejected, ${fx.cost_usd:.2f})")
    human = []
    if heal_out and heal_out.unrepaired:
        human.append(f"{len(heal_out.unrepaired)} module(s) still failing after code heal")
    if "FAIL" in gate_msg and not (result["fix"] and result["fix"].status == "fixed"):
        human.append("regression not auto-fixed")
    if result["fix"] and result["fix"].status in ("gave_up", "partially_fixed", "error"):
        human.append(f"autofix {result['fix'].status}")
    if summary["review_queue"]:
        human.append(f"{summary['review_queue']} trial(s) to label (autoharness review)")
    result["human_needed"] = human
    return result


def snapshot(paths: list[Path]) -> dict[str, tuple[float, int]]:
    snap: dict[str, tuple[float, int]] = {}
    for p in paths:
        files = [f for f in p.rglob("*") if f.is_file()] if p.is_dir() else ([p] if p.exists() else [])
        for f in files:
            if f.name.startswith(".") or "__pycache__" in f.parts:
                continue
            st = f.stat()
            snap[str(f)] = (st.st_mtime, st.st_size)
    return snap


def changed(a: dict, b: dict) -> list[str]:
    return sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))


def notify(title: str, msg: str) -> None:
    if platform.system() == "Darwin":
        safe = msg.replace('"', "'")[:200]
        subprocess.run(["osascript", "-e", f'display notification "{safe}" with title "{title}"'],
                       capture_output=True)


def watch(cfg: Config, store: Store, *, run_now: bool = False, once: bool = False) -> None:
    lcfg = cfg["loop"]
    default_watch = list(cfg["fixer"]["editable"]) + list(cfg["cases"]) + list(cfg.raw.get("code_paths") or [])
    paths = [cfg.path(p) for p in (lcfg["watch"] or default_watch)]
    say("watching: " + ", ".join(str(p.relative_to(cfg.root)) if p.is_relative_to(cfg.root) else str(p) for p in paths))
    say(f"autofix {'ON' if lcfg['autofix'] else 'off'} · editable by fixer: {', '.join(cfg['fixer']['editable']) or '(none)'}")
    snap = snapshot(paths)
    pending = run_now
    while True:
        if not pending:
            time.sleep(lcfg["interval_s"])
            new = snapshot(paths)
            diff = changed(snap, new)
            if not diff:
                continue
            say("changed: " + ", ".join(Path(d).name for d in diff[:5]))
            # debounce: wait until files stop changing (an editor or agent may write several times)
            while True:
                time.sleep(lcfg["debounce_s"])
                newer = snapshot(paths)
                if not changed(new, newer):
                    break
                new = newer
        pending = False
        try:
            res = cycle(cfg, store, autofix_on=lcfg["autofix"])
        except Exception as e:  # keep watching even if one cycle blows up
            say(f"cycle failed: {type(e).__name__}: {e}")
            res = {"human_needed": [f"cycle crashed: {e}"]}
        if res.get("human_needed"):
            say("HUMAN NEEDED: " + "; ".join(res["human_needed"]))
            if lcfg.get("notify"):
                notify("autoharness", "; ".join(res["human_needed"]))
        else:
            say("all good - no human action needed")
        snap = snapshot(paths)  # absorb the fixer's own edits so they don't retrigger
        if once:
            return
