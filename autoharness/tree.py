"""Harness² 8-component modular harness tree (`Agent = Frozen Model + Editable Harness`).

Inspired by `google-research/harness2` (Recursive Agent Harnessing for an Open World),
this module structures the software scaffolding surrounding a frozen LLM into eight
editable runtime roles grouped across three architectural spaces:

1. Context Construction Space:
   - `system`        (`system.md`)        Core identity, reasoning workflow & communication contract
   - `guardrails`    (`guardrails.md`)    Hard operational boundaries & safety invariants
   - `memory`        (`memory.md`)        Codebase context map & working-memory conventions
   - `skills`        (`skills.md`)        Reusable procedural playbooks (debugging, refactoring, Q&A)

2. Tool & Interface Space:
   - `tool_guidance` (`tool_guidance.md`) Instructions on how, when & in what order to invoke tools
   - `scripts`       (`scripts.md` + `scripts/*`) Executable workspace helper utilities

3. Delegation & Middleware Space:
   - `subagents`     (`subagents.md`)     Sub-task decomposition & self-verification personas
   - `plugins`       (`plugins.json` / `plugins.md`) Deterministic pre/post-tool runtime middleware

Backward compatibility: pointing at a single `.md` file loads a 1-component tree (`system`),
while pointing at a harness directory loads all present components seamlessly.
"""

from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .sandbox import Sandbox
from .store import fingerprint


@dataclass(frozen=True)
class ComponentSpec:
    key: str
    filename: str
    space: str
    title: str
    description: str


COMPONENTS: tuple[ComponentSpec, ...] = (
    # 1. Context Construction Space
    ComponentSpec(
        "system",
        "system.md",
        "context",
        "System Instructions",
        "Core agent identity, reasoning loop, and response contract.",
    ),
    ComponentSpec(
        "guardrails",
        "guardrails.md",
        "context",
        "Safety & Scope Guardrails",
        "Hard operational boundaries, non-destructive rules, and scope invariants.",
    ),
    ComponentSpec(
        "memory",
        "memory.md",
        "context",
        "Architecture & Working Memory",
        "Persistent codebase map, module contracts, and state-tracking schema.",
    ),
    ComponentSpec(
        "skills",
        "skills.md",
        "context",
        "Procedural Skills",
        "Step-by-step playbooks for debugging, refactoring, testing, and Q&A.",
    ),
    # 2. Tool & Interface Space
    ComponentSpec(
        "tool_guidance",
        "tool_guidance.md",
        "interface",
        "Tool Invocation Guidance",
        "Rules for tool selection, read-before-edit discipline, and shell usage.",
    ),
    ComponentSpec(
        "scripts",
        "scripts.md",
        "interface",
        "Executable Helper Scripts",
        "Reusable scripts staged into `.harness/scripts/` inside the workspace.",
    ),
    # 3. Delegation & Middleware Space
    ComponentSpec(
        "subagents",
        "subagents.md",
        "middleware",
        "Subagent & Verification Roles",
        "Structured decomposition and adversarial self-verification protocols.",
    ),
    ComponentSpec(
        "plugins",
        "plugins.json",
        "middleware",
        "Runtime Middleware Plugins",
        "Deterministic pre-tool/post-tool hooks enforced by the harness runtime.",
    ),
)

COMPONENT_BY_KEY: dict[str, ComponentSpec] = {c.key: c for c in COMPONENTS}
COMPONENT_BY_FILE: dict[str, ComponentSpec] = {c.filename: c for c in COMPONENTS}


DEFAULT_SCAFFOLD: dict[str, str] = {
    "system.md": (
        "You are a careful, autonomous software engineer working inside a workspace.\n\n"
        "Core workflow:\n"
        "- Understand the request first: distinguish code changes from read-only questions.\n"
        "- Inspect relevant files before making any edits.\n"
        "- Make the smallest, cleanest change that completely solves the task.\n"
        "- Verify every code modification by running tests or an executable check.\n"
        "- When asked a direct question, reply with only the requested answer unless asked to elaborate.\n"
    ),
    "guardrails.md": (
        "- Never run destructive commands (`rm -rf`, `git reset --hard`, dropping user data) unless explicitly requested.\n"
        "- If a cleanup or deletion request is ambiguous and could affect user data, ask or propose a safe plan before deleting.\n"
        "- Never modify unrelated files, configuration, or tests merely to force a check to pass.\n"
        "- Never fabricate command outputs or claim tests passed without running them.\n"
    ),
    "memory.md": (
        "Track these facts mentally as you work:\n"
        "- Entrypoints and module boundaries discovered so far.\n"
        "- Files read vs. files modified in the current session.\n"
        "- Verification status of the latest edit (unverified -> verified).\n"
    ),
    "skills.md": (
        "## Bugfix Playbook\n"
        "1. Read the failing module and its tests.\n"
        "2. Reproduce the failure or trace the exact root cause.\n"
        "3. Apply a minimal fix preserving existing signatures.\n"
        "4. Re-run tests to confirm zero regressions.\n\n"
        "## Direct Q&A Playbook\n"
        "1. Read the referenced file(s) without modifying workspace state.\n"
        "2. Return the exact answer concisely in the format requested.\n"
    ),
    "tool_guidance.md": (
        "- Always read a file (`read_file` or `cat`) before overwriting or editing it.\n"
        "- Prefer targeted inspection over broad recursive dumps.\n"
        "- After `write_file`, run `run_bash` with `pytest` or `python3` to verify behavior.\n"
        "- Stop retrying an identical failing command; inspect the error message and adjust.\n"
    ),
    "scripts.md": (
        "Helper utilities located in `.harness/scripts/` (when present) are available in your workspace:\n"
        "- `.harness/scripts/verify.sh`: Quick syntax and test runner for modified Python modules.\n"
    ),
    "subagents.md": (
        "Before returning your final answer, switch to a strict **Verifier** perspective:\n"
        "1. Did I answer the exact prompt without unrequested extras?\n"
        "2. Were any untouched files accidentally altered?\n"
        "3. If I edited code, did I run verification *after* the very last edit?\n"
    ),
    "plugins.json": json.dumps(
        {
            "blocked_bash_patterns": [
                "rm\\s+-rf\\s+/",
                "git\\s+reset\\s+--hard",
                "sudo\\s+rm",
            ],
            "require_read_before_overwrite": False,
            "post_write_reminder": "Reminder: run verification (tests or syntax check) after your final edit.",
        },
        indent=2,
    )
    + "\n",
}

DEFAULT_VERIFY_SCRIPT = """#!/usr/bin/env sh
set -eu
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q "$@"
"""


@dataclass
class MiddlewareState:
    """Per-trial state tracked by deterministic harness middleware plugins."""

    files_read: set[str] = field(default_factory=set)
    files_written: set[str] = field(default_factory=set)
    verified_since_last_write: bool = True


@dataclass
class HarnessTree:
    """In-memory representation of an editable single-file or 8-component Harness² directory."""

    root: Path
    is_directory: bool
    components: dict[str, str] = field(default_factory=dict)
    scripts: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path) -> "HarnessTree":
        p = Path(path)
        if p.is_file() or (not p.exists() and p.suffix in (".md", ".txt", ".prompt")):
            content = p.read_text() if p.exists() else ""
            return cls(root=p, is_directory=False, components={"system": content})

        comps: dict[str, str] = {}
        scripts: dict[str, str] = {}
        if p.exists() and p.is_dir():
            for spec in COMPONENTS:
                fp = p / spec.filename
                if fp.exists() and fp.is_file():
                    comps[spec.key] = fp.read_text()
                elif spec.key == "plugins" and (p / "plugins.md").exists():
                    comps["plugins"] = (p / "plugins.md").read_text()
            sdir = p / "scripts"
            if sdir.exists() and sdir.is_dir():
                for sf in sorted(sdir.rglob("*")):
                    if sf.is_file() and not sf.name.startswith("."):
                        scripts[str(sf.relative_to(sdir))] = sf.read_text(errors="replace")
        return cls(root=p, is_directory=True, components=comps, scripts=scripts)

    @classmethod
    def scaffold(cls, target_dir: str | Path, overwrite: bool = False) -> list[Path]:
        """Create a complete 8-component Harness² directory."""
        d = Path(target_dir)
        d.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []
        for fname, content in DEFAULT_SCAFFOLD.items():
            fp = d / fname
            if overwrite or not fp.exists():
                fp.write_text(content)
                written.append(fp)
        sdir = d / "scripts"
        sdir.mkdir(parents=True, exist_ok=True)
        vscript = sdir / "verify.sh"
        if overwrite or not vscript.exists():
            vscript.write_text(DEFAULT_VERIFY_SCRIPT)
            vscript.chmod(vscript.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            written.append(vscript)
        return written

    def editable_paths(self, rel_to: Path | None = None) -> list[str]:
        """Return relative file paths for all editable components in this harness."""
        if not self.is_directory:
            return [str(self.root.relative_to(rel_to)) if rel_to and self.root.is_relative_to(rel_to) else str(self.root)]
        files: list[Path] = []
        for spec in COMPONENTS:
            fp = self.root / spec.filename
            if fp.exists():
                files.append(fp)
        sdir = self.root / "scripts"
        if sdir.exists():
            files.extend(sorted(f for f in sdir.rglob("*") if f.is_file() and not f.name.startswith(".")))
        out = []
        for f in files:
            out.append(str(f.relative_to(rel_to)) if rel_to and f.is_relative_to(rel_to) else str(f))
        return out

    def compile_prompt(self, extra_memory: str = "") -> str:
        """Compile all active Context, Interface, and Delegation components into a unified system prompt."""
        if not self.is_directory and set(self.components) <= {"system"}:
            base = self.components.get("system", "")
            return f"{base}\n\n{extra_memory}".strip() if extra_memory else base

        sections: list[str] = []
        if self.components.get("system", "").strip():
            sections.append(self.components["system"].strip())

        ordered_tags = [
            ("guardrails", "guardrails"),
            ("memory", "working_memory"),
            ("skills", "skills"),
            ("tool_guidance", "tool_guidance"),
            ("scripts", "workspace_scripts"),
            ("subagents", "verification_protocol"),
        ]
        for key, tag in ordered_tags:
            body = self.components.get(key, "").strip()
            if body:
                sections.append(f"<{tag}>\n{body}\n</{tag}>")

        if self.scripts:
            listing = ", ".join(f"`.harness/scripts/{name}`" for name in sorted(self.scripts))
            sections.append(f"<available_scripts>\nStaged helper scripts: {listing}\n</available_scripts>")

        if extra_memory.strip():
            sections.append(f"<codebase_context>\n{extra_memory.strip()}\n</codebase_context>")

        return "\n\n".join(sections)

    def plugin_config(self) -> dict[str, Any]:
        raw = self.components.get("plugins", "").strip()
        if not raw:
            return {}
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}

    def stage_into_sandbox(self, sandbox: Sandbox) -> None:
        """Stage executable helper scripts into `.harness/scripts/` inside the trial workspace."""
        if not self.scripts:
            return
        base = sandbox.dir / ".harness" / "scripts"
        base.mkdir(parents=True, exist_ok=True)
        for rel, content in self.scripts.items():
            dst = (base / rel).resolve()
            if base.resolve() not in dst.parents and dst != base.resolve():
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(content)
            os.chmod(dst, dst.stat().st_mode | stat.S_IXUSR)

    def pre_tool_hook(self, name: str, args: dict[str, Any], state: MiddlewareState) -> str | None:
        """Deterministic pre-tool guardrail plugin. Returns an error message if blocked, else None."""
        cfg = self.plugin_config()
        if not cfg:
            return None

        if name == "run_bash":
            cmd = str(args.get("command", ""))
            for pat in cfg.get("blocked_bash_patterns", []):
                if re.search(pat, cmd):
                    return f"Blocked by harness guardrail plugin (matched /{pat}/): {cmd}"

        if name == "write_file" and cfg.get("require_read_before_overwrite"):
            target = str(args.get("path", ""))
            base = target.rsplit("/", 1)[-1]
            if base in state.files_written or base in state.files_read:
                return None
            # Only block if overwriting an existing file recorded as unread when configured strictly
            if cfg.get("strict_read_check") and base and base not in state.files_read:
                return f"Blocked by harness plugin: read {target!r} before overwriting it."
        return None

    def post_tool_hook(self, name: str, args: dict[str, Any], output: str, is_error: bool,
                       state: MiddlewareState) -> str:
        """Deterministic post-tool middleware plugin. Tracks state and appends hints when configured."""
        cfg = self.plugin_config()
        if name == "read_file":
            p = str(args.get("path", ""))
            state.files_read.add(p)
            state.files_read.add(p.rsplit("/", 1)[-1])
        elif name == "run_bash":
            cmd = str(args.get("command", ""))
            if re.search(r"\b(cat|head|tail|sed|awk|grep|rg)\b", cmd):
                state.files_read.add(cmd)
            if re.search(r"\b(pytest|unittest|python3?|node|npm|cargo|go test)\b", cmd) and not is_error:
                state.verified_since_last_write = True
        elif name == "write_file" and not is_error:
            p = str(args.get("path", ""))
            state.files_written.add(p)
            state.verified_since_last_write = False
            reminder = cfg.get("post_write_reminder")
            if reminder:
                return f"{output}\n[harness-plugin] {reminder}"
        return output

    def fingerprint(self) -> str:
        parts = [f"dir={self.is_directory}"]
        for k in sorted(self.components):
            parts.append(f"{k}:{self.components[k]}")
        for k in sorted(self.scripts):
            parts.append(f"script:{k}:{self.scripts[k]}")
        return fingerprint(*parts)

    def summary_rows(self) -> list[dict[str, Any]]:
        rows = []
        for spec in COMPONENTS:
            present = spec.key in self.components
            chars = len(self.components.get(spec.key, ""))
            if spec.key == "scripts" and self.scripts:
                present = True
                chars += sum(len(v) for v in self.scripts.values())
            rows.append({
                "space": spec.space,
                "component": spec.key,
                "file": spec.filename if self.is_directory else self.root.name,
                "active": present,
                "chars": chars,
                "description": spec.description,
            })
            if not self.is_directory:
                break
        return rows
