"""Failure mining: turn real sessions where the agent went wrong into draft eval cases.

Reads Claude Code transcripts (~/.claude/projects/<project>/<session>.jsonl) and
looks for signals that a human had to step in:
  - the user interrupted the agent
  - the user's next message is a correction ("no", "that's wrong", "still failing", ...)
  - runs of failing tool calls

Each hit becomes a `status: draft` case in cases/mined/. The prompt that led to
the failure becomes the case prompt, and the human's correction becomes the
judge rubric. Drafts are not run until you set `status: active` (or run with
--include-drafts), so a person approves each new case once; after that it is
a permanent regression test.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import yaml

CORRECTION = re.compile(
    r"^\s*(no\b|nope|wrong|that'?s (not|wrong)|not what i|you (broke|forgot|missed|didn'?t)|"
    r"still\b|does ?n[o']?t (work|make sense)|did ?n[o']?t work|why did you|stop\b|undo|revert|"
    r"incorrect|try again|that is not|this is not|not working|it'?s broken)",
    re.IGNORECASE,
)
INTERRUPT = "[Request interrupted"


@dataclass
class Episode:
    session: str
    project: str
    prompt: str
    correction: str
    signal: str
    tool_errors: int
    assistant_summary: str


def _user_text(rec: dict[str, Any]) -> str | None:
    if rec.get("type") != "user" or rec.get("isMeta"):
        return None
    c = (rec.get("message") or {}).get("content")
    if isinstance(c, str):
        text = c
    elif isinstance(c, list):
        if any(b.get("type") == "tool_result" for b in c if isinstance(b, dict)):
            return None
        text = "\n".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
    else:
        return None
    text = text.strip()
    # Skip harness-injected wrappers (command output, reminders); keep what a person typed.
    if not text or text.startswith("<") and not text.startswith(INTERRUPT):
        return None
    return text


def episodes_from_transcript(path: Path, max_tool_error_run: int = 3) -> Iterator[Episode]:
    recs = []
    for line in path.read_text(errors="replace").splitlines():
        try:
            recs.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    prompt: str | None = None
    tool_errors = 0
    err_run = 0
    assistant_text: list[str] = []
    for rec in recs:
        msg = rec.get("message") or {}
        content = msg.get("content")
        if rec.get("type") == "assistant" and isinstance(content, list):
            for b in content:
                if b.get("type") == "text" and b.get("text"):
                    assistant_text.append(b["text"])
            continue
        if rec.get("type") == "user" and isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    if b.get("is_error"):
                        tool_errors += 1
                        err_run += 1
                    else:
                        err_run = 0
        text = _user_text(rec)
        if text is None:
            continue
        signal = None
        if text.startswith(INTERRUPT):
            signal = "interrupted"
        elif prompt and CORRECTION.search(text):
            signal = "correction"
        elif prompt and err_run >= max_tool_error_run:
            signal = "tool_error_run"
        if signal and prompt:
            yield Episode(session=path.stem, project=path.parent.name, prompt=prompt,
                          correction=text if signal != "interrupted" else "",
                          signal=signal, tool_errors=tool_errors,
                          assistant_summary=(assistant_text[-1] if assistant_text else "")[:1500])
        if not text.startswith(INTERRUPT) and not (signal == "correction"):
            prompt = text  # a fresh instruction starts a new episode
        tool_errors = err_run = 0
        assistant_text = []


def draft_case(ep: Episode) -> dict[str, Any]:
    cid = "mined-" + hashlib.sha1((ep.session + ep.prompt).encode()).hexdigest()[:10]
    if ep.correction:
        rubric = (
            "In a real session, an agent given this task was corrected by the user with:\n"
            f'  "{ep.correction[:800]}"\n'
            "PASS only if the agent's work would NOT have needed this correction: it addresses the "
            "concern above and fully completes the task as asked."
        )
    elif ep.signal == "interrupted":
        rubric = ("In a real session the user interrupted the agent on this task (it was going the wrong way "
                  "or taking too long). PASS only if the agent stays on the task as asked, does not take "
                  "unrequested actions, and finishes efficiently.")
    else:
        rubric = ("In a real session the agent hit a run of failing tool calls on this task. PASS only if the "
                  "agent completes the task without repeatedly retrying failing commands.")
    return {
        "id": cid,
        "status": "draft",
        "description": f"Mined from {ep.project}/{ep.session} ({ep.signal}). Review the prompt and setup, then set status: active.",
        "tags": ["mined", ep.signal],
        "prompt": ep.prompt,
        "setup": {"files": {}},
        "checks": [
            {"type": "no_error"},
            {"type": "no_loops", "max_repeats": 3},
            {"name": "would-not-need-correction", "type": "judge", "rubric": rubric},
        ],
        "source_excerpt": {"last_assistant_message": ep.assistant_summary},
    }


def mine(transcripts: list[Path], out_dir: Path, limit: int = 50, existing_ids: set[str] | None = None) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    existing_ids = existing_ids or set()
    written: list[Path] = []
    for t in transcripts:
        for ep in episodes_from_transcript(t):
            case = draft_case(ep)
            if case["id"] in existing_ids or len(ep.prompt) < 15:
                continue
            existing_ids.add(case["id"])
            f = out_dir / f"{case['id']}.yaml"
            f.write_text(yaml.safe_dump(case, sort_keys=False, allow_unicode=True, width=100))
            written.append(f)
            if len(written) >= limit:
                return written
    return written


def find_transcripts(project_glob: str = "*", root: Path | None = None, newest: int = 20) -> list[Path]:
    root = root or Path.home() / ".claude" / "projects"
    files = [p for d in root.glob(project_glob) if d.is_dir() for p in d.glob("*.jsonl")]
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)[:newest]
