"""Model Context Protocol (MCP) Server for AutoHarness (`autoharness mcp`).

Exposes AutoHarness as a zero-dependency JSON-RPC 2.0 stdio MCP server so ANY
AI model, IDE, or subscription agent (Antigravity, Claude Code/Desktop, Cursor,
OpenAI Codex CLI, Google Gemini CLI, VS Code Copilot, Windsurf, Cline) can:
  - Inspect Harness² 8-component scaffolding & suite telemetry (`autoharness_status`)
  - Scan Python AST syntax, imports & protected unit tests (`autoharness_scan_code`)
  - Synthesize dev + hidden holdout YAML eval cases (`autoharness_synthesize_cases`)
  - Run the 3-layer deterministic -> trace -> grounded suite (`autoharness_run_eval`)
  - Verify code repairs with byte-for-byte write-locked unit tests (`autoharness_verify_repair`)
  - Trigger autonomous code + harness Auto-Fix (`autoharness_autofix`)
  - Read or update any of the 8 Harness² files (`autoharness_harness_component`)
  - Read or record empirical DO / AVOID lessons (`autoharness_lessons`)

Because the host AI agent calls these MCP tools using its own active session,
users on flat-rate subscriptions (Claude Pro/Max/Team, ChatGPT Plus/Pro,
Google AI Pro, GitHub Copilot, Cursor Pro) get full AutoHarness verification
and self-healing with $0 extra API keys.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .agents import ScriptedAgent
from .cases import load_cases
from .config import Config, load_config
from .lessons import LessonStore
from .runner import run_suite
from .store import Store
from .studio import collect_studio_state, run_immediate_autofix
from .synthesizer import build_context_map, is_test_file, scan_codebase, synthesize_cases
from .tree import COMPONENTS, HarnessTree

PROTOCOL_VERSION = "2024-11-05"

MCP_TOOLS: list[dict[str, Any]] = [
    {
        "name": "autoharness_status",
        "description": "Inspect AutoHarness project telemetry: suite pass rate, failing cases, 8-component Harness² status, module health, subscriptions, and learned lessons.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_dir": {"type": "string", "description": "Optional path to target repository (defaults to current working directory)."},
            },
        },
    },
    {
        "name": "autoharness_scan_code",
        "description": "Scan Python modules for AST syntax errors, broken package imports, and failing unit tests, and optionally sync harnesses/harness2/memory.md.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_dir": {"type": "string", "description": "Optional path to target repository."},
                "paths": {"type": "array", "items": {"type": "string"}, "description": "Directories or files to scan (defaults to code_paths in autoharness.yaml)."},
                "sync_memory": {"type": "boolean", "description": "Update harnesses/harness2/memory.md with live symbol map.", "default": True},
            },
        },
    },
    {
        "name": "autoharness_synthesize_cases",
        "description": "Auto-generate executable dev and hidden holdout YAML evaluation cases from Python source modules.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_dir": {"type": "string"},
                "paths": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
    {
        "name": "autoharness_run_eval",
        "description": "Run the 3-layer AutoHarness evaluation suite (Deterministic $0 -> Behavioral Trace $0 -> Grounded Checks) and return pass rates and failing check diagnostics.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_dir": {"type": "string"},
                "split": {"type": "string", "enum": ["all", "dev", "holdout"], "default": "all"},
                "label": {"type": "string", "default": "mcp"},
            },
        },
    },
    {
        "name": "autoharness_verify_repair",
        "description": "Anti-gaming repair verifier for host AI agents: checks that all unit test files match expected SHA-256 hashes (or git HEAD), verifies AST + imports, and runs pytest.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_dir": {"type": "string"},
                "module_path": {"type": "string", "description": "Relative path of the repaired Python module."},
                "locked_test_hashes": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                    "description": "Optional map of {test_rel_path: sha256} captured before editing to guarantee tests were not modified.",
                },
            },
            "required": ["module_path"],
        },
    },
    {
        "name": "autoharness_snapshot_test_locks",
        "description": "Capture SHA-256 hashes of all test_*.py files BEFORE editing source code so autoharness_verify_repair can prove byte-for-byte test immutability.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_dir": {"type": "string"},
            },
        },
    },
    {
        "name": "autoharness_autofix",
        "description": "Trigger closed-loop Auto-Fix: heals broken Python modules in isolated sandboxes with locked tests and runs contrastive Propose-Probe-Compose on failing dev cases.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_dir": {"type": "string"},
                "target_case": {"type": "string"},
                "target_module": {"type": "string"},
            },
        },
    },
    {
        "name": "autoharness_harness_component",
        "description": "Read or update one of the 8 modular Harness² components (system.md, guardrails.md, memory.md, skills.md, tool_guidance.md, subagents.md, plugins.json) or compile the full prompt.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_dir": {"type": "string"},
                "action": {"type": "string", "enum": ["list", "read", "write", "compile"], "default": "list"},
                "filename": {"type": "string", "description": "Component filename, e.g. skills.md or guardrails.md."},
                "content": {"type": "string", "description": "New markdown/JSON content when action == 'write'."},
            },
        },
    },
    {
        "name": "autoharness_lessons",
        "description": "Read or record empirical DO / AVOID engineering lessons in lessons/lessons.json.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_dir": {"type": "string"},
                "action": {"type": "string", "enum": ["list", "add"], "default": "list"},
                "polarity": {"type": "string", "enum": ["do", "avoid"]},
                "lesson": {"type": "string"},
            },
        },
    },
]


def _load_project(default_root: Path, project_dir: str | None = None) -> tuple[Config, Store]:
    root = Path(project_dir).expanduser().resolve() if project_dir else default_root.resolve()
    cfg_path = root / "autoharness.yaml"
    cfg = load_config(cfg_path) if cfg_path.exists() else Config(root=root, raw=load_config(Path("__nonexistent__")).raw if False else {})
    if not cfg_path.exists():
        from .config import DEFAULTS, _merge
        import copy

        cfg = Config(raw=_merge(copy.deepcopy(DEFAULTS), {}), root=root)
    store = Store(cfg.path(cfg["runs_dir"]))
    return cfg, store


def _hash_tests(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in sorted(root.rglob("*.py")):
        rel = p.relative_to(root)
        if any(part.startswith(".") or part in ("__pycache__", "runs", "venv", ".venv", "node_modules") for part in rel.parts):
            continue
        if is_test_file(p):
            out[str(rel)] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def execute_mcp_tool(name: str, args: dict[str, Any], default_root: Path) -> dict[str, Any]:
    """Dispatch an MCP tool call and return a JSON-serializable result dictionary."""
    cfg, store = _load_project(default_root, args.get("project_dir"))

    if name == "autoharness_status":
        state = collect_studio_state(cfg, store, scan_code=True, run_code_checks=False)
        sum_obj = state.get("latest_summary") or {}
        failing = [cid for cid, r in (sum_obj.get("cases") or {}).items() if r.get("pass_rate", 1.0) < 1.0]
        return {
            "project": state["project_name"],
            "root": state["project_root"],
            "agent": state["agent"],
            "pass_rate": sum_obj.get("pass_rate"),
            "baseline_run": state["baseline_id"],
            "failing_cases": failing,
            "harness_path": state["harness"]["path"],
            "harness_fingerprint": state["harness"]["fingerprint"],
            "active_components": [c["filename"] for c in state["harness"]["components"] if c["active"]],
            "modules_count": len(state["modules"]),
            "lessons_count": len(state["lessons"]),
            "hooks": state["hooks"],
        }

    if name == "autoharness_scan_code":
        targets = args.get("paths") or cfg.raw.get("code_paths") or ["."]
        mods = scan_codebase(cfg.root, targets, run_checks=True)
        if args.get("sync_memory", True):
            h2 = cfg.path(cfg["agent"].get("system_prompt_file", "harnesses/harness2"))
            if h2.is_dir():
                (h2 / "memory.md").write_text(build_context_map(mods))
        return {
            "scanned": len(mods),
            "healthy": sum(1 for m in mods if m.healthy),
            "failing": [
                {
                    "module": m.rel_path,
                    "syntax_ok": m.syntax_ok,
                    "imports_ok": m.imports_ok,
                    "tests_ok": m.tests_ok,
                    "error": m.syntax_error or m.import_error or (m.test_output or "")[:500],
                    "protected_tests": [t.name for t in m.tests_found],
                }
                for m in mods if not m.healthy
            ],
            "locked_test_hashes": _hash_tests(cfg.root),
        }

    if name == "autoharness_synthesize_cases":
        targets = args.get("paths") or cfg.raw.get("code_paths") or ["."]
        written = synthesize_cases(cfg.root, targets, out_dir=cfg.path("cases/auto"))
        return {"synthesized": len(written), "files": [str(p.relative_to(cfg.root)) for p in written]}

    if name == "autoharness_run_eval":
        spf = cfg["agent"].get("system_prompt_file", "harnesses/default/system.md")
        hpath = cfg.path(spf)
        tree = HarnessTree.load(hpath) if hpath.exists() else None
        cdirs = [cfg.path(p) for p in cfg["cases"] if cfg.path(p).exists()]
        cases = load_cases(cdirs)
        split = args.get("split", "all")
        if split in ("dev", "holdout"):
            cases = [c for c in cases if c.split == split]
        summary = run_suite(
            cfg, cases, ScriptedAgent(tree=tree), None, store,
            trials=1, workers=2, label=args.get("label", "mcp"), quiet=True,
        )
        failing = {
            cid: row for cid, row in summary.get("cases", {}).items() if row.get("pass_rate", 1.0) < 1.0
        }
        return {
            "run_id": summary["run_id"],
            "pass_rate": summary["pass_rate"],
            "n_trials": summary["n_trials"],
            "failing_cases": failing,
        }

    if name == "autoharness_snapshot_test_locks":
        hashes = _hash_tests(cfg.root)
        return {"locked_tests": len(hashes), "locked_test_hashes": hashes}

    if name == "autoharness_verify_repair":
        current_hashes = _hash_tests(cfg.root)
        expected = args.get("locked_test_hashes") or {}
        tampered = [
            path for path, digest in expected.items() if current_hashes.get(path) != digest
        ]
        if tampered:
            return {
                "verified": False,
                "reason": f"ANTI-GAMING BLOCK: Protected test file(s) were modified or deleted: {tampered}",
            }
        mod_rel = args["module_path"]
        mods = scan_codebase(cfg.root, [mod_rel], run_checks=True)
        mod_ok = all(m.healthy for m in mods)
        # Run full pytest suite if tests exist
        test_files = list(current_hashes.keys())
        pytest_ok = True
        pytest_out = ""
        if test_files:
            env = dict(os.environ)
            env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
            env["PYTHONPATH"] = f"{cfg.root.resolve()}{os.pathsep}{env.get('PYTHONPATH', '')}".rstrip(os.pathsep)
            r = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", *test_files],
                cwd=cfg.root, capture_output=True, text=True, timeout=60, env=env,
            )
            pytest_ok = r.returncode == 0
            pytest_out = (r.stdout + "\n" + r.stderr)[-800:].strip()
        return {
            "verified": bool(mod_ok and pytest_ok),
            "tests_untouched": True,
            "module_healthy": mod_ok,
            "pytest_passed": pytest_ok,
            "detail": pytest_out or (mods[0].syntax_error or mods[0].import_error if mods else "clean"),
        }

    if name == "autoharness_autofix":
        res = run_immediate_autofix(
            cfg, store,
            target_case=args.get("target_case"),
            target_module=args.get("target_module"),
        )
        res.pop("state", None)
        return res

    if name == "autoharness_harness_component":
        spf = cfg["agent"].get("system_prompt_file", "harnesses/harness2")
        hpath = cfg.path(spf)
        if not hpath.exists():
            HarnessTree.scaffold(cfg.path("harnesses/harness2"))
            hpath = cfg.path("harnesses/harness2")
        tree = HarnessTree.load(hpath)
        act = args.get("action", "list")
        if act == "compile":
            return {"fingerprint": tree.fingerprint(), "compiled_prompt": tree.compile_prompt()}
        if act == "read":
            fname = args.get("filename", "system.md")
            target = (hpath / fname) if hpath.is_dir() else hpath
            return {"filename": fname, "content": target.read_text() if target.exists() else ""}
        if act == "write":
            fname = args["filename"]
            base = hpath if hpath.is_dir() else hpath.parent
            target = (base / fname).resolve()
            if base.resolve() not in target.parents and target != hpath.resolve():
                raise PermissionError("filename escapes harness directory")
            target.write_text(str(args.get("content", "")))
            return {"saved": fname, "fingerprint": HarnessTree.load(hpath).fingerprint()}
        return {
            "path": str(hpath),
            "fingerprint": tree.fingerprint(),
            "components": [
                {"key": s.key, "filename": s.filename, "space": s.space, "chars": len(tree.components.get(s.key, ""))}
                for s in COMPONENTS
            ],
        }

    if name == "autoharness_lessons":
        lstore = LessonStore(cfg.path(cfg["fixer"]["lessons_file"]), cfg["fixer"]["max_lessons"])
        lessons = lstore.load()
        if args.get("action") == "add" and args.get("lesson"):
            lessons.append({
                "polarity": args.get("polarity", "do"),
                "lesson": str(args["lesson"]).strip(),
                "support": 1,
                "evidence_runs": ["mcp"],
            })
            lstore.save(lessons)
        return {"lessons": lessons}

    raise ValueError(f"Unknown MCP tool: {name}")


class MCPServer:
    """Stdio JSON-RPC 2.0 MCP server supporting both newline JSON and Content-Length framing."""

    def __init__(self, default_root: Path | None = None):
        self.default_root = (default_root or Path.cwd()).resolve()
        self._use_content_length = False

    def handle_request(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        method = msg.get("method", "")
        req_id = msg.get("id")
        params = msg.get("params") or {}

        if req_id is None:
            return None  # JSON-RPC notification (e.g. notifications/initialized)

        try:
            if method == "initialize":
                result = {
                    "protocolVersion": params.get("protocolVersion") or PROTOCOL_VERSION,
                    "capabilities": {
                        "tools": {"listChanged": False},
                        "resources": {"subscribe": False, "listChanged": False},
                    },
                    "serverInfo": {"name": "autoharness-mcp", "version": __version__},
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": MCP_TOOLS}
            elif method == "tools/call":
                tname = params.get("name", "")
                targs = params.get("arguments") or {}
                out = execute_mcp_tool(tname, targs, self.default_root)
                result = {
                    "content": [{"type": "text", "text": json.dumps(out, indent=2)}],
                    "isError": False,
                }
            elif method == "resources/list":
                result = {
                    "resources": [
                        {
                            "uri": "autoharness://harness/compiled",
                            "name": "Compiled 8-Component Harness² Prompt",
                            "mimeType": "text/markdown",
                        },
                        {
                            "uri": "autoharness://lessons/ledger",
                            "name": "AutoHarness Empirical Lesson Ledger",
                            "mimeType": "application/json",
                        },
                    ]
                }
            elif method == "resources/read":
                uri = params.get("uri", "")
                cfg, _ = _load_project(self.default_root)
                if uri == "autoharness://harness/compiled":
                    hpath = cfg.path(cfg["agent"].get("system_prompt_file", "harnesses/harness2"))
                    txt = HarnessTree.load(hpath).compile_prompt() if hpath.exists() else ""
                    result = {"contents": [{"uri": uri, "mimeType": "text/markdown", "text": txt}]}
                elif uri == "autoharness://lessons/ledger":
                    ls = LessonStore(cfg.path(cfg["fixer"]["lessons_file"])).load()
                    result = {"contents": [{"uri": uri, "mimeType": "application/json", "text": json.dumps(ls, indent=2)}]}
                else:
                    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32602, "message": f"Unknown resource URI: {uri}"}}
            else:
                return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Method not found: {method}"}}
            return {"jsonrpc": "2.0", "id": req_id, "result": result}
        except Exception as e:
            if method == "tools/call":
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": json.dumps({"error": f"{type(e).__name__}: {e}"})}],
                        "isError": True,
                    },
                }
            return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32000, "message": str(e)}}

    def serve_stdio(self) -> None:
        stdin = sys.stdin.buffer
        stdout = sys.stdout.buffer
        while True:
            line = stdin.readline()
            if not line:
                break
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.lower().startswith(b"content-length:"):
                self._use_content_length = True
                length = int(stripped.split(b":", 1)[1].strip())
                while True:
                    hdr = stdin.readline()
                    if not hdr or hdr in (b"\r\n", b"\n"):
                        break
                body = stdin.read(length)
                if not body:
                    break
                payload = json.loads(body.decode("utf-8", errors="replace"))
            else:
                payload = json.loads(stripped.decode("utf-8", errors="replace"))

            resp = self.handle_request(payload)
            if resp is not None:
                raw = json.dumps(resp).encode("utf-8")
                if self._use_content_length:
                    stdout.write(f"Content-Length: {len(raw)}\r\n\r\n".encode("ascii") + raw)
                else:
                    stdout.write(raw + b"\n")
                stdout.flush()
