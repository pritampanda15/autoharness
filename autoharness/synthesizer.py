"""Automatic codebase scanner, health evaluator, and eval-case synthesizer.

Eliminates manual eval authoring & manual lookup: whenever new or existing code
is written, this module inspects AST structure, discovers unit tests, runs fast
deterministic health checks, builds a live architecture memory map, and synthesizes
executable `dev` and `holdout` YAML evaluation cases automatically.
"""

from __future__ import annotations

import ast
import hashlib
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

IGNORE_DIRS = {
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    "env",
    "node_modules",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "runs",
    "build",
    "dist",
}


@dataclass
class ModuleHealth:
    rel_path: str
    abs_path: Path
    syntax_ok: bool
    syntax_error: str | None = None
    imports_ok: bool = True
    import_error: str | None = None
    tests_found: list[Path] = field(default_factory=list)
    tests_ok: bool = True
    test_output: str = ""
    functions: list[str] = field(default_factory=list)
    classes: list[str] = field(default_factory=list)
    docstring: str = ""

    @property
    def healthy(self) -> bool:
        return self.syntax_ok and self.imports_ok and self.tests_ok


def is_test_file(path: Path) -> bool:
    name = path.name
    return (
        name.startswith("test_")
        or name.endswith("_test.py")
        or "tests" in path.parts
        or name == "conftest.py"
    )


def discover_code_files(root: Path, code_paths: list[str | Path] | None = None) -> list[Path]:
    """Discover non-test Python source files under `code_paths` (or `root` if omitted)."""
    targets = [root / p if not Path(p).is_absolute() else Path(p) for p in (code_paths or ["."])]
    found: list[Path] = []
    seen: set[Path] = set()
    for t in targets:
        if not t.exists():
            continue
        candidates = [t] if t.is_file() else sorted(t.rglob("*.py"))
        for f in candidates:
            if f.suffix != ".py":
                continue
            rel_parts = f.relative_to(root).parts if f.is_relative_to(root) else f.parts
            if any(part in IGNORE_DIRS or part.endswith(".egg-info") or part.startswith(".") for part in rel_parts):
                continue
            if is_test_file(f):
                continue
            resolved = f.resolve()
            if resolved not in seen:
                seen.add(resolved)
                found.append(f)
    return found


def find_matching_tests(module_path: Path, root: Path) -> list[Path]:
    """Locate test files corresponding to `module_path` (e.g. `test_foo.py`, `tests/test_foo.py`)."""
    stem = module_path.stem
    if stem == "__init__":
        stem = module_path.parent.name
    candidates = [
        module_path.parent / f"test_{stem}.py",
        module_path.parent / f"{stem}_test.py",
        root / "tests" / f"test_{stem}.py",
        root / "tests" / f"{stem}_test.py",
    ]
    if root.exists():
        for tf in sorted(root.rglob(f"*test*{stem}*.py")):
            rel_parts = tf.relative_to(root).parts if tf.is_relative_to(root) else tf.parts
            if not any(p in IGNORE_DIRS for p in rel_parts):
                candidates.append(tf)
    out: list[Path] = []
    seen: set[Path] = set()
    for c in candidates:
        if c.exists() and c.is_file() and c.resolve() not in seen:
            seen.add(c.resolve())
            out.append(c)
    return out


def _clean_env(root: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{root.resolve()}{os.pathsep}{existing}" if existing else str(root.resolve())
    return env


def analyze_module(module_path: Path, root: Path, run_checks: bool = True, timeout_s: float = 30.0) -> ModuleHealth:
    """Inspect AST, imports, and matching unit tests for a source file without manual intervention."""
    rel = str(module_path.relative_to(root)) if module_path.is_relative_to(root) else module_path.name
    source = module_path.read_text(errors="replace")
    health = ModuleHealth(rel_path=rel, abs_path=module_path, syntax_ok=True)
    health.tests_found = find_matching_tests(module_path, root)

    try:
        tree = ast.parse(source, filename=rel)
        health.docstring = (ast.get_docstring(tree) or "").splitlines()[0] if ast.get_docstring(tree) else ""
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
                health.functions.append(node.name)
            elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
                health.classes.append(node.name)
    except SyntaxError as e:
        health.syntax_ok = False
        health.syntax_error = f"{rel}:{e.lineno}: SyntaxError: {e.msg}"
        return health

    if not run_checks:
        return health

    env = _clean_env(root)
    # Check module import / execution safety if it has no top-level script side-effects or via import
    mod_parts = list(Path(rel).with_suffix("").parts)
    is_dunder_main = mod_parts[-1] == "__main__"
    if mod_parts[-1] == "__init__":
        mod_parts = mod_parts[:-1]
    if not is_dunder_main and mod_parts and all(p.isidentifier() for p in mod_parts):
        mod_name = ".".join(mod_parts)
        try:
            r = subprocess.run(
                [sys.executable, "-c", f"import {mod_name}"],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                env=env,
            )
            if r.returncode != 0:
                health.imports_ok = False
                health.import_error = (r.stderr or r.stdout)[-800:].strip()
        except subprocess.TimeoutExpired:
            health.imports_ok = False
            health.import_error = f"importing {mod_name} timed out after {timeout_s}s"

    if health.tests_found:
        test_args = [str(t.relative_to(root)) if t.is_relative_to(root) else str(t) for t in health.tests_found]
        try:
            r = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", *test_args],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                env=env,
            )
            health.tests_ok = r.returncode == 0
            health.test_output = (r.stdout + "\n" + r.stderr)[-1500:].strip()
        except subprocess.TimeoutExpired:
            health.tests_ok = False
            health.test_output = f"pytest timed out after {timeout_s}s"

    return health


def scan_codebase(root: Path, code_paths: list[str | Path] | None = None,
                  run_checks: bool = True) -> list[ModuleHealth]:
    """Scan and evaluate all discovered source modules in `code_paths`."""
    files = discover_code_files(root, code_paths)
    return [analyze_module(f, root, run_checks=run_checks) for f in files]


def build_context_map(modules: list[ModuleHealth]) -> str:
    """Build a concise codebase architecture summary for Harness² `memory.md`."""
    if not modules:
        return ""
    lines = ["## Auto-Generated Codebase Map"]
    for m in modules:
        symbols = [f"`{c}` (class)" for c in m.classes[:6]] + [f"`{fn}()`" for fn in m.functions[:8]]
        sym_txt = ", ".join(symbols) if symbols else "(no public symbols)"
        status = "healthy" if m.healthy else "FAILING"
        doc = f" — {m.docstring}" if m.docstring else ""
        lines.append(f"- `{m.rel_path}` [{status}]{doc}: {sym_txt}")
    return "\n".join(lines) + "\n"


def synthesize_cases(
    root: Path,
    code_paths: list[str | Path] | None = None,
    out_dir: Path | None = None,
    include_healthy: bool = True,
) -> list[Path]:
    """Automatically generate executable dev & holdout YAML evaluation cases from source code."""
    out_dir = out_dir or (root / "cases" / "auto")
    out_dir.mkdir(parents=True, exist_ok=True)
    modules = scan_codebase(root, code_paths, run_checks=True)
    written: list[Path] = []

    for idx, m in enumerate(modules):
        if m.healthy and not include_healthy:
            continue
        slug = hashlib.sha1(m.rel_path.encode()).hexdigest()[:8]
        stem = Path(m.rel_path).stem
        files_map: dict[str, str] = {m.rel_path: m.abs_path.read_text(errors="replace")}
        for tf in m.tests_found:
            trel = str(tf.relative_to(root)) if tf.is_relative_to(root) else tf.name
            files_map[trel] = tf.read_text(errors="replace")

        if not m.healthy:
            cid = f"auto-heal-{stem}-{slug}"
            failure_hint = m.syntax_error or m.import_error or m.test_output[:400]
            checks: list[dict[str, Any]] = [
                {"name": "syntax-valid", "type": "python_syntax", "path": m.rel_path},
                {"name": "read-first", "type": "read_before_write", "paths": [m.rel_path]},
                {"name": "verified", "type": "verified_after_edit"},
                {"type": "no_loops", "max_repeats": 3},
            ]
            if m.tests_found:
                trel = str(m.tests_found[0].relative_to(root)) if m.tests_found[0].is_relative_to(root) else m.tests_found[0].name
                checks.insert(
                    1,
                    {
                        "name": "tests-pass",
                        "type": "command",
                        "run": f"PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q {trel}",
                    },
                )
                checks.append(
                    {
                        "name": "tests-untouched",
                        "type": "file_unchanged",
                        "path": trel,
                        "content": files_map[trel],
                    }
                )
            doc = {
                "id": cid,
                "status": "active",
                "split": "dev",
                "description": f"Auto-synthesized repair verification for failing module {m.rel_path}.",
                "tags": ["auto", "code-heal", stem],
                "prompt": (
                    f"Module `{m.rel_path}` is currently failing automated verification.\n"
                    f"Observed error:\n{failure_hint}\n\n"
                    f"Inspect `{m.rel_path}` (and any tests), fix the root cause in `{m.rel_path}` without "
                    f"modifying test files, and run verification."
                ),
                "setup": {"files": files_map},
                "checks": checks,
            }
        else:
            # Synthesize deterministic read-only / non-regression guard cases (alternating dev / holdout)
            split = "holdout" if idx % 3 == 2 else "dev"
            target_sym = (m.functions or m.classes or [stem])[0]
            cid = f"auto-guard-{stem}-{slug}"
            checks = [
                {"name": "mentions-symbol", "type": "output_contains", "text": target_sym},
                {"name": "source-untouched", "type": "file_unchanged", "path": m.rel_path, "content": files_map[m.rel_path]},
                {"type": "must_not_call_tool", "tool": "write_file"},
                {"type": "max_turns", "value": 5},
            ]
            doc = {
                "id": cid,
                "status": "active",
                "split": split,
                "description": f"Auto-synthesized non-destructive inspection guard for {m.rel_path}.",
                "tags": ["auto", "guard", stem],
                "prompt": (
                    f"Inspect `{m.rel_path}` without modifying any files and name a primary public function or "
                    f"class defined in it. Keep your answer concise."
                ),
                "setup": {"files": {m.rel_path: files_map[m.rel_path]}},
                "checks": checks,
                "mock": {
                    "actions": [{"tool": "read_file", "input": {"path": m.rel_path}}],
                    "output": target_sym,
                },
            }

        out_file = out_dir / f"{doc['id']}.yaml"
        out_file.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=100))
        written.append(out_file)
    return written
