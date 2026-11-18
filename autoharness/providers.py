"""Universal Model & Substrate Registry for AutoHarness.

Supports closed and open-source AI models interchangeably across Agent, Judge,
Contrastive Fixer, and Code Self-Healer:
  - OpenAI Codex       (`codex` CLI & OpenAI Responses/Chat API)
  - Google Gemini      (`gemini` CLI & Gemini API)
  - Anthropic Claude   (`claude` CLI & Anthropic API)
  - Ollama (Local OSS) (`ollama` daemon on :11434 — Qwen2.5-Coder, DeepSeek-R1, Llama 3.3, Gemma 3, Devstral)
  - vLLM / LM Studio / llama.cpp / OpenRouter / Together / Groq (OpenAI-compatible `/v1/chat/completions`)
  - Auto-Failover      (`auto` — routes around missing keys or CLI rate limits automatically)
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .config import claude_cli

SUBSTRATE_PRESETS: list[dict[str, Any]] = [
    {
        "id": "subscription-pool",
        "label": "Subscription Pool Router",
        "family": "Flat-Rate Subscriptions",
        "agent_type": "auto",
        "auth_mode": "subscription",
        "model": "auto",
        "fixer_backend": "auto",
        "judge_backend": "auto",
        "badge": "Subscriptions · $0 API",
        "description": "Uses your flat-rate OAuth subscriptions (Claude Pro/Max/Team, ChatGPT Plus/Pro, Gemini Pro, Copilot) + local Ollama, stripping API keys so you never pay per token.",
    },
    {
        "id": "claude-sub",
        "label": "Claude Pro / Max / Team",
        "family": "Flat-Rate Subscription",
        "agent_type": "command",
        "auth_mode": "subscription",
        "model": "opus",
        "fixer_backend": "claude-cli",
        "judge_backend": "claude-cli",
        "badge": "Subscription · $0 API",
        "description": "Uses your claude.ai Pro, Max, or Team subscription login (claude login) with API keys isolated.",
    },
    {
        "id": "codex-sub",
        "label": "ChatGPT Plus / Pro (Codex)",
        "family": "Flat-Rate Subscription",
        "agent_type": "codex",
        "auth_mode": "subscription",
        "model": "gpt-5-codex",
        "fixer_backend": "codex-cli",
        "judge_backend": "codex-cli",
        "badge": "Subscription · $0 API",
        "description": "Uses your ChatGPT Plus, Pro, or Team OAuth subscription via Codex CLI (codex login) with zero API billing.",
    },
    {
        "id": "gemini-sub",
        "label": "Google AI Pro / Ultra (Gemini)",
        "family": "Flat-Rate Subscription",
        "agent_type": "gemini-cli",
        "auth_mode": "subscription",
        "model": "gemini-2.5-pro",
        "fixer_backend": "gemini-cli",
        "judge_backend": "gemini-cli",
        "badge": "Subscription · $0 API",
        "description": "Uses your Google account / Google One AI Pro OAuth login via Gemini CLI (gemini) without GEMINI_API_KEY.",
    },
    {
        "id": "copilot-sub",
        "label": "GitHub Copilot Pro / Pro+",
        "family": "Flat-Rate Subscription",
        "agent_type": "copilot",
        "auth_mode": "subscription",
        "model": "claude-sonnet-4.5",
        "fixer_backend": "copilot-cli",
        "judge_backend": "copilot-cli",
        "badge": "Subscription · $0 API",
        "description": "Uses your GitHub Copilot subscription via GitHub Copilot CLI (copilot -p) authenticated with gh auth login.",
    },
    {
        "id": "ollama-qwen",
        "label": "Qwen 2.5 Coder (Ollama)",
        "family": "Open-Source Local",
        "agent_type": "ollama",
        "model": "qwen2.5-coder:14b",
        "base_url": "http://127.0.0.1:11434",
        "fixer_backend": "ollama",
        "judge_backend": "ollama",
        "badge": "Open-Source · $0",
        "description": "100% local open-source coding model via Ollama (:11434) with native + XML tool calling.",
    },
    {
        "id": "ollama-deepseek",
        "label": "DeepSeek-R1 / V3 (Ollama)",
        "family": "Open-Source Local",
        "agent_type": "ollama",
        "model": "deepseek-r1:14b",
        "base_url": "http://127.0.0.1:11434",
        "fixer_backend": "ollama",
        "judge_backend": "ollama",
        "badge": "Open-Source · $0",
        "description": "Local reasoning & code repair model via Ollama with automatic <think> stripping & tool loop.",
    },
    {
        "id": "vllm-local",
        "label": "vLLM / LM Studio / llama.cpp",
        "family": "Open-Source Server",
        "agent_type": "vllm",
        "model": "Qwen/Qwen2.5-Coder-32B-Instruct",
        "base_url": "http://127.0.0.1:8000/v1",
        "fixer_backend": "openai",
        "judge_backend": "openai",
        "badge": "Self-Hosted OSS",
        "description": "Any self-hosted OpenAI-compatible open-source server (vLLM :8000, LM Studio :1234, llama-server).",
    },
    {
        "id": "openrouter-oss",
        "label": "OpenRouter / API Keys (Pay-Per-Token)",
        "family": "API Billing",
        "agent_type": "openrouter",
        "auth_mode": "api",
        "model": "qwen/qwen3-coder",
        "base_url": "https://openrouter.ai/api/v1",
        "fixer_backend": "openrouter",
        "judge_backend": "openrouter",
        "badge": "API Key",
        "description": "Optional pay-as-you-go API mode (OPENROUTER_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY, ANTHROPIC_API_KEY).",
    },
]


def find_cli(name: str, explicit: str | None = None) -> str | None:
    if explicit and Path(explicit).exists():
        return explicit
    hit = shutil.which(name)
    if hit:
        return hit
    extra_dirs = [
        Path("/opt/homebrew/bin"),
        Path("/usr/local/bin"),
        Path.home() / ".local/bin",
        Path.home() / ".npm-global/bin",
        Path.home() / ".bun/bin",
        Path("/Applications/Codex.app/Contents/Resources"),
    ]
    nvm_root = Path.home() / ".nvm/versions/node"
    if nvm_root.exists():
        extra_dirs.extend(sorted((p / "bin" for p in nvm_root.iterdir() if (p / "bin").exists()), reverse=True))
    for prefix in extra_dirs:
        cand = prefix / name
        if cand.exists():
            return str(cand)
    return None


def probe_local_endpoint(url: str, timeout: float = 0.35) -> list[str]:
    """Quick non-blocking check for local Ollama / vLLM / LM Studio model servers."""
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode(errors="replace"))
        if "models" in data and isinstance(data["models"], list):
            return [str(m.get("name") or m.get("model", "")) for m in data["models"] if isinstance(m, dict)]
        if "data" in data and isinstance(data["data"], list):
            return [str(m.get("id", "")) for m in data["data"] if isinstance(m, dict)]
    except Exception:
        pass
    return []


SUBSCRIPTION_STRIP_KEYS: tuple[str, ...] = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
)


def subscription_clean_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Return a copy of os.environ with pay-per-token API keys stripped so CLIs strictly use OAuth subscriptions."""
    env = dict(os.environ)
    for k in SUBSCRIPTION_STRIP_KEYS:
        env.pop(k, None)
    if extra:
        env.update(extra)
    return env


def detect_subscriptions() -> dict[str, dict[str, Any]]:
    """Detect active flat-rate OAuth subscriptions across Claude, ChatGPT/Codex, Google Gemini, and GitHub Copilot."""
    subs: dict[str, dict[str, Any]] = {}

    # 1. Claude Pro / Max / Team via `claude auth status`
    claude_bin = find_cli("claude")
    claude_info: dict[str, Any] = {"active": False, "plan": "Not logged in", "method": "claude login"}
    if claude_bin:
        try:
            r = subprocess.run(
                [claude_bin, "auth", "status"],
                capture_output=True,
                text=True,
                timeout=1.5,
                env=subscription_clean_env(),
            )
            data = json.loads(r.stdout.strip() or "{}")
            if data.get("loggedIn"):
                stype = str(data.get("subscriptionType") or "pro").capitalize()
                org = data.get("orgName") or data.get("email") or "claude.ai"
                claude_info = {
                    "active": True,
                    "plan": f"Claude {stype}",
                    "account": str(org),
                    "method": str(data.get("authMethod") or "claude.ai"),
                }
        except Exception:
            pass
    subs["claude"] = claude_info

    # 2. ChatGPT Plus / Pro / Team via ~/.codex/auth.json + `codex` CLI
    codex_auth = (Path.home() / ".codex" / "auth.json").exists()
    codex_bin = find_cli("codex")
    subs["codex"] = {
        "active": bool(codex_auth and codex_bin),
        "oauth_cached": codex_auth,
        "plan": "ChatGPT Plus / Pro" if (codex_auth and codex_bin) else ("OAuth Ready (install @openai/codex)" if codex_auth else "Run codex login"),
        "method": "OAuth (~/.codex/auth.json)",
    }

    # 3. Google AI Pro / Ultra via ~/.gemini/oauth_creds.json or google_accounts.json + `gemini` CLI
    gem_dir = Path.home() / ".gemini"
    gem_auth = (gem_dir / "oauth_creds.json").exists() or (gem_dir / "google_accounts.json").exists()
    gem_bin = find_cli("gemini")
    subs["gemini"] = {
        "active": bool(gem_auth and gem_bin),
        "oauth_cached": gem_auth,
        "plan": "Google AI Pro / Ultra" if (gem_auth and gem_bin) else ("OAuth Ready (install @google/gemini-cli)" if gem_auth else "Run gemini to sign in"),
        "method": "OAuth (~/.gemini)",
    }

    # 4. GitHub Copilot Pro / Pro+ via GitHub CLI auth (`~/.config/gh/hosts.yml`) + `copilot`
    gh_hosts = Path.home() / ".config" / "gh" / "hosts.yml"
    gh_logged_in = gh_hosts.exists()
    copilot_bin = find_cli("copilot")
    subs["copilot"] = {
        "active": bool(gh_logged_in and copilot_bin),
        "gh_authenticated": gh_logged_in,
        "plan": "GitHub Copilot" if (gh_logged_in and copilot_bin) else ("GitHub Auth Ready (install `copilot`)" if gh_logged_in else "Run `gh auth login`"),
        "method": "GitHub OAuth",
    }
    return subs


def discover_providers() -> dict[str, Any]:
    """Detect installed CLIs, flat-rate subscriptions, configured API keys, and running local OSS daemons."""
    ollama_url = (os.environ.get("OLLAMA_HOST") or "http://127.0.0.1:11434").rstrip("/")
    if not ollama_url.startswith("http"):
        ollama_url = f"http://{ollama_url}"
    ollama_models = probe_local_endpoint(f"{ollama_url}/api/tags")
    vllm_models = probe_local_endpoint("http://127.0.0.1:8000/v1/models")
    lmstudio_models = probe_local_endpoint("http://127.0.0.1:1234/v1/models")

    clis = {
        "claude": find_cli("claude"),
        "codex": find_cli("codex"),
        "gemini": find_cli("gemini"),
        "copilot": find_cli("copilot"),
        "ollama": find_cli("ollama"),
    }
    keys = {
        "openai": bool(os.environ.get("OPENAI_API_KEY")),
        "gemini": bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")),
        "anthropic": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "openrouter": bool(os.environ.get("OPENROUTER_API_KEY")),
    }
    return {
        "clis": {k: bool(v) for k, v in clis.items()},
        "cli_paths": {k: v for k, v in clis.items() if v},
        "subscriptions": detect_subscriptions(),
        "api_keys": keys,
        "local_servers": {
            "ollama": {"online": bool(ollama_models), "url": ollama_url, "models": ollama_models[:12]},
            "vllm": {"online": bool(vllm_models), "url": "http://127.0.0.1:8000/v1", "models": vllm_models[:12]},
            "lmstudio": {"online": bool(lmstudio_models), "url": "http://127.0.0.1:1234/v1", "models": lmstudio_models[:12]},
        },
        "presets": SUBSTRATE_PRESETS,
    }


def apply_substrate_selection(
    cfg: Any,
    preset_id: str,
    custom_model: str | None = None,
    custom_base_url: str | None = None,
) -> dict[str, Any]:
    """Update `cfg` in-memory and persist `autoharness.yaml` to use any AI model substrate."""
    import yaml

    preset = next((p for p in SUBSTRATE_PRESETS if p["id"] == preset_id), None)
    if preset is None:
        preset = {
            "id": preset_id,
            "label": preset_id,
            "agent_type": preset_id if preset_id in ("codex", "gemini", "gemini-cli", "copilot", "ollama", "vllm", "lmstudio", "openrouter", "openai", "claude", "command", "auto") else "ollama",
            "model": custom_model or preset_id,
            "fixer_backend": "auto",
            "judge_backend": "auto",
        }

    model = (custom_model or preset.get("model") or "auto").strip()
    base_url = (custom_base_url or preset.get("base_url") or "").strip()
    auth_mode = preset.get("auth_mode")

    from .config import load_config

    cfg_file = cfg.root / "autoharness.yaml"
    disk_raw = yaml.safe_load(cfg_file.read_text()) or {} if cfg_file.exists() else dict(cfg.raw)
    for sec, vals in (
        ("agent", {"type": preset["agent_type"], "model": model}),
        ("fixer", {"backend": preset["fixer_backend"], "model": model}),
        ("judge", {"backend": preset["judge_backend"], "model": model}),
    ):
        disk_raw.setdefault(sec, {})
        disk_raw[sec].update(vals)
        if auth_mode:
            disk_raw[sec]["auth_mode"] = auth_mode
        else:
            disk_raw[sec].pop("auth_mode", None)
        if base_url:
            disk_raw[sec]["base_url"] = base_url
        elif sec == "agent" and preset["agent_type"] not in ("ollama", "vllm", "lmstudio", "openrouter", "openai"):
            disk_raw[sec].pop("base_url", None)

    if cfg_file.exists():
        cfg_file.write_text(yaml.safe_dump(disk_raw, sort_keys=False))
        cfg.raw = load_config(cfg_file).raw
    else:
        for sec in ("agent", "fixer", "judge"):
            cfg.raw.setdefault(sec, {})
            cfg.raw[sec].update(disk_raw[sec])
    return preset


def extract_json_object(raw: str) -> dict[str, Any] | None:
    """Extract a JSON object cleanly even if an open-source model wraps `<think>...</think>` or markdown fences."""
    if not raw:
        return None
    cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, flags=re.DOTALL)
    if fence:
        try:
            obj = json.loads(fence.group(1))
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    try:
        obj = json.loads(cleaned)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end > start:
        try:
            obj = json.loads(cleaned[start : end + 1])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    return None


def is_rate_limit_or_quota(err: str | None) -> bool:
    if not err:
        return False
    low = err.lower()
    return any(tok in low for tok in ("hit your limit", "rate limit", "429", "quota", "insufficient_quota", "overloaded"))


def complete_structured(
    backend: str,
    cfg: dict[str, Any],
    prompt: str,
    schema: dict[str, Any],
    system: str,
) -> tuple[dict[str, Any], float, str | None]:
    """Universal structured-JSON completion across Claude, Codex, Gemini, OpenAI, Ollama, vLLM & OpenRouter."""
    if backend == "auto":
        order = _auto_backend_order(cfg)
        errors: list[str] = []
        for b_name, b_cfg in order:
            data, cost, err = complete_structured(b_name, b_cfg, prompt, schema, system)
            if not err and isinstance(data, dict) and data:
                return data, cost, None
            errors.append(f"{b_name}: {err or 'empty'}")
        return {}, 0.0, " | ".join(errors) if errors else "no available model backends configured"

    if backend == "claude-cli":
        return _call_claude_cli(cfg, prompt, schema, system)
    if backend == "codex-cli":
        return _call_codex_cli(cfg, prompt, schema, system)
    if backend == "gemini-cli":
        return _call_gemini_cli(cfg, prompt, schema, system)
    if backend == "copilot-cli":
        return _call_copilot_cli(cfg, prompt, schema, system)
    if backend == "gemini":
        if (cfg.get("auth_mode") == "subscription" or not (cfg.get("api_key") or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))) and find_cli("gemini"):
            return _call_gemini_cli(cfg, prompt, schema, system)
        return _call_gemini_http(cfg, prompt, schema, system)
    if backend == "ollama":
        return _call_ollama_http(cfg, prompt, schema, system)
    if backend in ("openai", "vllm", "lmstudio", "openrouter", "opensource"):
        return _call_openai_compat(backend, cfg, prompt, schema, system)
    return _call_anthropic_sdk(cfg, prompt, schema, system)


def _auto_backend_order(cfg: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    info = discover_providers()
    sub_only = cfg.get("auth_mode") == "subscription"
    sub_cfg = {**cfg, "auth_mode": "subscription"} if sub_only else cfg
    chain: list[tuple[str, dict[str, Any]]] = []
    if info["local_servers"]["ollama"]["online"]:
        m = info["local_servers"]["ollama"]["models"][0]
        chain.append(("ollama", {**cfg, "model": m, "base_url": info["local_servers"]["ollama"]["url"]}))
    if info["local_servers"]["vllm"]["online"]:
        m = info["local_servers"]["vllm"]["models"][0]
        chain.append(("vllm", {**cfg, "model": m, "base_url": "http://127.0.0.1:8000/v1"}))
    if info["local_servers"]["lmstudio"]["online"]:
        m = info["local_servers"]["lmstudio"]["models"][0]
        chain.append(("lmstudio", {**cfg, "model": m, "base_url": "http://127.0.0.1:1234/v1"}))
    if not sub_only and info["api_keys"]["gemini"]:
        chain.append(("gemini", {**cfg, "model": "gemini-2.5-pro"}))
    elif info["clis"]["gemini"]:
        chain.append(("gemini-cli", {**sub_cfg, "model": "gemini-2.5-pro"}))
    if not sub_only and info["api_keys"]["openai"]:
        chain.append(("openai", {**cfg, "model": "gpt-4.1"}))
    elif info["clis"]["codex"]:
        chain.append(("codex-cli", {**sub_cfg, "model": "gpt-5-codex"}))
    if info["clis"]["copilot"]:
        chain.append(("copilot-cli", {**sub_cfg, "model": "claude-sonnet-4.5"}))
    if not sub_only and info["api_keys"]["openrouter"]:
        chain.append(("openrouter", {**cfg, "model": "qwen/qwen3-coder"}))
    if not sub_only and info["api_keys"]["anthropic"]:
        chain.append(("api", {**cfg, "model": "claude-sonnet-4-6"}))
    if info["clis"]["claude"]:
        chain.append(("claude-cli", {**sub_cfg, "model": cfg.get("model") if cfg.get("model") not in (None, "auto") else "opus"}))
    return chain


def _schema_instruction(prompt: str, schema: dict[str, Any], system: str) -> str:
    return (
        f"{system}\n\n"
        f"{prompt}\n\n"
        "Respond ONLY with a valid JSON object matching this JSON Schema (no markdown prose outside JSON):\n"
        f"{json.dumps(schema)}"
    )


def _call_claude_cli(cfg: dict[str, Any], prompt: str, schema: dict[str, Any], system: str) -> tuple[dict, float, str | None]:
    bin_path = find_cli("claude", cfg.get("cli")) or claude_cli(cfg.get("cli"))
    model = cfg.get("model", "opus")
    if model == "auto":
        model = "opus"
    cmd = [
        bin_path, "-p", "--output-format", "json", "--json-schema", json.dumps(schema),
        "--tools", "", "--strict-mcp-config", "--setting-sources", "", "--system-prompt", system,
        "--no-session-persistence", "--model", model,
    ]
    if cfg.get("effort"):
        cmd += ["--effort", str(cfg["effort"])]
    sub_mode = cfg.get("auth_mode") == "subscription"
    env = subscription_clean_env() if sub_mode else None
    try:
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=cfg.get("timeout_s", 600), env=env)
    except FileNotFoundError:
        return {}, 0.0, "claude CLI not installed"
    except subprocess.TimeoutExpired:
        return {}, 0.0, "claude CLI timed out"
    try:
        out = json.loads(r.stdout)
    except json.JSONDecodeError:
        return {}, 0.0, f"claude cli exit {r.returncode}: {(r.stderr or r.stdout)[-400:]}"
    cost = 0.0 if sub_mode else float(out.get("total_cost_usd") or 0.0)
    data = out.get("structured_output")
    if out.get("is_error") or not isinstance(data, dict):
        return {}, cost, f"claude cli: {out.get('subtype')} {str(out.get('result'))[:300]}"
    return data, cost, None


def _call_codex_cli(cfg: dict[str, Any], prompt: str, schema: dict[str, Any], system: str) -> tuple[dict, float, str | None]:
    bin_path = find_cli("codex", cfg.get("cli"))
    sub_mode = cfg.get("auth_mode") == "subscription"
    if not bin_path:
        if not sub_mode and os.environ.get("OPENAI_API_KEY"):
            return _call_openai_compat("openai", {**cfg, "model": cfg.get("model", "o4-mini")}, prompt, schema, system)
        return {}, 0.0, "codex CLI not installed (run `npm i -g @openai/codex && codex login`)"
    import tempfile

    with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False) as sf, \
         tempfile.NamedTemporaryFile("w+", suffix=".txt", delete=False) as of:
        json.dump(schema, sf)
        sf.flush()
        schema_file, out_file = sf.name, of.name

    cmd = [bin_path, "exec", "--skip-git-repo-check", "--output-schema", schema_file, "-o", out_file]
    if cfg.get("model") and cfg["model"] not in ("auto", "opus", "sonnet"):
        cmd += ["-m", str(cfg["model"])]
    cmd.append(_schema_instruction(prompt, schema, system))
    env = subscription_clean_env() if sub_mode else None
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=cfg.get("timeout_s", 600), env=env)
        txt = Path(out_file).read_text(errors="replace") if Path(out_file).exists() else r.stdout
    except subprocess.TimeoutExpired:
        return {}, 0.0, "codex CLI timed out"
    finally:
        Path(schema_file).unlink(missing_ok=True)
        Path(out_file).unlink(missing_ok=True)

    obj = extract_json_object(txt)
    if obj is None:
        return {}, 0.0, f"codex cli exit {r.returncode}: {(r.stderr or txt)[-350:]}"
    return obj, 0.0, None


def _call_gemini_cli(cfg: dict[str, Any], prompt: str, schema: dict[str, Any], system: str) -> tuple[dict, float, str | None]:
    bin_path = find_cli("gemini", cfg.get("cli"))
    sub_mode = cfg.get("auth_mode") == "subscription"
    if not bin_path:
        if not sub_mode:
            return _call_gemini_http(cfg, prompt, schema, system)
        return {}, 0.0, "gemini CLI not installed (run `npm i -g @google/gemini-cli && gemini`)"
    full = _schema_instruction(prompt, schema, system)
    cmd = [bin_path, "-p", full, "-o", "json"]
    if cfg.get("model") and cfg["model"] not in ("auto", "opus", "sonnet"):
        cmd += ["-m", str(cfg["model"])]
    env = subscription_clean_env() if sub_mode else None
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=cfg.get("timeout_s", 600), env=env)
    except subprocess.TimeoutExpired:
        return {}, 0.0, "gemini CLI timed out"
    raw = r.stdout.strip()
    try:
        outer = json.loads(raw)
        if isinstance(outer, dict) and isinstance(outer.get("response"), str):
            raw = outer["response"]
    except json.JSONDecodeError:
        pass
    obj = extract_json_object(raw)
    if obj is None:
        return {}, 0.0, f"gemini cli exit {r.returncode}: {(r.stderr or raw)[-350:]}"
    return obj, 0.0, None


def _call_copilot_cli(cfg: dict[str, Any], prompt: str, schema: dict[str, Any], system: str) -> tuple[dict, float, str | None]:
    bin_path = find_cli("copilot", cfg.get("cli"))
    if not bin_path:
        return {}, 0.0, "GitHub Copilot CLI (`copilot`) not installed (run `npm i -g @github/copilot && gh auth login`)"
    full = _schema_instruction(prompt, schema, system)
    cmd = [bin_path, "-p", full, "-s"]
    if cfg.get("model") and cfg["model"] not in ("auto", "opus", "sonnet"):
        cmd += ["--model", str(cfg["model"])]
    env = subscription_clean_env() if cfg.get("auth_mode") == "subscription" else None
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=cfg.get("timeout_s", 600), env=env)
    except subprocess.TimeoutExpired:
        return {}, 0.0, "copilot CLI timed out"
    obj = extract_json_object(r.stdout.strip())
    if obj is None:
        return {}, 0.0, f"copilot cli exit {r.returncode}: {(r.stderr or r.stdout)[-350:]}"
    return obj, 0.0, None


def _call_gemini_http(cfg: dict[str, Any], prompt: str, schema: dict[str, Any], system: str) -> tuple[dict, float, str | None]:
    api_key = cfg.get("api_key") or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        return {}, 0.0, "missing GEMINI_API_KEY / GOOGLE_API_KEY"
    model = cfg.get("model") or "gemini-2.5-pro"
    if model in ("auto", "opus", "sonnet"):
        model = "gemini-2.5-pro"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    payload = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": prompt + "\n\nRespond strictly as JSON matching schema:\n" + json.dumps(schema)}]}],
        "generationConfig": {"responseMimeType": "application/json"},
    }
    try:
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=180) as resp:
            raw = json.loads(resp.read().decode())
        cand = (raw.get("candidates") or [{}])[0]
        text = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts") or [])
        obj = extract_json_object(text)
        if obj is None:
            return {}, 0.0, "Gemini returned non-JSON response"
        return obj, 0.0, None
    except Exception as e:
        return {}, 0.0, f"Gemini error: {type(e).__name__}: {e}"


def _call_ollama_http(cfg: dict[str, Any], prompt: str, schema: dict[str, Any], system: str) -> tuple[dict, float, str | None]:
    base = (cfg.get("base_url") or os.environ.get("OLLAMA_HOST") or "http://127.0.0.1:11434").rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    model = cfg.get("model") or "qwen2.5-coder:14b"
    payload = {
        "model": model,
        "stream": False,
        "format": schema,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt + "\n\nReturn JSON matching schema:\n" + json.dumps(schema)},
        ],
    }
    try:
        req = urllib.request.Request(f"{base}/api/chat", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=cfg.get("timeout_s", 300)) as resp:
            raw = json.loads(resp.read().decode())
        text = (raw.get("message") or {}).get("content") or ""
        obj = extract_json_object(text)
        if obj is None:
            return {}, 0.0, f"Ollama ({model}) returned invalid JSON"
        return obj, 0.0, None
    except Exception as e:
        return {}, 0.0, f"Ollama ({base}) unreachable or failed: {type(e).__name__}: {e}"


def _call_openai_compat(backend: str, cfg: dict[str, Any], prompt: str, schema: dict[str, Any], system: str) -> tuple[dict, float, str | None]:
    default_urls = {
        "openai": "https://api.openai.com/v1",
        "vllm": "http://127.0.0.1:8000/v1",
        "lmstudio": "http://127.0.0.1:1234/v1",
        "openrouter": "https://openrouter.ai/api/v1",
        "opensource": "http://127.0.0.1:8000/v1",
    }
    base_url = (cfg.get("base_url") or os.environ.get("OPENAI_BASE_URL") or default_urls.get(backend, "https://api.openai.com/v1")).rstrip("/")
    api_key = (
        cfg.get("api_key")
        or (os.environ.get("OPENROUTER_API_KEY") if backend == "openrouter" else None)
        or os.environ.get("OPENAI_API_KEY")
        or "local-oss"
    )
    model = cfg.get("model") or ("qwen/qwen3-coder" if backend == "openrouter" else "gpt-4.1")
    is_strict_openai = "api.openai.com" in base_url
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt if is_strict_openai else (prompt + "\n\nRespond strictly as JSON matching schema:\n" + json.dumps(schema))},
        ],
    }
    if is_strict_openai:
        payload["response_format"] = {"type": "json_schema", "json_schema": {"name": "autoharness_out", "strict": True, "schema": schema}}
    else:
        payload["response_format"] = {"type": "json_object"}
    try:
        req = urllib.request.Request(
            f"{base_url}/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        )
        with urllib.request.urlopen(req, timeout=cfg.get("timeout_s", 240)) as resp:
            raw = json.loads(resp.read().decode())
        text = ((raw.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        obj = extract_json_object(text)
        if obj is None:
            return {}, 0.0, f"{backend} ({model}) returned non-JSON output"
        return obj, 0.0, None
    except Exception as e:
        return {}, 0.0, f"{backend} error: {type(e).__name__}: {e}"


def _call_anthropic_sdk(cfg: dict[str, Any], prompt: str, schema: dict[str, Any], system: str) -> tuple[dict, float, str | None]:
    try:
        import anthropic
    except ImportError:
        return {}, 0.0, "anthropic package not installed"
    try:
        client = anthropic.Anthropic()
        resp = client.messages.create(
            model=cfg.get("model", "claude-sonnet-4-6"),
            max_tokens=16000,
            system=system,
            output_config={
                "effort": cfg.get("effort", "high"),
                "format": {"type": "json_schema", "schema": schema},
            },
            messages=[{"role": "user", "content": prompt}],
        )
        cost = (resp.usage.input_tokens * 4 + resp.usage.output_tokens * 20) / 1e6
        text = next((b.text for b in resp.content if b.type == "text"), "")
        obj = extract_json_object(text)
        if obj is None:
            return {}, cost, "Anthropic API returned invalid JSON"
        return obj, cost, None
    except Exception as e:
        return {}, 0.0, f"{type(e).__name__}: {e}"
