---
name: autoharness
description: Autonomous 8-component Harness² engineering, code AST/import/pytest self-healing with byte-locked unit tests, contrastive Propose-Probe-Compose harness repair, and 3-layer evaluation via AutoHarness MCP tools and CLI. Use when testing, healing broken modules, synthesizing eval cases, inspecting Harness² files, or preventing regressions.
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
