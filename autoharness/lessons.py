"""Persistent Lesson Ledger & Improvement Library (`lessons/lessons.json` + `LESSONS.md`).

Implements Harness² harnessing-level recursion: every Propose-Probe-Compose or Code-Heal
session distills empirically validated `do` and `avoid` lessons backed by run IDs so
subsequent sessions inherit learned engineering priors and never repeat failed edits.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


class LessonStore:
    def __init__(self, path: Path, max_lessons: int = 25):
        self.path = path
        self.max = max_lessons

    def load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            return json.loads(self.path.read_text()).get("lessons", [])
        except (json.JSONDecodeError, AttributeError):
            return []

    def save(self, lessons: list[dict[str, Any]]) -> None:
        lessons = [l for l in lessons if l.get("lesson")][: self.max]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"updated": time.strftime("%Y-%m-%d %H:%M:%S"), "lessons": lessons}, indent=1))
        md = [
            "# Lessons\n",
            "Learned by autoharness from evaluated Propose-Probe-Compose and self-healing sessions. "
            "Maintained automatically by the reflector; safe to edit by hand.\n",
        ]
        for l in lessons:
            mark = "DO" if l.get("polarity") == "do" else "AVOID"
            runs = ", ".join(l.get("evidence_runs", [])[:4])
            md.append(f"- **{mark}** {l['lesson']}  _(support {l.get('support', 1)}; runs: {runs})_")
        (self.path.parent / "LESSONS.md").write_text("\n".join(md) + "\n")

    @staticmethod
    def fallback(lessons: list[dict[str, Any]], session: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """No reflector model available: record each evaluated outcome directly as a lesson."""
        out = list(lessons)
        for s in session:
            if s.get("accepted"):
                out.insert(
                    0,
                    {
                        "polarity": "do",
                        "lesson": f"Worked: {s.get('approach')} - {s.get('diagnosis', '')[:200]}",
                        "support": 1,
                        "evidence_runs": [s.get("run", "")],
                    },
                )
            elif s.get("reasons"):
                out.append(
                    {
                        "polarity": "avoid",
                        "lesson": f"Rejected: {s.get('approach')} ({'; '.join(s['reasons'])[:200]})",
                        "support": 1,
                        "evidence_runs": [s.get("run") or "not run"],
                    }
                )
        return out
