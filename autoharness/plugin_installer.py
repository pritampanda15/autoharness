"""Universal Plugin & MCP Installer for AutoHarness (`autoharness plugin`).

Packages and installs AutoHarness as an MCP Server + Agent Skill/Plugin across:
  - Antigravity        (`~/.gemini/config/plugins/autoharness/`)
  - Claude Code        (`.mcp.json` + `.claude-plugin/plugin.json` + `skills/autoharness/SKILL.md`)
  - Cursor             (`.cursor/mcp.json` + `.cursor/rules/autoharness.mdc`)
  - VS Code / Copilot  (`.vscode/mcp.json`)
  - Google Gemini CLI  (`.gemini/settings.json` workspace MCP entry)
  - OpenAI Codex CLI   (`.codex/config.toml` workspace MCP entry)
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any

from . import __version__

PLUGIN_TARGETS = ["antigravity", "claude", "cursor", "vscode", "gemini", "codex", "all"]

LOGO_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 128 128">
  <rect x="12" y="12" width="104" height="104" rx="26" fill="#0f172a"/>
  <path d="M 64 28 A 36 36 0 0 1 97 50" fill="none" stroke="#4285F4" stroke-width="7.5" stroke-linecap="round"/>
  <polygon points="102,57 90,50 102,43" fill="#4285F4"/>
  <path d="M 97 78 A 36 36 0 0 1 64 100" fill="none" stroke="#34A853" stroke-width="7.5" stroke-linecap="round"/>
  <polygon points="57,100 67,94 66,106" fill="#34A853"/>
  <path d="M 64 100 A 36 36 0 0 1 31 78" fill="none" stroke="#FBBC04" stroke-width="7.5" stroke-linecap="round"/>
  <polygon points="26,71 38,78 26,85" fill="#FBBC04"/>
  <path d="M 31 50 A 36 36 0 0 1 64 28" fill="none" stroke="#EA4335" stroke-width="7.5" stroke-linecap="round"/>
  <polygon points="71,28 61,34 62,22" fill="#EA4335"/>
  <rect x="46" y="46" width="36" height="36" rx="9" fill="#1e293b" stroke="#cbd5e1" stroke-width="2.2"/>
  <polygon points="64,52 76,64 64,76 52,64" fill="#4285F4"/>
  <circle cx="64" cy="64" r="4" fill="#ffffff"/>
</svg>
"""

SKILL_MD = """---
name: "autoharness"
description: "Autonomous 8-component Harness² engineering, code AST/import/pytest self-healing with byte-locked unit tests, contrastive Propose-Probe-Compose harness repair, and 3-layer evaluation via AutoHarness MCP tools and CLI. Use when testing, healing broken modules, synthesizing eval cases, inspecting Harness² files, or preventing regressions."
when_to_use: "Trigger when the user wants full closed-loop AutoHarness self-healing, MCP agent verification, flat-rate subscription routing ($0 API), or the Apple Liquid Glass Studio workbench."
allowed-tools: ["bash", "read_file", "write_file", "edit_file", "list_files"]
version: "0.3.0"
---

# AutoHarness Skill & MCP Agent Guide

AutoHarness turns any AI coding assistant or subscription model into a **closed-loop, anti-gaming self-healing engineer**.

## Core Workflow (Zero Extra API Keys)

When AutoHarness MCP (`autoharness_mcp`) is connected, drive repairs directly with your own host model session:

1. **Snapshot Test Locks First (`autoharness_snapshot_test_locks`)**:
   Capture SHA-256 digests of all `test_*.py` files before touching source code.
2. **Scan AST, Imports & Tests (`autoharness_scan_code`)**:
   Identify syntax errors, standalone vs. package `ImportError` bugs, and failing `pytest` assertions, while syncing `harnesses/harness2/memory.md`.
3. **Repair Source Code Only (Never Edit Locked Tests)**:
   Fix the root cause in the failing module with a minimal change.
4. **Verify Against Anti-Gaming Locks (`autoharness_verify_repair`)**:
   Pass the `locked_test_hashes` captured in step 1 to prove byte-for-byte test immutability, clean AST/imports, and green `pytest`.
5. **Run 3-Layer Suite & Contrastive Harness² Tuning (`autoharness_run_eval`, `autoharness_harness_component`, `autoharness_lessons`)**:
   Execute deterministic + trace checks (`$0`), inspect or refine the 8 modular Harness² files (`system.md`, `guardrails.md`, `memory.md`, `skills.md`, `tool_guidance.md`, `scripts/`, `subagents.md`, `plugins.json`), and persist `DO` / `AVOID` priors into `lessons/lessons.json`.

## CLI & Liquid Glass Studio Commands

- `autoharness studio` — Launch the Apple Liquid Glass visual workbench + 1-click `Auto-Fix`
- `autoharness heal --with-harness` — Scan & heal broken modules + run contrastive harness fixing
- `autoharness models` — Inspect or switch across Flat-Rate Subscriptions (`Claude Pro/Max`, `ChatGPT Plus/Codex`, `Google AI Pro/Gemini`, `Copilot`) and Open-Source (`Ollama`, `vLLM`, `LM Studio`)
- `autoharness mcp` — Start the JSON-RPC 2.0 MCP stdio server
"""


def resolve_mcp_command() -> tuple[str, list[str]]:
    bin_hit = shutil.which("autoharness")
    if bin_hit:
        return bin_hit, ["mcp"]
    return sys.executable, ["-m", "autoharness", "mcp"]


def mcp_server_entry(root: Path | None = None) -> dict[str, Any]:
    cmd, args = resolve_mcp_command()
    if root is not None:
        args = [*args, "--root", str(root.resolve())]
    return {"command": cmd, "args": args}


def install_plugin_target(
    target: str,
    workspace_root: Path,
    *,
    antigravity_plugins_dir: Path | None = None,
) -> list[str]:
    """Install AutoHarness MCP + Skill/Plugin configuration for one or all agent ecosystems."""
    targets = ["antigravity", "claude", "cursor", "vscode", "gemini", "codex"] if target == "all" else [target]
    written: list[str] = []
    ws = workspace_root.resolve()

    for t in targets:
        if t == "antigravity":
            from .skills_packager import FORBIDDEN_DIRS, discover_skill_dirs

            pdir = (antigravity_plugins_dir or (Path.home() / ".gemini" / "config" / "plugins")) / "autoharness"
            (pdir / "assets").mkdir(parents=True, exist_ok=True)
            (pdir / "skills" / "autoharness").mkdir(parents=True, exist_ok=True)
            (pdir / "assets" / "logo.svg").write_text(LOGO_SVG)
            (pdir / "skills" / "autoharness" / "SKILL.md").write_text(SKILL_MD)
            for child in discover_skill_dirs(ws):
                dst_skill = pdir / "skills" / child.name
                if dst_skill.exists():
                    shutil.rmtree(dst_skill)
                shutil.copytree(child, dst_skill, ignore=shutil.ignore_patterns(*FORBIDDEN_DIRS, ".DS_Store"))
            cmd, args = resolve_mcp_command()
            (pdir / "mcp_config.json").write_text(json.dumps({
                "mcpServers": {
                    "autoharness_mcp": {"command": cmd, "args": args}
                }
            }, indent=2) + "\n")
            (pdir / "plugin.json").write_text(json.dumps({
                "name": "autoharness",
                "displayName": "AutoHarness",
                "version": __version__,
                "description": "Autonomous 8-component Harness² engineering, write-locked code self-healing, and 3-layer evaluation MCP server & skill.",
                "logo": "assets/logo.svg",
                "author": {"name": "pritampanda15", "url": "https://github.com/pritampanda15"},
                "repository": "https://github.com/pritampanda15/autoharness",
                "suggestedPrompts": [
                    "Scan this repository with AutoHarness and fix any broken imports or failing tests",
                    "Synthesize dev and hidden holdout evaluation cases for this codebase",
                    "Inspect the 8-component Harness² tree and distill lessons from recent runs"
                ]
            }, indent=2) + "\n")
            written.append(str(pdir))

        elif t == "claude":
            mcp_file = ws / ".mcp.json"
            existing = json.loads(mcp_file.read_text()) if mcp_file.exists() else {}
            existing.setdefault("mcpServers", {})["autoharness_mcp"] = mcp_server_entry(ws)
            mcp_file.write_text(json.dumps(existing, indent=2) + "\n")

            cp_dir = ws / ".claude-plugin"
            cp_dir.mkdir(parents=True, exist_ok=True)
            (cp_dir / "plugin.json").write_text(json.dumps({
                "name": "autoharness",
                "version": __version__,
                "description": "AutoHarness MCP + 8-component Harness² & write-locked code self-healing skill.",
                "author": {"name": "pritampanda15"},
            }, indent=2) + "\n")
            sk_dir = ws / "skills" / "autoharness"
            sk_dir.mkdir(parents=True, exist_ok=True)
            (sk_dir / "SKILL.md").write_text(SKILL_MD)
            written.append(str(mcp_file))

        elif t == "cursor":
            cur_dir = ws / ".cursor"
            (cur_dir / "rules").mkdir(parents=True, exist_ok=True)
            mcp_file = cur_dir / "mcp.json"
            existing = json.loads(mcp_file.read_text()) if mcp_file.exists() else {}
            existing.setdefault("mcpServers", {})["autoharness_mcp"] = mcp_server_entry(ws)
            mcp_file.write_text(json.dumps(existing, indent=2) + "\n")
            (cur_dir / "rules" / "autoharness.mdc").write_text(
                "---\ndescription: AutoHarness write-locked code healing & Harness² verification\nalwaysApply: false\n---\n\n" + SKILL_MD
            )
            written.append(str(mcp_file))

        elif t == "vscode":
            vs_dir = ws / ".vscode"
            vs_dir.mkdir(parents=True, exist_ok=True)
            mcp_file = vs_dir / "mcp.json"
            existing = json.loads(mcp_file.read_text()) if mcp_file.exists() else {}
            entry = mcp_server_entry(ws)
            existing.setdefault("servers", {})["autoharness_mcp"] = {"type": "stdio", **entry}
            mcp_file.write_text(json.dumps(existing, indent=2) + "\n")
            written.append(str(mcp_file))

        elif t == "gemini":
            gm_dir = ws / ".gemini"
            gm_dir.mkdir(parents=True, exist_ok=True)
            s_file = gm_dir / "settings.json"
            existing = json.loads(s_file.read_text()) if s_file.exists() else {}
            existing.setdefault("mcpServers", {})["autoharness_mcp"] = mcp_server_entry(ws)
            s_file.write_text(json.dumps(existing, indent=2) + "\n")
            written.append(str(s_file))

        elif t == "codex":
            cx_dir = ws / ".codex"
            cx_dir.mkdir(parents=True, exist_ok=True)
            toml_file = cx_dir / "config.toml"
            entry = mcp_server_entry(ws)
            args_json = json.dumps(entry["args"])
            block = f'\n[mcp_servers.autoharness_mcp]\ncommand = "{entry["command"]}"\nargs = {args_json}\n'
            cur_txt = toml_file.read_text() if toml_file.exists() else ""
            if "[mcp_servers.autoharness_mcp]" not in cur_txt:
                toml_file.write_text((cur_txt.rstrip() + "\n" + block).lstrip())
            written.append(str(toml_file))

        else:
            raise ValueError(f"Unknown plugin target: {t!r}")

    return written


def plugin_status(workspace_root: Path, *, antigravity_plugins_dir: Path | None = None) -> dict[str, bool]:
    ws = workspace_root.resolve()
    ag_dir = (antigravity_plugins_dir or (Path.home() / ".gemini" / "config" / "plugins")) / "autoharness" / "plugin.json"
    return {
        "antigravity": ag_dir.exists(),
        "claude": (ws / ".mcp.json").exists(),
        "cursor": (ws / ".cursor" / "mcp.json").exists(),
        "vscode": (ws / ".vscode" / "mcp.json").exists(),
        "gemini": (ws / ".gemini" / "settings.json").exists(),
        "codex": (ws / ".codex" / "config.toml").exists(),
    }
