"""On-disk layout:

runs/
  BASELINE                  -> id of the run new runs are compared against
  calibration.json          -> latest judge-vs-human agreement
  <run_id>/
    meta.json               -> config snapshot, harness fingerprint, timing
    summary.json            -> per-case + suite aggregates
    results.jsonl           -> one TrialResult per line
    traces/<case>__t<n>.json
    report.html
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from .trace import Trace


class Store:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def new_run(self, label: str = "") -> tuple[str, Path]:
        stem = time.strftime("%Y%m%d-%H%M%S") + (f"-{label}" if label else "")
        run_id, n = stem, 1
        while (self.root / run_id).exists():  # two runs in the same second
            n += 1
            run_id = f"{stem}-{n}"
        d = self.root / run_id
        (d / "traces").mkdir(parents=True)
        return run_id, d

    def run_dir(self, run_id: str) -> Path:
        if run_id in ("latest", "last"):
            run_id = self.latest()
        elif run_id == "baseline":
            run_id = self.baseline() or ""
        d = self.root / run_id
        if not run_id or not d.exists():
            raise FileNotFoundError(f"no such run: {run_id!r}")
        return d

    def runs(self) -> list[str]:
        return sorted(p.name for p in self.root.iterdir() if p.is_dir() and (p / "summary.json").exists())

    def latest(self) -> str:
        r = self.runs()
        if not r:
            raise FileNotFoundError("no completed runs yet")
        return r[-1]

    def baseline(self) -> str | None:
        f = self.root / "BASELINE"
        return f.read_text().strip() if f.exists() else None

    def set_baseline(self, run_id: str) -> None:
        self.run_dir(run_id)
        (self.root / "BASELINE").write_text(run_id)

    # -------------------------------------------------------------- io helpers
    @staticmethod
    def write_json(path: Path, data: Any) -> None:
        path.write_text(json.dumps(data, indent=2, default=str))

    @staticmethod
    def read_json(path: Path) -> Any:
        return json.loads(path.read_text())

    def save_trace(self, run_dir: Path, trace: Trace) -> None:
        self.write_json(run_dir / "traces" / f"{_safe(trace.case_id)}__t{trace.trial}.json", trace.to_dict())

    def load_trace(self, run_dir: Path, case_id: str, trial: int) -> Trace:
        return Trace.from_dict(self.read_json(run_dir / "traces" / f"{_safe(case_id)}__t{trial}.json"))

    def load_results(self, run_dir: Path) -> list[dict[str, Any]]:
        f = run_dir / "results.jsonl"
        return [json.loads(l) for l in f.read_text().splitlines() if l.strip()] if f.exists() else []

    def load_summary(self, run_id: str) -> dict[str, Any]:
        return self.read_json(self.run_dir(run_id) / "summary.json")

    # -------------------------------------------------------------- calibration
    def calibration(self) -> dict[str, Any] | None:
        f = self.root / "calibration.json"
        return self.read_json(f) if f.exists() else None

    def save_calibration(self, data: dict[str, Any]) -> None:
        self.write_json(self.root / "calibration.json", data)


def fingerprint(*texts: str) -> str:
    h = hashlib.sha256()
    for t in texts:
        h.update(t.encode())
    return h.hexdigest()[:12]


def _safe(s: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in s)
