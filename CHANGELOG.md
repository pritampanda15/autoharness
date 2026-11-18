# Changelog

## 0.3.0 (2026-11-18)
- **8-Component Harness² Architecture (`autoharness/tree.py`, `autoharness tree`)**: Inspired by `google-research/harness2`, harnesses can now be structured as modular 8-component directories across Context Space (`system.md`, `guardrails.md`, `memory.md`, `skills.md`), Interface Space (`tool_guidance.md`, `scripts/*`), and Delegation & Middleware Space (`subagents.md`, `plugins.json`).
- **Zero-Intervention Autonomous Grounded Evaluator (`autoharness/graders/autonomous.py`, `--autonomous`)**: Cross-verifies LLM judge criteria against workspace AST/JSON/YAML integrity, trace coherence, and artifact grounding so unanimous grounded verdicts auto-certify without blocking on manual calibration queues.
- **Continuous Code + Harness Self-Healing Loop (`autoharness heal`, `autoharness synthesize`, `autoharness hook`)**: Automatically discovers new & legacy source code, synthesizes `dev`/`holdout` evaluation cases, repairs broken modules in isolated sandboxes (with byte-for-byte test write-protection), syncs `memory.md`, and runs via `watch` or Git `pre-commit`/`post-commit` hooks.
- **Model-Agnostic Substrates**: Added zero-dependency `gemini` and `openai` (OpenAI / OpenRouter / Ollama) agent and fixer backends alongside `claude`, `command` (Claude Code / Codex / OpenCode), and `scripted`.

## 0.2.0 (2026-11-18)
- Renamed to **autoharness**. Added `init`, `demo`, and `lessons` commands, plus the `ah` alias.
- Multi-candidate fixer: propose K diverse edits, probe each in an isolated workspace in parallel, compose what worked from per-case contrasts, and select the best candidate passing every guard.
- Persistent lesson ledger (`lessons/lessons.json` and `LESSONS.md`) updated via post-session reflection.
- Claude Code default backend for agent, judge, and fixer (no API key required).

## 0.1.0
- Layered grading (deterministic → trace → judge), judge calibration with review queue, baseline regression gate, self-contained HTML report, failure mining from Claude Code sessions, and `watch` loop with keep-or-revert autofix.
