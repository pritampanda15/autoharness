"""Model-agnostic agent substrates + Harness² runtime integration.

Substrates (`agent.type`):
- `claude`:    Anthropic Messages API tool-use loop with sandboxed tools + Harness² middleware.
- `gemini`:    Google Gemini API tool-use loop (`GEMINI_API_KEY`, zero extra dependencies).
- `openai`:    OpenAI / OpenRouter / Ollama compatible chat-completions tool loop (`OPENAI_API_KEY`).
- `command`:   Any CLI agent (Claude Code, Codex, OpenCode, custom script) inside the sandbox.
- `scripted`:  Replays a case's `mock:` block offline for deterministic smoke testing.

When `agent.system_prompt_file` points to an 8-component Harness² directory, `HarnessTree`
compiles all active Context, Interface, and Delegation components, stages `.harness/scripts/*`
into the workspace, and enforces deterministic `plugins.json` pre/post-tool middleware.
"""

from __future__ import annotations

import json
import os
import shlex
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Protocol

from .cases import Case
from .config import claude_cli
from .sandbox import Sandbox
from .trace import ToolCall, Trace
from .tree import HarnessTree, MiddlewareState


class Agent(Protocol):
    def run(self, case: Case, sandbox: Sandbox, trial: int) -> Trace: ...


# --------------------------------------------------------------------------- tools

TOOL_DEFS: list[dict[str, Any]] = [
    {
        "name": "list_files",
        "description": "List files under a directory of the workspace (recursive, max 500 entries).",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Directory, relative to workspace root. Use '.' for root."}},
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "read_file",
        "description": "Read a UTF-8 text file from the workspace.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "write_file",
        "description": "Create or overwrite a text file in the workspace with the full new contents.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
            "additionalProperties": False,
        },
    },
    {
        "name": "run_bash",
        "description": "Run a shell command with the workspace as the working directory. Returns exit code, stdout and stderr.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
            "additionalProperties": False,
        },
    },
]


def execute_tool(
    sandbox: Sandbox,
    name: str,
    args: dict[str, Any],
    bash_timeout: float = 60,
    tree: HarnessTree | None = None,
    mw_state: MiddlewareState | None = None,
) -> tuple[str, bool]:
    """Run one tool inside the sandbox, applying Harness² pre/post middleware plugins when present."""
    if tree is not None and mw_state is not None:
        blocked = tree.pre_tool_hook(name, args, mw_state)
        if blocked:
            return blocked, True
    try:
        if name == "list_files":
            root = sandbox.resolve(args.get("path", "."))
            entries = []
            for p in sorted(root.rglob("*")):
                if any(part.startswith(".") for part in p.relative_to(sandbox.dir).parts):
                    continue
                entries.append(str(p.relative_to(sandbox.dir)) + ("/" if p.is_dir() else ""))
                if len(entries) >= 500:
                    entries.append("…(truncated)")
                    break
            out, is_err = ("\n".join(entries) or "(empty)"), False
        elif name == "read_file":
            out, is_err = sandbox.resolve(args["path"]).read_text(), False
        elif name == "write_file":
            p = sandbox.resolve(args["path"])
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(args["content"])
            out, is_err = f"wrote {len(args['content'])} chars to {args['path']}", False
        elif name == "run_bash":
            r = sandbox.run(args["command"], timeout=bash_timeout)
            out = f"exit={r.returncode}\n--- stdout ---\n{r.stdout[-8000:]}\n--- stderr ---\n{r.stderr[-4000:]}"
            is_err = r.returncode != 0
        else:
            return f"unknown tool: {name}", True
    except Exception as e:  # tool failures are data for the agent, not crashes for the runner
        return f"{type(e).__name__}: {e}", True

    if tree is not None and mw_state is not None:
        out = tree.post_tool_hook(name, args, out, is_err, mw_state)
    return out, is_err


# --------------------------------------------------------------------------- Claude API agent


class ClaudeAgent:
    def __init__(self, cfg: dict[str, Any], system_prompt: str, client=None, tree: HarnessTree | None = None):
        import anthropic

        self.cfg = cfg
        self.system_prompt = system_prompt
        self.client = client or anthropic.Anthropic()
        self.tree = tree

    def run(self, case: Case, sandbox: Sandbox, trial: int) -> Trace:
        import anthropic

        if self.tree:
            self.tree.stage_into_sandbox(sandbox)
        mw_state = MiddlewareState()
        trace = Trace(case_id=case.id, trial=trial, workdir=str(sandbox.dir))
        messages: list[dict[str, Any]] = [{"role": "user", "content": case.prompt}]
        start = time.time()
        try:
            for _ in range(self.cfg["max_turns"]):
                resp = self.client.messages.create(
                    model=self.cfg["model"],
                    max_tokens=self.cfg["max_tokens"],
                    system=self.system_prompt,
                    tools=TOOL_DEFS,
                    output_config={"effort": self.cfg["effort"]},
                    messages=messages,
                )
                trace.turns += 1
                trace.input_tokens += resp.usage.input_tokens
                trace.output_tokens += resp.usage.output_tokens
                trace.stop_reason = resp.stop_reason or ""
                messages.append({"role": "assistant", "content": resp.content})

                text = "".join(b.text for b in resp.content if b.type == "text")
                if text:
                    trace.final_output = text

                if resp.stop_reason == "refusal":
                    cat = getattr(resp.stop_details, "category", None) if resp.stop_details else None
                    trace.error = f"refusal ({cat})"
                    break
                if resp.stop_reason == "pause_turn":
                    continue
                tool_uses = [b for b in resp.content if b.type == "tool_use"]
                if not tool_uses:
                    break

                results = []
                for tu in tool_uses:
                    out, err = execute_tool(
                        sandbox, tu.name, tu.input, self.cfg["bash_timeout_s"], tree=self.tree, mw_state=mw_state
                    )
                    trace.tool_calls.append(ToolCall(tu.name, dict(tu.input), out, err))
                    results.append({"type": "tool_result", "tool_use_id": tu.id, "content": out, "is_error": err})
                messages.append({"role": "user", "content": results})
            else:
                trace.error = f"hit max_turns={self.cfg['max_turns']}"
        except anthropic.APIStatusError as e:
            trace.error = f"API {e.status_code}: {e.message}"
        except anthropic.APIConnectionError as e:
            trace.error = f"connection error: {e}"
        trace.duration_s = round(time.time() - start, 2)
        trace.messages = _serialisable(messages)
        return trace


def _serialisable(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for m in messages:
        c = m["content"]
        if isinstance(c, list):
            c = [b.model_dump(exclude_none=True) if hasattr(b, "model_dump") else b for b in c]
        out.append({"role": m["role"], "content": c})
    return out


# --------------------------------------------------------------------------- Gemini API substrate (stdlib HTTP)


class GeminiAgent:
    """Google Gemini API tool-use substrate (`GEMINI_API_KEY`), using stdlib HTTPS."""

    def __init__(self, cfg: dict[str, Any], system_prompt: str, tree: HarnessTree | None = None):
        self.cfg = cfg
        self.system_prompt = system_prompt
        self.tree = tree
        self.api_key = cfg.get("api_key") or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or ""

    def run(self, case: Case, sandbox: Sandbox, trial: int) -> Trace:
        if self.tree:
            self.tree.stage_into_sandbox(sandbox)
        mw_state = MiddlewareState()
        trace = Trace(case_id=case.id, trial=trial, workdir=str(sandbox.dir))
        if not self.api_key:
            from .providers import find_cli

            if find_cli("gemini"):
                return GeminiCliAgent(self.cfg, self.system_prompt, tree=self.tree).run(case, sandbox, trial)
            trace.error = "missing GEMINI_API_KEY / GOOGLE_API_KEY (and gemini CLI not found)"
            return trace

        model = self.cfg.get("model", "gemini-2.5-pro")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={self.api_key}"
        decls = [
            {"name": t["name"], "description": t["description"], "parameters": {
                k: v for k, v in t["input_schema"].items() if k != "additionalProperties"
            }}
            for t in TOOL_DEFS
        ]
        contents: list[dict[str, Any]] = [{"role": "user", "parts": [{"text": case.prompt}]}]
        start = time.time()
        try:
            for _ in range(self.cfg.get("max_turns", 20)):
                payload = {
                    "systemInstruction": {"parts": [{"text": self.system_prompt}]},
                    "contents": contents,
                    "tools": [{"functionDeclarations": decls}],
                }
                req = urllib.request.Request(
                    url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
                )
                with urllib.request.urlopen(req, timeout=120) as resp:
                    data = json.loads(resp.read().decode())
                trace.turns += 1
                usage = data.get("usageMetadata") or {}
                trace.input_tokens += int(usage.get("promptTokenCount", 0))
                trace.output_tokens += int(usage.get("candidatesTokenCount", 0))
                cand = (data.get("candidates") or [{}])[0]
                parts = (cand.get("content") or {}).get("parts") or []
                contents.append({"role": "model", "parts": parts})

                texts = [p["text"] for p in parts if "text" in p]
                if texts:
                    trace.final_output = "".join(texts)
                fcalls = [p["functionCall"] for p in parts if "functionCall" in p]
                if not fcalls:
                    trace.stop_reason = cand.get("finishReason", "STOP")
                    break

                resp_parts = []
                for fc in fcalls:
                    fname = fc["name"]
                    fargs = fc.get("args") or {}
                    out, err = execute_tool(
                        sandbox, fname, fargs, self.cfg.get("bash_timeout_s", 60), tree=self.tree, mw_state=mw_state
                    )
                    trace.tool_calls.append(ToolCall(fname, dict(fargs), out, err))
                    resp_parts.append({"functionResponse": {"name": fname, "response": {"result": out, "error": err}}})
                contents.append({"role": "user", "parts": resp_parts})
            else:
                trace.error = f"hit max_turns={self.cfg.get('max_turns', 20)}"
        except urllib.error.HTTPError as e:
            trace.error = f"Gemini HTTP {e.code}: {e.read().decode(errors='replace')[:400]}"
        except Exception as e:
            trace.error = f"Gemini error: {type(e).__name__}: {e}"
        trace.duration_s = round(time.time() - start, 2)
        return trace


# --------------------------------------------------------------------------- OpenAI-compatible substrate (stdlib HTTP)


class OpenAIAgent:
    """OpenAI / vLLM / LM Studio / OpenRouter `/v1/chat/completions` tool-use substrate."""

    DEFAULT_URLS = {
        "openai": "https://api.openai.com/v1",
        "vllm": "http://127.0.0.1:8000/v1",
        "lmstudio": "http://127.0.0.1:1234/v1",
        "openrouter": "https://openrouter.ai/api/v1",
    }

    def __init__(self, cfg: dict[str, Any], system_prompt: str, tree: HarnessTree | None = None):
        self.cfg = cfg
        self.system_prompt = system_prompt
        self.tree = tree
        kind = cfg.get("type", "openai")
        self.api_key = (
            cfg.get("api_key")
            or (os.environ.get("OPENROUTER_API_KEY") if kind == "openrouter" else None)
            or os.environ.get("OPENAI_API_KEY")
            or "local-oss"
        )
        default_url = self.DEFAULT_URLS.get(kind, "https://api.openai.com/v1")
        self.base_url = (cfg.get("base_url") or os.environ.get("OPENAI_BASE_URL") or default_url).rstrip("/")

    def run(self, case: Case, sandbox: Sandbox, trial: int) -> Trace:
        if self.tree:
            self.tree.stage_into_sandbox(sandbox)
        mw_state = MiddlewareState()
        trace = Trace(case_id=case.id, trial=trial, workdir=str(sandbox.dir))
        tools = [
            {"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]}}
            for t in TOOL_DEFS
        ]
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": case.prompt},
        ]
        start = time.time()
        try:
            for _ in range(self.cfg.get("max_turns", 20)):
                payload = {"model": self.cfg.get("model", "gpt-4.1"), "messages": messages, "tools": tools}
                req = urllib.request.Request(
                    f"{self.base_url}/chat/completions",
                    data=json.dumps(payload).encode(),
                    headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"},
                )
                with urllib.request.urlopen(req, timeout=120) as resp:
                    data = json.loads(resp.read().decode())
                trace.turns += 1
                usage = data.get("usage") or {}
                trace.input_tokens += int(usage.get("prompt_tokens", 0))
                trace.output_tokens += int(usage.get("completion_tokens", 0))
                choice = (data.get("choices") or [{}])[0]
                msg = choice.get("message") or {}
                messages.append(msg)
                if msg.get("content"):
                    trace.final_output = _strip_think(msg["content"])
                tcalls = msg.get("tool_calls") or []
                if not tcalls and msg.get("content"):
                    oss_calls = _extract_oss_tool_calls(msg["content"])
                    if oss_calls:
                        for fname, fargs in oss_calls:
                            out, err = execute_tool(
                                sandbox, fname, fargs, self.cfg.get("bash_timeout_s", 60), tree=self.tree, mw_state=mw_state
                            )
                            trace.tool_calls.append(ToolCall(fname, dict(fargs), out, err))
                            messages.append({"role": "user", "content": f"<tool_response name=\"{fname}\">\n{out}\n</tool_response>"})
                        continue
                if not tcalls:
                    trace.stop_reason = choice.get("finish_reason", "stop")
                    break
                for tc in tcalls:
                    fn = tc.get("function") or {}
                    fname = fn.get("name", "")
                    try:
                        fargs = json.loads(fn.get("arguments") or "{}")
                    except json.JSONDecodeError:
                        fargs = {}
                    out, err = execute_tool(
                        sandbox, fname, fargs, self.cfg.get("bash_timeout_s", 60), tree=self.tree, mw_state=mw_state
                    )
                    trace.tool_calls.append(ToolCall(fname, dict(fargs), out, err))
                    messages.append({"role": "tool", "tool_call_id": tc.get("id", ""), "content": out})
            else:
                trace.error = f"hit max_turns={self.cfg.get('max_turns', 20)}"
        except urllib.error.HTTPError as e:
            trace.error = f"OpenAI HTTP {e.code}: {e.read().decode(errors='replace')[:400]}"
        except Exception as e:
            trace.error = f"OpenAI error: {type(e).__name__}: {e}"
        trace.duration_s = round(time.time() - start, 2)
        return trace


# --------------------------------------------------------------------------- external command agent


class CommandAgent:
    """Run any agent CLI inside the sandbox.

    `command` placeholders (all shell-quoted): {prompt}, {workdir}, {system_prompt_file}.
    If `system_prompt_file` points to an 8-component Harness² directory, its compiled prompt
    and helper scripts are staged into `.harness/` inside the sandbox automatically.
    """

    def __init__(self, cfg: dict[str, Any], root=None):
        self.cfg = cfg
        spf = cfg.get("system_prompt_file")
        self._raw_spf = (Path(root) / spf).resolve() if (root and spf) else (Path(spf) if spf else None)
        self.tree = HarnessTree.load(self._raw_spf) if (self._raw_spf and self._raw_spf.exists()) else None
        self.system_prompt_file = str(self._raw_spf) if self._raw_spf else ""

    def run(self, case: Case, sandbox: Sandbox, trial: int) -> Trace:
        trace = Trace(case_id=case.id, trial=trial, workdir=str(sandbox.dir))
        spf_for_cmd = self.system_prompt_file
        if self.tree is not None:
            self.tree.stage_into_sandbox(sandbox)
            if self.tree.is_directory:
                compiled = sandbox.dir / ".harness" / "compiled_system.md"
                compiled.parent.mkdir(parents=True, exist_ok=True)
                compiled.write_text(self.tree.compile_prompt())
                spf_for_cmd = str(compiled)

        cmd = self.cfg["command"].format(
            prompt=shlex.quote(case.prompt),
            workdir=shlex.quote(str(sandbox.dir)),
            system_prompt_file=shlex.quote(spf_for_cmd),
            claude=shlex.quote(claude_cli(self.cfg.get("cli"))),
        )
        from .providers import SUBSCRIPTION_STRIP_KEYS

        sub_mode = self.cfg.get("auth_mode") == "subscription"
        start = time.time()
        r = sandbox.run(
            cmd,
            timeout=self.cfg.get("timeout_s", 900),
            env={"AUTOHARNESS_PROMPT": case.prompt},
            unset_env=SUBSCRIPTION_STRIP_KEYS if sub_mode else (),
        )
        trace.duration_s = round(time.time() - start, 2)
        trace.final_output = r.stdout
        if r.returncode != 0:
            trace.error = f"exit {r.returncode}: {(r.stderr or r.stdout)[-2000:]}"
        _parse_stream_json(r.stdout, trace)
        if sub_mode:
            trace.cost_usd = 0.0
        return trace


TOOL_ALIASES = {
    "Read": "read_file", "Write": "write_file", "Edit": "write_file", "MultiEdit": "write_file",
    "NotebookEdit": "write_file", "Bash": "run_bash", "Glob": "list_files", "LS": "list_files",
}
INPUT_ALIASES = {"file_path": "path", "notebook_path": "path"}


def _canonical(name: str, inp: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    inp = {INPUT_ALIASES.get(k, k): v for k, v in (inp or {}).items()}
    return TOOL_ALIASES.get(name, name), inp


def _parse_stream_json(stdout: str, trace: Trace) -> None:
    """Best-effort parse of Claude Code `--output-format stream-json --verbose` output."""
    pending: dict[str, ToolCall] = {}
    seen_msgs: set[str] = set()
    parsed_any = False
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        parsed_any = True
        msg = ev.get("message") or {}
        content = msg.get("content") if isinstance(msg.get("content"), list) else []
        if ev.get("type") == "assistant":
            mid = msg.get("id") or str(len(seen_msgs))
            if mid not in seen_msgs:
                seen_msgs.add(mid)
                trace.turns += 1
                usage = msg.get("usage") or {}
                trace.input_tokens += usage.get("input_tokens", 0) + usage.get("cache_read_input_tokens", 0) \
                    + usage.get("cache_creation_input_tokens", 0)
                trace.output_tokens += usage.get("output_tokens", 0)
            for b in content:
                if b.get("type") == "tool_use":
                    name, inp = _canonical(b.get("name", "?"), b.get("input") or {})
                    tc = ToolCall(name, inp)
                    pending[b.get("id", "")] = tc
                    trace.tool_calls.append(tc)
                elif b.get("type") == "text" and b.get("text"):
                    trace.final_output = b["text"]
        elif ev.get("type") == "user":
            for b in content:
                if b.get("type") == "tool_result" and b.get("tool_use_id") in pending:
                    tc = pending[b["tool_use_id"]]
                    c = b.get("content")
                    if isinstance(c, list):
                        c = "\n".join(x.get("text", "") for x in c if isinstance(x, dict))
                    tc.output = (c or "")[:8000]
                    tc.is_error = bool(b.get("is_error"))
        elif ev.get("type") == "result":
            if ev.get("result"):
                trace.final_output = ev["result"]
            trace.stop_reason = ev.get("subtype", "")
            trace.cost_usd = float(ev.get("total_cost_usd") or 0.0)
            if ev.get("num_turns"):
                trace.turns = ev["num_turns"]
            if ev.get("is_error") and not trace.error:
                trace.error = f"agent reported error: {ev.get('subtype')}"
    if not parsed_any:
        return
    if trace.final_output == stdout:
        trace.final_output = ""


# --------------------------------------------------------------------------- scripted (offline) agent


class ScriptedAgent:
    """Replays `mock: {actions: [{tool, input}], output, turns}` from the case."""

    def __init__(self, tree: HarnessTree | None = None):
        self.tree = tree

    def run(self, case: Case, sandbox: Sandbox, trial: int) -> Trace:
        if self.tree:
            self.tree.stage_into_sandbox(sandbox)
        mw_state = MiddlewareState()
        mock = case.mock or {}
        trace = Trace(case_id=case.id, trial=trial, workdir=str(sandbox.dir))
        for a in mock.get("actions", []):
            out, err = execute_tool(sandbox, a["tool"], a.get("input", {}), tree=self.tree, mw_state=mw_state)
            trace.tool_calls.append(ToolCall(a["tool"], a.get("input", {}), out, err))
        trace.final_output = mock.get("output", "")
        trace.turns = mock.get("turns", len(trace.tool_calls) + 1)
        trace.input_tokens = mock.get("input_tokens", 1000 * trace.turns)
        trace.output_tokens = mock.get("output_tokens", 200 * trace.turns)
        trace.stop_reason = "end_turn"
        return trace


# --------------------------------------------------------------------------- Open-Source Ollama, Codex CLI, Gemini CLI & Auto-Failover


OSS_TOOL_GUIDE = """
If your runtime does not emit native tool_calls, you may invoke tools by outputting:
<tool_call>{"name": "read_file", "arguments": {"path": "example.py"}}</tool_call>
Available tools: read_file(path), write_file(path, content), list_files(path?), run_bash(command).
"""


def _strip_think(text: str) -> str:
    import re

    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL).strip()


def _extract_oss_tool_calls(text: str) -> list[tuple[str, dict[str, Any]]]:
    import re

    calls: list[tuple[str, dict[str, Any]]] = []
    for m in re.finditer(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", text or "", flags=re.DOTALL):
        try:
            obj = json.loads(m.group(1))
            name = obj.get("name") or obj.get("tool")
            args = obj.get("arguments") or obj.get("input") or {}
            if isinstance(name, str) and isinstance(args, dict):
                calls.append((name, args))
        except json.JSONDecodeError:
            continue
    return calls


class OllamaAgent:
    """Native local/remote Ollama open-source substrate (Qwen2.5-Coder, DeepSeek-R1, Llama 3.3, Gemma 3, Devstral)."""

    def __init__(self, cfg: dict[str, Any], system_prompt: str, tree: HarnessTree | None = None):
        self.cfg = cfg
        self.system_prompt = (system_prompt + "\n\n" + OSS_TOOL_GUIDE).strip()
        self.tree = tree
        base = (cfg.get("base_url") or os.environ.get("OLLAMA_HOST") or "http://127.0.0.1:11434").rstrip("/")
        self.base_url = base[:-3] if base.endswith("/v1") else base

    def run(self, case: Case, sandbox: Sandbox, trial: int) -> Trace:
        if self.tree:
            self.tree.stage_into_sandbox(sandbox)
        mw_state = MiddlewareState()
        trace = Trace(case_id=case.id, trial=trial, workdir=str(sandbox.dir))
        model = self.cfg.get("model") or "qwen2.5-coder:14b"
        tools = [
            {"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]}}
            for t in TOOL_DEFS
        ]
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": case.prompt},
        ]
        use_native_tools = True
        start = time.time()
        try:
            for _ in range(self.cfg.get("max_turns", 20)):
                payload: dict[str, Any] = {"model": model, "stream": False, "messages": messages}
                if use_native_tools:
                    payload["tools"] = tools
                req = urllib.request.Request(
                    f"{self.base_url}/api/chat",
                    data=json.dumps(payload).encode(),
                    headers={"Content-Type": "application/json"},
                )
                try:
                    with urllib.request.urlopen(req, timeout=180) as resp:
                        data = json.loads(resp.read().decode())
                except urllib.error.HTTPError as e:
                    err_body = e.read().decode(errors="replace")
                    if use_native_tools and "does not support tools" in err_body.lower():
                        use_native_tools = False
                        continue
                    trace.error = f"Ollama HTTP {e.code}: {err_body[:400]}"
                    break
                trace.turns += 1
                trace.input_tokens += int(data.get("prompt_eval_count", 0))
                trace.output_tokens += int(data.get("eval_count", 0))
                msg = data.get("message") or {}
                messages.append(msg)
                content = msg.get("content") or ""
                if content:
                    trace.final_output = _strip_think(content)

                tcalls = msg.get("tool_calls") or []
                parsed_calls: list[tuple[str, dict[str, Any]]] = []
                for tc in tcalls:
                    fn = tc.get("function") or {}
                    fname = fn.get("name", "")
                    fargs = fn.get("arguments") or {}
                    if isinstance(fargs, str):
                        try:
                            fargs = json.loads(fargs)
                        except json.JSONDecodeError:
                            fargs = {}
                    if fname:
                        parsed_calls.append((fname, fargs))
                if not parsed_calls and content:
                    parsed_calls = _extract_oss_tool_calls(content)

                if not parsed_calls:
                    trace.stop_reason = "stop"
                    break
                for fname, fargs in parsed_calls:
                    out, err = execute_tool(
                        sandbox, fname, fargs, self.cfg.get("bash_timeout_s", 60), tree=self.tree, mw_state=mw_state
                    )
                    trace.tool_calls.append(ToolCall(fname, dict(fargs), out, err))
                    if use_native_tools and tcalls:
                        messages.append({"role": "tool", "content": out})
                    else:
                        messages.append({"role": "user", "content": f"<tool_response name=\"{fname}\">\n{out}\n</tool_response>"})
            else:
                trace.error = f"hit max_turns={self.cfg.get('max_turns', 20)}"
        except Exception as e:
            trace.error = f"Ollama ({self.base_url}) error: {type(e).__name__}: {e}"
        trace.duration_s = round(time.time() - start, 2)
        return trace


class CodexAgent:
    """OpenAI Codex CLI (`codex exec --full-auto --json`) with automatic OpenAI API fallback."""

    def __init__(self, cfg: dict[str, Any], system_prompt: str, tree: HarnessTree | None = None):
        self.cfg = cfg
        self.system_prompt = system_prompt
        self.tree = tree

    def run(self, case: Case, sandbox: Sandbox, trial: int) -> Trace:
        from .providers import SUBSCRIPTION_STRIP_KEYS, find_cli

        sub_mode = self.cfg.get("auth_mode") == "subscription"
        bin_path = find_cli("codex", self.cfg.get("cli"))
        if not bin_path:
            if not sub_mode and (os.environ.get("OPENAI_API_KEY") or self.cfg.get("api_key")):
                fallback_cfg = {**self.cfg, "type": "openai", "model": self.cfg.get("model", "gpt-4.1")}
                return OpenAIAgent(fallback_cfg, self.system_prompt, tree=self.tree).run(case, sandbox, trial)
            return Trace(case_id=case.id, trial=trial, workdir=str(sandbox.dir), error="codex CLI not installed (run `npm i -g @openai/codex && codex login`)")

        if self.tree:
            self.tree.stage_into_sandbox(sandbox)
        trace = Trace(case_id=case.id, trial=trial, workdir=str(sandbox.dir))
        full_prompt = f"{self.system_prompt}\n\nTask:\n{case.prompt}".strip() if self.system_prompt else case.prompt
        model_arg = f"-m {shlex.quote(str(self.cfg['model']))}" if self.cfg.get("model") and self.cfg["model"] not in ("auto", "opus", "sonnet") else ""
        cmd = f"{shlex.quote(bin_path)} exec --full-auto --skip-git-repo-check --json {model_arg} {shlex.quote(full_prompt)}"
        start = time.time()
        r = sandbox.run(cmd, timeout=self.cfg.get("timeout_s", 600), unset_env=SUBSCRIPTION_STRIP_KEYS if sub_mode else ())
        trace.duration_s = round(time.time() - start, 2)
        trace.final_output = r.stdout.strip()
        if r.returncode != 0:
            trace.error = f"codex exit {r.returncode}: {(r.stderr or r.stdout)[-1500:]}"
        _parse_codex_jsonl(r.stdout, trace)
        return trace


def _parse_codex_jsonl(stdout: str, trace: Trace) -> None:
    """Parse `codex exec --json` JSONL events into canonical AutoHarness ToolCalls."""
    parsed = False
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        parsed = True
        item = ev.get("item") or {}
        itype = item.get("type") or ev.get("type", "")
        if itype in ("command_execution", "shell"):
            cmd = item.get("command") or ev.get("command", "")
            out = item.get("aggregated_output") or item.get("output") or ""
            exit_c = item.get("exit_code", 0)
            if ev.get("type", "").endswith("completed") or "item" not in ev:
                trace.tool_calls.append(ToolCall("run_bash", {"command": cmd}, str(out)[:4000], bool(exit_c)))
                trace.turns += 1
        elif itype == "file_change":
            for ch in item.get("changes") or []:
                p = ch.get("path", "")
                if p:
                    trace.tool_calls.append(ToolCall("write_file", {"path": p}, ch.get("kind", "modified"), False))
        elif itype in ("agent_message", "message") and (item.get("text") or ev.get("text")):
            trace.final_output = item.get("text") or ev.get("text")
            trace.turns = max(1, trace.turns)
        if ev.get("usage"):
            u = ev["usage"]
            trace.input_tokens += int(u.get("input_tokens", 0))
            trace.output_tokens += int(u.get("output_tokens", 0))
    if parsed and trace.turns == 0:
        trace.turns = max(1, len(trace.tool_calls) + 1)


class GeminiCliAgent:
    """Google Gemini CLI (`gemini -p ... --yolo -o json`) substrate."""

    def __init__(self, cfg: dict[str, Any], system_prompt: str, tree: HarnessTree | None = None):
        self.cfg = cfg
        self.system_prompt = system_prompt
        self.tree = tree

    def run(self, case: Case, sandbox: Sandbox, trial: int) -> Trace:
        from .providers import SUBSCRIPTION_STRIP_KEYS, find_cli

        sub_mode = self.cfg.get("auth_mode") == "subscription"
        bin_path = find_cli("gemini", self.cfg.get("cli"))
        if not bin_path:
            if not sub_mode:
                return GeminiAgent(self.cfg, self.system_prompt, tree=self.tree).run(case, sandbox, trial)
            return Trace(case_id=case.id, trial=trial, workdir=str(sandbox.dir), error="gemini CLI not installed (run `npm i -g @google/gemini-cli && gemini`)")
        if self.tree:
            self.tree.stage_into_sandbox(sandbox)
        trace = Trace(case_id=case.id, trial=trial, workdir=str(sandbox.dir))
        full_prompt = f"{self.system_prompt}\n\nTask:\n{case.prompt}".strip() if self.system_prompt else case.prompt
        model_arg = f"-m {shlex.quote(str(self.cfg['model']))}" if self.cfg.get("model") and self.cfg["model"] not in ("auto", "opus", "sonnet") else ""
        cmd = f"{shlex.quote(bin_path)} -p {shlex.quote(full_prompt)} --yolo -o json {model_arg}"
        start = time.time()
        r = sandbox.run(cmd, timeout=self.cfg.get("timeout_s", 600), unset_env=SUBSCRIPTION_STRIP_KEYS if sub_mode else ())
        trace.duration_s = round(time.time() - start, 2)
        trace.turns = 1
        trace.final_output = r.stdout.strip()
        try:
            obj = json.loads(r.stdout)
            if isinstance(obj, dict):
                if isinstance(obj.get("response"), str):
                    trace.final_output = obj["response"]
                stats = (obj.get("stats") or {}).get("models") or {}
                for mstat in stats.values():
                    toks = mstat.get("tokens") or {}
                    trace.input_tokens += int(toks.get("prompt", 0))
                    trace.output_tokens += int(toks.get("candidates", 0))
        except json.JSONDecodeError:
            pass
        if r.returncode != 0:
            trace.error = f"gemini cli exit {r.returncode}: {(r.stderr or r.stdout)[-1500:]}"
        return trace


class CopilotCliAgent:
    """GitHub Copilot subscription CLI (`copilot -p ... --allow-all-tools -s`) substrate."""

    def __init__(self, cfg: dict[str, Any], system_prompt: str, tree: HarnessTree | None = None):
        self.cfg = cfg
        self.system_prompt = system_prompt
        self.tree = tree

    def run(self, case: Case, sandbox: Sandbox, trial: int) -> Trace:
        from .providers import SUBSCRIPTION_STRIP_KEYS, find_cli

        bin_path = find_cli("copilot", self.cfg.get("cli"))
        if not bin_path:
            return Trace(
                case_id=case.id,
                trial=trial,
                workdir=str(sandbox.dir),
                error="GitHub Copilot CLI (`copilot`) not installed (run `npm i -g @github/copilot && gh auth login`)",
            )
        if self.tree:
            self.tree.stage_into_sandbox(sandbox)
        trace = Trace(case_id=case.id, trial=trial, workdir=str(sandbox.dir))
        full_prompt = f"{self.system_prompt}\n\nTask:\n{case.prompt}".strip() if self.system_prompt else case.prompt
        model_arg = f"--model {shlex.quote(str(self.cfg['model']))}" if self.cfg.get("model") and self.cfg["model"] not in ("auto", "opus", "sonnet") else ""
        cmd = f"{shlex.quote(bin_path)} -p {shlex.quote(full_prompt)} --allow-all-tools -s {model_arg}"
        sub_mode = self.cfg.get("auth_mode") == "subscription"
        start = time.time()
        r = sandbox.run(cmd, timeout=self.cfg.get("timeout_s", 600), unset_env=SUBSCRIPTION_STRIP_KEYS if sub_mode else ())
        trace.duration_s = round(time.time() - start, 2)
        trace.turns = 1
        trace.final_output = r.stdout.strip()
        if r.returncode != 0:
            trace.error = f"copilot cli exit {r.returncode}: {(r.stderr or r.stdout)[-1500:]}"
        return trace


class AutoRouterAgent:
    """Automatic multi-model router: tries available local OSS (Ollama/vLLM), Subscriptions (Gemini, Codex, Copilot, Claude), and APIs."""

    def __init__(self, cfg: dict[str, Any], system_prompt: str, root=None, tree: HarnessTree | None = None):
        self.cfg = cfg
        self.system_prompt = system_prompt
        self.root = root
        self.tree = tree

    def _candidates(self) -> list[Agent]:
        from .providers import discover_providers

        info = discover_providers()
        sub_only = self.cfg.get("auth_mode") == "subscription"
        sub_cfg = {**self.cfg, "auth_mode": "subscription"} if sub_only else self.cfg
        chain: list[Agent] = []
        if info["local_servers"]["ollama"]["online"]:
            m = info["local_servers"]["ollama"]["models"][0]
            chain.append(OllamaAgent({**self.cfg, "model": m, "base_url": info["local_servers"]["ollama"]["url"]}, self.system_prompt, tree=self.tree))
        if info["local_servers"]["vllm"]["online"]:
            m = info["local_servers"]["vllm"]["models"][0]
            chain.append(OpenAIAgent({**self.cfg, "type": "vllm", "model": m, "base_url": "http://127.0.0.1:8000/v1"}, self.system_prompt, tree=self.tree))
        if info["local_servers"]["lmstudio"]["online"]:
            m = info["local_servers"]["lmstudio"]["models"][0]
            chain.append(OpenAIAgent({**self.cfg, "type": "lmstudio", "model": m, "base_url": "http://127.0.0.1:1234/v1"}, self.system_prompt, tree=self.tree))
        if not sub_only and info["api_keys"]["gemini"]:
            chain.append(GeminiAgent({**self.cfg, "model": "gemini-2.5-pro"}, self.system_prompt, tree=self.tree))
        elif info["clis"]["gemini"]:
            chain.append(GeminiCliAgent({**sub_cfg, "model": "gemini-2.5-pro"}, self.system_prompt, tree=self.tree))
        if info["clis"]["codex"] or (not sub_only and info["api_keys"]["openai"]):
            chain.append(CodexAgent({**sub_cfg, "model": "gpt-5-codex"}, self.system_prompt, tree=self.tree))
        if info["clis"]["copilot"]:
            chain.append(CopilotCliAgent({**sub_cfg, "model": "claude-sonnet-4.5"}, self.system_prompt, tree=self.tree))
        if not sub_only and info["api_keys"]["openrouter"]:
            chain.append(OpenAIAgent({**self.cfg, "type": "openrouter", "model": "qwen/qwen3-coder"}, self.system_prompt, tree=self.tree))
        if info["clis"]["claude"] and self.cfg.get("command"):
            chain.append(CommandAgent(sub_cfg, self.root))
        if not sub_only and info["api_keys"]["anthropic"]:
            chain.append(ClaudeAgent({**self.cfg, "model": "claude-sonnet-4-6"}, self.system_prompt, tree=self.tree))
        return chain

    def run(self, case: Case, sandbox: Sandbox, trial: int) -> Trace:
        from .providers import is_rate_limit_or_quota

        cands = self._candidates()
        if not cands:
            return ScriptedAgent(tree=self.tree).run(case, sandbox, trial)
        last_trace: Trace | None = None
        for ag in cands:
            tr = ag.run(case, sandbox, trial)
            last_trace = tr
            if not tr.error or not is_rate_limit_or_quota(tr.error):
                return tr
        return last_trace or Trace(case_id=case.id, trial=trial, workdir=str(sandbox.dir), error="all configured providers rate-limited")


def build_agent(cfg: dict[str, Any], root) -> Agent:
    kind = cfg["type"]
    spf = cfg.get("system_prompt_file")
    prompt_path = (Path(root) / spf) if (root and spf) else (Path(spf) if spf else None)
    tree = HarnessTree.load(prompt_path) if (prompt_path and prompt_path.exists()) else None
    compiled_prompt = tree.compile_prompt() if tree else (prompt_path.read_text() if (prompt_path and prompt_path.is_file()) else "")

    if kind == "claude":
        return ClaudeAgent(cfg, compiled_prompt, tree=tree)
    if kind == "codex":
        return CodexAgent(cfg, compiled_prompt, tree=tree)
    if kind == "gemini":
        return GeminiAgent(cfg, compiled_prompt, tree=tree)
    if kind == "gemini-cli":
        return GeminiCliAgent(cfg, compiled_prompt, tree=tree)
    if kind == "copilot":
        return CopilotCliAgent(cfg, compiled_prompt, tree=tree)
    if kind in ("ollama", "opensource"):
        return OllamaAgent(cfg, compiled_prompt, tree=tree)
    if kind in ("openai", "vllm", "lmstudio", "openrouter"):
        return OpenAIAgent(cfg, compiled_prompt, tree=tree)
    if kind == "auto":
        return AutoRouterAgent(cfg, compiled_prompt, root=root, tree=tree)
    if kind == "command":
        return CommandAgent(cfg, root)
    if kind == "scripted":
        return ScriptedAgent(tree=tree)
    raise ValueError(f"unknown agent type {kind!r}")
