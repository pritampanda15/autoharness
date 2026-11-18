"""AutoHarness Studio (`autoharness studio`): Apple-designed interactive visual workbench.

Serves a local zero-CDN Apple Human Interface Guidelines (HIG) web application backed
directly by your live project:
  1. Overview & Telemetry      — Bento KPI radial gauges, run trend chart, 3-layer waterfall
  2. 8-Component Harness² Tree — Interactive 3D orbital canvas + live component editor & compiler
  3. Code Self-Healer          — AST / import / pytest module scanner, locked-test guard & case synthesizer
  4. Probe Arena               — Parallel Propose-Probe-Compose contrastive matrix & fixlog diffs
  5. Runs & Trace Inspector    — Filterable dev/holdout cases, check layers & step-by-step tool traces
  6. Lessons & Continuous Loop — Persistent DO/AVOID ledger, session miner & Git hook controls
"""

from __future__ import annotations

import json
import re
import threading
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
from .store import Store
from .synthesizer import build_context_map, scan_codebase, synthesize_cases
from .tree import COMPONENTS, HarnessTree


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
        diff_txt = ""
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
        diff_txt = "\n".join(diff_lines)
        entries.append({
            "header": header,
            "verdict": verdict,
            "approach": approach or "Candidate edit",
            "diagnosis": diagnosis,
            "rejected_why": rejected_why,
            "metrics": metrics,
            "diff": diff_txt,
        })
    return list(reversed(entries[-24:]))


def collect_studio_state(cfg: Config, store: Store, scan_code: bool = True) -> dict[str, Any]:
    """Assemble the complete live project state for AutoHarness Studio."""
    spf = cfg["agent"].get("system_prompt_file", "harnesses/default/system.md")
    hpath = cfg.path(spf)
    # Also check if harnesses/harness2 exists so the user can inspect the full 8-component tree
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

    # Discover cases
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

    # Runs & traces
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

    # Codebase scan
    code_targets = cfg.raw.get("code_paths") or (["autoharness"] if (cfg.root / "autoharness").exists() else ["."])
    modules_data = []
    context_map = ""
    if scan_code:
        mods = scan_codebase(cfg.root, code_targets, run_checks=False)
        context_map = build_context_map(mods)
        for m in mods[:40]:
            modules_data.append({
                "path": m.rel_path,
                "healthy": m.healthy,
                "syntax_ok": m.syntax_ok,
                "syntax_error": m.syntax_error,
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
    }


STUDIO_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AutoHarness Studio</title>
<style>
:root {
  --bg: #f5f5f7;
  --card: rgba(255, 255, 255, 0.88);
  --card-solid: #ffffff;
  --ink: #1d1d1f;
  --ink-2: #424245;
  --mute: #86868b;
  --line: rgba(15, 23, 42, 0.08);
  --blue: #0071e3;
  --blue-soft: rgba(0, 113, 227, 0.10);
  --green: #1d9d5f;
  --green-soft: rgba(29, 157, 95, 0.11);
  --amber: #d97706;
  --amber-soft: rgba(217, 119, 6, 0.12);
  --red: #e11d48;
  --red-soft: rgba(225, 29, 72, 0.10);
  --purple: #7c3aed;
  --purple-soft: rgba(124, 58, 237, 0.10);
  --shadow-sm: 0 2px 8px rgba(15, 23, 42, 0.04), 0 0 1px rgba(15, 23, 42, 0.08);
  --shadow-md: 0 12px 34px rgba(15, 23, 42, 0.06), 0 2px 6px rgba(15, 23, 42, 0.03);
  --shadow-lg: 0 24px 60px rgba(15, 23, 42, 0.10), 0 4px 16px rgba(0, 113, 227, 0.05);
  --radius: 20px;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body {
  min-height: 100%;
  background:
    radial-gradient(1100px 620px at 14% -6%, rgba(0, 113, 227, 0.09), transparent 65%),
    radial-gradient(950px 580px at 88% 4%, rgba(124, 58, 237, 0.07), transparent 62%),
    var(--bg);
  color: var(--ink);
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "SF Pro Text", "Helvetica Neue", Inter, sans-serif;
  -webkit-font-smoothing: antialiased;
}
.mono {
  font-family: "SF Mono", ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}

/* Sticky Apple Frosted Glass Navigation Bar */
.topbar {
  position: sticky;
  top: 0;
  z-index: 100;
  height: 64px;
  padding: 0 32px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: rgba(255, 255, 255, 0.76);
  backdrop-filter: saturate(180%) blur(24px);
  -webkit-backdrop-filter: saturate(180%) blur(24px);
  border-bottom: 1px solid var(--line);
}
.brand {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 19px;
  font-weight: 700;
  letter-spacing: -0.02em;
}
.brand .sub-pill {
  font-size: 11.5px;
  font-weight: 700;
  padding: 3px 9px;
  border-radius: 999px;
  background: var(--blue-soft);
  color: var(--blue);
  letter-spacing: 0.02em;
}

/* Segmented Control */
.seg-nav {
  display: flex;
  gap: 4px;
  padding: 4px;
  border-radius: 13px;
  background: rgba(15, 23, 42, 0.055);
}
.seg-btn {
  border: none;
  background: transparent;
  padding: 7px 15px;
  border-radius: 9px;
  font-size: 13.5px;
  font-weight: 600;
  color: var(--ink-2);
  cursor: pointer;
  transition: all 0.18s cubic-bezier(0.25, 1, 0.5, 1);
}
.seg-btn:hover { color: var(--ink); }
.seg-btn.active {
  background: #ffffff;
  color: var(--blue);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08), 0 0 1px rgba(0, 0, 0, 0.08);
}

.top-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}
.status-chip {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 6px 13px;
  border-radius: 999px;
  font-size: 12.5px;
  font-weight: 600;
  background: var(--green-soft);
  color: var(--green);
}
.status-chip .dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--green);
}
.btn {
  border: none;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 8px 16px;
  border-radius: 980px;
  font-size: 13.5px;
  font-weight: 600;
  transition: transform 0.14s ease, box-shadow 0.14s ease, opacity 0.14s ease;
}
.btn:active { transform: scale(0.97); }
.btn-blue {
  background: linear-gradient(180deg, #0077ed 0%, #0062cc 100%);
  color: #ffffff;
  box-shadow: 0 4px 12px rgba(0, 113, 227, 0.25);
}
.btn-tinted {
  background: var(--blue-soft);
  color: var(--blue);
}
.btn-green {
  background: linear-gradient(180deg, #22c55e 0%, #15803d 100%);
  color: #ffffff;
  box-shadow: 0 4px 12px rgba(21, 128, 61, 0.22);
}

/* Main Container */
.container {
  max-width: 1460px;
  margin: 0 auto;
  padding: 28px 32px 72px;
}
.hero-strip {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  margin-bottom: 24px;
}
.hero-strip .eyebrow {
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--blue);
  margin-bottom: 6px;
}
.hero-strip h1 {
  font-size: 34px;
  font-weight: 800;
  letter-spacing: -0.028em;
  line-height: 1.08;
}
.hero-strip .subtitle {
  color: var(--mute);
  font-size: 15px;
  margin-top: 4px;
}

/* Bento Cards */
.bento-4 { display: grid; grid-template-columns: repeat(4, 1fr); gap: 18px; margin-bottom: 20px; }
.bento-3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 18px; margin-bottom: 20px; }
.bento-2 { display: grid; grid-template-columns: 1.12fr 0.88fr; gap: 20px; margin-bottom: 20px; }
.bento-split { display: grid; grid-template-columns: 0.95fr 1.05fr; gap: 20px; margin-bottom: 20px; }

.card {
  background: var(--card-solid);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 22px 24px;
  box-shadow: var(--shadow-md);
}
.card-title {
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.10em;
  text-transform: uppercase;
  color: var(--mute);
  margin-bottom: 6px;
}
.card-h2 {
  font-size: 20px;
  font-weight: 700;
  letter-spacing: -0.018em;
  margin-bottom: 14px;
}

/* Radial KPI Gauge Tile */
.kpi-tile {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
}
.kpi-val {
  font-size: 36px;
  font-weight: 800;
  letter-spacing: -0.03em;
  line-height: 1.05;
}
.kpi-sub {
  font-size: 13px;
  color: var(--mute);
  margin-top: 5px;
  font-weight: 500;
}
.ring-svg { width: 68px; height: 68px; flex-shrink: 0; }

/* Badges & Chips */
.badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 700;
}
.b-green { background: var(--green-soft); color: var(--green); }
.b-blue  { background: var(--blue-soft); color: var(--blue); }
.b-amber { background: var(--amber-soft); color: var(--amber); }
.b-red   { background: var(--red-soft); color: var(--red); }
.b-purple{ background: var(--purple-soft); color: var(--purple); }

/* Views */
.view-pane { display: none; }
.view-pane.active { display: block; }

/* Tables & Lists */
.apple-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 14px;
}
.apple-table th {
  text-align: left;
  font-size: 11.5px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--mute);
  padding: 10px 12px;
  border-bottom: 1px solid var(--line);
}
.apple-table td {
  padding: 13px 12px;
  border-bottom: 1px solid rgba(15, 23, 42, 0.05);
  vertical-align: middle;
}
.apple-table tr:last-child td { border-bottom: none; }
.apple-table tr.clickable { cursor: pointer; transition: background 0.12s ease; }
.apple-table tr.clickable:hover { background: rgba(0, 113, 227, 0.04); }
.apple-table tr.selected { background: rgba(0, 113, 227, 0.08); }

/* Component Grid */
.comp-list {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 10px;
  margin-top: 12px;
}
.comp-item {
  padding: 13px 15px;
  border-radius: 14px;
  border: 1.5px solid var(--line);
  background: #fbfbfd;
  cursor: pointer;
  transition: all 0.16s ease;
}
.comp-item:hover { border-color: rgba(0, 113, 227, 0.4); background: #ffffff; }
.comp-item.active {
  border-color: var(--blue);
  background: #f2f8ff;
  box-shadow: 0 4px 14px rgba(0, 113, 227, 0.10);
}

textarea.code-editor {
  width: 100%;
  min-height: 360px;
  padding: 18px 20px;
  border-radius: 14px;
  border: 1px solid var(--line);
  background: #fbfbfd;
  color: var(--ink);
  font-size: 13.5px;
  line-height: 1.6;
  resize: vertical;
  outline: none;
}
textarea.code-editor:focus {
  border-color: var(--blue);
  box-shadow: 0 0 0 4px rgba(0, 113, 227, 0.12);
  background: #ffffff;
}

pre.code-block {
  background: #0f172a;
  color: #e2e8f0;
  padding: 16px 18px;
  border-radius: 14px;
  font-size: 12.5px;
  line-height: 1.55;
  overflow-x: auto;
  white-space: pre-wrap;
}

.toast {
  position: fixed;
  bottom: 24px;
  right: 28px;
  padding: 12px 20px;
  border-radius: 14px;
  background: rgba(15, 23, 42, 0.92);
  color: #ffffff;
  font-size: 13.5px;
  font-weight: 600;
  box-shadow: var(--shadow-lg);
  opacity: 0;
  transform: translateY(12px);
  transition: all 0.22s ease;
  pointer-events: none;
  z-index: 200;
}
.toast.show { opacity: 1; transform: translateY(0); }

@media (max-width: 1100px) {
  .bento-4, .bento-3, .bento-2, .bento-split { grid-template-columns: 1fr; }
}
</style>
</head>
<body>

<header class="topbar">
  <div class="brand">
    <svg width="32" height="32" viewBox="0 0 128 128">
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
    <span>Auto<span style="color:var(--blue)">Harness</span> Studio</span>
    <span id="hdr-fp" class="sub-pill mono">v0.3</span>
  </div>

  <nav class="seg-nav" id="main-tabs">
    <button class="seg-btn active" data-tab="overview">Overview</button>
    <button class="seg-btn" data-tab="harness2">Harness² Tree</button>
    <button class="seg-btn" data-tab="healer">Code Healer</button>
    <button class="seg-btn" data-tab="arena">Probe Arena</button>
    <button class="seg-btn" data-tab="traces">Runs &amp; Traces</button>
    <button class="seg-btn" data-tab="lessons">Lessons &amp; Loop</button>
  </nav>

  <div class="top-actions">
    <span id="hdr-status" class="status-chip"><span class="dot"></span><span>Autonomous Ready</span></span>
    <button class="btn btn-blue" id="btn-quick-eval">▶ Run Offline Suite</button>
  </div>
</header>

<main class="container">
  <!-- ======================== 1. OVERVIEW ======================== -->
  <section class="view-pane active" id="pane-overview">
    <div class="hero-strip">
      <div>
        <div class="eyebrow">Autonomous Harness Engineering</div>
        <h1 id="ov-title">System Telemetry &amp; Quality Gate</h1>
        <div class="subtitle" id="ov-sub">Frozen model weights · 8-component Harness² scaffolding · hidden holdout protection</div>
      </div>
      <div id="ov-gate-pill"></div>
    </div>

    <div class="bento-4" id="ov-kpis"></div>

    <div class="bento-2">
      <div class="card">
        <div class="card-title">Evaluation Trajectory</div>
        <div class="card-h2">Pass Rate &amp; Reliability Across Runs</div>
        <div id="ov-chart" style="height:220px;"></div>
      </div>
      <div class="card">
        <div class="card-title">Cheapest-First Grading Architecture</div>
        <div class="card-h2">3-Layer Verification Waterfall</div>
        <div id="ov-waterfall" style="display:flex; flex-direction:column; gap:12px;"></div>
      </div>
    </div>

    <div class="bento-2">
      <div class="card">
        <div class="card-title">Suite Breakdown</div>
        <div class="card-h2">Active Evaluation Cases (Dev &amp; Hidden Holdout)</div>
        <div id="ov-cases-table"></div>
      </div>
      <div class="card">
        <div class="card-title">Harnessing-Level Recursion</div>
        <div class="card-h2">Latest Empirical Lessons (`LESSONS.md`)</div>
        <div id="ov-lessons"></div>
      </div>
    </div>
  </section>

  <!-- ======================== 2. HARNESS² TREE ======================== -->
  <section class="view-pane" id="pane-harness2">
    <div class="hero-strip">
      <div>
        <div class="eyebrow">Inspired by Google Research Harness²</div>
        <h1>8-Component Modular Harness Architecture</h1>
        <div class="subtitle">Drag the 3D orbital sphere or select any component below to inspect and edit its live instructions.</div>
      </div>
      <div style="display:flex; gap:10px;">
        <button class="btn btn-tinted" id="btn-show-compiled">View Compiled Prompt</button>
        <button class="btn btn-blue" id="btn-save-comp">Save Component</button>
      </div>
    </div>

    <div class="bento-split">
      <div class="card" style="display:flex; flex-direction:column; justify-content:space-between;">
        <div>
          <div style="display:flex; justify-content:space-between; align-items:center;">
            <div>
              <div class="card-title">Interactive 3D Orbital Topology</div>
              <div class="card-h2" style="margin-bottom:4px;">Frozen Model + 8 Runtime Roles</div>
            </div>
            <span id="h2-path-badge" class="badge b-blue mono"></span>
          </div>
          <canvas id="h2-canvas" width="640" height="350" style="width:100%; height:330px; cursor:grab;"></canvas>
        </div>
        <div>
          <div class="card-title">Click a Component to Inspect</div>
          <div class="comp-list" id="h2-comp-list"></div>
        </div>
      </div>

      <div class="card" style="display:flex; flex-direction:column;">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;">
          <div>
            <div class="card-title" id="ed-space">CONTEXT SPACE</div>
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
        <div class="eyebrow">Zero-Intervention Code Health</div>
        <h1>Autonomous Codebase Scanner &amp; Self-Healer</h1>
        <div class="subtitle">Scans AST syntax, imports, and unit tests; repairs broken modules in throwaway copies with byte-for-byte locked test files.</div>
      </div>
      <div style="display:flex; gap:10px;">
        <button class="btn btn-tinted" id="btn-synth-cases">✦ Auto-Synthesize Cases</button>
        <button class="btn btn-green" id="btn-rescan-code">↻ Run Live AST &amp; Test Scan</button>
      </div>
    </div>

    <div class="bento-3" id="hl-summary-cards"></div>

    <div class="bento-2">
      <div class="card">
        <div class="card-title">Discovered Python Modules</div>
        <div class="card-h2">AST, Import &amp; Protected Test Coverage</div>
        <div id="hl-modules-table" style="max-height:460px; overflow-y:auto;"></div>
      </div>
      <div class="card">
        <div class="card-title">Auto-Synced Context Map (`memory.md`)</div>
        <div class="card-h2">Live Architecture Feed Injected Into Harness</div>
        <pre id="hl-context-map" class="code-block mono" style="min-height:380px; max-height:460px; overflow-y:auto;"></pre>
      </div>
    </div>
  </section>

  <!-- ======================== 4. PROBE ARENA ======================== -->
  <section class="view-pane" id="pane-arena">
    <div class="hero-strip">
      <div>
        <div class="eyebrow">Task-Level Contrastive Recursion</div>
        <h1>Propose · Probe · Compose Arena</h1>
        <div class="subtitle">Parallel candidate harnesses evaluated in isolated workspaces and gated against hidden holdout cases.</div>
      </div>
      <span class="badge b-green">Anti-Overfitting Guards Active</span>
    </div>

    <div class="bento-3">
      <div class="card" style="border-top:4px solid var(--blue);">
        <div class="card-title">Guard 01 · Pre-Run ($0)</div>
        <div class="card-h2">Static Leak &amp; Bloat Filter</div>
        <p style="font-size:14px; color:var(--ink-2); line-height:1.55;">Scans every proposed diff before spending tokens. Blocks edits pasting case IDs, filenames, or expected literal answers into the harness.</p>
      </div>
      <div class="card" style="border-top:4px solid var(--purple);">
        <div class="card-title">Guard 02 · Isolated Sandboxes</div>
        <div class="card-h2">Parallel Probe &amp; Contrastive Merge</div>
        <p style="font-size:14px; color:var(--ink-2); line-height:1.55;">Evaluates K diverse strategies concurrently in throwaway copies, then composes winning per-case contrasts into a unified candidate.</p>
      </div>
      <div class="card" style="border-top:4px solid var(--green);">
        <div class="card-title">Guard 03 · Secret Split</div>
        <div class="card-h2">Hidden Holdout Gate</div>
        <p style="font-size:14px; color:var(--ink-2); line-height:1.55;"><code>split: holdout</code> cases are never shown to the fixer. Any drop on holdout cases triggers immediate rejection and byte-for-byte revert.</p>
      </div>
    </div>

    <div class="card">
      <div class="card-title">Evaluated Candidate Ledger (`runs/fixlog.md`)</div>
      <div class="card-h2">Accepted &amp; Rejected Probe History</div>
      <div id="ar-fixlog" style="display:grid; grid-template-columns:repeat(auto-fill, minmax(420px, 1fr)); gap:16px;"></div>
    </div>
  </section>

  <!-- ======================== 5. RUNS & TRACES ======================== -->
  <section class="view-pane" id="pane-traces">
    <div class="hero-strip">
      <div>
        <div class="eyebrow">Deep Execution Forensics</div>
        <h1>Trial Results, Check Layers &amp; Tool Traces</h1>
        <div class="subtitle">Click any trial row to inspect its deterministic checks, behavioral trace rules, judge votes, and sandbox tool calls.</div>
      </div>
      <div class="seg-nav" id="trace-filter">
        <button class="seg-btn active" data-f="all">All Trials</button>
        <button class="seg-btn" data-f="dev">Dev</button>
        <button class="seg-btn" data-f="holdout">Holdout</button>
        <button class="seg-btn" data-f="fail">Failing</button>
      </div>
    </div>

    <div class="bento-split">
      <div class="card" style="max-height:640px; overflow-y:auto;">
        <div class="card-title">Latest Suite Execution</div>
        <div class="card-h2" id="tr-run-id">Trials</div>
        <div id="tr-list"></div>
      </div>
      <div class="card" style="max-height:640px; overflow-y:auto;">
        <div class="card-title">Trial Forensic Inspector</div>
        <div class="card-h2" id="tr-detail-title">Select a trial</div>
        <div id="tr-detail-body"></div>
      </div>
    </div>
  </section>

  <!-- ======================== 6. LESSONS & LOOP ======================== -->
  <section class="view-pane" id="pane-lessons">
    <div class="hero-strip">
      <div>
        <div class="eyebrow">Harnessing-Level Recursion &amp; Automation</div>
        <h1>Persistent Lesson Ledger &amp; Git Hooks</h1>
        <div class="subtitle">Empirically validated DO and AVOID priors retained across sessions, plus one-click Git pre-commit automation.</div>
      </div>
    </div>

    <div class="bento-2">
      <div class="card">
        <div class="card-title">Experience Reflector Store (`lessons/lessons.json`)</div>
        <div class="card-h2">Evidence-Backed Engineering Priors</div>
        <div id="ls-cards" style="display:flex; flex-direction:column; gap:12px;"></div>
      </div>
      <div class="card">
        <div class="card-title">Always-On Automation</div>
        <div class="card-h2">Git Commit Hooks &amp; Continuous Watcher</div>
        <div id="ls-hooks" style="margin-bottom:20px;"></div>
        <div class="card-title" style="margin-top:18px;">CLI Quick Commands</div>
        <pre class="code-block mono">$ autoharness watch --now
$ autoharness heal src/ --with-harness
$ autoharness synthesize src/
$ autoharness hook install --kind pre-commit</pre>
      </div>
    </div>
  </section>
</main>

<div class="toast" id="toast">Saved</div>

<script>
let STATE = __INITIAL_STATE__;
let selectedCompIdx = 0;
let selectedTrialIdx = 0;
let traceFilter = "all";
let orbitYaw = 0.6, orbitPitch = 0.34, isDragging = false, lastX = 0, lastY = 0;

function toast(msg) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.classList.add("show");
  setTimeout(() => t.classList.remove("show"), 2600);
}

function esc(s) {
  return String(s ?? "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}

function ringGaugeSVG(pct, color) {
  const r = 27, c = 2 * Math.PI * r;
  const clamped = Math.max(0, Math.min(1, pct));
  const off = c * (1 - clamped);
  return `<svg class="ring-svg" viewBox="0 0 68 68">
    <circle cx="34" cy="34" r="${r}" fill="none" stroke="rgba(15,23,42,0.08)" stroke-width="7"/>
    <circle cx="34" cy="34" r="${r}" fill="none" stroke="${color}" stroke-width="7"
      stroke-linecap="round" stroke-dasharray="${c.toFixed(1)}" stroke-dashoffset="${off.toFixed(1)}"
      transform="rotate(-90 34 34)"/>
  </svg>`;
}

/* ==================== RENDER ALL PANES ==================== */
function renderAll() {
  document.getElementById("hdr-fp").textContent = `${STATE.harness.fingerprint.slice(0, 8)} · ${STATE.agent.type}`;
  renderOverview();
  renderHarnessPane();
  renderHealerPane();
  renderArenaPane();
  renderTracesPane();
  renderLessonsPane();
}

function renderOverview() {
  const sum = STATE.latest_summary;
  const passRate = sum ? sum.pass_rate : 1.0;
  const passK = sum ? sum.all_trials_pass_rate : 1.0;
  const reviewQ = sum ? sum.review_queue : 0;
  const nComps = STATE.harness.components.filter(c => c.active).length;

  document.getElementById("ov-title").textContent = `${STATE.project_name} · Harness Telemetry`;
  const diff = STATE.latest_diff;
  document.getElementById("ov-gate-pill").innerHTML = diff
    ? `<span class="badge ${diff.gate_passed ? 'b-green' : 'b-red'}" style="font-size:14px;padding:8px 16px;">${diff.gate_passed ? '✓ Gate Passed vs Baseline' : '✗ Gate Regression Detected'}</span>`
    : `<span class="badge b-blue" style="font-size:13.5px;padding:8px 16px;">Baseline: ${esc(STATE.baseline_id || 'ready')}</span>`;

  const kpis = [
    { label: "Suite Pass Rate", val: sum ? `${Math.round(passRate * 100)}%` : "—", sub: sum ? `${sum.n_trials} trials graded` : "Run suite to populate", pct: passRate, col: "#0071e3" },
    { label: "Every-Trial Reliability (pass^k)", val: sum ? `${Math.round(passK * 100)}%` : "—", sub: "100% consistency across trials", pct: passK, col: "#1d9d5f" },
    { label: "Harness² Active Roles", val: `${nComps} / ${STATE.harness.is_directory ? 8 : 1}`, sub: STATE.harness.path, pct: nComps / (STATE.harness.is_directory ? 8 : 1), col: "#7c3aed" },
    { label: "Human Review Queue", val: String(reviewQ), sub: reviewQ === 0 ? "Zero manual intervention needed" : "Uncertain judge trials", pct: reviewQ === 0 ? 1.0 : 0.35, col: reviewQ === 0 ? "#1d9d5f" : "#d97706" },
  ];
  document.getElementById("ov-kpis").innerHTML = kpis.map(k => `
    <div class="card kpi-tile">
      <div>
        <div class="card-title">${esc(k.label)}</div>
        <div class="kpi-val">${esc(k.val)}</div>
        <div class="kpi-sub">${esc(k.sub)}</div>
      </div>
      ${ringGaugeSVG(k.pct, k.col)}
    </div>
  `).join("");

  // SVG Run Trajectory Chart
  const hist = STATE.runs_history;
  if (!hist.length) {
    document.getElementById("ov-chart").innerHTML = `<div style="color:var(--mute);padding:60px 0;text-align:center;">No runs recorded yet. Click <strong>Run Offline Suite</strong> in the top bar.</div>`;
  } else {
    const w = 680, h = 200, padX = 42, padY = 24;
    const pts = hist.map((r, i) => {
      const x = padX + (hist.length === 1 ? (w - 2 * padX) / 2 : (i / (hist.length - 1)) * (w - 2 * padX));
      const y = h - padY - r.pass_rate * (h - 2 * padY);
      return { x, y, r };
    });
    const lineD = pts.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ");
    const areaD = `${lineD} L${pts[pts.length - 1].x.toFixed(1)},${h - padY} L${pts[0].x.toFixed(1)},${h - padY} Z`;
    document.getElementById("ov-chart").innerHTML = `
      <svg viewBox="0 0 ${w} ${h}" style="width:100%; height:100%;">
        <defs>
          <linearGradient id="areaG" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="#0071e3" stop-opacity="0.24"/>
            <stop offset="100%" stop-color="#0071e3" stop-opacity="0.0"/>
          </linearGradient>
        </defs>
        <line x1="${padX}" y1="${padY}" x2="${w - padX}" y2="${padY}" stroke="rgba(15,23,42,0.07)" stroke-dasharray="4,4"/>
        <line x1="${padX}" y1="${h/2}" x2="${w - padX}" y2="${h/2}" stroke="rgba(15,23,42,0.07)" stroke-dasharray="4,4"/>
        <line x1="${padX}" y1="${h - padY}" x2="${w - padX}" y2="${h - padY}" stroke="rgba(15,23,42,0.12)"/>
        <text x="6" y="${padY + 4}" font-size="11" fill="#86868b">100%</text>
        <text x="12" y="${h/2 + 4}" font-size="11" fill="#86868b">50%</text>
        <text x="18" y="${h - padY + 4}" font-size="11" fill="#86868b">0%</text>
        ${pts.length > 1 ? `<path d="${areaD}" fill="url(#areaG)"/>` : ''}
        <path d="${lineD}" fill="none" stroke="#0071e3" stroke-width="3" stroke-linecap="round"/>
        ${pts.map(p => `
          <circle cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="${p.r.is_baseline ? 6.5 : 5}"
            fill="${p.r.is_baseline ? '#1d9d5f' : '#0071e3'}" stroke="#ffffff" stroke-width="2"/>
        `).join("")}
      </svg>`;
  }

  // Waterfall
  const layers = [
    ["Layer 1 · Deterministic ($0)", "AST syntax, pytest exit codes, JSON schema, file immutability", "b-blue", "Exact & Free"],
    ["Layer 2 · Behavioral Trace ($0)", "read-before-write, verified-after-edit, loop & forbidden-cmd guards", "b-purple", "Exact & Free"],
    ["Layer 3 · Grounded Judge (3x Vote)", "Self-consistency rubric voting + workspace artifact evidence check", "b-green", "Auto-Certified"],
  ];
  document.getElementById("ov-waterfall").innerHTML = layers.map(([t, d, cls, tag]) => `
    <div style="padding:14px 16px; border-radius:14px; background:#fbfbfd; border:1px solid var(--line); display:flex; justify-content:space-between; align-items:center; gap:12px;">
      <div>
        <div style="font-weight:700; font-size:14.5px;">${esc(t)}</div>
        <div style="font-size:13px; color:var(--mute); margin-top:2px;">${esc(d)}</div>
      </div>
      <span class="badge ${cls}">${esc(tag)}</span>
    </div>
  `).join("");

  // Cases table
  const caseMap = (sum && sum.cases) ? sum.cases : {};
  document.getElementById("ov-cases-table").innerHTML = `
    <table class="apple-table">
      <tr><th>Case</th><th>Split</th><th>Checks</th><th>Pass Rate</th></tr>
      ${STATE.cases.slice(0, 10).map(c => {
        const row = caseMap[c.id];
        const pr = row ? Math.round(row.pass_rate * 100) + "%" : "—";
        const cls = !row ? "b-blue" : (row.pass_rate === 1 ? "b-green" : "b-red");
        return `<tr>
          <td><div style="font-weight:700;">${esc(c.id)}</div><div style="font-size:12.5px;color:var(--mute);">${esc(c.description)}</div></td>
          <td><span class="badge ${c.split === 'holdout' ? 'b-purple' : 'b-blue'}">${esc(c.split)}</span></td>
          <td class="mono" style="font-size:13px;">${c.n_checks}</td>
          <td><span class="badge ${cls}">${pr}</span></td>
        </tr>`;
      }).join("")}
    </table>`;

  // Lessons preview
  const ls = STATE.lessons;
  document.getElementById("ov-lessons").innerHTML = ls.length
    ? ls.slice(0, 5).map(l => `
        <div style="padding:13px 15px; border-radius:13px; background:#fbfbfd; border:1px solid var(--line); margin-bottom:10px;">
          <span class="badge ${l.polarity === 'do' ? 'b-green' : 'b-red'}" style="margin-right:8px;">${l.polarity === 'do' ? 'DO' : 'AVOID'} · x${l.support || 1}</span>
          <span style="font-size:13.8px; line-height:1.45;">${esc(l.lesson)}</span>
        </div>`).join("")
    : `<div style="padding:36px 16px; text-align:center; color:var(--mute); font-size:14px;">Lessons populate automatically after the first Propose-Probe-Compose or Code-Heal session (run <code>autoharness demo</code> or <code>autoharness fix</code>).</div>`;
}

/* ==================== 2. HARNESS² PANE & 3D CANVAS ==================== */
function renderHarnessPane() {
  document.getElementById("h2-path-badge").textContent = STATE.harness.path;
  const comps = STATE.harness.components;
  if (selectedCompIdx >= comps.length) selectedCompIdx = 0;

  document.getElementById("h2-comp-list").innerHTML = comps.map((c, idx) => `
    <div class="comp-item ${idx === selectedCompIdx ? 'active' : ''}" data-cidx="${idx}">
      <div style="display:flex; justify-content:space-between; align-items:center;">
        <span style="font-size:11px; font-weight:700; text-transform:uppercase; color:var(--blue);">${esc(c.space)}</span>
        <span class="mono" style="font-size:11.5px; color:var(--mute);">${c.chars}c</span>
      </div>
      <div class="mono" style="font-size:14px; font-weight:700; margin-top:3px;">${esc(c.filename)}</div>
    </div>
  `).join("");

  document.querySelectorAll("#h2-comp-list .comp-item").forEach(el => {
    el.onclick = () => { selectedCompIdx = +el.dataset.cidx; renderHarnessPane(); };
  });

  const cur = comps[selectedCompIdx];
  if (cur) {
    document.getElementById("ed-space").textContent = `${cur.space.toUpperCase()} SPACE · ${cur.title.toUpperCase()}`;
    document.getElementById("ed-filename").textContent = cur.filename;
    document.getElementById("ed-chars").textContent = `${cur.chars} chars`;
    document.getElementById("ed-desc").textContent = cur.description;
    document.getElementById("ed-textarea").value = cur.content;
    document.getElementById("ed-textarea").readOnly = false;
  }
}

function animateOrbitalCanvas() {
  const cv = document.getElementById("h2-canvas");
  if (cv && document.getElementById("pane-harness2").classList.contains("active")) {
    const ctx = cv.getContext("2d");
    const w = cv.width, h = cv.height;
    ctx.clearRect(0, 0, w, h);
    if (!isDragging) orbitYaw += 0.008;
    const cx = w / 2, cy = h / 2, R = 128;
    const colors = { context: "#0071e3", interface: "#1d9d5f", middleware: "#d97706" };
    const comps = STATE.harness.components;

    const project = (x, y, z) => {
      const x1 = x * Math.cos(orbitYaw) - z * Math.sin(orbitYaw);
      const z1 = x * Math.sin(orbitYaw) + z * Math.cos(orbitYaw);
      const y2 = y * Math.cos(orbitPitch) - z1 * Math.sin(orbitPitch);
      const z2 = y * Math.sin(orbitPitch) + z1 * Math.cos(orbitPitch);
      const f = 460 / (460 - z2);
      return { sx: cx + x1 * f, sy: cy + y2 * f, z: z2, f };
    };

    // Orbital ring
    ctx.beginPath();
    for (let i = 0; i <= 64; i++) {
      const a = (i / 64) * Math.PI * 2;
      const p = project(R * Math.cos(a), 0, R * Math.sin(a));
      if (i === 0) ctx.moveTo(p.sx, p.sy); else ctx.lineTo(p.sx, p.sy);
    }
    ctx.strokeStyle = "rgba(0,113,227,0.18)";
    ctx.lineWidth = 1.5;
    ctx.stroke();

    const nodes = comps.map((c, i) => {
      const lon = (i / Math.max(1, comps.length)) * Math.PI * 2;
      const lat = (i % 2 === 0 ? 0.30 : -0.30);
      const rl = R * Math.cos(lat);
      const p = project(rl * Math.cos(lon), R * Math.sin(lat), rl * Math.sin(lon));
      return { idx: i, c, ...p };
    });
    nodes.push({ core: true, sx: cx, sy: cy, z: 0, f: 1 });
    nodes.sort((a, b) => a.z - b.z);

    for (const n of nodes) {
      if (n.core) {
        const g = ctx.createRadialGradient(cx - 10, cy - 10, 4, cx, cy, 44);
        g.addColorStop(0, "#60a5fa"); g.addColorStop(0.6, "#0071e3"); g.addColorStop(1, "#0f172a");
        ctx.beginPath(); ctx.arc(cx, cy, 40, 0, Math.PI * 2);
        ctx.fillStyle = g; ctx.fill();
        ctx.fillStyle = "#fff"; ctx.font = "700 11px -apple-system, sans-serif"; ctx.textAlign = "center";
        ctx.fillText("FROZEN", cx, cy - 2); ctx.fillText("MODEL", cx, cy + 12);
      } else {
        ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(n.sx, n.sy);
        ctx.strokeStyle = n.idx === selectedCompIdx ? "rgba(0,113,227,0.55)" : "rgba(148,163,184,0.22)";
        ctx.lineWidth = n.idx === selectedCompIdx ? 2.4 : 1.3;
        ctx.stroke();

        const rad = (n.idx === selectedCompIdx ? 14 : 10) * n.f;
        ctx.beginPath(); ctx.arc(n.sx, n.sy, rad, 0, Math.PI * 2);
        ctx.fillStyle = colors[n.c.space] || "#0071e3";
        ctx.fill();
        ctx.font = "600 11.5px 'SF Mono', Menlo, monospace";
        ctx.fillStyle = "#1d1d1f"; ctx.textAlign = "center";
        ctx.fillText(n.c.filename, n.sx, n.sy + rad + 15);
      }
    }
  }
  requestAnimationFrame(animateOrbitalCanvas);
}

/* ==================== 3. CODE HEALER PANE ==================== */
function renderHealerPane() {
  const mods = STATE.modules;
  const healthy = mods.filter(m => m.healthy).length;
  const withTests = mods.filter(m => m.tests.length > 0).length;
  document.getElementById("hl-summary-cards").innerHTML = `
    <div class="card"><div class="card-title">Scanned Source Modules</div><div class="kpi-val">${mods.length}</div><div class="kpi-sub">Targets: ${esc(STATE.code_targets.join(", "))}</div></div>
    <div class="card"><div class="card-title">AST &amp; Health Status</div><div class="kpi-val" style="color:${healthy === mods.length ? 'var(--green)' : 'var(--red)'}">${healthy} / ${mods.length}</div><div class="kpi-sub">Clean Python AST &amp; module structure</div></div>
    <div class="card"><div class="card-title">Protected Test Suites</div><div class="kpi-val" style="color:var(--blue)">${withTests}</div><div class="kpi-sub">🔒 Byte-for-byte write-locked during repair</div></div>
  `;
  document.getElementById("hl-modules-table").innerHTML = `
    <table class="apple-table">
      <tr><th>Module</th><th>AST</th><th>Protected Tests</th><th>Public Symbols</th></tr>
      ${mods.map(m => `
        <tr>
          <td class="mono" style="font-weight:600; font-size:13px;">${esc(m.path)}</td>
          <td><span class="badge ${m.syntax_ok ? 'b-green' : 'b-red'}">${m.syntax_ok ? '✓ Valid' : '✗ Error'}</span></td>
          <td class="mono" style="font-size:12px;">${m.tests.length ? '🔒 ' + esc(m.tests.join(', ')) : '<span style="color:var(--mute)">—</span>'}</td>
          <td class="mono" style="font-size:12px; color:var(--ink-2);">${esc(m.symbols.slice(0, 4).join(', '))}</td>
        </tr>
      `).join("")}
    </table>`;
  document.getElementById("hl-context-map").textContent = STATE.context_map || "# No Python modules scanned yet.";
}

/* ==================== 4. PROBE ARENA PANE ==================== */
function renderArenaPane() {
  const fl = STATE.fixlog;
  document.getElementById("ar-fixlog").innerHTML = fl.length
    ? fl.map(e => `
      <div class="card" style="box-shadow:var(--shadow-sm); border-left:5px solid ${e.verdict === 'ACCEPTED' ? 'var(--green)' : 'var(--red)'};">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
          <span class="badge ${e.verdict === 'ACCEPTED' ? 'b-green' : 'b-red'}">${e.verdict === 'ACCEPTED' ? '✓ KEPT' : '✗ REJECTED'}</span>
          <span class="mono" style="font-size:11.5px; color:var(--mute);">${esc(e.metrics)}</span>
        </div>
        <div style="font-weight:700; font-size:16px; margin-bottom:6px;">${esc(e.approach)}</div>
        ${e.diagnosis ? `<div style="font-size:13px; color:var(--ink-2); margin-bottom:8px;">${esc(e.diagnosis)}</div>` : ''}
        ${e.rejected_why ? `<div style="font-size:12.5px; color:var(--red); font-weight:600; margin-bottom:8px;">Reason: ${esc(e.rejected_why)}</div>` : ''}
        ${e.diff ? `<pre class="code-block mono" style="font-size:11.5px; max-height:160px;">${esc(e.diff)}</pre>` : ''}
      </div>`).join("")
    : `<div style="grid-column:1/-1; padding:36px; text-align:center; color:var(--mute);">No fixlog entries yet in <code>runs/fixlog.md</code>. Run <code>autoharness demo</code> or <code>autoharness fix</code> to inspect candidate contrasts.</div>`;
}

/* ==================== 5. RUNS & TRACES PANE ==================== */
function renderTracesPane() {
  const sum = STATE.latest_summary;
  document.getElementById("tr-run-id").textContent = sum ? `Run ${sum.run_id}` : "No Completed Runs Yet";
  const caseMeta = sum ? (sum.cases || {}) : {};
  const filtered = STATE.latest_results.filter(r => {
    const sp = (caseMeta[r.case_id] || {}).split || "dev";
    if (traceFilter === "dev") return sp === "dev";
    if (traceFilter === "holdout") return sp === "holdout";
    if (traceFilter === "fail") return !r.passed;
    return true;
  });

  if (!filtered.length) {
    document.getElementById("tr-list").innerHTML = `<div style="padding:32px; color:var(--mute); text-align:center;">No matching trials.</div>`;
    document.getElementById("tr-detail-body").innerHTML = "";
    return;
  }
  if (selectedTrialIdx >= filtered.length) selectedTrialIdx = 0;

  document.getElementById("tr-list").innerHTML = `
    <table class="apple-table">
      <tr><th>Status</th><th>Case</th><th>Trial</th><th>Turns</th><th>Tokens</th></tr>
      ${filtered.map((r, idx) => `
        <tr class="clickable ${idx === selectedTrialIdx ? 'selected' : ''}" data-tidx="${idx}">
          <td><span class="badge ${r.passed ? 'b-green' : 'b-red'}">${r.passed ? 'PASS' : 'FAIL'}</span></td>
          <td style="font-weight:700;">${esc(r.case_id)}</td>
          <td class="mono">#${r.trial}</td>
          <td class="mono">${r.turns}</td>
          <td class="mono">${r.tokens.toLocaleString()}</td>
        </tr>`).join("")}
    </table>`;

  document.querySelectorAll("#tr-list tr.clickable").forEach(tr => {
    tr.onclick = () => { selectedTrialIdx = +tr.dataset.tidx; renderTracesPane(); };
  });

  const cur = filtered[selectedTrialIdx];
  const key = `${cur.case_id}__t${cur.trial}`;
  const trace = STATE.latest_traces[key];
  document.getElementById("tr-detail-title").textContent = `${cur.case_id} · Trial ${cur.trial}`;
  document.getElementById("tr-detail-body").innerHTML = `
    <div style="margin-bottom:16px;">
      <div class="card-title">Layered Check Verdicts</div>
      ${cur.checks.map(ch => `
        <div style="padding:10px 12px; border-radius:11px; background:#fbfbfd; border:1px solid var(--line); margin-top:8px; display:flex; justify-content:space-between; align-items:flex-start; gap:10px;">
          <div>
            <div style="font-weight:700; font-size:13.5px;">${esc(ch.name)} <span class="badge b-blue" style="margin-left:6px;">${esc(ch.layer)}</span></div>
            <div class="mono" style="font-size:12px; color:var(--ink-2); margin-top:4px; white-space:pre-wrap;">${esc(ch.detail)}</div>
          </div>
          <span class="badge ${ch.skipped ? 'b-amber' : (ch.passed ? 'b-green' : 'b-red')}">${ch.skipped ? 'SKIPPED' : (ch.passed ? 'PASS' : 'FAIL')}</span>
        </div>`).join("")}
    </div>
    ${trace ? `
      <div class="card-title" style="margin-top:18px;">Sandbox Tool Call Timeline (${trace.tool_calls.length})</div>
      ${trace.tool_calls.map((tc, i) => `
        <div style="padding:10px 12px; border-radius:11px; background:#f8fafc; border:1px solid var(--line); margin-top:8px;">
          <div class="mono" style="font-size:12.5px; font-weight:700; color:var(--blue);">[${i + 1}] ${esc(tc.name)}(${esc(JSON.stringify(tc.input)).slice(0, 110)})</div>
          ${tc.output ? `<pre class="mono" style="font-size:11.5px; color:var(--ink-2); margin-top:6px; white-space:pre-wrap;">${esc(tc.output.slice(0, 360))}</pre>` : ''}
        </div>`).join("")}
      <div class="card-title" style="margin-top:18px;">Agent Final Output</div>
      <pre class="code-block mono">${esc(trace.final_output)}</pre>
    ` : ''}
  `;
}

/* ==================== 6. LESSONS & HOOKS PANE ==================== */
function renderLessonsPane() {
  const ls = STATE.lessons;
  document.getElementById("ls-cards").innerHTML = ls.length
    ? ls.map(l => `
      <div style="padding:16px 18px; border-radius:14px; background:#fbfbfd; border:1px solid var(--line);">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
          <span class="badge ${l.polarity === 'do' ? 'b-green' : 'b-red'}">${l.polarity === 'do' ? '✓ DO' : '✗ AVOID'} · support x${l.support || 1}</span>
          <span class="mono" style="font-size:12px; color:var(--mute);">runs: ${esc((l.evidence_runs || []).join(', '))}</span>
        </div>
        <div style="font-size:14.5px; line-height:1.5;">${esc(l.lesson)}</div>
      </div>`).join("")
    : `<div style="padding:28px; color:var(--mute); text-align:center;">No lessons recorded yet.</div>`;

  const hk = STATE.hooks || {};
  document.getElementById("ls-hooks").innerHTML = Object.keys(hk).length
    ? Object.entries(hk).map(([kind, status]) => `
      <div style="padding:14px 16px; border-radius:14px; background:#fbfbfd; border:1px solid var(--line); margin-bottom:10px; display:flex; justify-content:space-between; align-items:center;">
        <div>
          <div class="mono" style="font-weight:700; font-size:14px;">.git/hooks/${esc(kind)}</div>
          <div style="font-size:12.5px; color:var(--mute);">Status: ${esc(status)}</div>
        </div>
        <button class="btn ${status === 'autoharness' ? 'btn-tinted' : 'btn-blue'}" onclick="toggleHook('${kind}', '${status}')">
          ${status === 'autoharness' ? 'Uninstall' : 'Install Hook'}
        </button>
      </div>`).join("")
    : `<div style="color:var(--mute);">Initialize Git to manage commit hooks.</div>`;
}

/* ==================== INTERACTIONS & API CALLS ==================== */
document.querySelectorAll("#main-tabs .seg-btn").forEach(btn => {
  btn.onclick = () => {
    document.querySelectorAll("#main-tabs .seg-btn").forEach(b => b.classList.remove("active"));
    document.querySelectorAll(".view-pane").forEach(p => p.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById(`pane-${btn.dataset.tab}`).classList.add("active");
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
cv.onmousedown = e => { isDragging = true; lastX = e.clientX; lastY = e.clientY; };
window.onmouseup = () => { isDragging = false; };
window.onmousemove = e => {
  if (!isDragging) return;
  orbitYaw += (e.clientX - lastX) * 0.01;
  orbitPitch = Math.max(-0.9, Math.min(0.9, orbitPitch + (e.clientY - lastY) * 0.01));
  lastX = e.clientX; lastY = e.clientY;
};

async function apiPost(path, body = {}) {
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!res.ok) throw new Error(await res.text());
  return await res.json();
}

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
  toast("Running offline evaluation suite...");
  try {
    const data = await apiPost("/api/run_offline", {});
    STATE = data.state;
    renderAll();
    toast(`Run complete · Pass rate ${Math.round(STATE.latest_summary.pass_rate * 100)}%`);
  } catch (e) { toast("Live server required to trigger runs"); }
};

document.getElementById("btn-rescan-code").onclick = async () => {
  toast("Scanning codebase AST & tests...");
  try {
    const data = await apiPost("/api/scan", {});
    STATE = data.state;
    renderAll();
    toast(`Scanned ${STATE.modules.length} modules`);
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

renderAll();
animateOrbitalCanvas();
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

        def _send_json(self, obj: Any, code: int = 200) -> None:
            raw = json.dumps(obj).encode()
            self.send_response(code)
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
                    self._send_json({"ok": True, "state": collect_studio_state(cfg, store, scan_code=True)})
                    return

                if parsed.path == "/api/synthesize":
                    targets = cfg.raw.get("code_paths") or ["autoharness" if (cfg.root / "autoharness").exists() else "."]
                    written = synthesize_cases(cfg.root, targets, out_dir=cfg.path("cases/auto"))
                    self._send_json({"ok": True, "written": len(written), "state": collect_studio_state(cfg, store)})
                    return

                if parsed.path == "/api/run_offline":
                    from .agents import ScriptedAgent
                    from .runner import run_suite

                    cases = load_cases([cfg.path(p) for p in cfg["cases"]])
                    summary = run_suite(cfg, cases, ScriptedAgent(), None, store, trials=1, workers=2, label="studio", quiet=True)
                    if not store.baseline():
                        store.set_baseline(summary["run_id"])
                    self._send_json({"ok": True, "run_id": summary["run_id"], "state": collect_studio_state(cfg, store)})
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
