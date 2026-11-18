# Configuration Reference

All keys in `autoharness.yaml` are optional; defaults live in `autoharness/config.py`.

| Key | Default | Description |
|---|---|---|
| `cases` | `["cases/golden"]` | Case files or directories (`cases/mined`, `cases/auto`) |
| `code_paths` | `[]` | Source directories/files scanned & auto-healed when code changes |
| `runs_dir` | `"runs"` | Directory for run artifacts, baseline pointer, fix log, and calibration |
| `trials` / `workers` | `3` / `4` | Trials per case / parallel worker threads |
| `agent.type` | `"claude"` | `"command"`, `"claude"`, `"gemini"`, `"openai"`, or `"scripted"` |
| `agent.model` | `"claude-opus-5-5"` | Model identifier for the chosen substrate |
| `agent.system_prompt_file` | `"harnesses/default/system.md"` | Single file **or** 8-component Harness² directory (`harnesses/harness2`) |
| `agent.command` | — | Shell template for `command` agents (`{claude}`, `{prompt}`, `{workdir}`, `{system_prompt_file}`) |
| `judge.backend` | `"api"` | `"claude-cli"` or `"api"` |
| `judge.model` / `samples` / `min_confidence` | `"claude-opus-5-5"` / `3` / `0.7` | Judge model, self-consistency sample count, and confidence threshold |
| `judge.autonomous` | `false` | Auto-certify unanimous evidence-grounded judge verdicts without manual labels |
| `calibration.min_labels` / `min_agreement` | `20` / `0.85` | Thresholds for manual judge calibration trust |
| `fixer.backend` | `"claude-cli"` | `"claude-cli"`, `"api"`, `"gemini"`, or `"openai"` |
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
