# Architecture

AutoHarness implements **dual-level recursive harness engineering** alongside **continuous code self-healing**:

1. **Task & Code Level Recursion**: Repairs broken source modules in isolated workspaces (`healer.py`) and refines the active harness via **Propose → Guard → Probe → Compose → Select** (`loop.py` + `fixer.py`).
2. **Harnessing-Level Recursion**: Distills empirically validated `DO` and `AVOID` insights into a persistent **Lesson Ledger** (`lessons/lessons.json` & `LESSONS.md`) fed back into future code-healing and harness-refinement sessions.

## Module Overview

| Module | Role |
|---|---|
| `tree.py` | **8-Component Harness² Tree**: loads, scaffolds, compiles, stages helper scripts (`.harness/scripts/`), and executes `plugins.json` pre/post-tool middleware |
| `synthesizer.py` | Scans Python AST, imports, and matching unit tests; generates live codebase `memory.md` maps and synthesizes `dev`/`holdout` YAML cases |
| `healer.py` | **Autonomous Code Self-Healer**: repairs broken new or legacy code inside throwaway project copies while enforcing byte-for-byte test immutability |
| `hooks.py` | Installs/manages Git `pre-commit`, `post-commit`, and `pre-push` hooks + incremental `git status` Python file detection |
| `cases.py` | Loads YAML evaluation cases (`dev` / `holdout` splits, `active` / `draft` status, setup files, mocks) |
| `sandbox.py` | Per-trial isolated temporary workspace with path-escape prevention |
| `agents.py` | Model-agnostic substrates: `ClaudeAgent`, `GeminiAgent`, `OpenAIAgent`, `CommandAgent` (Claude Code / Codex / OpenCode), and `ScriptedAgent` |
| `trace.py` | Structured execution trace: tool calls, token usage, turns, cost, messages, and post-run file artifacts |
| `graders/` | `deterministic.py` → `trace_checks.py` → `judge.py` + `autonomous.py` (evidence-grounded self-certification) |
| `runner.py` | Parallel `cases × trials` execution engine, cost estimator, and `pass^k` reliability aggregator |
| `diff.py` | Per-case and suite-level baseline delta calculator and regression gate |
| `calibrate.py` | Judge-vs-human agreement and chance-corrected Cohen's κ |
| `fixer.py` | Structured-output optimizer: `propose` (Invariant / Skill / Guardrail candidates), `compose` (contrastive merge), and `reflect` |
| `lessons.py` | Persistent evidence-backed Lesson Ledger (`lessons.json` + `LESSONS.md`) |
| `loop.py` | Closed-loop orchestrator: `run_once`, `autofix`, `cycle` (code heal + eval + gate + harness fix), and file-system `watch` |
| `miner.py` | Extracts failure episodes (user interruptions, corrections, tool error streaks) from real session transcripts |
| `report.py` | Self-contained dark/light HTML report generator |
| `demo.py` | Zero-credential offline demonstration of code healing + Harness² contrastive refinement |

## The 8-Component Harness² Layout

```
harnesses/harness2/
├── system.md          # Context Space: core role & response discipline
├── guardrails.md      # Context Space: hard safety & scope invariants
├── memory.md          # Context Space: auto-synced codebase map & state schema
├── skills.md          # Context Space: debugging, refactoring & Q&A playbooks
├── tool_guidance.md   # Interface Space: read-before-edit & verification rules
├── scripts.md         # Interface Space: documentation for staged utilities
├── scripts/
│   └── verify.sh      # Interface Space: executable script staged into .harness/scripts/
├── subagents.md       # Middleware Space: pre-return Verifier persona
└── plugins.json       # Middleware Space: deterministic pre/post-tool runtime hooks
```

## On-Disk State Layout

```
autoharness.yaml           # Default Claude Code CLI profile (zero API keys)
autoharness.api.yaml       # Direct API profile (Anthropic / Gemini / OpenAI)
harnesses/…                # Single-file or 8-component Harness² directories
cases/golden/*.yaml        # Curated dev + holdout cases
cases/auto/*.yaml          # Auto-synthesized cases from source code (git-ignored)
cases/mined/*.yaml         # Failure episodes mined from session logs (git-ignored)
labels/calibration.jsonl   # Optional human calibration verdicts (git-ignored)
lessons/lessons.json       # Persistent DO / AVOID lesson ledger (+ LESSONS.md)
runs/
  BASELINE                 # Pointer to current regression baseline run ID
  calibration.json         # Latest calibration metrics
  fixlog.md                # Audit log of every accepted & rejected candidate diff
  fix_attempts.json        # Fingerprints of exhausted states
  <run_id>/                # summary.json, results.jsonl, traces/, diff.json, report.html
```
