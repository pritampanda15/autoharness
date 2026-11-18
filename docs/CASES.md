# Writing & Auto-Synthesizing Cases

You can either **auto-synthesize** cases directly from your codebase (`autoharness synthesize src/`) or author curated YAML cases in `cases/golden/`.

```yaml
id: fix-off-by-one
description: Fix a bug so the tests pass, with a minimal edit and verification.
tags: [coding, bugfix]
split: dev                  # dev: visible to fixer on failure. holdout: hidden guard against overfitting
status: active              # draft cases (e.g. mined) are skipped until promoted to active
trials: 3                   # optional per-case override
prompt: |
  The tests in test_stats.py are failing. Fix the bug in stats.py so they pass.
setup:
  files:
    stats.py: "..."
    test_stats.py: "..."
  commands: ["pip install -q -r requirements.txt"]
checks:
  - {name: syntax, type: python_syntax, path: stats.py}
  - {name: tests-pass, type: command, run: PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q}
  - {name: read-before-write, type: read_before_write, paths: [stats.py]}
  - {type: verified_after_edit}
  - {type: max_turns, value: 8}
  - name: minimal-fix
    type: judge
    include_files: [stats.py]
    rubric: |
      1. The fix corrects the loop bound rather than special-casing the tests.
      2. The rest of the function is unchanged.
mock:                       # optional: scripted replay for offline CI tests
  actions:
    - {tool: read_file, input: {path: stats.py}}
  output: "..."
```

Every check accepts optional `name`, `required` (default `true`), and `weight` (default `1.0`).

## Layer 1: Deterministic Checks (`graders/deterministic.py`)

| `type` | Fields | Passes when |
|---|---|---|
| `command` | `run`, `exit_code` (`0`), `stdout_pattern`, `timeout_s` | Post-run command exits with expected code and pattern |
| `python_syntax` | `path` (optional; defaults to all `.py` files) | Python AST parses with zero `SyntaxError`s |
| `imports_clean` | `module`, `timeout_s` | `import <module>` succeeds inside the workspace |
| `workspace_integrity` | — | Every `.py`, `.json`, and `.yaml`/`.yml` file in the workspace parses cleanly |
| `file_exists` / `file_absent` | `path` | File presence/absence matches |
| `file_contains` / `file_not_contains` | `path`, `pattern` (regex) | Regex matches / does not match file contents |
| `file_unchanged` | `path`, `content` | File remains byte-for-byte identical (protects unrelated files & tests) |
| `output_contains` | `text`, `ignore_case` (`true`) | Final agent response contains `text` |
| `output_matches` / `output_not_matches` | `pattern` | Regex matches / does not match final output |
| `output_max_chars` | `value` | Final output length ≤ `value` |
| `json_valid` | `path` (or final output), `required_keys` | Valid JSON object containing all `required_keys` |

## Layer 2: Behavioral Trace Checks (`graders/trace_checks.py`)

Tool names are normalized (`read_file`, `write_file`, `run_bash`, `list_files`), mapping Claude Code's `Read`, `Edit`, `Write`, `Bash`, `Glob`, and `LS` automatically.

| `type` | Fields | Passes when |
|---|---|---|
| `max_turns` / `max_tokens` / `max_tool_calls` / `max_tool_errors` | `value` | Metric stays within budget |
| `must_call_tool` / `must_not_call_tool` | `tool`, `input_pattern`, `min_times` | Tool invocation count/pattern constraint holds |
| `tool_order` | `before`, `after` | `before` tool precedes first `after` tool |
| `read_before_write` | `paths` | Every edited file was inspected first (`read_file` or `cat`/`sed`/`grep`) |
| `verified_after_edit` | `pattern` | A test or verification command ran *after* the final file edit |
| `no_loops` | `max_repeats` (`3`) | No identical tool call repeated more than `max_repeats` times |
| `no_error` | — | Trial completed without adapter or API errors |

## Layer 3: Grounded LLM Judge (`graders/judge.py` + `graders/autonomous.py`)

| Field | Meaning |
|---|---|
| `rubric` | Criteria evaluated independently across `samples` votes (pass requires all criteria met) |
| `reference` | Optional reference solution |
| `include_files` | Workspace files snapshotted post-run and supplied as evidence to the judge |
| `samples` | Self-consistency votes per check (default `3`) |
