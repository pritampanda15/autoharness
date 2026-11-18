# Configuration Reference

All keys in `autoharness.yaml` are optional; defaults live in `autoharness/config.py`. You can switch substrates interactively via `autoharness studio` or from the CLI via `autoharness models --use <preset>`.

| Key | Default | Description |
|---|---|---|
| `cases` | `["cases/golden"]` | Case files or directories (`cases/mined`, `cases/auto`) |
| `code_paths` | `[]` | Source directories/files scanned & auto-healed when code changes |
| `runs_dir` | `"runs"` | Directory for run artifacts, baseline pointer, fix log, and calibration |
| `trials` / `workers` | `3` / `4` | Trials per case / parallel worker threads |
| `agent.type` | `"claude"` | `"auto"`, `"command"`, `"codex"`, `"gemini"`, `"gemini-cli"`, `"copilot"`, `"ollama"`, `"vllm"`, `"lmstudio"`, `"openrouter"`, `"openai"`, `"claude"`, or `"scripted"` |
| `agent.auth_mode` | `"auto"` | Set `"subscription"` to strip pay-per-token API keys (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `GOOGLE_API_KEY`) so CLIs strictly use flat-rate OAuth logins (`$0` API cost) |
| `agent.model` | `"opus"` | Model identifier for the chosen substrate (e.g. `"opus"`, `"gpt-5-codex"`, `"gemini-2.5-pro"`, `"qwen2.5-coder:14b"`, `"deepseek-r1:14b"`) |
| `agent.base_url` | — | Optional endpoint override for Ollama (`http://127.0.0.1:11434`), vLLM (`http://127.0.0.1:8000/v1`), LM Studio (`:1234/v1`), or OpenRouter |
| `agent.system_prompt_file` | `"harnesses/default/system.md"` | Single file **or** 8-component Harness² directory (`harnesses/harness2`) |
| `agent.command` | — | Shell template for `command` agents (`{claude}`, `{prompt}`, `{workdir}`, `{system_prompt_file}`) |
| `judge.backend` | `"api"` | `"auto"`, `"claude-cli"`, `"codex-cli"`, `"gemini-cli"`, `"copilot-cli"`, `"ollama"`, `"vllm"`, `"openrouter"`, `"openai"`, `"gemini"`, or `"api"` |
| `judge.model` / `samples` / `min_confidence` | `"opus"` / `3` / `0.7` | Judge model, self-consistency sample count, and confidence threshold |
| `judge.autonomous` | `false` | Auto-certify unanimous evidence-grounded judge verdicts without manual labels |
| `calibration.min_labels` / `min_agreement` | `20` / `0.85` | Thresholds for manual judge calibration trust |
| `fixer.backend` | `"claude-cli"` | `"auto"`, `"claude-cli"`, `"codex-cli"`, `"gemini-cli"`, `"copilot-cli"`, `"ollama"`, `"vllm"`, `"openrouter"`, `"openai"`, `"gemini"`, or `"api"` |
| `fixer.editable` | `[]` | Files **or** Harness² directories the fixer is permitted to modify |
| `fixer.candidates` / `parallel` | `3` / `3` | Diverse candidates proposed per attempt / parallel isolated probes |
| `fixer.compose` / `reflect` | `true` / `true` | Contrastive probe composition / post-session lesson reflection |
| `fixer.lessons_file` / `max_lessons` | `"lessons/lessons.json"` / `25` | Persistent Lesson Ledger location and capacity |
| `fixer.max_iters` / `max_usd_per_trigger` | `3` / `5.0` | Iteration and dollar budget caps per trigger |
| `fixer.max_token_increase_pct` / `max_growth_pct` | `30` / `100` | Rejects edits that bloat agent token usage or file size |
| `healer.enabled` / `max_attempts` | `true` / `2` | Autonomous code repair settings for `code_paths` |
| `healer.sync_memory` | `true` | Automatically update `harnesses/<dir>/memory.md` with live codebase map |
| `healer.synthesize_cases` | `false` | Auto-emit `cases/auto/*.yaml` during healing cycles |
| `loop.watch` | editable + cases + code_paths | Paths monitored by `autoharness watch` |
| `loop.interval_s` / `debounce_s` / `autofix` | `3` / `5` / `true` | Watcher polling, debounce window, and auto-fix toggle |
| `gate.max_pass_rate_drop` / `max_case_drop` | `0.05` / `0.34` | Suite-level and per-case regression tolerances |
