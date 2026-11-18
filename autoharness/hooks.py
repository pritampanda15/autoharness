"""Git hook integration (`autoharness hook install|uninstall|status`).

Installs a clean Git `pre-commit` or `post-commit` hook so every time new or
existing code/harness files are touched, `autoharness` automatically evaluates
changed targets and heals or gates regressions before bad changes ship.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

MARKER = "# autoharness-managed-hook"


def git_hooks_dir(repo_root: Path) -> Path:
    git_dir = repo_root / ".git"
    if not git_dir.exists():
        raise FileNotFoundError(f"not a git repository: {repo_root}")
    hooks = git_dir / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    return hooks


def install_hook(repo_root: Path, kind: str = "pre-commit", config: str = "autoharness.yaml",
                 autofix: bool = True) -> Path:
    hooks = git_hooks_dir(repo_root)
    hook_path = hooks / kind
    if hook_path.exists() and MARKER not in hook_path.read_text(errors="replace"):
        raise FileExistsError(
            f"{hook_path} already exists and was not created by autoharness. "
            "Remove or merge it manually before installing."
        )
    fix_flag = "" if autofix else " --dry-run"
    script = (
        "#!/usr/bin/env sh\n"
        f"{MARKER}\n"
        "set -eu\n"
        f"echo '[autoharness] Running autonomous code & harness verification ({kind})...'\n"
        f"autoharness --config {config} heal --changed{fix_flag}\n"
    )
    hook_path.write_text(script)
    hook_path.chmod(hook_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return hook_path


def uninstall_hook(repo_root: Path, kind: str = "pre-commit") -> bool:
    hooks = git_hooks_dir(repo_root)
    hook_path = hooks / kind
    if not hook_path.exists():
        return False
    if MARKER not in hook_path.read_text(errors="replace"):
        raise PermissionError(f"{hook_path} was not created by autoharness; refusing to delete it.")
    hook_path.unlink()
    return True


def hook_status(repo_root: Path) -> dict[str, str]:
    git_dir = repo_root / ".git"
    if not git_dir.exists():
        return {}
    hooks = git_dir / "hooks"
    out: dict[str, str] = {}
    for kind in ("pre-commit", "post-commit", "pre-push"):
        p = hooks / kind
        if p.exists():
            out[kind] = "autoharness" if MARKER in p.read_text(errors="replace") else "external"
        else:
            out[kind] = "not installed"
    return out


def changed_python_files(repo_root: Path) -> list[Path]:
    """Return staged + unstaged + untracked `.py` files in `repo_root` via `git status --porcelain`."""
    try:
        r = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if r.returncode != 0:
        return []
    files: list[Path] = []
    for line in r.stdout.splitlines():
        if len(line) < 4:
            continue
        rel = line[3:].strip().split(" -> ")[-1].strip('"')
        p = repo_root / rel
        if p.suffix == ".py" and p.exists():
            files.append(p)
    return files
