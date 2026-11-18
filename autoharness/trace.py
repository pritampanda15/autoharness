"""The record of one agent trial. Every grader reads from this."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class ToolCall:
    name: str
    input: dict[str, Any]
    output: str = ""
    is_error: bool = False


@dataclass
class Trace:
    case_id: str
    trial: int
    final_output: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    turns: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    duration_s: float = 0.0
    cost_usd: float = 0.0  # reported by the agent itself when it knows (e.g. Claude Code)
    stop_reason: str = ""
    error: str | None = None
    workdir: str = ""
    messages: list[dict[str, Any]] = field(default_factory=list)
    # Files snapshotted from the workspace after the run (for judges and for re-grading later).
    artifacts: dict[str, str] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["total_tokens"] = self.total_tokens
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Trace":
        d = dict(d)
        d.pop("total_tokens", None)
        d["tool_calls"] = [ToolCall(**tc) for tc in d.get("tool_calls", [])]
        return cls(**d)

    def transcript_text(self, max_chars: int = 60_000) -> str:
        """Compact, judge-readable rendering of what the agent did."""
        lines = []
        for i, tc in enumerate(self.tool_calls, 1):
            out = tc.output if len(tc.output) < 1500 else tc.output[:1500] + " …[truncated]"
            flag = " (ERROR)" if tc.is_error else ""
            lines.append(f"[{i}] {tc.name}({_short(tc.input)}){flag}\n    -> {out}")
        lines.append(f"\nFINAL OUTPUT:\n{self.final_output}")
        text = "\n".join(lines)
        if len(text) > max_chars:
            # Keep the beginning and the end; the middle of long traces is usually repetitive.
            half = max_chars // 2
            text = text[:half] + "\n…[middle of trace omitted]…\n" + text[-half:]
        return text


def _short(obj: Any, limit: int = 300) -> str:
    s = repr(obj)
    return s if len(s) <= limit else s[:limit] + "…"
