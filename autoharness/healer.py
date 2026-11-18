"""Autonomous Code + Context Self-Healing Loop (`autoharness heal`).

Whenever new or legacy code is written, `heal_codebase()`:
1. Scans source modules for syntax errors, broken imports, and failing unit tests.
2. Repairs failing modules inside an isolated workspace copy using the active
   Harness² agent + learned lessons.
3. Enforces anti-gaming guards: test files are write-protected (verified byte-for-byte
   unchanged), AST/imports must pass, and the full test suite must not regress before
   any repair touches real files.
4. Optionally updates the Harness² `memory.md` codebase map and runs the harness
   regression gate + contrastive autofix loop.
"""

from __future__ import annotations

import difflib
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .agents import Agent, build_agent
from .cases import Case
from .config import Config
from .lessons import LessonStore
from .sandbox import Sandbox
from .store import Store
from .synthesizer import IGNORE_DIRS, ModuleHealth, analyze_module, build_context_map, is_test_file, scan_codebase


@dataclass
class ModuleFixRecord:
    module: str
    repaired: bool
    diff: str = ""
    reason: str = ""
    turns: int = 0
    cost_usd: float = 0.0


@dataclass
class HealOutcome:
    status: str  # clean | healed | partially_healed | failed
    scanned: int = 0
    initially_failing: int = 0
    repaired: list[ModuleFixRecord] = field(default_factory=list)
    unrepaired: list[ModuleFixRecord] = field(default_factory=list)
    synthesized_cases: int = 0
    harness_result: dict[str, Any] | None = None
    cost_usd: float = 0.0


def _copy_project_to_temp(root: Path) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="autoharness-heal-"))
    shutil.copytree(
        root,
        tmp,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(*IGNORE_DIRS, "*.egg-info", ".DS_Store"),
    )
    return tmp


def _snapshot_tests(root: Path) -> dict[str, str]:
    snaps: dict[str, str] = {}
    for p in sorted(root.rglob("*.py")):
        rel_parts = p.relative_to(root).parts
        if any(part in IGNORE_DIRS for part in rel_parts):
            continue
        if is_test_file(p):
            snaps[str(p.relative_to(root))] = p.read_text(errors="replace")
    return snaps


def _run_all_tests(root: Path, timeout_s: float = 60.0) -> tuple[bool, str]:
    test_files = [
        str(p.relative_to(root))
        for p in sorted(root.rglob("*.py"))
        if not any(part in IGNORE_DIRS for part in p.relative_to(root).parts) and is_test_file(p)
    ]
    if not test_files:
        return True, "no test suite found"
    env = dict(os.environ)
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    env["PYTHONPATH"] = f"{root.resolve()}{os.pathsep}{env.get('PYTHONPATH', '')}".rstrip(os.pathsep)
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", *test_files],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            env=env,
        )
        return r.returncode == 0, (r.stdout + "\n" + r.stderr)[-1200:].strip()
    except subprocess.TimeoutExpired:
        return False, f"full test suite timed out after {timeout_s}s"


def _build_repair_prompt(m: ModuleHealth, lessons: list[dict[str, Any]]) -> str:
    failure = m.syntax_error or m.import_error or m.test_output
    test_list = ", ".join(str(t.name) for t in m.tests_found) or "(none)"
    parts = [
        f"Autonomous Code Self-Healing Task for `{m.rel_path}`.",
        f"Matching test files (READ-ONLY — do NOT modify tests): {test_list}",
        f"\nObserved failure:\n```\n{failure[:2000]}\n```",
        (
            "\nInstructions:\n"
            f"1. Read `{m.rel_path}` and any matching test files first.\n"
            f"2. Fix the root cause inside `{m.rel_path}` with the smallest clean change.\n"
            "3. Never edit or delete test files.\n"
            "4. Run `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q` (or `python3 -m py_compile`) to verify."
        ),
    ]
    if lessons:
        avoid = [l["lesson"] for l in lessons if l.get("polarity") == "avoid"][:5]
        if avoid:
            parts.append("\nLessons from prior sessions (avoid repeating):\n- " + "\n- ".join(avoid))
    return "\n".join(parts)


def heal_module_isolated(
    root: Path,
    m: ModuleHealth,
    agent: Agent,
    lessons: list[dict[str, Any]],
    max_attempts: int = 2,
) -> tuple[ModuleFixRecord, dict[str, str]]:
    """Attempt to repair one failing module inside a throwaway copy of `root`.

    Returns `(record, verified_file_updates)`. `verified_file_updates` is empty unless
    every syntax, import, test-immutability, and suite-wide check succeeded.
    """
    total_cost = 0.0
    total_turns = 0
    last_reason = "repair did not resolve failure"

    for attempt in range(1, max_attempts + 1):
        tmp = _copy_project_to_temp(root)
        try:
            before_tests = _snapshot_tests(tmp)
            before_src = (tmp / m.rel_path).read_text(errors="replace")
            case = Case(
                id=f"heal-{Path(m.rel_path).stem}",
                prompt=_build_repair_prompt(m, lessons),
                checks=[],
            )
            sb = Sandbox(case, attempt, keep=True)
            # Point sandbox at our isolated project copy instead of an empty temp dir
            shutil.rmtree(sb.dir, ignore_errors=True)
            sb.dir = tmp

            trace = agent.run(case, sb, attempt)
            total_cost += trace.cost_usd
            total_turns += trace.turns

            # Guard 1: Test files must remain byte-for-byte identical
            after_tests = _snapshot_tests(tmp)
            if after_tests != before_tests:
                last_reason = "rejected: agent modified or deleted protected test files"
                continue

            # Guard 2: Target module must actually have changed and now be 100% healthy
            target_in_tmp = tmp / m.rel_path
            if not target_in_tmp.exists():
                last_reason = f"rejected: agent deleted target module {m.rel_path}"
                continue
            after_src = target_in_tmp.read_text(errors="replace")
            if after_src == before_src:
                last_reason = "agent made no changes to failing module"
                continue

            recheck = analyze_module(target_in_tmp, tmp, run_checks=True)
            if not recheck.healthy:
                err = recheck.syntax_error or recheck.import_error or recheck.test_output[:300]
                last_reason = f"module still failing after edit: {err}"
                continue

            # Guard 3: Full existing test suite in workspace must pass (zero collateral regressions)
            suite_ok, suite_out = _run_all_tests(tmp)
            if not suite_ok:
                last_reason = f"collateral test regression: {suite_out[:300]}"
                continue

            # Collect all changed non-test source files inside `tmp`
            updates: dict[str, str] = {}
            for p in sorted(tmp.rglob("*.py")):
                rel_parts = p.relative_to(tmp).parts
                if any(part in IGNORE_DIRS or part.startswith(".") for part in rel_parts) or is_test_file(p):
                    continue
                rel = str(p.relative_to(tmp))
                orig = (root / rel).read_text(errors="replace") if (root / rel).exists() else None
                cur = p.read_text(errors="replace")
                if orig != cur:
                    updates[rel] = cur

            diff = "".join(
                difflib.unified_diff(
                    before_src.splitlines(True),
                    after_src.splitlines(True),
                    f"a/{m.rel_path}",
                    f"b/{m.rel_path}",
                )
            )
            return (
                ModuleFixRecord(
                    module=m.rel_path,
                    repaired=True,
                    diff=diff,
                    reason="verified: AST, imports, and full test suite pass",
                    turns=total_turns,
                    cost_usd=round(total_cost, 4),
                ),
                updates,
            )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    return (
        ModuleFixRecord(
            module=m.rel_path,
            repaired=False,
            reason=last_reason,
            turns=total_turns,
            cost_usd=round(total_cost, 4),
        ),
        {},
    )


def heal_codebase(
    cfg: Config,
    store: Store,
    code_paths: list[str | Path] | None = None,
    *,
    agent_factory: Callable[[Config], Agent] | None = None,
    run_harness_cycle: bool = False,
    sync_memory: bool = False,
    dry_run: bool = False,
) -> HealOutcome:
    """Scan new & existing code, auto-repair broken modules safely, and optionally gate the harness."""
    from . import loop
    from .synthesizer import synthesize_cases

    paths = code_paths or cfg.raw.get("code_paths") or []
    if not paths:
        return HealOutcome(status="clean", scanned=0)

    modules = scan_codebase(cfg.root, paths, run_checks=True)
    failing = [m for m in modules if not m.healthy]
    outcome = HealOutcome(status="clean", scanned=len(modules), initially_failing=len(failing))

    lstore = LessonStore(cfg.path(cfg["fixer"]["lessons_file"]), cfg["fixer"]["max_lessons"])
    lessons = lstore.load()

    if failing and not dry_run:
        agent = (agent_factory or (lambda c: build_agent(c["agent"], c.root)))(cfg)
        max_attempts = int(cfg.raw.get("healer", {}).get("max_attempts", 2))
        session_records: list[dict[str, Any]] = []

        for m in failing:
            loop.say(f"heal: repairing failing module {m.rel_path}")
            rec, updates = heal_module_isolated(cfg.root, m, agent, lessons, max_attempts=max_attempts)
            outcome.cost_usd += rec.cost_usd
            if rec.repaired and updates:
                for rel, content in updates.items():
                    dst = cfg.root / rel
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    dst.write_text(content)
                outcome.repaired.append(rec)
                session_records.append({
                    "accepted": True,
                    "approach": f"auto-heal {m.rel_path}",
                    "diagnosis": rec.reason,
                    "run": f"heal-{int(time.time())}",
                })
                loop.say(f"  HEALED {m.rel_path} ({rec.reason})")
            else:
                outcome.unrepaired.append(rec)
                session_records.append({
                    "accepted": False,
                    "approach": f"auto-heal {m.rel_path}",
                    "reasons": [rec.reason],
                    "run": None,
                })
                loop.say(f"  UNREPAIRED {m.rel_path}: {rec.reason}")

        if session_records:
            lstore.save(LessonStore.fallback(lessons, session_records))
    elif failing and dry_run:
        for m in failing:
            err = m.syntax_error or m.import_error or m.test_output[:200]
            outcome.unrepaired.append(ModuleFixRecord(module=m.rel_path, repaired=False, reason=f"dry-run: {err}"))

    # Refresh architecture memory if using a directory Harness² tree and sync_memory is requested
    if sync_memory:
        refreshed = scan_codebase(cfg.root, paths, run_checks=False)
        spf = cfg["agent"].get("system_prompt_file", "")
        hpath = cfg.path(spf) if spf else None
        if hpath and hpath.is_dir():
            (hpath / "memory.md").write_text(build_context_map(refreshed))

    if cfg.raw.get("healer", {}).get("synthesize_cases", False):
        auto_dir = cfg.path("cases/auto")
        written = synthesize_cases(cfg.root, paths, out_dir=auto_dir)
        outcome.synthesized_cases = len(written)

    if run_harness_cycle:
        outcome.harness_result = loop.cycle(cfg, store, autofix_on=cfg["loop"]["autofix"], label="heal")
        outcome.cost_usd += float(outcome.harness_result.get("cost_usd", 0.0))

    if not failing:
        outcome.status = "clean"
    elif not outcome.unrepaired:
        outcome.status = "healed"
    elif outcome.repaired:
        outcome.status = "partially_healed"
    else:
        outcome.status = "failed"

    outcome.cost_usd = round(outcome.cost_usd, 4)
    return outcome
