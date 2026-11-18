"""Layer 1: deterministic checks. Free, fast, and never wrong about what they test."""

from __future__ import annotations

import ast
import json
import re
import shlex
from typing import Any, Callable

from ..sandbox import Sandbox
from ..trace import Trace
from .autonomous import audit_workspace_integrity

Check = Callable[[dict[str, Any], Trace, "Sandbox | None"], tuple[bool, str]]


def _need(sandbox):
    if sandbox is None:
        raise RuntimeError("this check needs the trial's workspace (re-grade with --keep-workdirs runs only)")
    return sandbox


def file_exists(spec, trace, sandbox):
    p = _need(sandbox).resolve(spec["path"])
    return p.exists(), f"{spec['path']} {'exists' if p.exists() else 'missing'}"


def file_absent(spec, trace, sandbox):
    p = _need(sandbox).resolve(spec["path"])
    return not p.exists(), f"{spec['path']} {'still exists' if p.exists() else 'absent'}"


def file_contains(spec, trace, sandbox):
    p = _need(sandbox).resolve(spec["path"])
    if not p.exists():
        return False, f"{spec['path']} missing"
    hit = re.search(spec["pattern"], p.read_text(), re.MULTILINE)
    return bool(hit), f"/{spec['pattern']}/ {'found' if hit else 'not found'} in {spec['path']}"


def file_not_contains(spec, trace, sandbox):
    ok, detail = file_contains(spec, trace, sandbox)
    if "missing" in detail:
        return True, detail
    return not ok, detail


def file_unchanged(spec, trace, sandbox):
    """The file still has exactly the content given in the spec (guards against collateral edits)."""
    p = _need(sandbox).resolve(spec["path"])
    ok = p.exists() and p.read_text() == spec["content"]
    return ok, f"{spec['path']} {'unchanged' if ok else 'modified or deleted'}"


def output_contains(spec, trace, sandbox):
    text, needle = trace.final_output, spec["text"]
    if spec.get("ignore_case", True):
        text, needle = text.lower(), needle.lower()
    return needle in text, f"{spec['text']!r} {'present' if needle in text else 'absent'} in final output"


def output_matches(spec, trace, sandbox):
    hit = re.search(spec["pattern"], trace.final_output, re.MULTILINE | re.IGNORECASE)
    return bool(hit), f"/{spec['pattern']}/ {'matched' if hit else 'did not match'} final output"


def output_not_matches(spec, trace, sandbox):
    ok, detail = output_matches(spec, trace, sandbox)
    return not ok, detail


def output_max_chars(spec, trace, sandbox):
    n = len(trace.final_output)
    return n <= spec["value"], f"final output is {n} chars (max {spec['value']})"


def command(spec, trace, sandbox):
    """Run a command in the workspace after the agent finishes, e.g. the test suite."""
    r = _need(sandbox).run(spec["run"], timeout=spec.get("timeout_s", 120))
    want = spec.get("exit_code", 0)
    ok = r.returncode == want
    if ok and spec.get("stdout_pattern"):
        ok = bool(re.search(spec["stdout_pattern"], r.stdout, re.MULTILINE))
    tail = (r.stdout + r.stderr)[-600:].strip()
    return ok, f"`{spec['run']}` exit={r.returncode} (want {want})\n{tail}"


def json_valid(spec, trace, sandbox):
    src = _need(sandbox).resolve(spec["path"]).read_text() if spec.get("path") else trace.final_output
    if spec.get("extract_block", not spec.get("path")):
        m = re.search(r"```(?:json)?\s*(.*?)```", src, re.DOTALL)
        src = m.group(1) if m else src
    try:
        data = json.loads(src)
    except json.JSONDecodeError as e:
        return False, f"invalid JSON: {e}"
    missing = [k for k in spec.get("required_keys", []) if not (isinstance(data, dict) and k in data)]
    return not missing, "valid JSON" + (f"; missing keys {missing}" if missing else "")


def python_syntax(spec, trace, sandbox):
    """Verify one or all Python files in the workspace parse with zero AST SyntaxErrors."""
    sb = _need(sandbox)
    paths = [sb.resolve(spec["path"])] if spec.get("path") else sorted(
        p for p in sb.dir.rglob("*.py")
        if not any(part.startswith(".") or part == "__pycache__" for part in p.relative_to(sb.dir).parts)
    )
    if not paths:
        return False, "no Python files found to check"
    errors = []
    for p in paths:
        rel = str(p.relative_to(sb.dir)) if p.is_relative_to(sb.dir) else str(p)
        if not p.exists():
            errors.append(f"{rel}: missing")
            continue
        try:
            ast.parse(p.read_text(), filename=rel)
        except SyntaxError as e:
            errors.append(f"{rel}:{e.lineno}: {e.msg}")
    return not errors, ("AST valid (" + f"{len(paths)} file(s))" if not errors else "; ".join(errors))


def imports_clean(spec, trace, sandbox):
    """Verify a Python module imports cleanly inside the sandbox."""
    mod = spec["module"]
    r = _need(sandbox).run(f"python3 -c {shlex.quote(f'import {mod}')}", timeout=spec.get("timeout_s", 30))
    ok = r.returncode == 0
    tail = (r.stderr or r.stdout)[-500:].strip()
    return ok, f"import {mod}: {'ok' if ok else tail}"


def workspace_integrity(spec, trace, sandbox):
    """Verify all Python, JSON, and YAML files in the workspace parse cleanly."""
    ok, checked, issues = audit_workspace_integrity(_need(sandbox))
    return ok, (f"integrity ok ({len(checked)} file(s))" if ok else "; ".join(issues))


CHECKS: dict[str, Check] = {
    "file_exists": file_exists,
    "file_absent": file_absent,
    "file_contains": file_contains,
    "file_not_contains": file_not_contains,
    "file_unchanged": file_unchanged,
    "output_contains": output_contains,
    "output_matches": output_matches,
    "output_not_matches": output_not_matches,
    "output_max_chars": output_max_chars,
    "command": command,
    "json_valid": json_valid,
    "python_syntax": python_syntax,
    "imports_clean": imports_clean,
    "workspace_integrity": workspace_integrity,
}
