# AutoHarness

[![CI](https://github.com/pritampanda15/autoharness/actions/workflows/ci.yml/badge.svg)](https://github.com/pritampanda15/autoharness/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)

**Autonomous Harness² Engineering & Continuous Code + Context Self-Healing Loop.**

$$\text{Reliable Agent} = \text{Frozen Model} + \text{Self-Improving Harness}$$

**Harness Engineering** is the discipline of designing, evaluating, and evolving everything surrounding a foundation model: system instructions, guardrails, working memory, procedural skills, tool guidance, helper scripts, subagent verification protocols, and runtime middleware.

Traditionally, context design and agent evaluation require constant human babysitting: manually writing test cases, manually inspecting failed traces, and manually tweaking prompts or fixing regressions whenever new or legacy code changes.

**AutoHarness** closes that entire loop automatically — combining recursive **Harness²** optimization (inspired by [google-research/harness2](https://github.com/google-research/harness2)) with **continuous code & harness self-healing** and **zero-intervention grounded evaluation**:

- **Every time new or existing code is written** (`autoharness watch`, `autoharness heal`, or Git hooks), AutoHarness scans AST/imports/tests, synthesizes executable verification cases, repairs broken modules inside isolated sandboxes (while write-protecting test files!), and refreshes architectural memory.
- **Every time a harness or agent behavior regresses**, AutoHarness proposes diverse candidate edits across the **8-Component Harness² Tree**, probes each in parallel isolated workspaces, composes winning behavioral contrasts, gates against **hidden holdout cases**, and distills evidence-backed lessons into a persistent ledger.
- **Zero manual validation bottlenecks**: layered deterministic → behavioral trace → **evidence-grounded autonomous self-verification** certifies objective, artifact-backed runs automatically without waiting on human lookup.

```
  New or existing code / harness component saved (or committed)
                               │
                               ▼
  ┌────────────────────────────────────────────────────────────┐
  │ 1. DISCOVER & SELF-HEAL CODE (`healer` + `synthesizer`)    │
  │    • AST syntax + import + unit test scan (`code_paths`)   │
  │    • Auto-synthesize dev & holdout YAML eval cases         │
  │    • Repair failing modules in isolated copy (tests locked)│
  │    • Sync live codebase map -> Harness² `memory.md`        │
  └────────────────────────────┬───────────────────────────────┘
                               │
                               ▼
  ┌────────────────────────────┐   pass   ┌────────────────────┐
  │ 2. EVALUATE HARNESS SUITE  │ ───────▶ │ GATE vs BASELINE   │──▶ promote baseline
  │    Deterministic → Trace → │          └────────────────────┘
  │    Autonomous Grounding    │   fail
  └────────────────────────────┘ ───────▶ ┌──────────────────────────────────────────────┐
               ▲                          │ 3. HARNESS² CONTRASTIVE REFINEMENT (`fixer`) │
               │                          │    • Propose K candidates (Invariant /       │
               │                          │      Skill / Guardrail across 8 components)  │
               │                          │    • Static anti-overfitting guard           │
               │                          │    • Probe in parallel isolated workspaces   │
               │                          │    • Compose winning per-case contrasts      │
               │                          │    • Gate on hidden holdout cases + cost     │
               │                          │    • Reflect DO / AVOID priors -> LESSONS.md │
               │                          └──────────────────────┬───────────────────────┘
               └─────────────────────────────────────────────────┘
                         kept winner applied atomically, or byte-for-byte revert
```

---

## Quickstart

```bash
pip install git+https://github.com/pritampanda15/autoharness.git
```

### 1. Run the Full Offline Demo (No API Keys Needed)
See autonomous code self-healing + Harness² `Propose → Probe → Compose` + hidden holdout gating run end-to-end in ~2 seconds:

```bash
autoharness demo
```

### 2. Initialize Your Project
Scaffolds `autoharness.yaml`, an **8-component Harness² directory** (`harnesses/harness2/`), and starter golden cases:

```bash
autoharness init my-agent && cd my-agent
autoharness tree harnesses/harness2
```

### 3. Continuous Loop on Code & Harness Changes
Watch your source directories (`code_paths`) and harness components simultaneously — every save or Git commit evaluates and heals automatically:

```bash
# Continuous background watcher: heal code -> run evals -> gate -> auto-fix harness
autoharness watch --now

# Or install as an automatic Git pre-commit hook:
autoharness hook install --kind pre-commit
```

### 4. One-Shot Codebase Scan, Case Synthesis & Self-Healing
Point AutoHarness at any new or legacy codebase to synthesize evaluation cases and heal failing modules on demand:

```bash
autoharness synthesize src/          # auto-generate dev + holdout YAML cases from AST & tests
autoharness heal src/ --with-harness # repair broken modules safely + gate/fix the harness
```

---

## The 8-Component Harness² Tree

Inspired by [Google Research's Harness²](https://github.com/google-research/harness2), AutoHarness models an agent's software scaffolding as **8 modular, editable components** across **3 runtime spaces** (`autoharness tree`):

| Space | Component | File | Runtime Role |
|---|---|---|---|
| **Context Construction** | `system` | `system.md` | Core identity, reasoning loop, and response contract |
| **Context Construction** | `guardrails` | `guardrails.md` | Hard safety boundaries, non-destructive rules, and scope limits |
| **Context Construction** | `memory` | `memory.md` | Live codebase architecture map & working-memory schema |
| **Context Construction** | `skills` | `skills.md` | Procedural playbooks (debugging, refactoring, concise Q&A) |
| **Tool & Interface** | `tool_guidance` | `tool_guidance.md` | Tool selection rules, read-before-edit discipline, shell usage |
| **Tool & Interface** | `scripts` | `scripts.md` + `scripts/*` | Executable helper utilities staged into `.harness/scripts/` |
| **Delegation & Middleware** | `subagents` | `subagents.md` | Decomposition & adversarial self-verification personas |
| **Delegation & Middleware** | `plugins` | `plugins.json` | Deterministic pre-tool blockers & post-tool verification hooks |

> Single-file harnesses (`harnesses/default/system.md`) remain 100% supported — upgrade to a full 8-component tree anytime with `autoharness tree --init harnesses/harness2`.

---

## Zero-Intervention Layered Evaluation

Trials are graded cheapest-and-most-objective first:

| Layer | Examples | Cost |
|---|---|---|
| **1. Deterministic** | `command` (`pytest`), `python_syntax` (AST), `imports_clean`, `workspace_integrity`, `file_unchanged`, `json_valid`, regex | **Free & exact** |
| **2. Behavioral Trace** | `read_before_write`, `verified_after_edit`, `no_loops`, `must_not_call_tool`, `max_turns`, `max_tokens` | **Free & exact** |
| **3. Grounded Judge** | Multi-sample rubric voting + **Autonomous Evidence Grounding** (`--autonomous` / `judge.autonomous: true`) | **Paid (skipped if Layer 1/2 fails)** |

When `autonomous` mode is enabled, AutoHarness cross-verifies every rubric criterion's cited evidence against workspace AST/JSON/YAML integrity, tool outputs, and file snapshots — automatically certifying unanimous, artifact-grounded runs without stalling on manual human labeling. When human calibration is desired, `autoharness review` and `autoharness calibrate` compute exact agreement and **Cohen's κ**.

---

## Anti-Gaming & Safety Guardrails

An autonomous fixer is only useful if it **cannot cheat the evaluation**:

- **Write-Protected Tests & Cases**: During code healing (`healer.py`), test files (`test_*.py`, `tests/**`) are snapshotted beforehand and verified **byte-for-byte identical** afterward. During harness fixing (`loop.py`), only `fixer.editable` files can be touched.
- **Hidden Holdout Split**: `split: holdout` cases are never shown to the fixer's `propose()` or `compose()` prompts, yet any drop on holdout cases immediately rejects the candidate.
- **Static Leak Detection**: Candidate edits that paste case IDs, filenames, or expected literal answers into the harness are rejected *before* spending a single token running probes.
- **Isolated Parallel Workspaces**: All probes and repairs run in throwaway temporary copies; real workspace files are updated atomically only after a candidate passes every gate.
- **Cost & Bloat Caps**: Enforces `max_usd_per_trigger`, `max_iters`, `max_token_increase_pct`, and `max_growth_pct`.

---

## Model-Agnostic Substrates

Run and optimize harnesses around **any** model or CLI agent (`agent.type` / `fixer.backend`):

- **`command` (Default — Zero API Key)**: Headless **Claude Code** (`claude -p`), Codex, OpenCode, or any custom CLI agent (`{prompt}`, `{workdir}`, `{system_prompt_file}`).
- **`claude`**: Anthropic Messages API (`ANTHROPIC_API_KEY`).
- **`gemini`**: Google Gemini API (`GEMINI_API_KEY`, zero extra dependencies).
- **`openai`**: OpenAI, OpenRouter, vLLM, or local Ollama (`OPENAI_API_KEY` / `OPENAI_BASE_URL`).
- **`scripted`**: Deterministic offline replay for CI smoke tests.

---

## CLI Reference (`autoharness` / `ah`)

| Command | Description |
|---|---|
| `autoharness init [DIR]` | Scaffold config, 8-component `harnesses/harness2/`, and example cases |
| `autoharness demo` | Run offline code self-healing + Harness² `Propose → Probe → Compose` demo |
| `autoharness watch [--now]` | Continuous closed loop on code & harness changes |
| `autoharness heal [PATHS...]` | Scan source code, repair broken modules in isolated sandboxes, sync `memory.md` |
| `autoharness synthesize [PATHS...]` | Auto-generate executable `dev` & `holdout` YAML eval cases from source code |
| `autoharness run [--gate] [--autonomous]` | Run evaluation suite, diff against baseline, and render HTML report |
| `autoharness fix [--retry]` | Run one multi-candidate Harness² contrastive fix session |
| `autoharness tree [--init DIR]` | Inspect or scaffold an 8-component Harness² directory |
| `autoharness hook {install,uninstall,status}` | Manage automatic Git `pre-commit` / `post-commit` hooks |
| `autoharness mine` / `auto` | Mine real failure episodes from session logs / run hands-off CI pipeline |
| `autoharness lessons` | Inspect persistent `DO` and `AVOID` engineering lessons |
| `autoharness review` / `calibrate` | Optional human-in-the-loop labeling & Cohen's κ calibration |
| `autoharness diff` / `baseline` / `report` / `cases` | Run inspection utilities |

---

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — Dual-level recursion, module map, and data flow
- [docs/CASES.md](docs/CASES.md) — Authoring & auto-synthesizing cases + full check reference
- [docs/CONFIG.md](docs/CONFIG.md) — Complete `autoharness.yaml` reference
- [CONTRIBUTING.md](CONTRIBUTING.md) — Development setup and offline testing guidelines

## Acknowledgements & Inspiration

Inspired by [Google Research's Harness² (`google-research/harness2`)](https://github.com/google-research/harness2) (*Recursive Agent Harnessing for an Open World*), extending recursive `Propose → Probe → Compose` harness refinement and persistent lesson ledgers into a continuous, regression-gated code + harness engineering loop.

## Author & License

Created by **pritampanda15** (<pritampkp15@gmail.com>) · [github.com/pritampanda15/autoharness](https://github.com/pritampanda15/autoharness)

Licensed under the [Apache License 2.0](LICENSE).
