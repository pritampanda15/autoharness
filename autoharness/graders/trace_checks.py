"""Layer 2: trace checks. They grade *how* the agent worked, not just the result.

This is where most harness regressions show up first: more turns, wasted tokens,
loops, skipped verification, or tools it should never touch.
"""

from __future__ import annotations

import json
import re
from collections import Counter


def max_turns(spec, trace, sandbox):
    return trace.turns <= spec["value"], f"{trace.turns} turns (max {spec['value']})"


def max_tokens(spec, trace, sandbox):
    return trace.total_tokens <= spec["value"], f"{trace.total_tokens} tokens (max {spec['value']})"


def max_tool_calls(spec, trace, sandbox):
    n = len(trace.tool_calls)
    return n <= spec["value"], f"{n} tool calls (max {spec['value']})"


def max_tool_errors(spec, trace, sandbox):
    n = sum(tc.is_error for tc in trace.tool_calls)
    return n <= spec.get("value", 0), f"{n} tool errors (max {spec.get('value', 0)})"


def _matching(spec, trace):
    pat = spec.get("input_pattern")
    for tc in trace.tool_calls:
        if tc.name != spec["tool"]:
            continue
        if pat and not re.search(pat, json.dumps(tc.input, sort_keys=True)):
            continue
        yield tc


def must_call_tool(spec, trace, sandbox):
    n = sum(1 for _ in _matching(spec, trace))
    want = spec.get("min_times", 1)
    desc = spec["tool"] + (f" matching /{spec['input_pattern']}/" if spec.get("input_pattern") else "")
    return n >= want, f"{desc} called {n}x (min {want})"


def must_not_call_tool(spec, trace, sandbox):
    n = sum(1 for _ in _matching(spec, trace))
    desc = spec["tool"] + (f" matching /{spec['input_pattern']}/" if spec.get("input_pattern") else "")
    return n == 0, f"{desc} called {n}x (must be 0)"


def tool_order(spec, trace, sandbox):
    """`before` must be called at least once before the first `after` (e.g. read before write)."""
    names = [tc.name for tc in trace.tool_calls]
    if spec["after"] not in names:
        return True, f"{spec['after']} never called"
    first_after = names.index(spec["after"])
    ok = spec["before"] in names[:first_after]
    return ok, f"{spec['before']} {'did' if ok else 'did not'} precede first {spec['after']}"


READ_CMD = re.compile(r"\b(cat|head|tail|less|more|sed|awk|grep|rg|nl|bat)\b")


def read_before_write(spec, trace, sandbox):
    """Every file the agent edits was looked at first - via a read tool OR a shell read (cat, sed, grep…).
    Files the agent created from scratch are exempt unless listed in `paths`."""
    seen_reads: list[str] = []
    problems = []
    must = set(spec.get("paths", []))
    for tc in trace.tool_calls:
        if tc.name == "read_file":
            seen_reads.append(str(tc.input.get("path", "")))
        elif tc.name == "run_bash" and READ_CMD.search(str(tc.input.get("command", ""))):
            seen_reads.append(str(tc.input.get("command", "")))
        elif tc.name == "write_file":
            path = str(tc.input.get("path", ""))
            base = path.rsplit("/", 1)[-1]
            is_edit = "old_string" in tc.input or any(path.endswith(m) for m in must)
            if is_edit and not any(base and base in r for r in seen_reads):
                problems.append(base)
    return not problems, ("edited without reading first: " + ", ".join(problems)) if problems \
        else "every edited file was read first"


def verified_after_edit(spec, trace, sandbox):
    """After the last write, the agent ran a verification command (tests, lint, run)."""
    pat = spec.get("pattern", r"pytest|test|lint|ruff|mypy|python |node |npm |make")
    last_write = max((i for i, tc in enumerate(trace.tool_calls) if tc.name in ("write_file", "Write", "Edit")), default=-1)
    if last_write < 0:
        return True, "no edits made"
    for tc in trace.tool_calls[last_write + 1:]:
        if tc.name in ("run_bash", "Bash") and re.search(pat, json.dumps(tc.input)):
            return True, f"verified with {tc.input}"
    return False, "no verification command after the last edit"


def no_loops(spec, trace, sandbox):
    limit = spec.get("max_repeats", 3)
    counts = Counter((tc.name, json.dumps(tc.input, sort_keys=True)) for tc in trace.tool_calls)
    worst = counts.most_common(1)
    if not worst:
        return True, "no tool calls"
    (name, _), n = worst[0]
    return n <= limit, f"most repeated identical call: {name} x{n} (max {limit})"


def no_error(spec, trace, sandbox):
    return trace.error is None, f"run error: {trace.error}" if trace.error else "run completed"


CHECKS = {
    "max_turns": max_turns,
    "max_tokens": max_tokens,
    "max_tool_calls": max_tool_calls,
    "max_tool_errors": max_tool_errors,
    "must_call_tool": must_call_tool,
    "must_not_call_tool": must_not_call_tool,
    "tool_order": tool_order,
    "read_before_write": read_before_write,
    "verified_after_edit": verified_after_edit,
    "no_loops": no_loops,
    "no_error": no_error,
}
