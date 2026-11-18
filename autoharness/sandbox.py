"""A throwaway working directory per trial, seeded with the case's files."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .cases import Case


class Sandbox:
    def __init__(self, case: Case, trial: int, keep: bool = False):
        self.case = case
        self.keep = keep
        self.dir = Path(tempfile.mkdtemp(prefix=f"autoharness-{_slug(case.id)}-t{trial}-"))

    def __enter__(self) -> "Sandbox":
        for rel, content in self.case.files.items():
            target = self.resolve(rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        for cmd in self.case.setup_commands:
            r = self.run(cmd, timeout=120)
            if r.returncode != 0:
                raise RuntimeError(f"setup command failed: {cmd}\n{r.stdout}\n{r.stderr}")
        return self

    def __exit__(self, *exc) -> None:
        if not self.keep:
            shutil.rmtree(self.dir, ignore_errors=True)

    def resolve(self, rel: str) -> Path:
        """Resolve a path inside the sandbox; refuse anything that escapes it."""
        p = (self.dir / rel).resolve()
        if p != self.dir.resolve() and self.dir.resolve() not in p.parents:
            raise PermissionError(f"path escapes sandbox: {rel}")
        return p

    def run(
        self,
        cmd: str,
        timeout: float = 60,
        env: dict[str, str] | None = None,
        unset_env: tuple[str, ...] | list[str] = (),
    ) -> subprocess.CompletedProcess:
        if env or unset_env:
            full_env = dict(os.environ)
            for k in unset_env:
                full_env.pop(k, None)
            if env:
                full_env.update(env)
        else:
            full_env = None
        try:
            return subprocess.run(
                cmd, shell=True, cwd=self.dir, capture_output=True, text=True, timeout=timeout, env=full_env
            )
        except subprocess.TimeoutExpired as e:
            return subprocess.CompletedProcess(
                cmd, 124, _txt(e.stdout), _txt(e.stderr) + f"\n[timed out after {timeout}s]"
            )


def _slug(s: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in s)[:40]


def _txt(b) -> str:
    if b is None:
        return ""
    return b.decode(errors="replace") if isinstance(b, bytes) else b
