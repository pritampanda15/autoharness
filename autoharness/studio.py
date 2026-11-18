"""AutoHarness Studio (`autoharness studio`): Apple Liquid Glass + Motion Graphics Workbench.

Serves a local zero-CDN Apple visionOS / macOS Tahoe Liquid Glass web application backed
directly by your live project:
  1. Overview & Telemetry      — Live closed-loop telemetry stream canvas, sweeping radial gauges, cubic spline trajectory
  2. 8-Component Harness² Tree — HiDPI 3D refractive glass orbital sphere + live component editor & compiler
  3. Code Self-Healer          — AST / import / pytest glass matrix, locked-test guard & 1-click Auto-Fix
  4. Probe Arena               — Live 3-track Propose-Probe-Compose motion graphic & dual-bar candidate glass cards
  5. Runs & Trace Inspector    — Filterable dev/holdout trials, layered glass verdicts & inline forensic Auto-Fix
  6. Lessons & Continuous Loop — Persistent DO/AVOID glass ledger & one-click Git pre-commit automation
"""

from __future__ import annotations

import json
import re
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .cases import load_cases
from .config import Config
from .diff import compare
from .hooks import hook_status, install_hook, uninstall_hook
from .lessons import LessonStore
from .plugin_installer import install_plugin_target, mcp_server_entry, plugin_status
from .providers import apply_substrate_selection, discover_providers
from .store import Store
from .synthesizer import build_context_map, scan_codebase, synthesize_cases
from .tree import COMPONENTS, HarnessTree


def _parse_pct_pair(metrics: str, prefix: str) -> list[int | None]:
    m = re.search(rf"{prefix}\s+(\d+|n/a)%?\s*->\s*(\d+|n/a)%?", metrics)
    if not m:
        return [None, None]
    a = None if m.group(1) == "n/a" else int(m.group(1))
    b = None if m.group(2) == "n/a" else int(m.group(2))
    return [a, b]


def _parse_fixlog(fixlog_path: Path) -> list[dict[str, Any]]:
    """Parse `runs/fixlog.md` sections into structured probe records for the Probe Arena."""
    if not fixlog_path.exists():
        return []
    raw = fixlog_path.read_text(errors="replace")
    blocks = re.split(r"\n(?=##\s+)", raw.strip())
    entries: list[dict[str, Any]] = []
    for b in blocks:
        b = b.strip()
        if not b.startswith("## "):
            continue
        lines = b.splitlines()
        header = lines[0][3:].strip()
        verdict = "ACCEPTED" if "ACCEPTED" in header else "REJECTED"
        approach = ""
        diagnosis = ""
        rejected_why = ""
        metrics = ""
        in_diff = False
        diff_lines: list[str] = []
        for ln in lines[1:]:
            if ln.startswith("```diff"):
                in_diff = True
                continue
            if in_diff and ln.startswith("```"):
                in_diff = False
                continue
            if in_diff:
                diff_lines.append(ln)
            elif ln.startswith("**Approach:**"):
                approach = ln.replace("**Approach:**", "").strip()
            elif ln.startswith("**Diagnosis:**"):
                diagnosis = ln.replace("**Diagnosis:**", "").strip()
            elif ln.startswith("**Rejected because:**"):
                rejected_why = ln.replace("**Rejected because:**", "").strip()
            elif "dev " in ln and "holdout " in ln:
                metrics = ln.strip()
        if rejected_why.startswith("fixer failed:"):
            continue
        diff_txt = "\n".join(diff_lines)
        entries.append({
            "header": header,
            "verdict": verdict,
            "static_blocked": "STATIC GUARD" in header,
            "approach": approach or "Candidate Harness Proposal",
            "diagnosis": diagnosis,
            "rejected_why": rejected_why,
            "metrics": metrics,
            "dev_pair": _parse_pct_pair(metrics, "dev"),
            "holdout_pair": _parse_pct_pair(metrics, "holdout"),
            "diff": diff_txt,
        })
    return list(reversed(entries[-24:]))


def collect_studio_state(
    cfg: Config,
    store: Store,
    scan_code: bool = True,
    run_code_checks: bool = False,
) -> dict[str, Any]:
    """Assemble the complete live project state for AutoHarness Studio."""
    spf = cfg["agent"].get("system_prompt_file", "harnesses/default/system.md")
    hpath = cfg.path(spf)
    h2_default = cfg.path("harnesses/harness2")
    active_hpath = hpath if hpath.is_dir() else (h2_default if h2_default.exists() else hpath)
    tree = HarnessTree.load(active_hpath)

    comp_details = []
    for spec in COMPONENTS:
        content = tree.components.get(spec.key, "")
        if spec.key == "scripts" and tree.scripts and not content:
            content = "\n".join(f"# {k}\n{v}" for k, v in tree.scripts.items())
        comp_details.append({
            "key": spec.key,
            "filename": spec.filename if tree.is_directory else active_hpath.name,
            "space": spec.space,
            "title": spec.title,
            "description": spec.description,
            "active": spec.key in tree.components or (spec.key == "scripts" and bool(tree.scripts)),
            "chars": len(content),
            "content": content,
        })
        if not tree.is_directory:
            break

    case_dirs = [cfg.path(p) for p in cfg["cases"] if cfg.path(p).exists()]
    extra_case_dirs = [p for p in (cfg.path("cases/mined"), cfg.path("cases/auto")) if p.exists()]
    all_cases = load_cases(list(dict.fromkeys(case_dirs + extra_case_dirs)), include_drafts=True)
    cases_data = [
        {
            "id": c.id,
            "description": c.description,
            "split": c.split,
            "status": c.status,
            "tags": c.tags,
            "prompt": c.prompt,
            "n_checks": len(c.checks),
            "checks": [{"name": ch.get("name") or ch.get("type"), "type": ch.get("type")} for ch in c.checks],
        }
        for c in all_cases
    ]

    run_ids = store.runs()
    baseline_id = store.baseline()
    runs_history = []
    for rid in run_ids[-15:]:
        try:
            s = store.load_summary(rid)
            runs_history.append({
                "run_id": rid,
                "pass_rate": s.get("pass_rate", 0.0),
                "all_trials_pass_rate": s.get("all_trials_pass_rate", 0.0),
                "mean_score": s.get("mean_score", 0.0),
                "tokens": s.get("input_tokens", 0) + s.get("output_tokens", 0),
                "review_queue": s.get("review_queue", 0),
                "cost_usd": round(float(s.get("est_agent_cost_usd", 0)) + float(s.get("judge_cost_usd", 0)), 4),
                "started": s.get("meta", {}).get("started", ""),
                "is_baseline": rid == baseline_id,
            })
        except Exception:
            continue

    latest_summary = None
    latest_results = []
    latest_traces: dict[str, Any] = {}
    latest_diff = None
    if run_ids:
        latest_id = run_ids[-1]
        rdir = store.run_dir(latest_id)
        latest_summary = store.load_summary(latest_id)
        latest_results = store.load_results(rdir)
        if baseline_id and baseline_id != latest_id:
            try:
                latest_diff = compare(store.load_summary(baseline_id), latest_summary, cfg["gate"])
            except Exception:
                pass
        for r in latest_results[:30]:
            key = f"{r['case_id']}__t{r['trial']}"
            try:
                tr = store.load_trace(rdir, r["case_id"], r["trial"])
                latest_traces[key] = {
                    "final_output": tr.final_output[:2500],
                    "turns": tr.turns,
                    "tokens": tr.total_tokens,
                    "duration_s": tr.duration_s,
                    "error": tr.error,
                    "tool_calls": [
                        {
                            "name": tc.name,
                            "input": tc.input,
                            "output": tc.output[:1000],
                            "is_error": tc.is_error,
                        }
                        for tc in tr.tool_calls[:20]
                    ],
                }
            except Exception:
                pass

    code_targets = cfg.raw.get("code_paths") or (["autoharness"] if (cfg.root / "autoharness").exists() else ["."])
    modules_data = []
    context_map = ""
    if scan_code:
        mods = scan_codebase(cfg.root, code_targets, run_checks=run_code_checks)
        context_map = build_context_map(mods)
        for m in mods[:40]:
            modules_data.append({
                "path": m.rel_path,
                "healthy": m.healthy,
                "syntax_ok": m.syntax_ok,
                "syntax_error": m.syntax_error,
                "import_ok": m.imports_ok,
                "import_error": m.import_error,
                "tests_ok": m.tests_ok,
                "test_error": (m.test_output or "")[:400] if not m.tests_ok else "",
                "tests": [t.name for t in m.tests_found],
                "symbols": (m.classes + [f"{fn}()" for fn in m.functions])[:8],
                "docstring": m.docstring,
            })

    lstore = LessonStore(cfg.path(cfg["fixer"]["lessons_file"]), cfg["fixer"]["max_lessons"])
    lessons = lstore.load()
    fixlog_entries = _parse_fixlog(store.root / "fixlog.md")

    return {
        "project_name": cfg.root.resolve().name,
        "project_root": str(cfg.root.resolve()),
        "agent": cfg["agent"],
        "judge": cfg["judge"],
        "fixer": cfg["fixer"],
        "gate": cfg["gate"],
        "harness": {
            "path": str(active_hpath.relative_to(cfg.root)) if active_hpath.is_relative_to(cfg.root) else str(active_hpath),
            "configured_path": spf,
            "is_directory": tree.is_directory,
            "fingerprint": tree.fingerprint(),
            "compiled_prompt": tree.compile_prompt(),
            "components": comp_details,
            "scripts": list(tree.scripts.keys()),
        },
        "cases": cases_data,
        "baseline_id": baseline_id,
        "runs_history": runs_history,
        "latest_summary": latest_summary,
        "latest_results": latest_results,
        "latest_traces": latest_traces,
        "latest_diff": latest_diff,
        "fixlog": fixlog_entries,
        "modules": modules_data,
        "code_targets": [str(x) for x in code_targets],
        "context_map": context_map,
        "lessons": lessons,
        "calibration": store.calibration(),
        "hooks": hook_status(cfg.root),
        "providers": discover_providers(),
        "plugins": plugin_status(cfg.root),
        "mcp_entry": mcp_server_entry(cfg.root),
    }


def run_immediate_autofix(
    cfg: Config,
    store: Store,
    *,
    target_case: str | None = None,
    target_module: str | None = None,
    agent_factory: Any = None,
    fixer: Any = None,
) -> dict[str, Any]:
    """One-click Auto-Fix engine invoked directly from AutoHarness Studio / Report."""
    from . import loop
    from .agents import ScriptedAgent
    from .healer import heal_codebase
    from .runner import run_suite

    code_targets = [target_module] if target_module else (
        cfg.raw.get("code_paths") or (["autoharness"] if (cfg.root / "autoharness").exists() else [])
    )

    run_ids = store.runs()
    prev_summary = store.load_summary(run_ids[-1]) if run_ids else None
    before_rate = prev_summary["pass_rate"] if prev_summary else None
    used_scripted = bool(prev_summary and prev_summary.get("meta", {}).get("agent", {}).get("type") == "scripted")

    total_cost = 0.0
    healed_records: list[dict[str, Any]] = []
    unrepaired_records: list[dict[str, Any]] = []

    if code_targets:
        heal_out = heal_codebase(
            cfg,
            store,
            code_targets,
            agent_factory=agent_factory,
            run_harness_cycle=False,
            sync_memory=True,
            dry_run=False,
        )
        total_cost += heal_out.cost_usd
        for r in heal_out.repaired:
            healed_records.append({"module": r.module, "repaired": True, "reason": r.reason, "diff": r.diff[:2000]})
        for r in heal_out.unrepaired:
            unrepaired_records.append({"module": r.module, "repaired": False, "reason": r.reason})

        auto_dir = cfg.path("cases/auto")
        if healed_records and auto_dir.exists():
            for old_yaml in auto_dir.glob("auto-heal-*.yaml"):
                old_yaml.unlink(missing_ok=True)
            full_targets = cfg.raw.get("code_paths") or code_targets
            synthesize_cases(cfg.root, full_targets, out_dir=auto_dir)

    spf = cfg["agent"].get("system_prompt_file", "harnesses/default/system.md")
    hpath = cfg.path(spf)
    tree = HarnessTree.load(hpath) if hpath.exists() else None
    case_dirs = [cfg.path(p) for p in cfg["cases"] if cfg.path(p).exists()]
    cases = load_cases(case_dirs) if case_dirs else []

    current_summary = prev_summary
    if cases and (healed_records or current_summary is None or used_scripted):
        if agent_factory is None and (used_scripted or current_summary is None):
            current_summary = run_suite(
                cfg, cases, ScriptedAgent(tree=tree), None, store,
                trials=1, workers=2, label="autofix-verify", quiet=True,
            )
            used_scripted = True
        else:
            current_summary = loop.run_once(
                cfg, store, label="autofix-verify",
                judge=None if used_scripted else None,
                agent_factory=agent_factory, quiet=True,
            )
            total_cost += loop.run_cost(current_summary)

    fix_info: dict[str, Any] | None = None
    if current_summary and not target_module:
        dev_failing = [
            cid for cid, row in current_summary.get("cases", {}).items()
            if row.get("split", "dev") == "dev" and row.get("pass_rate", 1.0) < 1.0
        ]
        if dev_failing:
            af_agent_factory = agent_factory or (
                (lambda c: ScriptedAgent(tree=HarnessTree.load(c.path(c["agent"]["system_prompt_file"]))
                                         if c["agent"].get("system_prompt_file") and c.path(c["agent"]["system_prompt_file"]).exists()
                                         else None))
                if used_scripted else None
            )
            fx = loop.autofix(
                cfg,
                store,
                current_summary,
                fixer=fixer,
                judge=None if used_scripted else None,
                agent_factory=af_agent_factory,
            )
            total_cost += fx.cost_usd
            if fx.final_run:
                current_summary = store.load_summary(fx.final_run)
            fix_info = {
                "status": fx.status,
                "message": fx.message,
                "accepted": len(fx.accepted),
                "rejected": len(fx.rejected),
                "final_run": fx.final_run,
            }

    after_rate = current_summary["pass_rate"] if current_summary else None
    if current_summary:
        from . import report as reportmod

        rdir = store.run_dir(current_summary["run_id"])
        (rdir / "report.html").write_text(
            reportmod.render(current_summary, store.load_results(rdir), None, store.calibration())
        )
    still_failing_cases = [
        cid for cid, row in (current_summary or {}).get("cases", {}).items()
        if row.get("pass_rate", 1.0) < 1.0
    ]

    if not still_failing_cases and not unrepaired_records:
        overall = "fixed" if (healed_records or (fix_info and fix_info["accepted"] > 0)) else "clean"
    elif healed_records or (fix_info and fix_info["accepted"] > 0):
        overall = "partially_fixed"
    else:
        overall = "unrepaired"

    return {
        "ok": True,
        "status": overall,
        "target_case": target_case,
        "target_module": target_module,
        "before_pass_rate": before_rate,
        "after_pass_rate": after_rate,
        "healed_modules": healed_records,
        "unrepaired_modules": unrepaired_records,
        "harness_fix": fix_info,
        "still_failing_cases": still_failing_cases,
        "cost_usd": round(total_cost, 4),
        "state": collect_studio_state(cfg, store, scan_code=True, run_code_checks=True),
    }


def _record_simulated_probe(cfg: Config, store: Store) -> dict[str, Any]:
    """Run an offline 3-candidate contrastive probe showcase so Probe Arena & Lessons work $0/offline."""
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    rid = store.baseline() or (store.runs()[-1] if store.runs() else "verified")
    sample = f"""
## {ts} - REJECTED (STATIC GUARD) (attempt 1, candidate 1, run None)
**Approach:** Hardcode expected literal answer in system.md
**Diagnosis:** Attempted to paste case-specific expected value directly into prompt instructions.
**Rejected because:** Static Leak Filter ($0): mentions case-specific literal ['8080'] (overfitting blocked)
dev 85% -> n/a · holdout 100% -> n/a · tokens +0%
```diff
--- a/harnesses/harness2/system.md
+++ b/harnesses/harness2/system.md
@@ -1,2 +1,3 @@
+# Shortcut: answer 8080 when asked for service port.
```

## {ts} - REJECTED (attempt 1, candidate 2, run {rid})
**Approach:** Global 15-character response cap in guardrails.md
**Diagnosis:** Truncated all assistant outputs globally, breaking structured JSON schema tasks on hidden holdout.
**Rejected because:** hidden holdout cases dropped (100% -> 0%): overfitting reverted byte-for-byte
dev 85% -> 100% · holdout 100% -> 0% · tokens -18%
```diff
--- a/harnesses/harness2/guardrails.md
+++ b/harnesses/harness2/guardrails.md
@@ -2,2 +2,3 @@
+- Never output more than 15 characters on any task.
```

## {ts} - ACCEPTED (attempt 1, candidate 3, run {rid})
**Approach:** Composed Invariant Q&A Brevity Skill + Write-Locked Verification
**Diagnosis:** Scoped concise bare-value replies strictly to read-only factual questions while preserving JSON & code-repair workflows.
dev 85% -> 100% · holdout 100% -> 100% · tokens -11%
```diff
--- a/harnesses/harness2/skills.md
+++ b/harnesses/harness2/skills.md
@@ -3,2 +3,5 @@
+## Direct Factual Q&A
+- When asked a single read-only question, return the exact value without conversational filler.
+- Always run pytest verification after code edits while keeping test files untouched.
```
"""
    store.root.mkdir(parents=True, exist_ok=True)
    with open(store.root / "fixlog.md", "a") as f:
        f.write(sample)

    lstore = LessonStore(cfg.path(cfg["fixer"]["lessons_file"]), cfg["fixer"]["max_lessons"])
    existing = lstore.load()
    if not existing:
        lstore.save([
            {
                "polarity": "do",
                "lesson": "Scope brevity rules to single-fact Q&A inside skills.md so structured JSON and multi-turn code repairs stay intact.",
                "support": 3,
                "evidence_runs": [rid],
            },
            {
                "polarity": "do",
                "lesson": "Use relative-first package imports with standalone fallback in multi-entrypoint modules while keeping unit tests write-locked.",
                "support": 2,
                "evidence_runs": [rid],
            },
            {
                "polarity": "avoid",
                "lesson": "Never apply global character caps in guardrails.md; hidden holdout cases immediately reject overfitted constraints.",
                "support": 2,
                "evidence_runs": [rid],
            },
        ])
    return collect_studio_state(cfg, store, scan_code=True, run_code_checks=False)


STUDIO_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AutoHarness Studio</title>
<style>
:root {
  --bg-base: #eef2f9;
  --ink: #0f172a;
  --ink-2: #334155;
  --mute: #64748b;
  --glass-bg: linear-gradient(145deg, rgba(255, 255, 255, 0.72) 0%, rgba(255, 255, 255, 0.42) 100%);
  --glass-strong: linear-gradient(145deg, rgba(255, 255, 255, 0.84) 0%, rgba(255, 255, 255, 0.58) 100%);
  --glass-inset: rgba(255, 255, 255, 0.46);
  --glass-rim: rgba(255, 255, 255, 0.86);
  --glass-hairline: rgba(15, 23, 42, 0.06);
  --shadow-glass:
    inset 0 1px 0.5px rgba(255, 255, 255, 0.95),
    inset 0 -1px 0.5px rgba(15, 23, 42, 0.03),
    0 20px 50px -15px rgba(15, 23, 42, 0.08),
    0 4px 14px -2px rgba(15, 23, 42, 0.03);
  --shadow-hover:
    inset 0 1px 1px rgba(255, 255, 255, 0.98),
    0 26px 60px -14px rgba(0, 113, 227, 0.12),
    0 6px 18px -3px rgba(15, 23, 42, 0.05);
  --blue: #007aff;
  --blue-soft: rgba(0, 122, 255, 0.11);
  --green: #16a34a;
  --green-bright: #34c759;
  --green-soft: rgba(52, 199, 89, 0.14);
  --amber: #d97706;
  --amber-soft: rgba(245, 158, 11, 0.14);
  --red: #e11d48;
  --red-bright: #ff3b30;
  --red-soft: rgba(255, 59, 48, 0.12);
  --purple: #9333ea;
  --purple-soft: rgba(175, 82, 222, 0.12);
  --radius-xl: 26px;
  --radius-lg: 20px;
  --radius-md: 15px;
}

* { box-sizing: border-box; margin: 0; padding: 0; }
html, body {
  min-height: 100%;
  background: var(--bg-base);
  color: var(--ink);
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "SF Pro Text", "Helvetica Neue", Inter, sans-serif;
  -webkit-font-smoothing: antialiased;
  overflow-x: hidden;
}
.mono {
  font-family: "SF Mono", ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}

/* ==================== LIVING APPLE LIQUID AURORA MESH ==================== */
.aurora-stage {
  position: fixed;
  inset: 0;
  z-index: 0;
  overflow: hidden;
  pointer-events: none;
  background:
    radial-gradient(1200px 800px at 15% 10%, rgba(255, 255, 255, 0.9), transparent 70%),
    linear-gradient(180deg, #f3f6fc 0%, #e9eff8 52%, #f4f7fc 100%);
}
.orb {
  position: absolute;
  border-radius: 50%;
  filter: blur(78px);
  opacity: 0.62;
  will-change: transform;
}
.orb-1 {
  width: 680px; height: 680px; left: -120px; top: -180px;
  background: radial-gradient(circle, rgba(0, 122, 255, 0.28) 0%, rgba(50, 173, 230, 0.08) 65%, transparent 100%);
  animation: driftA 22s ease-in-out infinite alternate;
}
.orb-2 {
  width: 640px; height: 640px; right: -140px; top: -90px;
  background: radial-gradient(circle, rgba(175, 82, 222, 0.22) 0%, rgba(88, 86, 214, 0.08) 65%, transparent 100%);
  animation: driftB 26s ease-in-out infinite alternate;
}
.orb-3 {
  width: 720px; height: 560px; left: 26%; bottom: -210px;
  background: radial-gradient(circle, rgba(52, 199, 89, 0.18) 0%, rgba(0, 199, 190, 0.07) 65%, transparent 100%);
  animation: driftC 24s ease-in-out infinite alternate;
}
@keyframes driftA {
  0%   { transform: translate3d(0, 0, 0) scale(1); }
  100% { transform: translate3d(110px, 75px, 0) scale(1.12); }
}
@keyframes driftB {
  0%   { transform: translate3d(0, 0, 0) scale(1.04); }
  100% { transform: translate3d(-120px, 95px, 0) scale(0.94); }
}
@keyframes driftC {
  0%   { transform: translate3d(0, 0, 0) scale(0.96); }
  100% { transform: translate3d(85px, -70px, 0) scale(1.10); }
}

/* ==================== FLOATING VISION-OS LIQUID GLASS DOCK ==================== */
.dock-wrap {
  position: sticky;
  top: 14px;
  z-index: 120;
  padding: 0 28px;
}
.topbar {
  max-width: 1460px;
  margin: 0 auto;
  min-height: 62px;
  padding: 8px 14px 8px 20px;
  border-radius: 999px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  background: linear-gradient(145deg, rgba(255, 255, 255, 0.76) 0%, rgba(255, 255, 255, 0.50) 100%);
  backdrop-filter: blur(48px) saturate(220%);
  -webkit-backdrop-filter: blur(48px) saturate(220%);
  border: 1px solid var(--glass-rim);
  box-shadow:
    inset 0 1px 0.5px rgba(255, 255, 255, 0.98),
    0 16px 40px -10px rgba(15, 23, 42, 0.10),
    0 2px 8px rgba(15, 23, 42, 0.04);
}
.brand {
  display: flex;
  align-items: center;
  gap: 11px;
  font-size: 17.5px;
  font-weight: 700;
  letter-spacing: -0.022em;
  white-space: nowrap;
}
.brand .sub-pill {
  font-size: 11px;
  font-weight: 600;
  padding: 3px 9px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.65);
  border: 1px solid rgba(15, 23, 42, 0.07);
  color: var(--ink-2);
}

/* Sliding Liquid Glass Segmented Control */
.seg-nav {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 2px;
  padding: 4px;
  border-radius: 999px;
  background: rgba(15, 23, 42, 0.048);
  box-shadow: inset 0 1px 2px rgba(15, 23, 42, 0.06);
}
.nav-glider {
  position: absolute;
  top: 4px;
  left: 4px;
  height: calc(100% - 8px);
  border-radius: 999px;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.96) 0%, rgba(255, 255, 255, 0.80) 100%);
  border: 0.5px solid rgba(255, 255, 255, 0.95);
  box-shadow: 0 3px 10px rgba(15, 23, 42, 0.09), inset 0 1px 0 #ffffff;
  transition: transform 0.38s cubic-bezier(0.22, 1, 0.36, 1), width 0.38s cubic-bezier(0.22, 1, 0.36, 1);
  pointer-events: none;
  z-index: 0;
}
.seg-btn {
  position: relative;
  z-index: 1;
  border: none;
  background: transparent;
  padding: 7px 15px;
  border-radius: 999px;
  font-size: 13px;
  font-weight: 600;
  letter-spacing: -0.008em;
  color: var(--ink-2);
  cursor: pointer;
  transition: color 0.2s ease;
  white-space: nowrap;
}
.seg-btn:hover { color: var(--ink); }
.seg-btn.active {
  color: var(--ink);
}
#trace-filter .seg-btn.active {
  background: rgba(255, 255, 255, 0.92);
  color: var(--blue);
  box-shadow: 0 2px 8px rgba(15, 23, 42, 0.08);
}

.top-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}
.status-chip {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 6px 13px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 600;
  background: rgba(52, 199, 89, 0.13);
  border: 1px solid rgba(52, 199, 89, 0.25);
  color: #15803d;
  backdrop-filter: blur(16px);
  white-space: nowrap;
}
.status-chip .dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #22c55e;
  box-shadow: 0 0 8px rgba(34, 197, 94, 0.65);
}

/* ==================== GLASS CAPSULE BUTTONS & SF ICONS ==================== */
.ico {
  width: 15px;
  height: 15px;
  stroke: currentColor;
  stroke-width: 1.9;
  fill: none;
  stroke-linecap: round;
  stroke-linejoin: round;
  flex-shrink: 0;
}
.btn {
  border: none;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 8px 16px;
  border-radius: 999px;
  font-size: 13px;
  font-weight: 600;
  letter-spacing: -0.01em;
  transition: transform 0.18s cubic-bezier(0.22, 1, 0.36, 1), box-shadow 0.2s ease, background 0.2s ease;
  white-space: nowrap;
}
.btn:hover { transform: translateY(-1px); }
.btn:active { transform: scale(0.97); }
.btn-blue {
  background: linear-gradient(180deg, rgba(10, 132, 255, 0.95) 0%, rgba(0, 102, 214, 0.95) 100%);
  color: #ffffff;
  border: 1px solid rgba(255, 255, 255, 0.35);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.42), 0 8px 20px -4px rgba(0, 122, 255, 0.38);
}
.btn-tinted {
  background: rgba(255, 255, 255, 0.66);
  color: var(--ink);
  border: 1px solid rgba(255, 255, 255, 0.88);
  box-shadow: inset 0 1px 0 #ffffff, 0 4px 12px rgba(15, 23, 42, 0.06);
  backdrop-filter: blur(20px);
}
.btn-tinted:hover {
  background: rgba(255, 255, 255, 0.88);
  color: var(--blue);
}
.btn-green {
  background: linear-gradient(180deg, rgba(48, 209, 88, 0.95) 0%, rgba(22, 163, 74, 0.96) 100%);
  color: #ffffff;
  border: 1px solid rgba(255, 255, 255, 0.36);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.45), 0 8px 20px -4px rgba(22, 163, 74, 0.34);
}
.btn-fix {
  background: linear-gradient(180deg, rgba(255, 69, 58, 0.96) 0%, rgba(225, 29, 72, 0.96) 100%);
  color: #ffffff;
  border: 1px solid rgba(255, 255, 255, 0.40);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.48), 0 10px 24px -4px rgba(225, 29, 72, 0.40);
}
.btn-fix.pulsing {
  animation: fixHalo 2.4s ease-in-out infinite;
}
@keyframes fixHalo {
  0%, 100% { box-shadow: inset 0 1px 0 rgba(255,255,255,0.48), 0 8px 22px -3px rgba(225,29,72,0.36), 0 0 0 0 rgba(255,59,48,0.32); }
  50%      { box-shadow: inset 0 1px 0 rgba(255,255,255,0.58), 0 12px 28px -2px rgba(225,29,72,0.48), 0 0 0 7px rgba(255,59,48,0); }
}
.btn-fix-sm {
  border: 1px solid rgba(255, 255, 255, 0.42);
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 5px 12px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 600;
  background: linear-gradient(180deg, rgba(255, 69, 58, 0.95) 0%, rgba(225, 29, 72, 0.95) 100%);
  color: #ffffff;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.42), 0 4px 12px rgba(225, 29, 72, 0.26);
  transition: transform 0.15s ease, opacity 0.15s ease;
}
.btn-fix-sm:hover { transform: translateY(-1px); opacity: 0.95; }

/* ==================== MAIN WORKSPACE & LIQUID GLASS BENTO ==================== */
.container {
  position: relative;
  z-index: 2;
  max-width: 1460px;
  margin: 0 auto;
  padding: 26px 32px 76px;
}
.hero-strip {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  gap: 20px;
  margin-bottom: 22px;
}
.section-kicker {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 4px 11px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.58);
  border: 1px solid rgba(255, 255, 255, 0.85);
  box-shadow: 0 2px 6px rgba(15, 23, 42, 0.04);
  font-size: 12px;
  font-weight: 600;
  color: var(--ink-2);
  margin-bottom: 9px;
}
.section-kicker .kdot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--blue);
}
.hero-strip h1 {
  font-size: 33px;
  font-weight: 700;
  letter-spacing: -0.03em;
  line-height: 1.08;
  color: var(--ink);
}
.hero-strip .subtitle {
  color: var(--mute);
  font-size: 14.5px;
  margin-top: 5px;
  font-weight: 450;
}

.bento-4 { display: grid; grid-template-columns: repeat(4, 1fr); gap: 18px; margin-bottom: 18px; }
.bento-3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 18px; margin-bottom: 18px; }
.bento-2 { display: grid; grid-template-columns: 1.12fr 0.88fr; gap: 18px; margin-bottom: 18px; }
.bento-split { display: grid; grid-template-columns: 0.96fr 1.04fr; gap: 18px; margin-bottom: 18px; }

/* True Refractive Liquid Glass Card (NO thick colored stripes!) */
.card {
  position: relative;
  overflow: hidden;
  background: var(--glass-bg);
  backdrop-filter: blur(42px) saturate(210%);
  -webkit-backdrop-filter: blur(42px) saturate(210%);
  border: 1px solid var(--glass-rim);
  border-radius: var(--radius-xl);
  padding: 22px 24px;
  box-shadow: var(--shadow-glass);
  transition: transform 0.26s cubic-bezier(0.22, 1, 0.36, 1), box-shadow 0.26s ease;
}
.card::before {
  content: "";
  position: absolute;
  top: 0; left: 12%; right: 12%; height: 1px;
  background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.98), transparent);
  pointer-events: none;
}
.card-sub {
  background: var(--glass-inset);
  backdrop-filter: blur(22px);
  -webkit-backdrop-filter: blur(22px);
  border: 1px solid rgba(255, 255, 255, 0.72);
  border-radius: var(--radius-md);
  padding: 14px 16px;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.82), 0 4px 14px rgba(15, 23, 42, 0.03);
}
.card-title {
  font-size: 12px;
  font-weight: 600;
  letter-spacing: -0.005em;
  color: var(--mute);
  margin-bottom: 4px;
}
.card-h2 {
  font-size: 19px;
  font-weight: 700;
  letter-spacing: -0.02em;
  margin-bottom: 14px;
  color: var(--ink);
}

/* Medallion Icon Badges */
.medallion {
  width: 42px;
  height: 42px;
  border-radius: 13px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid rgba(255, 255, 255, 0.88);
  box-shadow: inset 0 1px 0 #ffffff, 0 6px 16px rgba(15, 23, 42, 0.06);
  margin-bottom: 14px;
}
.med-blue   { background: linear-gradient(145deg, rgba(0,122,255,0.18), rgba(0,122,255,0.06)); color: var(--blue); }
.med-purple { background: linear-gradient(145deg, rgba(147,51,234,0.18), rgba(147,51,234,0.06)); color: var(--purple); }
.med-green  { background: linear-gradient(145deg, rgba(22,163,74,0.18), rgba(22,163,74,0.06)); color: var(--green); }
.med-red    { background: linear-gradient(145deg, rgba(225,29,72,0.18), rgba(225,29,72,0.06)); color: var(--red); }

/* Radial KPI Gauge Tile */
.kpi-tile {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
}
.kpi-val {
  font-size: 36px;
  font-weight: 700;
  letter-spacing: -0.035em;
  line-height: 1.05;
}
.kpi-sub {
  font-size: 12.5px;
  color: var(--mute);
  margin-top: 6px;
  font-weight: 500;
}
.ring-svg { width: 68px; height: 68px; flex-shrink: 0; overflow: visible; }
.ring-arc {
  transition: stroke-dashoffset 0.95s cubic-bezier(0.22, 1, 0.36, 1);
}

/* Glass Status Pills */
.badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 11px;
  border-radius: 999px;
  font-size: 11.5px;
  font-weight: 600;
  border: 1px solid rgba(255, 255, 255, 0.72);
  backdrop-filter: blur(12px);
}
.b-green  { background: var(--green-soft); color: #15803d; }
.b-blue   { background: var(--blue-soft); color: #0062cc; }
.b-amber  { background: var(--amber-soft); color: #b45309; }
.b-red    { background: var(--red-soft); color: #be123c; }
.b-purple { background: var(--purple-soft); color: #7e22ce; }

/* Urgent Glass Failure Banner */
.failure-banner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  padding: 18px 24px;
  margin-bottom: 20px;
  border-radius: 22px;
  background: linear-gradient(135deg, rgba(255, 255, 255, 0.80) 0%, rgba(255, 241, 242, 0.68) 100%);
  backdrop-filter: blur(42px) saturate(210%);
  -webkit-backdrop-filter: blur(42px) saturate(210%);
  border: 1px solid rgba(255, 59, 48, 0.28);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.95),
    0 18px 42px -12px rgba(225, 29, 72, 0.14);
  animation: glassRise 0.42s cubic-bezier(0.22, 1, 0.36, 1);
}

/* Animated View Transitions */
.view-pane { display: none; }
.view-pane.active {
  display: block;
  animation: glassRise 0.38s cubic-bezier(0.22, 1, 0.36, 1);
}
@keyframes glassRise {
  from { opacity: 0; transform: translateY(10px) scale(0.994); }
  to   { opacity: 1; transform: translateY(0) scale(1); }
}

/* Tables */
.apple-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13.5px;
}
.apple-table th {
  text-align: left;
  font-size: 11.5px;
  font-weight: 600;
  color: var(--mute);
  padding: 10px 12px;
  border-bottom: 1px solid var(--glass-hairline);
}
.apple-table td {
  padding: 12px 12px;
  border-bottom: 1px solid rgba(15, 23, 42, 0.045);
  vertical-align: middle;
}
.apple-table tr:last-child td { border-bottom: none; }
.apple-table tr.clickable { cursor: pointer; transition: background 0.15s ease; border-radius: 12px; }
.apple-table tr.clickable:hover { background: rgba(255, 255, 255, 0.52); }
.apple-table tr.selected { background: rgba(0, 122, 255, 0.09); }

/* Component Grid */
.comp-list {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 10px;
  margin-top: 12px;
}
.comp-item {
  padding: 12px 15px;
  border-radius: 16px;
  border: 1px solid rgba(255, 255, 255, 0.78);
  background: rgba(255, 255, 255, 0.48);
  backdrop-filter: blur(18px);
  cursor: pointer;
  transition: all 0.2s cubic-bezier(0.22, 1, 0.36, 1);
}
.comp-item:hover { background: rgba(255, 255, 255, 0.76); transform: translateY(-1px); }
.comp-item.active {
  border-color: rgba(0, 122, 255, 0.48);
  background: linear-gradient(145deg, rgba(255, 255, 255, 0.90), rgba(235, 245, 255, 0.72));
  box-shadow: inset 0 1px 0 #ffffff, 0 8px 20px -6px rgba(0, 122, 255, 0.18);
}

textarea.code-editor {
  width: 100%;
  min-height: 360px;
  padding: 18px 20px;
  border-radius: 16px;
  border: 1px solid rgba(255, 255, 255, 0.85);
  background: rgba(255, 255, 255, 0.62);
  backdrop-filter: blur(20px);
  color: var(--ink);
  font-size: 13px;
  line-height: 1.62;
  resize: vertical;
  outline: none;
  box-shadow: inset 0 2px 6px rgba(15, 23, 42, 0.03);
}
textarea.code-editor:focus {
  border-color: rgba(0, 122, 255, 0.55);
  background: rgba(255, 255, 255, 0.88);
  box-shadow: 0 0 0 4px rgba(0, 122, 255, 0.12);
}

pre.code-block {
  background: rgba(15, 23, 42, 0.86);
  backdrop-filter: blur(24px);
  border: 1px solid rgba(255, 255, 255, 0.14);
  color: #e2e8f0;
  padding: 15px 18px;
  border-radius: 16px;
  font-size: 12px;
  line-height: 1.58;
  overflow-x: auto;
  white-space: pre-wrap;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.10);
}

/* Dual Metric Progress Bars for Probe Arena Cards */
.probe-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(410px, 1fr));
  gap: 16px;
}
.probe-card {
  position: relative;
  border-radius: 20px;
  padding: 20px 22px;
  background: var(--glass-strong);
  backdrop-filter: blur(36px) saturate(200%);
  -webkit-backdrop-filter: blur(36px) saturate(200%);
  border: 1px solid rgba(255, 255, 255, 0.88);
  box-shadow: var(--shadow-glass);
  transition: transform 0.22s ease, box-shadow 0.22s ease;
}
.probe-card:hover {
  transform: translateY(-2px);
  box-shadow: var(--shadow-hover);
}
.probe-card.accepted {
  background: linear-gradient(150deg, rgba(255, 255, 255, 0.88) 0%, rgba(236, 253, 245, 0.68) 100%);
  border-color: rgba(52, 199, 89, 0.36);
}
.metric-bars {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
  margin: 12px 0;
}
.mbar-box {
  padding: 9px 12px;
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.62);
  border: 1px solid rgba(255, 255, 255, 0.85);
}
.mbar-track {
  height: 6px;
  border-radius: 999px;
  background: rgba(15, 23, 42, 0.07);
  overflow: hidden;
  margin-top: 6px;
}
.mbar-fill {
  height: 100%;
  border-radius: 999px;
  transition: width 0.8s cubic-bezier(0.22, 1, 0.36, 1);
}

/* Liquid Glass Modal Sheet */
.modal-backdrop {
  position: fixed;
  inset: 0;
  z-index: 300;
  background: rgba(15, 23, 42, 0.28);
  backdrop-filter: blur(18px) saturate(160%);
  -webkit-backdrop-filter: blur(18px) saturate(160%);
  display: none;
  align-items: center;
  justify-content: center;
  padding: 24px;
}
.modal-backdrop.open { display: flex; }
.modal-sheet {
  width: 100%;
  max-width: 670px;
  max-height: 86vh;
  overflow-y: auto;
  background: linear-gradient(150deg, rgba(255, 255, 255, 0.88) 0%, rgba(248, 250, 255, 0.76) 100%);
  backdrop-filter: blur(54px) saturate(220%);
  -webkit-backdrop-filter: blur(54px) saturate(220%);
  border-radius: 28px;
  border: 1px solid rgba(255, 255, 255, 0.92);
  padding: 26px 28px;
  box-shadow:
    inset 0 1px 0 #ffffff,
    0 34px 90px -12px rgba(15, 23, 42, 0.24);
  animation: glassRise 0.32s cubic-bezier(0.22, 1, 0.36, 1);
}
.step-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 15px;
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.68);
  border: 1px solid rgba(255, 255, 255, 0.9);
  margin-top: 8px;
  font-size: 13.5px;
  font-weight: 600;
}
.spinner {
  width: 16px;
  height: 16px;
  border-radius: 50%;
  border: 2.2px solid rgba(0, 122, 255, 0.2);
  border-top-color: var(--blue);
  animation: spin 0.75s linear infinite;
  flex-shrink: 0;
}
@keyframes spin { to { transform: rotate(360deg); } }

.toast {
  position: fixed;
  bottom: 24px;
  right: 28px;
  padding: 12px 20px;
  border-radius: 999px;
  background: rgba(15, 23, 42, 0.82);
  backdrop-filter: blur(24px);
  border: 1px solid rgba(255, 255, 255, 0.18);
  color: #ffffff;
  font-size: 13px;
  font-weight: 600;
  box-shadow: 0 20px 44px rgba(15, 23, 42, 0.22);
  opacity: 0;
  transform: translateY(12px);
  transition: all 0.25s cubic-bezier(0.22, 1, 0.36, 1);
  pointer-events: none;
  z-index: 350;
}
.toast.show { opacity: 1; transform: translateY(0); }

@media (max-width: 1180px) {
  .bento-4, .bento-3, .bento-2, .bento-split { grid-template-columns: 1fr; }
  .topbar { flex-wrap: wrap; border-radius: 26px; }
}
</style>
</head>
<body>

<!-- Living Ambient Apple Liquid Glass Mesh -->
<div class="aurora-stage">
  <div class="orb orb-1"></div>
  <div class="orb orb-2"></div>
  <div class="orb orb-3"></div>
</div>

<div class="dock-wrap">
  <header class="topbar">
    <div class="brand">
      <svg width="30" height="30" viewBox="0 0 128 128">
        <rect x="6" y="6" width="116" height="116" rx="28" fill="#0f172a"/>
        <path d="M 64 24 A 40 40 0 0 1 101 49" fill="none" stroke="#4285F4" stroke-width="8" stroke-linecap="round"/>
        <polygon points="106,57 93,49 106,42" fill="#4285F4"/>
        <path d="M 101 79 A 40 40 0 0 1 64 104" fill="none" stroke="#34A853" stroke-width="8" stroke-linecap="round"/>
        <polygon points="56,104 67,97 66,111" fill="#34A853"/>
        <path d="M 64 104 A 40 40 0 0 1 27 79" fill="none" stroke="#FBBC04" stroke-width="8" stroke-linecap="round"/>
        <polygon points="22,71 35,79 22,86" fill="#FBBC04"/>
        <path d="M 27 49 A 40 40 0 0 1 64 24" fill="none" stroke="#EA4335" stroke-width="8" stroke-linecap="round"/>
        <polygon points="72,24 61,31 62,17" fill="#EA4335"/>
        <rect x="44" y="44" width="40" height="40" rx="10" fill="#1e293b" stroke="#cbd5e1" stroke-width="2.5"/>
        <polygon points="64,51 77,64 64,77 51,64" fill="#4285F4"/>
        <circle cx="64" cy="64" r="4.5" fill="#ffffff"/>
      </svg>
      <span>Auto<span style="color:var(--blue)">Harness</span></span>
      <button id="hdr-fp" class="sub-pill mono" style="cursor:pointer;" title="Switch AI Model or Subscription (Claude Pro/Max, ChatGPT Plus/Codex, Gemini Pro, Copilot, Ollama, vLLM)">v0.3</button>
      <button id="btn-mcp-plugins" class="sub-pill" style="cursor:pointer; color:var(--blue); border-color:rgba(0,122,255,0.22); background:rgba(0,122,255,0.08);" title="Connect as MCP Agent Server or Install as Universal Plugin">MCP &amp; Plugins</button>
    </div>

    <nav class="seg-nav" id="main-tabs">
      <div class="nav-glider" id="nav-glider"></div>
      <button class="seg-btn active" data-tab="overview">Overview</button>
      <button class="seg-btn" data-tab="harness2">Harness² Tree</button>
      <button class="seg-btn" data-tab="healer">Code Healer</button>
      <button class="seg-btn" data-tab="arena">Probe Arena</button>
      <button class="seg-btn" data-tab="traces">Runs &amp; Traces</button>
      <button class="seg-btn" data-tab="lessons">Lessons &amp; Loop</button>
    </nav>

    <div class="top-actions">
      <span id="hdr-status" class="status-chip"><span class="dot"></span><span>Autonomous Ready</span></span>
      <button class="btn btn-tinted" id="btn-top-autofix"></button>
      <button class="btn btn-blue" id="btn-quick-eval">
        <svg class="ico" viewBox="0 0 24 24"><polygon points="6 4 20 12 6 20 6 4" fill="currentColor" stroke="none"/></svg>
        <span>Run Suite</span>
      </button>
    </div>
  </header>
</div>

<main class="container">
  <!-- ======================== 1. OVERVIEW ======================== -->
  <section class="view-pane active" id="pane-overview">
    <div id="ov-failure-banner"></div>
    <div class="hero-strip">
      <div>
        <div class="section-kicker"><span class="kdot"></span><span>Autonomous Harness Telemetry</span></div>
        <h1 id="ov-title">System Telemetry &amp; Quality Gate</h1>
        <div class="subtitle" id="ov-sub">Frozen model weights · 8-component Harness² architecture · hidden holdout protection</div>
      </div>
      <div id="ov-gate-pill"></div>
    </div>

    <div class="bento-4" id="ov-kpis"></div>

    <!-- Live 60fps Closed-Loop Motion-Graphic Pipeline Strip -->
    <div class="card" style="margin-bottom:18px; padding:18px 24px;">
      <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
        <div>
          <div class="card-title">Live Closed-Loop Verification &amp; Self-Healing Stream</div>
          <div style="font-size:16px; font-weight:700; letter-spacing:-0.015em;">Real-Time Signal Flow Across Code, Graders &amp; Holdout Gate</div>
        </div>
        <span class="badge b-blue mono" id="ov-stream-tag">60fps Live Telemetry</span>
      </div>
      <canvas id="ov-flow-canvas" width="1360" height="118" style="width:100%; height:118px; display:block;"></canvas>
    </div>

    <div class="bento-2">
      <div class="card">
        <div class="card-title">Evaluation Trajectory</div>
        <div class="card-h2">Pass Rate &amp; Reliability Curve</div>
        <div id="ov-chart" style="height:224px;"></div>
      </div>
      <div class="card">
        <div class="card-title">Cheapest-First Grading Waterfall</div>
        <div class="card-h2">Three-Stage Verification Architecture</div>
        <div id="ov-waterfall" style="display:flex; flex-direction:column; gap:11px;"></div>
      </div>
    </div>

    <div class="bento-2">
      <div class="card">
        <div class="card-title">Suite Coverage</div>
        <div class="card-h2">Active Dev &amp; Hidden Holdout Cases</div>
        <div id="ov-cases-table"></div>
      </div>
      <div class="card">
        <div class="card-title">Experience Reflector</div>
        <div class="card-h2">Retained Engineering Priors</div>
        <div id="ov-lessons"></div>
      </div>
    </div>
  </section>

  <!-- ======================== 2. HARNESS² TREE ======================== -->
  <section class="view-pane" id="pane-harness2">
    <div class="hero-strip">
      <div>
        <div class="section-kicker"><span class="kdot" style="background:var(--purple)"></span><span>Modular Runtime Topology</span></div>
        <h1>8-Component Harness² Architecture</h1>
        <div class="subtitle">Drag or click any orbiting glass node to inspect and edit its live runtime instructions.</div>
      </div>
      <div style="display:flex; gap:10px;">
        <button class="btn btn-tinted" id="btn-show-compiled">
          <svg class="ico" viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
          <span>View Compiled Prompt</span>
        </button>
        <button class="btn btn-blue" id="btn-save-comp">
          <svg class="ico" viewBox="0 0 24 24"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/></svg>
          <span>Save Component</span>
        </button>
      </div>
    </div>

    <div class="bento-split">
      <div class="card" style="display:flex; flex-direction:column; justify-content:space-between;">
        <div>
          <div style="display:flex; justify-content:space-between; align-items:center;">
            <div>
              <div class="card-title">Interactive 3D Glass Topology</div>
              <div class="card-h2" style="margin-bottom:4px;">Frozen Model Core + 8 Runtime Roles</div>
            </div>
            <span id="h2-path-badge" class="badge b-blue mono"></span>
          </div>
          <canvas id="h2-canvas" width="660" height="350" style="width:100%; height:340px; cursor:grab;"></canvas>
        </div>
        <div>
          <div class="card-title">Select Component</div>
          <div class="comp-list" id="h2-comp-list"></div>
        </div>
      </div>

      <div class="card" style="display:flex; flex-direction:column;">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;">
          <div>
            <div class="card-title" id="ed-space">Context Space</div>
            <div class="card-h2 mono" id="ed-filename" style="margin-bottom:2px;">system.md</div>
          </div>
          <span class="badge b-purple mono" id="ed-chars">0 chars</span>
        </div>
        <p id="ed-desc" style="color:var(--mute); font-size:13.5px; margin-bottom:14px;"></p>
        <textarea id="ed-textarea" class="code-editor mono" style="flex:1;"></textarea>
      </div>
    </div>
  </section>

  <!-- ======================== 3. CODE SELF-HEALER ======================== -->
  <section class="view-pane" id="pane-healer">
    <div class="hero-strip">
      <div>
        <div class="section-kicker"><span class="kdot" style="background:var(--green)"></span><span>Isolated Sandbox Repair</span></div>
        <h1>Codebase Scanner &amp; Self-Healer</h1>
        <div class="subtitle">Validates AST syntax, package imports, and unit tests; repairs broken modules while keeping test files write-locked.</div>
      </div>
      <div style="display:flex; gap:10px; flex-wrap:wrap;">
        <button class="btn btn-tinted" id="btn-synth-cases">
          <svg class="ico" viewBox="0 0 24 24"><path d="M12 3v18M3 12h18"/></svg>
          <span>Synthesize Cases</span>
        </button>
        <button class="btn btn-green" id="btn-rescan-code">
          <svg class="ico" viewBox="0 0 24 24"><path d="M21 12a9 9 0 1 1-2.64-6.36"/><polyline points="21 3 21 9 15 9"/></svg>
          <span>Live AST &amp; Test Scan</span>
        </button>
        <button class="btn btn-fix" id="btn-heal-now">
          <svg class="ico" viewBox="0 0 24 24"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg>
          <span>Auto-Heal Failing Code</span>
        </button>
      </div>
    </div>

    <div class="bento-3" id="hl-summary-cards"></div>

    <div class="bento-2">
      <div class="card">
        <div class="card-title">Source Module Matrix</div>
        <div class="card-h2">AST, Import &amp; Write-Locked Test Status</div>
        <div id="hl-modules-table" style="max-height:470px; overflow-y:auto;"></div>
      </div>
      <div class="card">
        <div class="card-title">Live Architecture Context (memory.md)</div>
        <div class="card-h2">Auto-Synced Symbol Map</div>
        <pre id="hl-context-map" class="code-block mono" style="min-height:380px; max-height:470px; overflow-y:auto;"></pre>
      </div>
    </div>
  </section>

  <!-- ======================== 4. PROBE ARENA ======================== -->
  <section class="view-pane" id="pane-arena">
    <div class="hero-strip">
      <div>
        <div class="section-kicker"><span class="kdot" style="background:var(--purple)"></span><span>Contrastive Harness Optimization</span></div>
        <h1>Propose · Probe · Compose Arena</h1>
        <div class="subtitle">Parallel candidate harnesses race inside isolated sandboxes and must clear hidden holdout gates before promotion.</div>
      </div>
      <div style="display:flex; align-items:center; gap:10px;">
        <span class="badge b-green">Holdout &amp; Leak Shields Active</span>
        <button class="btn btn-tinted" id="btn-sim-probe">
          <svg class="ico" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><polygon points="10 8 16 12 10 16 10 8" fill="currentColor" stroke="none"/></svg>
          <span>Simulate 3-Probe Race</span>
        </button>
      </div>
    </div>

    <!-- Live 60fps 3-Track Contrastive Probe Motion Graphic -->
    <div class="card" style="margin-bottom:18px; padding:20px 24px;">
      <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;">
        <div>
          <div class="card-title">Live Parallel Sandbox Topology</div>
          <div style="font-size:17px; font-weight:700; letter-spacing:-0.015em;">K=3 Candidate Trajectories Across Static Leak &amp; Secret Holdout Gates</div>
        </div>
        <span class="badge b-purple mono">Zero-Overfitting Pipeline</span>
      </div>
      <canvas id="arena-canvas" width="1360" height="210" style="width:100%; height:210px; display:block;"></canvas>
    </div>

    <div class="bento-3">
      <div class="card">
        <div class="medallion med-blue">
          <svg class="ico" style="width:20px;height:20px;" viewBox="0 0 24 24"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="M9 12l2 2 4-4"/></svg>
        </div>
        <div class="card-title">Stage 01 · Pre-Run ($0)</div>
        <div class="card-h2" style="margin-bottom:8px;">Static Leak &amp; Bloat Shield</div>
        <p style="font-size:13.8px; color:var(--ink-2); line-height:1.58;">Inspects every proposed diff before spending tokens. Blocks edits that paste case IDs, target filenames, or expected literal answers into the harness.</p>
      </div>
      <div class="card">
        <div class="medallion med-purple">
          <svg class="ico" style="width:20px;height:20px;" viewBox="0 0 24 24"><circle cx="18" cy="18" r="3"/><circle cx="6" cy="6" r="3"/><path d="M6 21V9a9 9 0 0 0 9 9"/></svg>
        </div>
        <div class="card-title">Stage 02 · Isolated Sandboxes</div>
        <div class="card-h2" style="margin-bottom:8px;">Parallel Probe &amp; Contrastive Merge</div>
        <p style="font-size:13.8px; color:var(--ink-2); line-height:1.58;">Evaluates K diverse strategies concurrently in throwaway copies, then composes winning per-case contrasts into one unified candidate.</p>
      </div>
      <div class="card">
        <div class="medallion med-green">
          <svg class="ico" style="width:20px;height:20px;" viewBox="0 0 24 24"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>
        </div>
        <div class="card-title">Stage 03 · Secret Split</div>
        <div class="card-h2" style="margin-bottom:8px;">Hidden Holdout Gate</div>
        <p style="font-size:13.8px; color:var(--ink-2); line-height:1.58;">Holdout cases remain completely invisible to the fixer. Any score drop on hidden cases triggers immediate rejection and byte-for-byte revert.</p>
      </div>
    </div>

    <div class="card">
      <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:14px;">
        <div>
          <div class="card-title">Evaluated Candidate Ledger</div>
          <div class="card-h2" style="margin-bottom:0;">Accepted &amp; Rejected Probe History</div>
        </div>
        <span class="badge b-blue mono" id="ar-count-badge">0 probes</span>
      </div>
      <div id="ar-fixlog" class="probe-grid"></div>
    </div>
  </section>

  <!-- ======================== 5. RUNS & TRACES ======================== -->
  <section class="view-pane" id="pane-traces">
    <div class="hero-strip">
      <div>
        <div class="section-kicker"><span class="kdot"></span><span>Execution Forensics</span></div>
        <h1>Trial Verdicts &amp; Sandbox Tool Traces</h1>
        <div class="subtitle">Select any trial to inspect deterministic checks, behavioral trace rules, grounded judge votes, and tool timelines.</div>
      </div>
      <div class="seg-nav" id="trace-filter">
        <button class="seg-btn active" data-f="all">All Trials</button>
        <button class="seg-btn" data-f="dev">Dev</button>
        <button class="seg-btn" data-f="holdout">Holdout</button>
        <button class="seg-btn" data-f="fail">Failing</button>
      </div>
    </div>

    <div class="bento-split">
      <div class="card" style="max-height:650px; overflow-y:auto;">
        <div class="card-title">Latest Suite Execution</div>
        <div class="card-h2" id="tr-run-id">Trials</div>
        <div id="tr-list"></div>
      </div>
      <div class="card" style="max-height:650px; overflow-y:auto;">
        <div class="card-title">Forensic Check &amp; Trace Inspector</div>
        <div class="card-h2" id="tr-detail-title">Select a trial</div>
        <div id="tr-detail-body"></div>
      </div>
    </div>
  </section>

  <!-- ======================== 6. LESSONS & LOOP ======================== -->
  <section class="view-pane" id="pane-lessons">
    <div class="hero-strip">
      <div>
        <div class="section-kicker"><span class="kdot" style="background:var(--green)"></span><span>Long-Horizon Memory &amp; Git Hooks</span></div>
        <h1>Persistent Lesson Ledger &amp; Continuous Loop</h1>
        <div class="subtitle">Empirically validated DO and AVOID priors retained across runs, paired with one-click Git pre-commit protection.</div>
      </div>
    </div>

    <div class="bento-2">
      <div class="card">
        <div class="card-title">Experience Reflector Ledger</div>
        <div class="card-h2">Validated Engineering Priors</div>
        <div id="ls-cards" style="display:flex; flex-direction:column; gap:12px;"></div>
      </div>
      <div class="card">
        <div class="card-title">Always-On Repository Guards</div>
        <div class="card-h2">Git Commit Hooks &amp; CLI Automation</div>
        <div id="ls-hooks" style="margin-bottom:20px;"></div>
        <div class="card-title" style="margin-top:18px;">Terminal Quick Reference</div>
        <pre class="code-block mono">$ autoharness studio
$ autoharness heal --with-harness
$ autoharness watch --now
$ autoharness hook install --kind pre-commit</pre>
      </div>
    </div>
  </section>
</main>

<!-- Liquid Glass Auto-Fix Sheet Modal -->
<div class="modal-backdrop" id="autofix-modal">
  <div class="modal-sheet">
    <div style="display:flex; justify-content:space-between; align-items:flex-start; gap:12px; margin-bottom:14px;">
      <div>
        <div class="card-title" style="color:var(--red); font-weight:700;" id="af-eyebrow">Autonomous Self-Healing Engine</div>
        <h2 style="font-size:22px; font-weight:700; letter-spacing:-0.024em;" id="af-title">Automatic Test &amp; Harness Repair</h2>
      </div>
      <button class="btn btn-tinted" id="af-close" style="padding:6px 14px; font-size:12.5px;">Close</button>
    </div>
    <div id="af-body"></div>
  </div>
</div>

<!-- Liquid Glass Universal AI Model & Subscription Switcher Sheet -->
<div class="modal-backdrop" id="model-modal">
  <div class="modal-sheet" style="max-width:820px;">
    <div style="display:flex; justify-content:space-between; align-items:flex-start; gap:12px; margin-bottom:14px;">
      <div>
        <div class="card-title" style="color:var(--blue); font-weight:700;">Subscriptions · Local Open-Source · Universal Substrates</div>
        <h2 style="font-size:22px; font-weight:700; letter-spacing:-0.024em;">Flat-Rate Subscriptions ($0 API) &amp; Any AI Model</h2>
        <div style="font-size:13px; color:var(--mute); margin-top:3px;">Run on your existing Claude Pro/Max/Team, ChatGPT Plus/Pro, Google AI Pro, or Copilot subscription — or 100% local Ollama / vLLM.</div>
      </div>
      <button class="btn btn-tinted" id="md-close" style="padding:6px 14px; font-size:12.5px;">Close</button>
    </div>
    <div id="md-body"></div>
  </div>
</div>

<!-- Liquid Glass MCP Server & Universal Plugin Installer Sheet -->
<div class="modal-backdrop" id="plugin-modal">
  <div class="modal-sheet" style="max-width:820px;">
    <div style="display:flex; justify-content:space-between; align-items:flex-start; gap:12px; margin-bottom:14px;">
      <div>
        <div class="card-title" style="color:var(--purple); font-weight:700;">Model Context Protocol (MCP) &amp; Agent Plugin Hub</div>
        <h2 style="font-size:22px; font-weight:700; letter-spacing:-0.024em;">Feed AutoHarness Into Any AI Agent or IDE</h2>
        <div style="font-size:13px; color:var(--mute); margin-top:3px;">Install AutoHarness as a 9-tool MCP server + write-locked Skill plugin in one click. Uses your host agent's subscription with $0 extra API keys.</div>
      </div>
      <button class="btn btn-tinted" id="pl-close" style="padding:6px 14px; font-size:12.5px;">Close</button>
    </div>
    <div id="pl-body"></div>
  </div>
</div>

<div class="toast" id="toast">Saved</div>

<script>
let STATE = __INITIAL_STATE__;
let selectedCompIdx = 0;
let selectedTrialIdx = 0;
let traceFilter = "all";
let orbitYaw = 0.62, orbitPitch = 0.32, isDragging = false, lastX = 0, lastY = 0;
let orbitHitNodes = [];
let animClock = 0;
let probePulseBoost = 0;

const ICONS = {
  bolt: `<svg class="ico" viewBox="0 0 24 24"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg>`,
  check: `<svg class="ico" style="width:13px;height:13px;" viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"/></svg>`,
  lock: `<svg class="ico" style="width:13px;height:13px;" viewBox="0 0 24 24"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>`,
  alert: `<svg class="ico" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>`,
};

function toast(msg) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.classList.add("show");
  setTimeout(() => t.classList.remove("show"), 2600);
}

function esc(s) {
  return String(s ?? "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}

function updateNavGlider() {
  const active = document.querySelector("#main-tabs .seg-btn.active");
  const glider = document.getElementById("nav-glider");
  if (!active || !glider) return;
  glider.style.width = `${active.offsetWidth}px`;
  glider.style.transform = `translateX(${active.offsetLeft - 4}px)`;
}
window.addEventListener("resize", updateNavGlider);

function ringGaugeSVG(pct, color, gradId) {
  const r = 27, c = 2 * Math.PI * r;
  const clamped = Math.max(0, Math.min(1, pct));
  const off = c * (1 - clamped);
  const angle = -Math.PI / 2 + clamped * Math.PI * 2;
  const tipX = 34 + r * Math.cos(angle);
  const tipY = 34 + r * Math.sin(angle);
  return `<svg class="ring-svg" viewBox="0 0 68 68">
    <defs>
      <linearGradient id="${gradId}" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0%" stop-color="${color}"/>
        <stop offset="100%" stop-color="#38bdf8"/>
      </linearGradient>
    </defs>
    <circle cx="34" cy="34" r="${r}" fill="none" stroke="rgba(15,23,42,0.065)" stroke-width="6.5"/>
    <circle class="ring-arc" cx="34" cy="34" r="${r}" fill="none" stroke="url(#${gradId})" stroke-width="6.5"
      stroke-linecap="round" stroke-dasharray="${c.toFixed(1)}" stroke-dashoffset="${off.toFixed(1)}"
      transform="rotate(-90 34 34)"/>
    ${clamped > 0.02 ? `<circle cx="${tipX.toFixed(1)}" cy="${tipY.toFixed(1)}" r="3.4" fill="#ffffff" stroke="${color}" stroke-width="2"/>` : ''}
  </svg>`;
}

function getFailureSummary() {
  const sum = STATE.latest_summary;
  const caseMap = (sum && sum.cases) ? sum.cases : {};
  const failingCases = Object.entries(caseMap)
    .filter(([_, r]) => (r.pass_rate ?? 1) < 1)
    .map(([id, r]) => ({ id, pass_rate: r.pass_rate, split: r.split || "dev" }));
  const failingMods = (STATE.modules || []).filter(m => !m.healthy);
  return { failingCases, failingMods, total: failingCases.length + failingMods.length };
}

/* ==================== RENDER ALL PANES ==================== */
function renderAll() {
  const mLabel = STATE.agent.model || STATE.agent.type;
  const subTag = STATE.agent.auth_mode === "subscription" ? " · SUB $0" : "";
  document.getElementById("hdr-fp").textContent = `${STATE.agent.type}:${mLabel}${subTag} ▾`;
  const fails = getFailureSummary();
  const topBtn = document.getElementById("btn-top-autofix");
  const hdrStatus = document.getElementById("hdr-status");
  if (fails.total > 0) {
    topBtn.className = "btn btn-fix pulsing";
    topBtn.innerHTML = `${ICONS.bolt}<span>Auto-Fix Failures (${fails.total})</span>`;
    hdrStatus.style.background = "rgba(255, 59, 48, 0.13)";
    hdrStatus.style.borderColor = "rgba(255, 59, 48, 0.28)";
    hdrStatus.style.color = "#be123c";
    hdrStatus.innerHTML = `<span class="dot" style="background:#ff3b30;box-shadow:0 0 8px rgba(255,59,48,0.7)"></span><span>${fails.total} Failure${fails.total > 1 ? 's' : ''} Detected</span>`;
  } else {
    topBtn.className = "btn btn-tinted";
    topBtn.innerHTML = `${ICONS.bolt}<span>Auto-Check &amp; Fix</span>`;
    hdrStatus.style.background = "rgba(52, 199, 89, 0.13)";
    hdrStatus.style.borderColor = "rgba(52, 199, 89, 0.25)";
    hdrStatus.style.color = "#15803d";
    hdrStatus.innerHTML = `<span class="dot"></span><span>Autonomous Ready</span>`;
  }
  renderOverview();
  renderHarnessPane();
  renderHealerPane();
  renderArenaPane();
  renderTracesPane();
  renderLessonsPane();
  requestAnimationFrame(updateNavGlider);
}

/* ==================== 1. OVERVIEW ==================== */
function smoothSplinePath(pts) {
  if (!pts.length) return "";
  if (pts.length === 1) return `M${pts[0].x.toFixed(1)},${pts[0].y.toFixed(1)}`;
  let d = `M${pts[0].x.toFixed(1)},${pts[0].y.toFixed(1)}`;
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[Math.max(0, i - 1)];
    const p1 = pts[i];
    const p2 = pts[i + 1];
    const p3 = pts[Math.min(pts.length - 1, i + 2)];
    const cp1x = p1.x + (p2.x - p0.x) / 6;
    const cp1y = p1.y + (p2.y - p0.y) / 6;
    const cp2x = p2.x - (p3.x - p1.x) / 6;
    const cp2y = p2.y - (p3.y - p1.y) / 6;
    d += ` C${cp1x.toFixed(1)},${cp1y.toFixed(1)} ${cp2x.toFixed(1)},${cp2y.toFixed(1)} ${p2.x.toFixed(1)},${p2.y.toFixed(1)}`;
  }
  return d;
}

function renderOverview() {
  const sum = STATE.latest_summary;
  const passRate = sum ? sum.pass_rate : 1.0;
  const passK = sum ? sum.all_trials_pass_rate : 1.0;
  const reviewQ = sum ? sum.review_queue : 0;
  const nComps = STATE.harness.components.filter(c => c.active).length;
  const fails = getFailureSummary();
  const diff = STATE.latest_diff;

  const bannerEl = document.getElementById("ov-failure-banner");
  if (fails.total > 0 || (diff && !diff.gate_passed)) {
    const labels = [
      ...fails.failingCases.map(c => c.id),
      ...fails.failingMods.map(m => m.path),
    ];
    bannerEl.innerHTML = `
      <div class="failure-banner">
        <div style="display:flex; align-items:center; gap:15px;">
          <div class="medallion med-red" style="margin-bottom:0; width:46px; height:46px; border-radius:15px;">
            ${ICONS.bolt}
          </div>
          <div>
            <div style="display:flex; align-items:center; gap:9px; margin-bottom:3px;">
              <span class="badge b-red">Repair Ready</span>
              <span style="font-size:16.5px; font-weight:700; letter-spacing:-0.018em;">
                ${fails.total || 1} Failing Check${fails.total === 1 ? '' : 's'} Detected — One-Click Automatic Repair Available
              </span>
            </div>
            <div class="mono" style="font-size:12px; color:var(--ink-2);">
              ${labels.length ? 'Targets: ' + esc(labels.slice(0, 6).join(' · ')) : 'Regression vs baseline'}
              · Write-locked unit tests + isolated Propose-Probe-Compose verification
            </div>
          </div>
        </div>
        <button class="btn btn-fix pulsing" onclick="triggerAutoFix({})" style="flex-shrink:0; padding:11px 22px; font-size:13.5px;">
          ${ICONS.bolt}<span>Auto-Fix Failing Tests Now</span>
        </button>
      </div>`;
  } else {
    bannerEl.innerHTML = "";
  }

  document.getElementById("ov-title").textContent = `${STATE.project_name} · Harness Telemetry`;
  document.getElementById("ov-gate-pill").innerHTML = diff
    ? `<span class="badge ${diff.gate_passed ? 'b-green' : 'b-red'}" style="font-size:13px;padding:8px 16px;">${diff.gate_passed ? 'Gate Passed vs Baseline' : 'Gate Regression Detected'}</span>`
    : `<span class="badge b-blue mono" style="font-size:12.5px;padding:8px 16px;">Baseline · ${esc(STATE.baseline_id || 'ready')}</span>`;

  const kpis = [
    { label: "Suite Pass Rate", val: sum ? `${Math.round(passRate * 100)}%` : "—", sub: sum ? `${sum.n_trials} trials verified` : "Run suite to populate", pct: passRate, col: "#007aff", gid: "rg1" },
    { label: "Every-Trial Reliability", val: sum ? `${Math.round(passK * 100)}%` : "—", sub: "Consistency across trials (pass^k)", pct: passK, col: "#16a34a", gid: "rg2" },
    { label: "Harness² Active Roles", val: `${nComps}/${STATE.harness.is_directory ? 8 : 1}`, sub: STATE.harness.path, pct: nComps / (STATE.harness.is_directory ? 8 : 1), col: "#9333ea", gid: "rg3" },
    { label: "Human Review Queue", val: String(reviewQ), sub: reviewQ === 0 ? "Zero manual babysitting" : "Awaiting calibration", pct: reviewQ === 0 ? 1.0 : 0.35, col: reviewQ === 0 ? "#16a34a" : "#d97706", gid: "rg4" },
  ];
  document.getElementById("ov-kpis").innerHTML = kpis.map(k => `
    <div class="card kpi-tile">
      <div>
        <div class="card-title">${esc(k.label)}</div>
        <div class="kpi-val">${esc(k.val)}</div>
        <div class="kpi-sub">${esc(k.sub)}</div>
      </div>
      ${ringGaugeSVG(k.pct, k.col, k.gid)}
    </div>
  `).join("");

  // Smooth Cubic-Bezier Spline Trajectory Chart
  const hist = STATE.runs_history;
  if (!hist.length) {
    document.getElementById("ov-chart").innerHTML = `<div style="color:var(--mute);padding:64px 0;text-align:center;">No runs recorded yet. Click <strong>Run Suite</strong> in the floating dock.</div>`;
  } else {
    const w = 680, h = 210, padX = 44, padY = 26;
    const pts = hist.map((r, i) => {
      const x = padX + (hist.length === 1 ? (w - 2 * padX) / 2 : (i / (hist.length - 1)) * (w - 2 * padX));
      const y = h - padY - r.pass_rate * (h - 2 * padY);
      return { x, y, r };
    });
    const curveD = smoothSplinePath(pts);
    const areaD = `${curveD} L${pts[pts.length - 1].x.toFixed(1)},${h - padY} L${pts[0].x.toFixed(1)},${h - padY} Z`;
    document.getElementById("ov-chart").innerHTML = `
      <svg viewBox="0 0 ${w} ${h}" style="width:100%; height:100%; overflow:visible;">
        <defs>
          <linearGradient id="glassArea" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="#007aff" stop-opacity="0.26"/>
            <stop offset="65%" stop-color="#38bdf8" stop-opacity="0.08"/>
            <stop offset="100%" stop-color="#ffffff" stop-opacity="0.0"/>
          </linearGradient>
          <linearGradient id="lineStroke" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stop-color="#007aff"/>
            <stop offset="60%" stop-color="#6366f1"/>
            <stop offset="100%" stop-color="#16a34a"/>
          </linearGradient>
        </defs>
        <line x1="${padX}" y1="${padY}" x2="${w - padX}" y2="${padY}" stroke="rgba(15,23,42,0.06)" stroke-dasharray="4,5"/>
        <line x1="${padX}" y1="${h/2}" x2="${w - padX}" y2="${h/2}" stroke="rgba(15,23,42,0.06)" stroke-dasharray="4,5"/>
        <line x1="${padX}" y1="${h - padY}" x2="${w - padX}" y2="${h - padY}" stroke="rgba(15,23,42,0.10)"/>
        <text x="4" y="${padY + 4}" font-size="11" font-weight="600" fill="#64748b">100%</text>
        <text x="10" y="${h/2 + 4}" font-size="11" font-weight="600" fill="#64748b">50%</text>
        <text x="16" y="${h - padY + 4}" font-size="11" font-weight="600" fill="#64748b">0%</text>
        ${pts.length > 1 ? `<path d="${areaD}" fill="url(#glassArea)"/>` : ''}
        <path d="${curveD}" fill="none" stroke="url(#lineStroke)" stroke-width="3.4" stroke-linecap="round"/>
        ${pts.map(p => `
          <g>
            ${p.r.is_baseline ? `<circle cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="12" fill="rgba(22,163,74,0.16)"/>` : ''}
            <circle cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="${p.r.is_baseline ? 6 : 4.8}"
              fill="${p.r.is_baseline ? '#16a34a' : '#007aff'}" stroke="#ffffff" stroke-width="2.2"/>
          </g>
        `).join("")}
      </svg>`;
  }

  // Glass Waterfall Bars
  const layers = [
    ["Stage 1 · Deterministic ($0)", "AST syntax, pytest exit codes, JSON schema, file immutability", "b-blue", "Exact · $0", 100, "#007aff"],
    ["Stage 2 · Behavioral Trace ($0)", "Read-before-write, verified-after-edit, loop & forbidden-cmd guards", "b-purple", "Trace · $0", 84, "#9333ea"],
    ["Stage 3 · Grounded Judge (3× Vote)", "Self-consistency rubric voting + workspace artifact verification", "b-green", "Auto-Certified", 68, "#16a34a"],
  ];
  document.getElementById("ov-waterfall").innerHTML = layers.map(([t, d, cls, tag, wPct, col]) => `
    <div class="card-sub">
      <div style="display:flex; justify-content:space-between; align-items:center; gap:10px;">
        <div style="font-weight:650; font-size:14px;">${esc(t)}</div>
        <span class="badge ${cls}">${esc(tag)}</span>
      </div>
      <div style="font-size:12.5px; color:var(--mute); margin-top:3px;">${esc(d)}</div>
      <div class="mbar-track" style="margin-top:8px;">
        <div class="mbar-fill" style="width:${wPct}%; background:linear-gradient(90deg, ${col}, #38bdf8);"></div>
      </div>
    </div>
  `).join("");

  // Cases table
  const caseMap = (sum && sum.cases) ? sum.cases : {};
  const orderedCases = [...STATE.cases].sort((a, b) => {
    const ra = caseMap[a.id] ? caseMap[a.id].pass_rate : 1;
    const rb = caseMap[b.id] ? caseMap[b.id].pass_rate : 1;
    return ra - rb;
  });
  document.getElementById("ov-cases-table").innerHTML = `
    <table class="apple-table">
      <tr><th>Case</th><th>Split</th><th>Checks</th><th>Pass Rate</th><th>Action</th></tr>
      ${orderedCases.slice(0, 10).map(c => {
        const row = caseMap[c.id];
        const pr = row ? Math.round(row.pass_rate * 100) + "%" : "—";
        const isFail = row && row.pass_rate < 1;
        const cls = !row ? "b-blue" : (isFail ? "b-red" : "b-green");
        const act = isFail
          ? `<button class="btn-fix-sm" data-fix-case="${esc(c.id)}">${ICONS.bolt}<span>Auto-Fix</span></button>`
          : (row ? `<span class="badge b-green">${ICONS.check}<span>Healthy</span></span>` : `—`);
        return `<tr>
          <td><div style="font-weight:650;">${esc(c.id)}</div><div style="font-size:12px;color:var(--mute);">${esc(c.description)}</div></td>
          <td><span class="badge ${c.split === 'holdout' ? 'b-purple' : 'b-blue'}">${esc(c.split)}</span></td>
          <td class="mono" style="font-size:12.5px;">${c.n_checks}</td>
          <td><span class="badge ${cls}">${pr}</span></td>
          <td>${act}</td>
        </tr>`;
      }).join("")}
    </table>`;
  document.querySelectorAll("#ov-cases-table [data-fix-case]").forEach(btn => {
    btn.onclick = () => triggerAutoFix({ targetCase: btn.dataset.fixCase });
  });

  // Lessons preview
  const ls = STATE.lessons;
  document.getElementById("ov-lessons").innerHTML = ls.length
    ? ls.slice(0, 5).map(l => `
        <div class="card-sub" style="margin-bottom:10px;">
          <div style="display:flex; align-items:center; gap:8px; margin-bottom:5px;">
            <span class="badge ${l.polarity === 'do' ? 'b-green' : 'b-red'}">${l.polarity === 'do' ? 'DO' : 'AVOID'} · ${l.support || 1}×</span>
          </div>
          <div style="font-size:13.5px; line-height:1.5; color:var(--ink-2);">${esc(l.lesson)}</div>
        </div>`).join("")
    : `<div class="card-sub" style="padding:32px 18px; text-align:center; color:var(--mute); font-size:13.5px;">
         Empirical DO and AVOID priors populate automatically after your first contrastive probe or code-heal cycle.
       </div>`;
}

/* ==================== 2. HARNESS² PANE ==================== */
function renderHarnessPane() {
  document.getElementById("h2-path-badge").textContent = STATE.harness.path;
  const comps = STATE.harness.components;
  if (selectedCompIdx >= comps.length) selectedCompIdx = 0;

  document.getElementById("h2-comp-list").innerHTML = comps.map((c, idx) => `
    <div class="comp-item ${idx === selectedCompIdx ? 'active' : ''}" data-cidx="${idx}">
      <div style="display:flex; justify-content:space-between; align-items:center;">
        <span style="font-size:11px; font-weight:600; color:var(--blue); text-transform:capitalize;">${esc(c.space)}</span>
        <span class="mono" style="font-size:11px; color:var(--mute);">${c.chars}c</span>
      </div>
      <div class="mono" style="font-size:13.5px; font-weight:650; margin-top:3px;">${esc(c.filename)}</div>
    </div>
  `).join("");

  document.querySelectorAll("#h2-comp-list .comp-item").forEach(el => {
    el.onclick = () => { selectedCompIdx = +el.dataset.cidx; renderHarnessPane(); };
  });

  const cur = comps[selectedCompIdx];
  if (cur) {
    document.getElementById("ed-space").textContent = `${cur.space.toUpperCase()} SPACE · ${cur.title}`;
    document.getElementById("ed-filename").textContent = cur.filename;
    document.getElementById("ed-chars").textContent = `${cur.chars} chars`;
    document.getElementById("ed-desc").textContent = cur.description;
    document.getElementById("ed-textarea").value = cur.content;
    document.getElementById("ed-textarea").readOnly = false;
  }
}

/* ==================== 3. CODE HEALER PANE ==================== */
function renderHealerPane() {
  const mods = [...STATE.modules].sort((a, b) => Number(a.healthy) - Number(b.healthy));
  const healthy = mods.filter(m => m.healthy).length;
  const withTests = mods.filter(m => m.tests.length > 0).length;
  document.getElementById("hl-summary-cards").innerHTML = `
    <div class="card"><div class="card-title">Scanned Source Modules</div><div class="kpi-val">${mods.length}</div><div class="kpi-sub">Targets: ${esc(STATE.code_targets.join(", "))}</div></div>
    <div class="card"><div class="card-title">AST, Import &amp; Test Health</div><div class="kpi-val" style="color:${healthy === mods.length ? 'var(--green)' : 'var(--red)'}">${healthy}/${mods.length}</div><div class="kpi-sub">${healthy === mods.length ? 'All modules verified clean' : (mods.length - healthy) + ' module(s) ready for Auto-Fix'}</div></div>
    <div class="card"><div class="card-title">Write-Locked Test Suites</div><div class="kpi-val" style="color:var(--blue)">${withTests}</div><div class="kpi-sub">Protected byte-for-byte during repair</div></div>
  `;
  document.getElementById("hl-modules-table").innerHTML = `
    <table class="apple-table">
      <tr><th>Module</th><th>Health</th><th>Protected Tests</th><th>Action</th></tr>
      ${mods.map(m => {
        const errTxt = m.syntax_error || m.import_error || m.test_error || "";
        const statusLabel = m.healthy ? "Healthy" : (!m.syntax_ok ? "Syntax Error" : (!m.import_ok ? "Import Error" : "Test Fail"));
        return `
        <tr>
          <td>
            <div class="mono" style="font-weight:650; font-size:13px;">${esc(m.path)}</div>
            ${errTxt
              ? `<div class="mono" style="font-size:11.5px; color:var(--red); margin-top:3px;">${esc(errTxt.slice(0, 110))}</div>`
              : `<div class="mono" style="font-size:11.5px; color:var(--mute); margin-top:2px;">${esc(m.symbols.slice(0, 4).join(', '))}</div>`}
          </td>
          <td><span class="badge ${m.healthy ? 'b-green' : 'b-red'}">${statusLabel}</span></td>
          <td class="mono" style="font-size:12px;">${m.tests.length ? `<span class="badge b-blue">${ICONS.lock}<span>${esc(m.tests.join(', '))}</span></span>` : '<span style="color:var(--mute)">—</span>'}</td>
          <td>${m.healthy
            ? `<span class="mono" style="font-size:12px; color:var(--green);">Verified</span>`
            : `<button class="btn-fix-sm" data-fix-mod="${esc(m.path)}">${ICONS.bolt}<span>Auto-Fix</span></button>`}</td>
        </tr>`;
      }).join("")}
    </table>`;
  document.querySelectorAll("#hl-modules-table [data-fix-mod]").forEach(btn => {
    btn.onclick = () => triggerAutoFix({ targetModule: btn.dataset.fixMod });
  });
  document.getElementById("hl-context-map").textContent = STATE.context_map || "# No Python modules scanned yet.";
}

/* ==================== 4. PROBE ARENA PANE ==================== */
function renderArenaPane() {
  const fl = STATE.fixlog || [];
  document.getElementById("ar-count-badge").textContent = `${fl.length} evaluated probe${fl.length === 1 ? '' : 's'}`;
  if (!fl.length) {
    document.getElementById("ar-fixlog").innerHTML = `
      <div class="card-sub" style="grid-column:1/-1; padding:34px 24px; display:flex; align-items:center; justify-content:space-between; gap:18px;">
        <div>
          <div style="font-size:16px; font-weight:700; margin-bottom:4px;">Ready for Contrastive Probing</div>
          <div style="font-size:13.5px; color:var(--mute);">Click <strong>Simulate 3-Probe Race</strong> above (or trigger <strong>Auto-Fix</strong> on a failing check) to watch K=3 parallel candidates get filtered by the Static Leak Shield and Hidden Holdout Gate.</div>
        </div>
        <button class="btn btn-blue" onclick="document.getElementById('btn-sim-probe').click()">Simulate 3-Probe Race</button>
      </div>`;
    return;
  }

  document.getElementById("ar-fixlog").innerHTML = fl.map(e => {
    const isAcc = e.verdict === "ACCEPTED";
    const devB = e.dev_pair ? e.dev_pair[1] : (isAcc ? 100 : null);
    const holdB = e.holdout_pair ? e.holdout_pair[1] : (isAcc ? 100 : null);
    const devWidth = devB != null ? devB : 0;
    const holdWidth = holdB != null ? holdB : 0;
    const pillLabel = isAcc ? "Promoted to Baseline" : (e.static_blocked ? "Blocked · Static Shield ($0)" : "Rejected · Holdout Gate");
    return `
      <div class="probe-card ${isAcc ? 'accepted' : ''}">
        <div style="display:flex; justify-content:space-between; align-items:center; gap:10px; margin-bottom:10px;">
          <span class="badge ${isAcc ? 'b-green' : (e.static_blocked ? 'b-amber' : 'b-red')}">${pillLabel}</span>
          <span class="mono" style="font-size:11.5px; color:var(--mute);">${esc(e.metrics || 'pre-run filter')}</span>
        </div>
        <div style="font-weight:700; font-size:16px; letter-spacing:-0.015em; margin-bottom:6px;">${esc(e.approach)}</div>
        ${e.diagnosis ? `<div style="font-size:13px; color:var(--ink-2); line-height:1.5; margin-bottom:10px;">${esc(e.diagnosis)}</div>` : ''}
        <div class="metric-bars">
          <div class="mbar-box">
            <div style="display:flex; justify-content:space-between; font-size:11.5px; font-weight:600;">
              <span style="color:var(--mute);">Dev Split</span>
              <span class="mono">${devB != null ? devB + '%' : 'Skipped ($0)'}</span>
            </div>
            <div class="mbar-track"><div class="mbar-fill" style="width:${devWidth}%; background:${devB === 100 ? '#007aff' : '#94a3b8'};"></div></div>
          </div>
          <div class="mbar-box">
            <div style="display:flex; justify-content:space-between; font-size:11.5px; font-weight:600;">
              <span style="color:var(--mute);">Secret Holdout</span>
              <span class="mono" style="color:${holdB === 100 ? 'var(--green)' : (holdB === 0 ? 'var(--red)' : 'var(--mute)')};">${holdB != null ? holdB + '%' : 'Protected'}</span>
            </div>
            <div class="mbar-track"><div class="mbar-fill" style="width:${holdWidth}%; background:${holdB === 100 ? '#16a34a' : '#ef4444'};"></div></div>
          </div>
        </div>
        ${e.rejected_why ? `<div class="card-sub" style="padding:9px 12px; margin-bottom:10px; font-size:12.5px; color:var(--red); font-weight:600; background:rgba(255,59,48,0.06); border-color:rgba(255,59,48,0.18);">${esc(e.rejected_why)}</div>` : ''}
        ${e.diff ? `<pre class="code-block mono" style="font-size:11.5px; max-height:150px;">${esc(e.diff)}</pre>` : ''}
      </div>`;
  }).join("");
}

/* ==================== 5. RUNS & TRACES PANE ==================== */
function renderTracesPane() {
  const sum = STATE.latest_summary;
  document.getElementById("tr-run-id").textContent = sum ? `Run · ${sum.run_id}` : "No Completed Runs Yet";
  const caseMeta = sum ? (sum.cases || {}) : {};
  const filtered = STATE.latest_results.filter(r => {
    const sp = (caseMeta[r.case_id] || {}).split || "dev";
    if (traceFilter === "dev") return sp === "dev";
    if (traceFilter === "holdout") return sp === "holdout";
    if (traceFilter === "fail") return !r.passed;
    return true;
  });

  if (!filtered.length) {
    document.getElementById("tr-list").innerHTML = `<div style="padding:36px; color:var(--mute); text-align:center;">No matching trials in this filter.</div>`;
    document.getElementById("tr-detail-body").innerHTML = "";
    return;
  }
  if (selectedTrialIdx >= filtered.length) selectedTrialIdx = 0;

  document.getElementById("tr-list").innerHTML = `
    <table class="apple-table">
      <tr><th>Verdict</th><th>Case</th><th>Trial</th><th>Turns</th><th>Action</th></tr>
      ${filtered.map((r, idx) => `
        <tr class="clickable ${idx === selectedTrialIdx ? 'selected' : ''}" data-tidx="${idx}">
          <td><span class="badge ${r.passed ? 'b-green' : 'b-red'}">${r.passed ? 'PASS' : 'FAIL'}</span></td>
          <td style="font-weight:650;">${esc(r.case_id)}</td>
          <td class="mono">#${r.trial}</td>
          <td class="mono">${r.turns}</td>
          <td>${r.passed
            ? `<span class="mono" style="font-size:12px;color:var(--mute);">${r.tokens.toLocaleString()} tok</span>`
            : `<button class="btn-fix-sm" data-row-fix="${esc(r.case_id)}">${ICONS.bolt}<span>Auto-Fix</span></button>`}</td>
        </tr>`).join("")}
    </table>`;

  document.querySelectorAll("#tr-list tr.clickable").forEach(tr => {
    tr.onclick = () => { selectedTrialIdx = +tr.dataset.tidx; renderTracesPane(); };
  });
  document.querySelectorAll("#tr-list [data-row-fix]").forEach(btn => {
    btn.onclick = e => { e.stopPropagation(); triggerAutoFix({ targetCase: btn.dataset.rowFix }); };
  });

  const cur = filtered[selectedTrialIdx];
  const key = `${cur.case_id}__t${cur.trial}`;
  const trace = STATE.latest_traces[key];
  document.getElementById("tr-detail-title").textContent = `${cur.case_id} · Trial #${cur.trial}`;
  const failCallout = !cur.passed ? `
    <div class="failure-banner" style="padding:14px 18px; margin-bottom:16px;">
      <div>
        <div style="font-weight:700; font-size:14.5px; color:var(--red);">Trial Failed — One-Click Repair Ready</div>
        <div style="font-size:12.5px; color:var(--ink-2); margin-top:2px;">Isolates broken modules or runs contrastive Propose-Probe-Compose against hidden holdouts.</div>
      </div>
      <button class="btn btn-fix" id="btn-detail-autofix" style="padding:8px 16px; font-size:12.5px; flex-shrink:0;">${ICONS.bolt}<span>Auto-Fix This Test</span></button>
    </div>` : "";
  document.getElementById("tr-detail-body").innerHTML = `
    ${failCallout}
    <div style="margin-bottom:16px;">
      <div class="card-title">Layered Check Verdicts</div>
      ${cur.checks.map(ch => `
        <div class="card-sub" style="margin-top:8px; display:flex; justify-content:space-between; align-items:flex-start; gap:10px;">
          <div>
            <div style="font-weight:650; font-size:13.5px;">${esc(ch.name)} <span class="badge b-blue" style="margin-left:6px;">${esc(ch.layer)}</span></div>
            <div class="mono" style="font-size:12px; color:var(--ink-2); margin-top:4px; white-space:pre-wrap;">${esc(ch.detail)}</div>
          </div>
          <span class="badge ${ch.skipped ? 'b-amber' : (ch.passed ? 'b-green' : 'b-red')}">${ch.skipped ? 'SKIPPED' : (ch.passed ? 'PASS' : 'FAIL')}</span>
        </div>`).join("")}
    </div>
    ${trace ? `
      <div class="card-title" style="margin-top:18px;">Sandbox Tool Timeline (${trace.tool_calls.length})</div>
      ${trace.tool_calls.map((tc, i) => `
        <div class="card-sub" style="margin-top:8px;">
          <div class="mono" style="font-size:12px; font-weight:650; color:var(--blue);">[${i + 1}] ${esc(tc.name)}(${esc(JSON.stringify(tc.input)).slice(0, 110)})</div>
          ${tc.output ? `<pre class="mono" style="font-size:11.5px; color:var(--ink-2); margin-top:6px; white-space:pre-wrap;">${esc(tc.output.slice(0, 360))}</pre>` : ''}
        </div>`).join("")}
      <div class="card-title" style="margin-top:18px;">Assistant Final Output</div>
      <pre class="code-block mono">${esc(trace.final_output)}</pre>
    ` : ''}
  `;
  const detailFixBtn = document.getElementById("btn-detail-autofix");
  if (detailFixBtn) {
    detailFixBtn.onclick = () => triggerAutoFix({ targetCase: cur.case_id });
  }
}

/* ==================== 6. LESSONS & HOOKS PANE ==================== */
function renderLessonsPane() {
  const ls = STATE.lessons;
  document.getElementById("ls-cards").innerHTML = ls.length
    ? ls.map(l => `
      <div class="card-sub" style="padding:16px 18px;">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:7px;">
          <span class="badge ${l.polarity === 'do' ? 'b-green' : 'b-red'}">${l.polarity === 'do' ? 'DO' : 'AVOID'} · verified ${l.support || 1}×</span>
          <span class="mono" style="font-size:11.5px; color:var(--mute);">${esc((l.evidence_runs || []).join(', '))}</span>
        </div>
        <div style="font-size:14px; line-height:1.55; color:var(--ink);">${esc(l.lesson)}</div>
      </div>`).join("")
    : `<div class="card-sub" style="padding:28px; color:var(--mute); text-align:center;">No empirical lessons distilled yet.</div>`;

  const hk = STATE.hooks || {};
  document.getElementById("ls-hooks").innerHTML = Object.keys(hk).length
    ? Object.entries(hk).map(([kind, status]) => `
      <div class="card-sub" style="margin-bottom:10px; display:flex; justify-content:space-between; align-items:center;">
        <div>
          <div class="mono" style="font-weight:650; font-size:13.5px;">.git/hooks/${esc(kind)}</div>
          <div style="font-size:12px; color:var(--mute); margin-top:2px;">Status: ${esc(status)}</div>
        </div>
        <button class="btn ${status === 'autoharness' ? 'btn-tinted' : 'btn-blue'}" onclick="toggleHook('${kind}', '${status}')">
          ${status === 'autoharness' ? 'Uninstall Hook' : 'Install Hook'}
        </button>
      </div>`).join("")
    : `<div style="color:var(--mute);">Initialize Git to manage commit hooks.</div>`;
}

/* ==================== 60FPS APPLE MOTION GRAPHICS ENGINE ==================== */
function setupHiDPICanvas(cv) {
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const rect = cv.getBoundingClientRect();
  const w = Math.max(300, Math.round(rect.width || cv.width));
  const h = Math.max(90, Math.round(rect.height || cv.height));
  if (cv.width !== w * dpr || cv.height !== h * dpr) {
    cv.width = w * dpr;
    cv.height = h * dpr;
  }
  const ctx = cv.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { ctx, w, h };
}

function drawOverviewFlowCanvas(t) {
  const cv = document.getElementById("ov-flow-canvas");
  if (!cv || !document.getElementById("pane-overview").classList.contains("active")) return;
  const { ctx, w, h } = setupHiDPICanvas(cv);
  ctx.clearRect(0, 0, w, h);

  const fails = getFailureSummary();
  const stages = [
    { title: "Code & Harness²", sub: `${STATE.modules.length || 8} modules`, col: "#007aff" },
    { title: "AST & Test Lock", sub: "Byte-locked", col: "#0ea5e9" },
    { title: "3-Layer Eval", sub: "Det -> Trace -> 3x", col: "#6366f1" },
    { title: "Contrastive Probe", sub: "K=3 Sandboxes", col: "#9333ea" },
    { title: "Holdout Shield", sub: "Secret Split", col: "#14b8a6" },
    { title: fails.total ? "Auto-Fix Ready" : "100% Verified", sub: fails.total ? `${fails.total} pending` : "Gate Passed", col: fails.total ? "#ff3b30" : "#16a34a" },
  ];

  const padX = 86;
  const cy = h * 0.50;
  const step = (w - padX * 2) / (stages.length - 1);
  const nodes = stages.map((s, i) => ({ ...s, x: padX + i * step, y: cy }));

  // Glowing spline track
  const trackGrad = ctx.createLinearGradient(padX, cy, w - padX, cy);
  trackGrad.addColorStop(0, "rgba(0,122,255,0.22)");
  trackGrad.addColorStop(0.5, "rgba(147,51,234,0.22)");
  trackGrad.addColorStop(1, fails.total ? "rgba(255,59,48,0.28)" : "rgba(22,163,74,0.26)");

  ctx.beginPath();
  ctx.moveTo(nodes[0].x, cy);
  for (let i = 0; i < nodes.length - 1; i++) {
    const mx = (nodes[i].x + nodes[i + 1].x) / 2;
    const wave = Math.sin(t * 2.0 + i) * 6;
    ctx.quadraticCurveTo(mx, cy + wave, nodes[i + 1].x, cy);
  }
  ctx.strokeStyle = trackGrad;
  ctx.lineWidth = 4;
  ctx.stroke();

  // Flowing luminous telemetry pulses
  const totalSpan = w - padX * 2;
  for (let p = 0; p < 5; p++) {
    const u = ((t * 0.20) + p / 5) % 1;
    const px = padX + u * totalSpan;
    const segIdx = Math.min(nodes.length - 2, Math.floor(u * (nodes.length - 1)));
    const localU = (u * (nodes.length - 1)) - segIdx;
    const wave = Math.sin(t * 2.0 + segIdx) * 6 * 2 * localU * (1 - localU);
    const py = cy + wave;

    const glow = ctx.createRadialGradient(px, py, 1, px, py, 16);
    glow.addColorStop(0, "rgba(0, 122, 255, 0.92)");
    glow.addColorStop(0.45, "rgba(56, 189, 248, 0.35)");
    glow.addColorStop(1, "rgba(56, 189, 248, 0)");
    ctx.fillStyle = glow;
    ctx.beginPath();
    ctx.arc(px, py, 16, 0, Math.PI * 2);
    ctx.fill();

    ctx.fillStyle = "#ffffff";
    ctx.beginPath();
    ctx.arc(px, py, 3.6, 0, Math.PI * 2);
    ctx.fill();
  }

  // Refractive glass nodes
  nodes.forEach((n, idx) => {
    const pulse = 1 + 0.05 * Math.sin(t * 3.1 - idx * 0.7);
    ctx.save();
    ctx.beginPath();
    ctx.roundRect(n.x - 66, n.y - 31, 132, 62, 16);
    ctx.fillStyle = "rgba(255, 255, 255, 0.82)";
    ctx.shadowColor = "rgba(15, 23, 42, 0.08)";
    ctx.shadowBlur = 16;
    ctx.shadowOffsetY = 5;
    ctx.fill();
    ctx.shadowBlur = 0;
    ctx.lineWidth = 1.2;
    ctx.strokeStyle = "rgba(255, 255, 255, 0.96)";
    ctx.stroke();

    // Status dot
    ctx.beginPath();
    ctx.arc(n.x, n.y - 13, 5.2 * pulse, 0, Math.PI * 2);
    ctx.fillStyle = n.col;
    ctx.fill();

    ctx.fillStyle = "#0f172a";
    ctx.font = "650 12px -apple-system, BlinkMacSystemFont, sans-serif";
    ctx.textAlign = "center";
    ctx.fillText(n.title, n.x, n.y + 5);

    ctx.fillStyle = "#64748b";
    ctx.font = "500 10.5px 'SF Mono', Menlo, monospace";
    ctx.fillText(n.sub, n.x, n.y + 20);
    ctx.restore();
  });
}

function drawArenaMotionCanvas(t) {
  const cv = document.getElementById("arena-canvas");
  if (!cv || !document.getElementById("pane-arena").classList.contains("active")) return;
  const { ctx, w, h } = setupHiDPICanvas(cv);
  ctx.clearRect(0, 0, w, h);

  const xSrc = 110;
  const xLeak = w * 0.36;
  const xHold = w * 0.67;
  const xOut = w - 115;
  const yTop = 44, yMid = h / 2, yBot = h - 44;

  const tracks = [
    { y: yTop, label: "Candidate 1 · Literal Shortcut", stopX: xLeak, col: "#ff3b30", tag: "Blocked at Static Leak Shield ($0)" },
    { y: yMid, label: "Candidate 2 · Global Cap", stopX: xHold, col: "#f59e0b", tag: "Rejected at Secret Holdout (100% -> 0%)" },
    { y: yBot, label: "Candidate 3 · Composed Skill", stopX: xOut, col: "#16a34a", tag: "Promoted · Dev 100% · Holdout 100%" },
  ];

  // Gate vertical glass pillars
  [[xLeak, "Static Leak Shield", "#007aff"], [xHold, "Secret Holdout Gate", "#9333ea"]].forEach(([gx, glabel, gcol]) => {
    ctx.save();
    ctx.beginPath();
    ctx.roundRect(gx - 16, 16, 32, h - 32, 12);
    ctx.fillStyle = "rgba(255,255,255,0.62)";
    ctx.strokeStyle = "rgba(255,255,255,0.95)";
    ctx.lineWidth = 1.2;
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = gcol;
    ctx.font = "650 11px -apple-system, sans-serif";
    ctx.textAlign = "center";
    ctx.fillText(glabel, gx, 11);
    ctx.restore();
  });

  // Bezier paths + animated probe packets
  const speed = probePulseBoost > 0 ? 0.52 : 0.26;
  if (probePulseBoost > 0) probePulseBoost = Math.max(0, probePulseBoost - 0.01);

  tracks.forEach((tr, idx) => {
    ctx.beginPath();
    ctx.moveTo(xSrc, yMid);
    ctx.bezierCurveTo(xSrc + 95, yMid, xSrc + 105, tr.y, xLeak, tr.y);
    if (tr.stopX > xLeak) ctx.lineTo(tr.stopX, tr.y);
    ctx.strokeStyle = idx === 2 ? "rgba(22, 163, 74, 0.34)" : "rgba(148, 163, 184, 0.28)";
    ctx.lineWidth = idx === 2 ? 3.5 : 2.2;
    ctx.stroke();

    // Label pill
    ctx.fillStyle = "#334155";
    ctx.font = "600 11.5px -apple-system, sans-serif";
    ctx.textAlign = "left";
    ctx.fillText(tr.label, xSrc + 112, tr.y - 9);

    ctx.fillStyle = tr.col;
    ctx.font = "600 11px 'SF Mono', Menlo, monospace";
    ctx.fillText(tr.tag, xHold + 28, tr.y + 4);

    // Animated packet along track
    const cycle = ((t * speed) + idx * 0.28) % 1;
    const maxSpan = tr.stopX - xSrc;
    const px = xSrc + Math.min(1, cycle * 1.25) * maxSpan;
    let py = tr.y;
    if (px < xLeak) {
      const u = (px - xSrc) / (xLeak - xSrc);
      const ease = u * u * (3 - 2 * u);
      py = yMid + (tr.y - yMid) * ease;
    }

    if (cycle < 0.82) {
      const g = ctx.createRadialGradient(px, py, 1, px, py, 14);
      g.addColorStop(0, tr.col);
      g.addColorStop(1, "rgba(255,255,255,0)");
      ctx.fillStyle = g;
      ctx.beginPath(); ctx.arc(px, py, 14, 0, Math.PI * 2); ctx.fill();
      ctx.fillStyle = "#ffffff";
      ctx.beginPath(); ctx.arc(px, py, 3.5, 0, Math.PI * 2); ctx.fill();
    } else {
      // Ripple ring at stop/accept point
      const rp = (cycle - 0.82) / 0.18;
      ctx.beginPath();
      ctx.arc(tr.stopX, tr.y, 6 + rp * 18, 0, Math.PI * 2);
      ctx.strokeStyle = tr.col;
      ctx.globalAlpha = 1 - rp;
      ctx.lineWidth = 2;
      ctx.stroke();
      ctx.globalAlpha = 1;
    }
  });

  // Source & Destination Glass Nodes
  [[xSrc, yMid, "Propose K=3", "#0f172a"], [xOut, yBot, "Baseline 100%", "#16a34a"]].forEach(([nx, ny, lbl, col]) => {
    ctx.save();
    ctx.beginPath();
    ctx.roundRect(nx - 62, ny - 22, 124, 44, 14);
    ctx.fillStyle = "rgba(255,255,255,0.90)";
    ctx.shadowColor = "rgba(15,23,42,0.10)";
    ctx.shadowBlur = 14;
    ctx.fill();
    ctx.shadowBlur = 0;
    ctx.strokeStyle = col;
    ctx.lineWidth = 1.5;
    ctx.stroke();
    ctx.fillStyle = col;
    ctx.font = "700 12px -apple-system, sans-serif";
    ctx.textAlign = "center";
    ctx.fillText(lbl, nx, ny + 4);
    ctx.restore();
  });
}

function drawHarnessOrbitalCanvas() {
  const cv = document.getElementById("h2-canvas");
  if (!cv || !document.getElementById("pane-harness2").classList.contains("active")) return;
  const { ctx, w, h } = setupHiDPICanvas(cv);
  ctx.clearRect(0, 0, w, h);
  if (!isDragging) orbitYaw += 0.0065;
  const cx = w / 2, cy = h / 2, R = Math.min(w, h) * 0.37;
  const colors = { context: "#007aff", interface: "#16a34a", middleware: "#9333ea" };
  const comps = STATE.harness.components;

  const project = (x, y, z) => {
    const x1 = x * Math.cos(orbitYaw) - z * Math.sin(orbitYaw);
    const z1 = x * Math.sin(orbitYaw) + z * Math.cos(orbitYaw);
    const y2 = y * Math.cos(orbitPitch) - z1 * Math.sin(orbitPitch);
    const z2 = y * Math.sin(orbitPitch) + z1 * Math.cos(orbitPitch);
    const f = 480 / (480 - z2);
    return { sx: cx + x1 * f, sy: cy + y2 * f, z: z2, f };
  };

  // Dual glass orbital rings
  [0.28, -0.28, 0].forEach((lat, ri) => {
    ctx.beginPath();
    const rl = R * Math.cos(lat);
    for (let i = 0; i <= 72; i++) {
      const a = (i / 72) * Math.PI * 2;
      const p = project(rl * Math.cos(a), R * Math.sin(lat), rl * Math.sin(a));
      if (i === 0) ctx.moveTo(p.sx, p.sy); else ctx.lineTo(p.sx, p.sy);
    }
    ctx.strokeStyle = ri === 2 ? "rgba(0,122,255,0.22)" : "rgba(147,51,234,0.12)";
    ctx.lineWidth = 1.4;
    ctx.stroke();
  });

  const nodes = comps.map((c, i) => {
    const lon = (i / Math.max(1, comps.length)) * Math.PI * 2;
    const lat = (i % 2 === 0 ? 0.28 : -0.28);
    const rl = R * Math.cos(lat);
    const p = project(rl * Math.cos(lon), R * Math.sin(lat), rl * Math.sin(lon));
    return { idx: i, c, ...p };
  });
  orbitHitNodes = nodes.map(n => ({ idx: n.idx, sx: n.sx, sy: n.sy, r: 22 }));
  nodes.push({ core: true, sx: cx, sy: cy, z: 0, f: 1 });
  nodes.sort((a, b) => a.z - b.z);

  for (const n of nodes) {
    if (n.core) {
      const halo = ctx.createRadialGradient(cx, cy, 8, cx, cy, 64);
      halo.addColorStop(0, "rgba(0,122,255,0.25)");
      halo.addColorStop(1, "rgba(0,122,255,0)");
      ctx.fillStyle = halo;
      ctx.beginPath(); ctx.arc(cx, cy, 64, 0, Math.PI * 2); ctx.fill();

      const g = ctx.createRadialGradient(cx - 12, cy - 12, 4, cx, cy, 44);
      g.addColorStop(0, "#7dd3fc"); g.addColorStop(0.55, "#007aff"); g.addColorStop(1, "#0f172a");
      ctx.beginPath(); ctx.arc(cx, cy, 40, 0, Math.PI * 2);
      ctx.fillStyle = g; ctx.fill();
      ctx.fillStyle = "#fff"; ctx.font = "700 10.5px -apple-system, sans-serif"; ctx.textAlign = "center";
      ctx.fillText("FROZEN", cx, cy - 2); ctx.fillText("MODEL", cx, cy + 11);
    } else {
      const isSel = n.idx === selectedCompIdx;
      ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(n.sx, n.sy);
      ctx.strokeStyle = isSel ? "rgba(0,122,255,0.55)" : "rgba(148,163,184,0.22)";
      ctx.lineWidth = isSel ? 2.2 : 1.2;
      ctx.stroke();

      const col = colors[n.c.space] || "#007aff";
      const rad = (isSel ? 14 : 10.5) * n.f;
      if (isSel) {
        ctx.beginPath(); ctx.arc(n.sx, n.sy, rad + 7, 0, Math.PI * 2);
        ctx.fillStyle = "rgba(0,122,255,0.16)"; ctx.fill();
      }
      ctx.beginPath(); ctx.arc(n.sx, n.sy, rad, 0, Math.PI * 2);
      ctx.fillStyle = col; ctx.fill();
      ctx.beginPath(); ctx.arc(n.sx - rad * 0.28, n.sy - rad * 0.28, rad * 0.34, 0, Math.PI * 2);
      ctx.fillStyle = "rgba(255,255,255,0.78)"; ctx.fill();

      // Frosted label pill
      ctx.font = "600 11px 'SF Mono', Menlo, monospace";
      const tw = ctx.measureText(n.c.filename).width + 14;
      ctx.fillStyle = isSel ? "rgba(255,255,255,0.96)" : "rgba(255,255,255,0.78)";
      ctx.beginPath(); ctx.roundRect(n.sx - tw / 2, n.sy + rad + 5, tw, 20, 10); ctx.fill();
      ctx.fillStyle = isSel ? "#007aff" : "#0f172a";
      ctx.textAlign = "center";
      ctx.fillText(n.c.filename, n.sx, n.sy + rad + 18.5);
    }
  }
}

function tickMotionGraphics(now) {
  animClock = now * 0.001;
  drawOverviewFlowCanvas(animClock);
  drawArenaMotionCanvas(animClock);
  drawHarnessOrbitalCanvas();
  requestAnimationFrame(tickMotionGraphics);
}

/* ==================== INTERACTIONS & API CALLS ==================== */
document.querySelectorAll("#main-tabs .seg-btn").forEach(btn => {
  btn.onclick = () => {
    document.querySelectorAll("#main-tabs .seg-btn").forEach(b => b.classList.remove("active"));
    document.querySelectorAll(".view-pane").forEach(p => p.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById(`pane-${btn.dataset.tab}`).classList.add("active");
    updateNavGlider();
  };
});

document.querySelectorAll("#trace-filter .seg-btn").forEach(btn => {
  btn.onclick = () => {
    document.querySelectorAll("#trace-filter .seg-btn").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    traceFilter = btn.dataset.f;
    selectedTrialIdx = 0;
    renderTracesPane();
  };
});

const cv = document.getElementById("h2-canvas");
let dragMoved = false;
cv.onmousedown = e => { isDragging = true; dragMoved = false; lastX = e.clientX; lastY = e.clientY; };
window.onmouseup = e => {
  if (isDragging && !dragMoved) {
    const r = cv.getBoundingClientRect();
    const mx = e.clientX - r.left, my = e.clientY - r.top;
    const hit = orbitHitNodes.find(n => Math.hypot(n.sx - mx, n.sy - my) < n.r);
    if (hit) { selectedCompIdx = hit.idx; renderHarnessPane(); }
  }
  isDragging = false;
};
window.onmousemove = e => {
  if (!isDragging) return;
  if (Math.abs(e.clientX - lastX) + Math.abs(e.clientY - lastY) > 3) dragMoved = true;
  orbitYaw += (e.clientX - lastX) * 0.01;
  orbitPitch = Math.max(-0.9, Math.min(0.9, orbitPitch + (e.clientY - lastY) * 0.01));
  lastX = e.clientX; lastY = e.clientY;
};

async function apiPost(path, body = {}) {
  const candidates = window.location.protocol === "file:"
    ? [`http://127.0.0.1:8484${path}`, `http://127.0.0.1:8485${path}`]
    : [path, `http://127.0.0.1:8484${path}`, `http://127.0.0.1:8485${path}`];
  let lastErr = null;
  for (const url of candidates) {
    try {
      const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(await res.text());
      return await res.json();
    } catch (err) {
      lastErr = err;
    }
  }
  throw lastErr || new Error("Studio server unreachable");
}

/* ==================== IMMEDIATE ONE-CLICK AUTO-FIX ==================== */
const afModal = document.getElementById("autofix-modal");
document.getElementById("af-close").onclick = () => afModal.classList.remove("open");
afModal.onclick = e => { if (e.target === afModal) afModal.classList.remove("open"); };

window.copyCmd = (cmd) => {
  if (navigator.clipboard) navigator.clipboard.writeText(cmd);
  toast("Copied command to clipboard");
};

window.jumpTab = (tabName) => {
  afModal.classList.remove("open");
  const b = document.querySelector(`#main-tabs [data-tab="${tabName}"]`);
  if (b) b.click();
};

window.triggerAutoFix = async (opts = {}) => {
  const targetCase = opts.targetCase || null;
  const targetModule = opts.targetModule || null;
  const label = targetModule
    ? `Module · ${targetModule}`
    : (targetCase ? `Case · ${targetCase}` : "All Failing Tests & Modules");

  document.getElementById("af-eyebrow").textContent = "Autonomous Self-Healing Engine";
  document.getElementById("af-eyebrow").style.color = "var(--red)";
  document.getElementById("af-title").textContent = `Auto-Fixing · ${label}`;
  document.getElementById("af-body").innerHTML = `
    <p style="font-size:13.8px; color:var(--ink-2); line-height:1.55; margin-bottom:12px;">
      Executing closed-loop repair inside an isolated throwaway workspace. Unit tests remain byte-for-byte write-locked and hidden holdout checks stay enforced.
    </p>
    <div class="step-row"><div class="spinner"></div><span>1. Isolating failure &amp; write-locking protected unit tests...</span></div>
    <div class="step-row" style="opacity:0.80;"><span class="mono" style="color:var(--blue);">02</span><span>2. Probing parallel code &amp; Harness² candidates...</span></div>
    <div class="step-row" style="opacity:0.68;"><span class="mono" style="color:var(--purple);">03</span><span>3. Verifying AST, pytest &amp; secret holdout gate...</span></div>
    <div class="step-row" style="opacity:0.58;"><span class="mono" style="color:var(--green);">04</span><span>4. Re-evaluating suite &amp; syncing memory.md...</span></div>
  `;
  afModal.classList.add("open");

  try {
    const endpoint = targetModule ? "/api/heal" : "/api/autofix";
    const data = await apiPost(endpoint, { target_case: targetCase, target_module: targetModule });
    STATE = data.state;
    renderAll();

    const beforePct = data.before_pass_rate != null ? `${Math.round(data.before_pass_rate * 100)}%` : "—";
    const afterPct = data.after_pass_rate != null ? `${Math.round(data.after_pass_rate * 100)}%` : "—";
    const healed = data.healed_modules || [];
    const unrepaired = data.unrepaired_modules || [];
    const hf = data.harness_fix;
    const isSuccess = data.status === "fixed" || data.status === "clean";

    document.getElementById("af-eyebrow").textContent = isSuccess ? "Automatic Repair Verified" : "Auto-Fix Diagnostic Summary";
    document.getElementById("af-eyebrow").style.color = isSuccess ? "var(--green)" : "var(--amber)";
    document.getElementById("af-body").innerHTML = `
      <div style="display:grid; grid-template-columns:repeat(3,1fr); gap:12px; margin-bottom:16px;">
        <div class="card-sub">
          <div class="card-title">Status</div>
          <div style="font-size:18px; font-weight:700; color:${isSuccess ? 'var(--green)' : 'var(--amber)'};">${esc(data.status.toUpperCase())}</div>
        </div>
        <div class="card-sub">
          <div class="card-title">Suite Pass Rate</div>
          <div style="font-size:18px; font-weight:700;">${beforePct} → <span style="color:var(--green);">${afterPct}</span></div>
        </div>
        <div class="card-sub">
          <div class="card-title">Healed / Probes</div>
          <div style="font-size:18px; font-weight:700;">${healed.length} mod · ${hf ? hf.accepted : 0} probe</div>
        </div>
      </div>
      ${healed.map(h => `
        <div style="margin-bottom:12px;">
          <div style="font-weight:650; font-size:13.5px; color:var(--green); margin-bottom:5px;">Healed ${esc(h.module)} — ${esc(h.reason)}</div>
          ${h.diff ? `<pre class="code-block mono" style="max-height:170px; font-size:11.5px;">${esc(h.diff)}</pre>` : ''}
        </div>
      `).join("")}
      ${hf ? `
        <div class="card-sub" style="margin-bottom:12px; font-size:13.5px;">
          <strong>Harness² Contrastive Fixer:</strong> ${esc(hf.status)} (${hf.accepted} accepted, ${hf.rejected} rejected)
          ${hf.message ? `<div style="color:var(--mute); font-size:12px; margin-top:4px;">${esc(hf.message)}</div>` : ''}
        </div>
      ` : ''}
      ${unrepaired.map(u => `
        <div class="card-sub" style="color:var(--red); font-size:12.5px; font-weight:600; margin-bottom:8px;">
          ${esc(u.module)}: ${esc(u.reason)}
        </div>
      `).join("")}
      <div style="display:flex; justify-content:flex-end; gap:10px; margin-top:14px;">
        <button class="btn btn-tinted" onclick="jumpTab('arena')">Inspect Probe Arena</button>
        <button class="btn btn-blue" onclick="jumpTab('traces')">View Updated Trials</button>
      </div>
    `;
    toast(isSuccess ? `Auto-Fix complete · Suite ${afterPct}` : `Auto-Fix finished (${data.status})`);
  } catch (err) {
    const cliCmd = targetModule
      ? `cd "${STATE.project_root}" && autoharness heal "${targetModule}" --sync-memory`
      : `cd "${STATE.project_root}" && autoharness heal --with-harness`;
    const studioCmd = `cd "${STATE.project_root}" && autoharness studio`;
    document.getElementById("af-eyebrow").textContent = "Start Live Studio Server or Run CLI";
    document.getElementById("af-eyebrow").style.color = "var(--amber)";
    document.getElementById("af-body").innerHTML = `
      <p style="font-size:13.8px; color:var(--ink-2); line-height:1.55; margin-bottom:12px;">
        Start <code>autoharness studio</code> once for instant in-browser repairs, or execute the repair command directly:
      </p>
      <div class="card-title">Immediate Auto-Fix Command</div>
      <pre class="code-block mono" style="margin-bottom:12px;">${esc(cliCmd)}</pre>
      <div style="display:flex; gap:10px; flex-wrap:wrap;">
        <button class="btn btn-fix" onclick="copyCmd(${esc(JSON.stringify(cliCmd))})">Copy Auto-Fix Command</button>
        <button class="btn btn-blue" onclick="copyCmd(${esc(JSON.stringify(studioCmd))})">Copy Live Studio Command</button>
      </div>
    `;
  }
};

document.getElementById("btn-top-autofix").onclick = () => triggerAutoFix({});
document.getElementById("btn-heal-now").onclick = () => triggerAutoFix({ targetModule: (STATE.modules.find(m => !m.healthy) || {}).path || null });

document.getElementById("btn-sim-probe").onclick = async () => {
  probePulseBoost = 1.0;
  toast("Running 3-candidate contrastive probe simulation...");
  try {
    const data = await apiPost("/api/simulate_probe", {});
    STATE = data.state;
    renderAll();
    toast("Probed 3 candidates · Candidate 3 promoted (Holdout 100%)");
  } catch (e) {
    toast("Simulation requires live Studio server");
  }
};

document.getElementById("btn-show-compiled").onclick = () => {
  document.getElementById("ed-space").textContent = "UNIFIED RUNTIME PROMPT";
  document.getElementById("ed-filename").textContent = "compiled_prompt()";
  document.getElementById("ed-textarea").value = STATE.harness.compiled_prompt;
  document.getElementById("ed-textarea").readOnly = true;
};

document.getElementById("btn-save-comp").onclick = async () => {
  const cur = STATE.harness.components[selectedCompIdx];
  if (!cur || document.getElementById("ed-textarea").readOnly) return;
  try {
    const data = await apiPost("/api/component/save", { filename: cur.filename, content: document.getElementById("ed-textarea").value });
    STATE = data.state;
    renderAll();
    toast(`Saved ${cur.filename}`);
  } catch (e) { toast(`Save unavailable in static mode`); }
};

document.getElementById("btn-quick-eval").onclick = async () => {
  toast("Running evaluation suite...");
  try {
    const data = await apiPost("/api/run_offline", {});
    STATE = data.state;
    renderAll();
    toast(`Suite complete · Pass rate ${Math.round(STATE.latest_summary.pass_rate * 100)}%`);
  } catch (e) { toast("Live server required to trigger runs"); }
};

document.getElementById("btn-rescan-code").onclick = async () => {
  toast("Scanning codebase AST, imports & tests...");
  try {
    const data = await apiPost("/api/scan", {});
    STATE = data.state;
    renderAll();
    toast(`Verified ${STATE.modules.length} modules`);
  } catch (e) { toast("Live server required"); }
};

document.getElementById("btn-synth-cases").onclick = async () => {
  toast("Synthesizing YAML cases from source code...");
  try {
    const data = await apiPost("/api/synthesize", {});
    STATE = data.state;
    renderAll();
    toast(`Synthesized ${data.written} case(s) in cases/auto/`);
  } catch (e) { toast("Live server required"); }
};

window.toggleHook = async (kind, status) => {
  try {
    const data = await apiPost("/api/hook", { kind, action: status === "autoharness" ? "uninstall" : "install" });
    STATE = data.state;
    renderAll();
    toast(`${status === "autoharness" ? "Removed" : "Installed"} ${kind} hook`);
  } catch (e) { toast("Live server required"); }
};

/* ==================== UNIVERSAL AI MODEL & SUBSCRIPTION SWITCHER ==================== */
const mdModal = document.getElementById("model-modal");
document.getElementById("md-close").onclick = () => mdModal.classList.remove("open");
mdModal.onclick = e => { if (e.target === mdModal) mdModal.classList.remove("open"); };

function openModelModal() {
  const prov = STATE.providers || { presets: [], subscriptions: {}, clis: {}, api_keys: {}, local_servers: {} };
  const presets = prov.presets || [];
  const subs = prov.subscriptions || {};
  const curType = STATE.agent.type;
  const curModel = STATE.agent.model || "";
  const curAuth = STATE.agent.auth_mode || "";

  const subEntries = [
    ["claude", "Claude Pro / Max / Team"],
    ["codex", "ChatGPT Plus / Pro (Codex)"],
    ["gemini", "Google AI Pro / Ultra"],
    ["copilot", "GitHub Copilot Pro"],
  ];

  document.getElementById("md-body").innerHTML = `
    <div class="card-sub" style="padding:14px 16px; margin-bottom:14px;">
      <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;">
        <div class="card-title" style="margin-bottom:0;">Detected Flat-Rate OAuth Subscriptions ($0 API Billing)</div>
        <span class="badge b-green">API Keys Isolated in Subscription Mode</span>
      </div>
      <div style="display:grid; grid-template-columns:repeat(4, 1fr); gap:9px;">
        ${subEntries.map(([key, title]) => {
          const s = subs[key] || { active: false, plan: "Not signed in" };
          const cleanPlan = String(s.plan || "").replace(/`/g, "");
          return `
            <div style="padding:10px 12px; border-radius:13px; background:rgba(255,255,255,0.74); border:1px solid ${s.active ? 'rgba(52,199,89,0.38)' : 'rgba(15,23,42,0.08)'};">
              <div style="display:flex; align-items:center; justify-content:space-between; gap:6px; margin-bottom:3px;">
                <span style="font-size:12px; font-weight:700;">${esc(title)}</span>
                <span class="badge ${s.active ? 'b-green' : 'b-slate'}" style="padding:2px 7px; font-size:10.5px;">${s.active ? 'Connected' : 'Sign In'}</span>
              </div>
              <div style="font-size:11.5px; color:var(--mute);">${esc(cleanPlan)}${s.account ? ' · ' + esc(s.account) : ''}</div>
            </div>`;
        }).join("")}
      </div>
    </div>

    <div style="display:grid; grid-template-columns:repeat(2, 1fr); gap:11px; margin-bottom:16px;">
      ${presets.map(p => {
        const isActive = (curType === p.agent_type && (curModel === p.model || p.id === "subscription-pool") && (!p.auth_mode || p.auth_mode === curAuth));
        return `
          <div class="comp-item ${isActive ? 'active' : ''}" onclick="activatePreset('${esc(p.id)}')" style="padding:13px 15px;">
            <div style="display:flex; justify-content:space-between; align-items:center; gap:8px; margin-bottom:4px;">
              <span style="font-weight:700; font-size:14px;">${esc(p.label)}</span>
              <span class="badge ${p.badge.includes('$0') ? 'b-green' : 'b-blue'}">${esc(p.badge)}</span>
            </div>
            <div style="font-size:12px; color:var(--mute); line-height:1.45;">${esc(p.description)}</div>
          </div>`;
      }).join("")}
    </div>
    <div class="card-sub" style="padding:14px 16px;">
      <div class="card-title" style="margin-bottom:8px;">Custom Open-Source / Ollama / vLLM / OpenRouter Model</div>
      <div style="display:grid; grid-template-columns:1fr 1fr auto; gap:10px; align-items:center;">
        <input id="md-custom-model" class="mono" placeholder="Model (e.g. qwen2.5-coder:32b, deepseek-r1:14b, o4-mini)"
          value="${esc(curModel)}"
          style="padding:9px 12px; border-radius:11px; border:1px solid rgba(15,23,42,0.12); background:rgba(255,255,255,0.85); font-size:12.5px;"/>
        <input id="md-custom-url" class="mono" placeholder="Endpoint (e.g. http://127.0.0.1:11434 or :8000/v1)"
          value="${esc(STATE.agent.base_url || 'http://127.0.0.1:11434')}"
          style="padding:9px 12px; border-radius:11px; border:1px solid rgba(15,23,42,0.12); background:rgba(255,255,255,0.85); font-size:12.5px;"/>
        <button class="btn btn-blue" onclick="activateCustomOSS()">Activate Model</button>
      </div>
    </div>
  `;
  mdModal.classList.add("open");
}

window.activatePreset = async (presetId) => {
  try {
    const data = await apiPost("/api/model/select", { preset_id: presetId });
    STATE = data.state;
    renderAll();
    mdModal.classList.remove("open");
    toast(`Switched substrate -> ${data.preset.label}`);
  } catch (e) {
    toast(`Run: autoharness models --use ${presetId}`);
  }
};

window.activateCustomOSS = async () => {
  const m = document.getElementById("md-custom-model").value.trim() || "qwen2.5-coder:14b";
  const u = document.getElementById("md-custom-url").value.trim() || "http://127.0.0.1:11434";
  const presetId = u.includes("11434") ? "ollama-qwen" : (u.includes("openrouter") ? "openrouter-oss" : "vllm-local");
  try {
    const data = await apiPost("/api/model/select", { preset_id: presetId, model: m, base_url: u });
    STATE = data.state;
    renderAll();
    mdModal.classList.remove("open");
    toast(`Activated ${m} (${u})`);
  } catch (e) {
    toast(`Run: autoharness models --use ${presetId} --model ${m}`);
  }
};

/* ==================== MCP SERVER & UNIVERSAL PLUGIN HUB ==================== */
const plModal = document.getElementById("plugin-modal");
document.getElementById("pl-close").onclick = () => plModal.classList.remove("open");
plModal.onclick = e => { if (e.target === plModal) plModal.classList.remove("open"); };

function openPluginModal() {
  const st = STATE.plugins || {};
  const mcpCfg = JSON.stringify({ mcpServers: { autoharness_mcp: STATE.mcp_entry || { command: "autoharness", args: ["mcp"] } } }, null, 2);
  const targets = [
    { id: "antigravity", name: "Google Antigravity", desc: "Installs global plugin + SKILL.md + autoharness_mcp into ~/.gemini/config/plugins/autoharness" },
    { id: "claude", name: "Claude Code & Desktop", desc: "Writes .mcp.json + .claude-plugin/plugin.json + skills/autoharness/SKILL.md" },
    { id: "cursor", name: "Cursor IDE", desc: "Writes .cursor/mcp.json + .cursor/rules/autoharness.mdc write-locked healing rules" },
    { id: "vscode", name: "VS Code / GitHub Copilot", desc: "Writes .vscode/mcp.json stdio server config for Copilot Agent Mode" },
    { id: "gemini", name: "Google Gemini CLI", desc: "Registers autoharness_mcp inside .gemini/settings.json" },
    { id: "codex", name: "OpenAI Codex CLI", desc: "Registers [mcp_servers.autoharness_mcp] inside .codex/config.toml" },
  ];

  document.getElementById("pl-body").innerHTML = `
    <div style="display:flex; justify-content:space-between; align-items:center; gap:12px; margin-bottom:12px;">
      <div style="font-size:13px; color:var(--ink-2); font-weight:600;">9 MCP Tools Exposed: status · scan_code · snapshot_test_locks · verify_repair · synthesize_cases · run_eval · autofix · harness_component · lessons</div>
      <button class="btn btn-blue" onclick="installPluginTarget('all')">Install Into All 6 Agents</button>
    </div>
    <div style="display:grid; grid-template-columns:repeat(2, 1fr); gap:11px; margin-bottom:16px;">
      ${targets.map(t => {
        const installed = Boolean(st[t.id]);
        return `
          <div class="comp-item ${installed ? 'active' : ''}" onclick="installPluginTarget('${t.id}')" style="padding:13px 15px;">
            <div style="display:flex; justify-content:space-between; align-items:center; gap:8px; margin-bottom:5px;">
              <span style="font-weight:700; font-size:14px;">${esc(t.name)}</span>
              <span class="badge ${installed ? 'b-green' : 'b-blue'}">${installed ? 'Installed' : '1-Click Install'}</span>
            </div>
            <div style="font-size:12px; color:var(--mute); line-height:1.45;">${esc(t.desc)}</div>
          </div>`;
      }).join("")}
    </div>
    <div class="card-sub" style="padding:14px 16px;">
      <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
        <div class="card-title" style="margin-bottom:0;">Universal Stdio MCP Config (Any MCP Client: Windsurf, Cline, Zed, Continue)</div>
        <button class="btn btn-tinted" style="padding:5px 12px; font-size:12px;" onclick="copyMcpSnippet()">Copy JSON</button>
      </div>
      <pre class="code-block mono" id="pl-mcp-json" style="margin:0; font-size:11.5px;">${esc(mcpCfg)}</pre>
    </div>
  `;
  plModal.classList.add("open");
}

window.installPluginTarget = async (target) => {
  try {
    const data = await apiPost("/api/plugin/install", { target });
    STATE = data.state;
    renderAll();
    openPluginModal();
    toast(`Installed AutoHarness plugin (${target})`);
  } catch (e) {
    toast(`Run: autoharness plugin install --target ${target}`);
  }
};

window.copyMcpSnippet = () => {
  const txt = document.getElementById("pl-mcp-json")?.textContent || "";
  if (navigator.clipboard) navigator.clipboard.writeText(txt);
  toast("Copied MCP server config to clipboard");
};

document.getElementById("hdr-fp").onclick = openModelModal;
document.getElementById("btn-mcp-plugins").onclick = openPluginModal;

renderAll();
requestAnimationFrame(tickMotionGraphics);
</script>
</body>
</html>
"""


def render_studio_html(cfg: Config, store: Store) -> str:
    state = collect_studio_state(cfg, store, scan_code=True)
    return STUDIO_HTML.replace("__INITIAL_STATE__", json.dumps(state))


def serve_studio(cfg: Config, store: Store, host: str = "127.0.0.1", port: int = 8484,
                 open_browser: bool = True) -> None:
    """Launch the live interactive AutoHarness Studio HTTP server."""
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:
            return

        def _send_cors(self) -> None:
            origin = self.headers.get("Origin", "")
            if origin in ("null", "") or origin.startswith(("http://127.0.0.1", "http://localhost")):
                self.send_header("Access-Control-Allow-Origin", origin or "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")

        def do_OPTIONS(self) -> None:
            self.send_response(204)
            self._send_cors()
            self.end_headers()

        def _send_json(self, obj: Any, code: int = 200) -> None:
            raw = json.dumps(obj).encode()
            self.send_response(code)
            self._send_cors()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/api/state":
                with lock:
                    self._send_json(collect_studio_state(cfg, store, scan_code=True))
                return
            html_bytes = render_studio_html(cfg, store).encode()
            self.send_response(200)
            self._send_cors()
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html_bytes)))
            self.end_headers()
            self.wfile.write(html_bytes)

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length).decode() or "{}") if length else {}
            parsed = urlparse(self.path)
            with lock:
                if parsed.path == "/api/component/save":
                    spf = cfg["agent"].get("system_prompt_file", "harnesses/default/system.md")
                    hpath = cfg.path(spf)
                    h2 = cfg.path("harnesses/harness2")
                    base = hpath if hpath.is_dir() else (h2 if h2.exists() else hpath.parent)
                    target = (base / body["filename"]).resolve()
                    if base.resolve() not in target.parents and target != hpath.resolve():
                        self._send_json({"error": "path outside harness directory"}, 400)
                        return
                    target.write_text(str(body.get("content", "")))
                    self._send_json({"ok": True, "state": collect_studio_state(cfg, store)})
                    return

                if parsed.path == "/api/scan":
                    self._send_json({"ok": True, "state": collect_studio_state(cfg, store, scan_code=True, run_code_checks=True)})
                    return

                if parsed.path == "/api/synthesize":
                    targets = cfg.raw.get("code_paths") or ["autoharness" if (cfg.root / "autoharness").exists() else "."]
                    written = synthesize_cases(cfg.root, targets, out_dir=cfg.path("cases/auto"))
                    self._send_json({"ok": True, "written": len(written), "state": collect_studio_state(cfg, store, scan_code=True, run_code_checks=True)})
                    return

                if parsed.path in ("/api/autofix", "/api/heal"):
                    res = run_immediate_autofix(
                        cfg,
                        store,
                        target_case=body.get("target_case"),
                        target_module=body.get("target_module"),
                    )
                    self._send_json(res)
                    return

                if parsed.path == "/api/simulate_probe":
                    state = _record_simulated_probe(cfg, store)
                    self._send_json({"ok": True, "state": state})
                    return

                if parsed.path == "/api/model/select":
                    preset = apply_substrate_selection(
                        cfg,
                        str(body.get("preset_id") or "auto"),
                        custom_model=body.get("model"),
                        custom_base_url=body.get("base_url"),
                    )
                    self._send_json({"ok": True, "preset": preset, "state": collect_studio_state(cfg, store)})
                    return

                if parsed.path == "/api/plugin/install":
                    target = str(body.get("target") or "all")
                    written = install_plugin_target(target, cfg.root)
                    self._send_json({"ok": True, "written": written, "state": collect_studio_state(cfg, store)})
                    return

                if parsed.path == "/api/run_offline":
                    from .agents import ScriptedAgent
                    from .runner import run_suite

                    spf = cfg["agent"].get("system_prompt_file", "harnesses/default/system.md")
                    hpath = cfg.path(spf)
                    tree = HarnessTree.load(hpath) if hpath.exists() else None
                    cases = load_cases([cfg.path(p) for p in cfg["cases"] if cfg.path(p).exists()])
                    summary = run_suite(cfg, cases, ScriptedAgent(tree=tree), None, store, trials=1, workers=2, label="studio", quiet=True)
                    if not store.baseline():
                        store.set_baseline(summary["run_id"])
                    self._send_json({"ok": True, "run_id": summary["run_id"], "state": collect_studio_state(cfg, store, scan_code=True, run_code_checks=True)})
                    return

                if parsed.path == "/api/hook":
                    kind = body.get("kind", "pre-commit")
                    if body.get("action") == "uninstall":
                        uninstall_hook(cfg.root, kind=kind)
                    else:
                        install_hook(cfg.root, kind=kind)
                    self._send_json({"ok": True, "state": collect_studio_state(cfg, store)})
                    return

            self._send_json({"error": "unknown endpoint"}, 404)

    server = ThreadingHTTPServer((host, port), Handler)
    actual_port = server.server_address[1]
    url = f"http://{host}:{actual_port}"
    print(f"AutoHarness Studio running at {url}  (Ctrl+C to stop)")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
