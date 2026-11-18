"""autoharness - Autonomous Harness² Engineering & Continuous Code+Harness Self-Healing Loop.

  autoharness init [DIR]          scaffold a project (config, Harness² tree, example cases)
  autoharness demo                run the full Propose -> Probe -> Compose loop offline (~30s)
  autoharness studio              launch the Apple-designed interactive AutoHarness Studio web UI
  autoharness run [--gate]        run the suite, grade, diff vs baseline, write HTML report
  autoharness watch               continuous loop: on every code or harness change, heal -> run -> gate -> fix
  autoharness heal [PATHS...]     scan new & legacy code, auto-repair broken modules safely, sync context
  autoharness synthesize [PATHS]  auto-generate executable dev/holdout eval cases from source code
  autoharness fix                 fix failing dev cases via Harness² Propose -> Probe -> Compose -> Reflect
  autoharness tree [--init DIR]   inspect or scaffold an 8-component Harness² directory
  autoharness hook <action>       install/uninstall/inspect Git pre-commit or post-commit hooks
  autoharness review [RUN]        label uncertain trials (optional when --autonomous is enabled)
  autoharness calibrate           measure judge-vs-human agreement and Cohen's kappa
  autoharness mine                draft cases from Claude Code sessions where a human corrected the agent
  autoharness auto                mine + heal + run + gate; prints only what genuinely needs a human
  autoharness lessons             display the persistent Lesson Ledger (DO / AVOID priors)
  autoharness diff | baseline | report | cases
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import webbrowser
from pathlib import Path

from . import __version__, diff as diffmod, loop
from .agents import build_agent
from .calibrate import append_label, calibrate, load_labels
from .cases import load_cases
from .config import load_config
from .graders.judge import Judge
from .healer import heal_codebase
from .hooks import changed_python_files, hook_status, install_hook, uninstall_hook
from .miner import find_transcripts, mine
from .report import render
from .runner import run_suite
from .store import Store
from .synthesizer import scan_codebase, synthesize_cases
from .tree import HarnessTree

AGENT_CHOICES = [
    "claude", "codex", "gemini", "gemini-cli",
    "ollama", "vllm", "lmstudio", "openrouter", "opensource",
    "openai", "auto", "command", "scripted",
]


def _ctx(args):
    cfg = load_config(args.config)
    if getattr(args, "agent", None):
        cfg.raw["agent"]["type"] = args.agent
    if getattr(args, "autonomous", False):
        cfg.raw["judge"]["autonomous"] = True
    store = Store(cfg.path(cfg["runs_dir"]))
    return cfg, store


def _case_paths(cfg, args):
    return [cfg.path(p) for p in (getattr(args, "cases", None) or cfg["cases"])]


def write_report(store: Store, cfg, run_id: str) -> tuple[Path, dict | None]:
    d = store.run_dir(run_id)
    summary = store.read_json(d / "summary.json")
    base_id = store.baseline()
    cmp = None
    if base_id and base_id != summary["run_id"]:
        cmp = diffmod.compare(store.load_summary(base_id), summary, cfg["gate"])
        store.write_json(d / "diff.json", cmp)
    path = d / "report.html"
    path.write_text(render(summary, store.load_results(d), cmp, store.calibration()))
    return path, cmp


# ---------------------------------------------------------------------------- commands


def cmd_run(args) -> int:
    cfg, store = _ctx(args)
    cases = load_cases(
        _case_paths(cfg, args),
        tags=args.tags,
        ids=args.ids,
        include_drafts=args.include_drafts,
        split=args.split,
    )
    if not cases:
        print("no cases selected", file=sys.stderr)
        return 2
    agent = build_agent(cfg["agent"], cfg.root)
    judge = None if args.no_judge else Judge(cfg["judge"])
    summary = run_suite(
        cfg,
        cases,
        agent,
        judge,
        store,
        trials=args.trials,
        workers=args.workers,
        label=args.label,
        harness_fp=loop.harness_fp(cfg),
        cases_fp=loop.cases_fp(cfg),
        keep=args.keep_workdirs,
    )
    run_id = summary["run_id"]
    if not store.baseline():
        store.set_baseline(run_id)
        print(f"no baseline yet - {run_id} is now the baseline", file=sys.stderr)
    path, cmp = write_report(store, cfg, run_id)
    print(
        f"\npass rate {summary['pass_rate']:.1%} (every trial: {summary['all_trials_pass_rate']:.1%})  "
        f"score {summary['mean_score']:.3f}  review queue {summary['review_queue']}  "
        f"~${summary['est_agent_cost_usd']:.2f}"
    )
    if cmp:
        print(diffmod.format_text(cmp))
    print(f"report: {path}")
    if args.open:
        webbrowser.open(path.as_uri())
    if args.gate and cmp and not cmp["gate_passed"]:
        return 1
    return 0


def cmd_heal(args) -> int:
    cfg, store = _ctx(args)
    if args.changed:
        targets = [str(p) for p in changed_python_files(cfg.root)]
        if not targets:
            print("no modified Python files detected in git status")
            return 0
    else:
        targets = args.paths or cfg.raw.get("code_paths") or ["."]

    out = heal_codebase(
        cfg,
        store,
        targets,
        run_harness_cycle=args.with_harness,
        sync_memory=not args.no_sync_memory,
        dry_run=args.dry_run,
    )
    print(
        f"heal: {out.status} · scanned {out.scanned} module(s), "
        f"{out.initially_failing} failing -> {len(out.repaired)} healed, "
        f"{len(out.unrepaired)} unrepaired (~${out.cost_usd:.2f})"
    )
    for r in out.repaired:
        print(f"\n--- HEALED {r.module} ---\n{r.diff.rstrip()}")
    for r in out.unrepaired:
        print(f"  ! {r.module}: {r.reason}")
    return 0 if out.status in ("clean", "healed") else 1


def cmd_synthesize(args) -> int:
    cfg, _ = _ctx(args)
    targets = args.paths or cfg.raw.get("code_paths") or ["."]
    out_dir = Path(args.out) if args.out else cfg.path("cases/auto")
    modules = scan_codebase(cfg.root, targets, run_checks=True)
    files = synthesize_cases(cfg.root, targets, out_dir=out_dir, include_healthy=not args.failing_only)
    failing_n = sum(not m.healthy for m in modules)
    print(f"scanned {len(modules)} module(s) ({failing_n} failing) -> synthesized {len(files)} case(s) in {out_dir}")
    for f in files[:20]:
        print(f"  {f.name}")
    return 0


def cmd_tree(args) -> int:
    cfg, _ = _ctx(args)
    if args.init:
        written = HarnessTree.scaffold(args.init, overwrite=args.overwrite)
        print(f"scaffolded 8-component Harness² tree in {Path(args.init).resolve()} ({len(written)} file(s) written)")
        for w in written:
            print(f"  {w.relative_to(Path(args.init))}")
        return 0

    target = Path(args.path) if args.path else cfg.path(cfg["agent"]["system_prompt_file"])
    tree = HarnessTree.load(target)
    mode = "8-component Harness² directory" if tree.is_directory else "single-file harness"
    print(f"Harness: {target} ({mode}) · fingerprint {tree.fingerprint()}\n")
    print(f"{'SPACE':12} {'COMPONENT':15} {'STATUS':8} {'CHARS':>7}  DESCRIPTION")
    print("-" * 86)
    for r in tree.summary_rows():
        st = "active" if r["active"] else "empty"
        print(f"{r['space']:12} {r['component']:15} {st:8} {r['chars']:7d}  {r['description']}")
    if args.compile:
        print("\n--- COMPILED PROMPT ---\n" + tree.compile_prompt())
    return 0


def cmd_hook(args) -> int:
    cfg, _ = _ctx(args)
    if args.action == "status":
        st = hook_status(cfg.root)
        if not st:
            print("not a git repository")
            return 1
        for k, v in st.items():
            print(f"{k:14} {v}")
        return 0
    if args.action == "install":
        p = install_hook(cfg.root, kind=args.kind, config=args.config, autofix=not args.dry_run)
        print(f"installed {args.kind} hook -> {p}")
        return 0
    if args.action == "uninstall":
        removed = uninstall_hook(cfg.root, kind=args.kind)
        print(f"removed {args.kind} hook" if removed else f"no {args.kind} hook installed")
        return 0
    return 1


def cmd_auto(args) -> int:
    """The hands-off loop: mine -> heal -> run -> gate. Designed for cron, CI or a session hook."""
    cfg, store = _ctx(args)
    mined_dir = cfg.path("cases/mined")
    existing = {c.id for c in load_cases([cfg.path(p) for p in cfg["cases"]] + [mined_dir], include_drafts=True)}
    new = mine(
        find_transcripts(args.project, newest=args.newest),
        mined_dir,
        limit=args.mine_limit,
        existing_ids=existing,
    )
    heal_out = None
    if cfg.raw.get("code_paths"):
        heal_out = heal_codebase(cfg, store, cfg.raw["code_paths"])
    args.cases, args.tags, args.ids, args.split = None, None, None, None
    args.include_drafts, args.no_judge, args.keep_workdirs, args.open, args.gate = False, False, False, False, True
    args.trials, args.workers, args.label = None, None, "auto"
    rc = cmd_run(args)
    summary = store.load_summary("latest")
    needs = []
    if heal_out and heal_out.unrepaired:
        needs.append(f"{len(heal_out.unrepaired)} module(s) failed autonomous code repair")
    if rc:
        needs.append("regression gate failed (see report)")
    if summary["review_queue"]:
        needs.append(f"{summary['review_queue']} trial(s) need a human label: autoharness review")
    if new:
        needs.append(f"{len(new)} new draft case(s) mined into cases/mined/: review and set status: active")
    print("\nHUMAN NEEDED:\n  - " + "\n  - ".join(needs) if needs else "\nNo human action needed.")
    return 1 if (rc or (heal_out and heal_out.unrepaired)) else 0


def cmd_review(args) -> int:
    cfg, store = _ctx(args)
    d = store.run_dir(args.run)
    run_id = d.name
    cases = {c.id: c for c in load_cases(_case_paths(cfg, args) + [cfg.path("cases/mined")], include_drafts=True)}
    results = store.load_results(d)
    labels_path = cfg.path(cfg["calibration"]["labels_file"])
    done = {(l["run_id"], l["case_id"], l["trial"], l["check"]) for l in load_labels(labels_path)}
    meta = store.read_json(d / "meta.json")

    queue = [r for r in results if r["needs_review"]]
    if args.sample:
        confident = [
            r for r in results
            if not r["needs_review"] and any(c["layer"] == "judge" and not c["skipped"] for c in r["checks"])
        ]
        queue += random.sample(confident, min(args.sample, len(confident)))
    todo = [
        (r, c)
        for r in queue
        for c in r["checks"]
        if c["layer"] == "judge" and not c["skipped"] and (run_id, r["case_id"], r["trial"], c["name"]) not in done
    ]
    if not todo:
        print("nothing to review")
        return 0
    print(f"{len(todo)} judge decision(s) to review. Answer p=pass, f=fail, s=skip, q=quit.\n")
    for i, (r, c) in enumerate(todo, 1):
        trace = store.load_trace(d, r["case_id"], r["trial"])
        case = cases.get(r["case_id"])
        spec = next((s for j, s in enumerate(case.checks) if (s.get("name") or f"{s.get('type')}#{j}") == c["name"]), {}) if case else {}
        print("=" * 80)
        print(f"[{i}/{len(todo)}] {r['case_id']} trial {r['trial']} · check {c['name']}")
        print(f"\nTASK:\n{case.prompt if case else '?'}\n")
        print(f"RUBRIC:\n{spec.get('rubric', '?')}\n")
        print("AGENT RECORD:\n" + trace.transcript_text(max_chars=args.max_chars))
        for p, content in trace.artifacts.items():
            print(f"\n--- {p} ---\n{content[:3000]}")
        print(f"\nJUDGE said {'PASS' if c['passed'] else 'FAIL'} (confidence {c['confidence']}): {c['detail']}")
        while True:
            ans = input("your verdict [p/f/s/q] > ").strip().lower()
            if ans in ("p", "f", "s", "q"):
                break
        if ans == "q":
            break
        if ans == "s":
            continue
        note = input("note (optional) > ").strip()
        append_label(
            labels_path,
            {
                "run_id": run_id,
                "case_id": r["case_id"],
                "trial": r["trial"],
                "check": c["name"],
                "human_pass": ans == "p",
                "judge_pass": c["passed"],
                "judge_model": meta["judge"]["model"],
                "note": note,
            },
        )
    print(f"\nlabels saved to {labels_path}. Run `autoharness calibrate` to update judge trust.")
    return 0


def cmd_calibrate(args) -> int:
    cfg, store = _ctx(args)
    cases = {c.id: c for c in load_cases(_case_paths(cfg, args) + [cfg.path("cases/mined")], include_drafts=True)}
    judge = Judge(cfg["judge"]) if args.rejudge else None
    res = calibrate(
        store, cfg.path(cfg["calibration"]["labels_file"]), cases, judge, cfg["judge"]["model"], rejudge=args.rejudge
    )
    cal = cfg["calibration"]
    trusted = res["n"] >= cal["min_labels"] and res["agreement"] >= cal["min_agreement"]
    print(
        f"labels {res['n']}  agreement {res['agreement']:.1%}  kappa {res['kappa']:.2f}  "
        f"confusion {res['confusion']}"
    )
    print(
        "judge is TRUSTED"
        if trusted
        else f"judge NOT trusted yet (need >= {cal['min_labels']} labels and >= {cal['min_agreement']:.0%} agreement)"
    )
    for dis in res["disagreements"][:10]:
        print(f"  - {dis['kind']}: {dis['case_id']} t{dis['trial']} {dis['check']}  {dis.get('note', '')}")
    return 0


def cmd_mine(args) -> int:
    cfg, _ = _ctx(args)
    out = cfg.path("cases/mined")
    existing = {c.id for c in load_cases([cfg.path(p) for p in cfg["cases"]] + [out], include_drafts=True)}
    files = mine(find_transcripts(args.project, newest=args.newest), out, limit=args.limit, existing_ids=existing)
    print(f"{len(files)} draft case(s) written to {out}")
    for f in files:
        print(f"  {f.name}")
    return 0


def cmd_diff(args) -> int:
    cfg, store = _ctx(args)
    cur = store.load_summary(args.run)
    base = store.load_summary(args.against)
    cmp = diffmod.compare(base, cur, cfg["gate"])
    print(json.dumps(cmp, indent=2) if args.json else diffmod.format_text(cmp))
    return 0 if cmp["gate_passed"] else 1


def cmd_baseline(args) -> int:
    _, store = _ctx(args)
    run_id = store.run_dir(args.run).name
    store.set_baseline(run_id)
    print(f"baseline -> {run_id}")
    return 0


def cmd_report(args) -> int:
    cfg, store = _ctx(args)
    path, _ = write_report(store, cfg, store.run_dir(args.run).name)
    print(path)
    if args.open:
        webbrowser.open(path.as_uri())
    return 0


def cmd_cases(args) -> int:
    cfg, _ = _ctx(args)
    extra = [p for p in (cfg.path("cases/mined"), cfg.path("cases/auto")) if p.exists()]
    for c in load_cases(_case_paths(cfg, args) + extra, include_drafts=True):
        print(f"{c.status:7} {c.split:7} {c.id:40} {','.join(c.tags)}")
    return 0


def cmd_fix(args) -> int:
    cfg, store = _ctx(args)
    if args.max_iters:
        cfg.raw["fixer"]["max_iters"] = args.max_iters
    latest = None
    try:
        latest = store.load_summary("latest")
    except FileNotFoundError:
        pass
    fresh = (
        latest
        and latest["meta"].get("harness_fingerprint") == loop.harness_fp(cfg)
        and latest["meta"].get("cases_fingerprint") == loop.cases_fp(cfg)
    )
    if fresh:
        loop.say(f"reusing {latest['run_id']} (harness and cases unchanged since)")
        base = latest
    else:
        loop.say("running the suite on the current harness first")
        base = loop.run_once(cfg, store, "pre-fix", quiet=True)
    if args.retry:
        f = store.root / "fix_attempts.json"
        f.unlink(missing_ok=True)
    out = loop.autofix(cfg, store, base)
    print(f"\nautofix: {out.status} - {out.message}")
    print(f"kept {len(out.accepted)} edit(s), rejected {len(out.rejected)}, spent ~${out.cost_usd:.2f}")
    for a in out.accepted:
        print(f"\n--- accepted (run {a['run']}) ---\n{a['diagnosis']}\n{a['diff']}")
    print(f"log: {store.root / 'fixlog.md'}")
    write_report(store, cfg, out.final_run)
    return 0 if out.status in ("fixed", "clean") else 1


def cmd_watch(args) -> int:
    cfg, store = _ctx(args)
    if args.no_fix:
        cfg.raw["loop"]["autofix"] = False
    try:
        loop.watch(cfg, store, run_now=args.now, once=args.once)
    except KeyboardInterrupt:
        loop.say("stopped")
    return 0


def cmd_init(args) -> int:
    import shutil

    from .demo import EXAMPLES

    target = Path(args.dir)
    target.mkdir(parents=True, exist_ok=True)
    created = []
    for src in EXAMPLES.rglob("*"):
        if src.is_dir() or "__pycache__" in src.parts:
            continue
        dst = target / src.relative_to(EXAMPLES)
        if dst.exists():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        created.append(str(dst.relative_to(target)))
    if args.harness2:
        written = HarnessTree.scaffold(target / "harnesses" / "harness2")
        created.extend(str(w.relative_to(target)) for w in written)
    for d in ("cases/mined", "cases/auto", "labels", "runs"):
        (target / d).mkdir(parents=True, exist_ok=True)
    print(f"initialised {target.resolve()} ({len(created)} file(s) created, existing files left alone)")
    print("next:  autoharness demo        # see the full loop offline")
    print("       autoharness tree        # inspect the Harness² component structure")
    print("       autoharness run --open  # first live baseline (uses your Claude Code login)")
    return 0


def cmd_demo(args) -> int:
    import tempfile

    from .demo import run_demo

    target = Path(args.dir) if args.dir else Path(tempfile.mkdtemp(prefix="autoharness-demo-"))
    out = run_demo(target)
    if args.open:
        webbrowser.open(out["report"].as_uri())
    return 0 if out["fix"].status == "fixed" else 1


def cmd_lessons(args) -> int:
    cfg, _ = _ctx(args)
    lessons = loop.lesson_store(cfg).load()
    if not lessons:
        print("no lessons yet - they are written after the first fix or heal session")
        return 0
    for l in lessons:
        print(f"{'DO   ' if l.get('polarity') == 'do' else 'AVOID'} (x{l.get('support', 1)}) {l['lesson']}")
    return 0


def cmd_models(args) -> int:
    from .providers import apply_substrate_selection, discover_providers

    if args.use:
        cfg, _ = _ctx(args)
        preset = apply_substrate_selection(cfg, args.use, custom_model=args.model, custom_base_url=args.base_url)
        print(
            f"switched substrate -> {preset['label']} "
            f"(agent={cfg['agent']['type']}, model={cfg['agent'].get('model')}, fixer={cfg['fixer'].get('backend')})"
        )
        return 0

    info = discover_providers()
    print("Detected CLIs & API Keys:")
    for k, ok in info["clis"].items():
        print(f"  CLI  {k:<10} {'installed (' + info['cli_paths'][k] + ')' if ok else 'not found'}")
    for k, ok in info["api_keys"].items():
        print(f"  KEY  {k:<10} {'configured' if ok else 'not set'}")
    for srv, meta in info["local_servers"].items():
        status = f"ONLINE ({', '.join(meta['models'][:4]) or 'ready'})" if meta["online"] else "offline"
        print(f"  OSS  {srv:<10} {status} [{meta['url']}]")
    print("\nAvailable Substrate Presets (`autoharness models --use <id>`):")
    for p in info["presets"]:
        print(f"  {p['id']:<16} {p['label']:<38} [{p['badge']}]")
    return 0


def cmd_studio(args) -> int:
    from .studio import render_studio_html, serve_studio

    cfg, store = _ctx(args)
    if args.export:
        out = Path(args.export)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render_studio_html(cfg, store))
        print(f"exported static AutoHarness Studio snapshot -> {out.resolve()}")
        if not args.no_open:
            webbrowser.open(out.resolve().as_uri())
        return 0
    serve_studio(cfg, store, host=args.host, port=args.port, open_browser=not args.no_open)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="autoharness", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--version", action="version", version=f"autoharness {__version__}")
    p.add_argument("--config", default="autoharness.yaml")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run the evaluation suite")
    r.add_argument("--cases", nargs="+")
    r.add_argument("--tags", nargs="+")
    r.add_argument("--ids", nargs="+")
    r.add_argument("--split", choices=["dev", "holdout"])
    r.add_argument("--trials", type=int)
    r.add_argument("--workers", type=int)
    r.add_argument("--label", default="")
    r.add_argument("--agent", choices=AGENT_CHOICES, help="override agent.type")
    r.add_argument("--no-judge", action="store_true", help="deterministic + trace checks only (free)")
    r.add_argument("--autonomous", action="store_true", help="auto-certify evidence-grounded judge verdicts")
    r.add_argument("--include-drafts", action="store_true")
    r.add_argument("--keep-workdirs", action="store_true")
    r.add_argument("--gate", action="store_true", help="exit 1 if the run regresses vs baseline")
    r.add_argument("--open", action="store_true", help="open the HTML report")
    r.set_defaults(fn=cmd_run)

    hl = sub.add_parser("heal", help="scan new & legacy code, auto-repair broken modules safely, sync context")
    hl.add_argument("paths", nargs="*", help="source directories or files (default: code_paths or '.')")
    hl.add_argument("--changed", action="store_true", help="only scan modified/untracked files from git status")
    hl.add_argument("--dry-run", action="store_true", help="evaluate code health without applying repairs")
    hl.add_argument("--with-harness", action="store_true", help="also run harness suite + autofix afterwards")
    hl.add_argument("--no-sync-memory", action="store_true", help="do not rewrite Harness² memory.md")
    hl.add_argument("--agent", choices=AGENT_CHOICES)
    hl.set_defaults(fn=cmd_heal)

    sy = sub.add_parser("synthesize", help="auto-generate executable dev/holdout YAML cases from source code")
    sy.add_argument("paths", nargs="*", help="source directories or files (default: code_paths or '.')")
    sy.add_argument("--out", help="output directory (default: cases/auto)")
    sy.add_argument("--failing-only", action="store_true", help="only emit repair cases for failing modules")
    sy.set_defaults(fn=cmd_synthesize)

    tr = sub.add_parser("tree", help="inspect or scaffold an 8-component Harness² directory")
    tr.add_argument("path", nargs="?", help="path to harness file or directory")
    tr.add_argument("--init", metavar="DIR", help="scaffold an 8-component Harness² directory in DIR")
    tr.add_argument("--overwrite", action="store_true")
    tr.add_argument("--compile", action="store_true", help="print the compiled unified prompt")
    tr.set_defaults(fn=cmd_tree)

    hk = sub.add_parser("hook", help="install, uninstall, or inspect Git automation hooks")
    hk.add_argument("action", choices=["install", "uninstall", "status"])
    hk.add_argument("--kind", choices=["pre-commit", "post-commit", "pre-push"], default="pre-commit")
    hk.add_argument("--dry-run", action="store_true", help="check-only hook (do not auto-edit files on commit)")
    hk.set_defaults(fn=cmd_hook)

    a = sub.add_parser("auto", help="mine + heal + run + gate; report only what needs a human")
    a.add_argument("--agent", choices=AGENT_CHOICES)
    a.add_argument("--autonomous", action="store_true", default=True)
    a.add_argument("--project", default="*", help="glob of ~/.claude/projects dirs to mine")
    a.add_argument("--newest", type=int, default=20)
    a.add_argument("--mine-limit", type=int, default=10)
    a.set_defaults(fn=cmd_auto)

    v = sub.add_parser("review", help="label uncertain trials")
    v.add_argument("run", nargs="?", default="latest")
    v.add_argument("--sample", type=int, default=0, help="also label N random confident judge verdicts")
    v.add_argument("--max-chars", type=int, default=6000)
    v.set_defaults(fn=cmd_review)

    c = sub.add_parser("calibrate", help="judge vs human agreement")
    c.add_argument("--rejudge", action="store_true", help="re-run the current judge on every labelled trace")
    c.set_defaults(fn=cmd_calibrate)

    m = sub.add_parser("mine", help="draft cases from Claude Code sessions that went wrong")
    m.add_argument("--project", default="*")
    m.add_argument("--newest", type=int, default=20)
    m.add_argument("--limit", type=int, default=20)
    m.set_defaults(fn=cmd_mine)

    d = sub.add_parser("diff", help="compare a run against baseline")
    d.add_argument("run", nargs="?", default="latest")
    d.add_argument("--against", default="baseline")
    d.add_argument("--json", action="store_true")
    d.set_defaults(fn=cmd_diff)

    b = sub.add_parser("baseline", help="set the baseline run")
    b.add_argument("run", nargs="?", default="latest")
    b.set_defaults(fn=cmd_baseline)

    rp = sub.add_parser("report", help="render HTML report for a run")
    rp.add_argument("run", nargs="?", default="latest")
    rp.add_argument("--open", action="store_true")
    rp.set_defaults(fn=cmd_report)

    cs = sub.add_parser("cases", help="list discovered evaluation cases")
    cs.set_defaults(fn=cmd_cases)

    fx = sub.add_parser("fix", help="auto-fix failing dev cases via Harness² Propose-Probe-Compose")
    fx.add_argument("--max-iters", type=int)
    fx.add_argument("--retry", action="store_true", help="retry even if this state was already given up on")
    fx.set_defaults(fn=cmd_fix)

    w = sub.add_parser("watch", help="continuous loop on code + harness changes: heal, run, gate, auto-fix")
    w.add_argument("--now", action="store_true", help="run one cycle immediately at start")
    w.add_argument("--once", action="store_true", help="exit after the first cycle")
    w.add_argument("--no-fix", action="store_true", help="run + gate only, never edit files")
    w.add_argument("--autonomous", action="store_true", default=True)
    w.set_defaults(fn=cmd_watch)

    ini = sub.add_parser("init", help="scaffold a project in DIR (default: current directory)")
    ini.add_argument("dir", nargs="?", default=".")
    ini.add_argument("--harness2", action="store_true", default=True, help="also scaffold harnesses/harness2/ 8-component tree")
    ini.set_defaults(fn=cmd_init)

    dm = sub.add_parser("demo", help="run the full loop offline with scripted agent/fixer/judge")
    dm.add_argument("--dir", help="where to put the demo project (default: a temp dir)")
    dm.add_argument("--open", action="store_true", help="open the HTML report")
    dm.set_defaults(fn=cmd_demo)

    ls = sub.add_parser("lessons", help="show the lessons the fixer has learned")
    ls.set_defaults(fn=cmd_lessons)

    md = sub.add_parser("models", help="inspect or switch AI substrates (Codex, Gemini, Claude, Ollama, vLLM, OpenRouter)")
    md.add_argument("--use", metavar="PRESET", help="switch autoharness.yaml to a substrate preset (e.g. codex, gemini, ollama-qwen, vllm-local, auto)")
    md.add_argument("--model", help="custom model ID (e.g. deepseek-r1:32b, qwen2.5-coder:32b, gpt-5-codex)")
    md.add_argument("--base-url", help="custom OpenAI/Ollama base URL (e.g. http://127.0.0.1:11434)")
    md.set_defaults(fn=cmd_models)

    st = sub.add_parser("studio", help="launch the Apple-designed interactive AutoHarness Studio UI")
    st.add_argument("--host", default="127.0.0.1")
    st.add_argument("--port", type=int, default=8484)
    st.add_argument("--export", metavar="HTML", help="export a standalone static HTML snapshot instead of serving")
    st.add_argument("--no-open", action="store_true", help="do not open browser automatically")
    st.set_defaults(fn=cmd_studio)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
