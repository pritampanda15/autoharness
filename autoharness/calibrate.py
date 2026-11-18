"""Judge calibration: how often does the LLM judge agree with a human?

The loop that keeps humans involved but rare:
  1. `autoharness review`    - a human labels only the trials in the review queue
  2. labels are appended to labels/calibration.jsonl
  3. `autoharness calibrate` - measures judge/human agreement on all labels
  4. once agreement >= threshold on enough labels, the judge is trusted and the
     review queue shrinks to just the split-vote / low-confidence trials.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .cases import Case
from .graders.judge import Judge
from .store import Store


def load_labels(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def append_label(path: Path, label: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps({**label, "labeled_at": time.strftime("%Y-%m-%d %H:%M:%S")}) + "\n")


def _latest_per_key(labels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A relabel of the same trial/check overrides the earlier one."""
    out: dict[tuple, dict[str, Any]] = {}
    for l in labels:
        out[(l["run_id"], l["case_id"], l["trial"], l["check"])] = l
    return list(out.values())


def calibrate(store: Store, labels_path: Path, cases: dict[str, Case], judge: Judge | None,
              judge_model: str, rejudge: bool = False) -> dict[str, Any]:
    labels = _latest_per_key(load_labels(labels_path))
    tp = tn = fp = fn = 0
    disagreements = []
    for l in labels:
        if rejudge:
            case = cases.get(l["case_id"])
            if case is None or judge is None:
                continue
            spec = next((c for i, c in enumerate(case.checks)
                         if (c.get("name") or f"{c.get('type')}#{i}") == l["check"]), None)
            if spec is None:
                continue
            trace = store.load_trace(store.run_dir(l["run_id"]), l["case_id"], l["trial"])
            judge_pass = judge.evaluate(case, trace, spec).passed
        else:
            judge_pass = l.get("judge_pass")
            # Stored verdicts only count for the judge that produced them; use --rejudge after a judge change.
            if judge_pass is None or l.get("judge_model") != judge_model:
                continue
        human = bool(l["human_pass"])
        if judge_pass and human:
            tp += 1
        elif not judge_pass and not human:
            tn += 1
        elif judge_pass and not human:
            fp += 1
            disagreements.append({**l, "judge_pass": judge_pass, "kind": "judge too lenient"})
        else:
            fn += 1
            disagreements.append({**l, "judge_pass": judge_pass, "kind": "judge too strict"})
    n = tp + tn + fp + fn
    agreement = (tp + tn) / n if n else 0.0
    # Cohen's kappa: agreement corrected for chance (a judge that always says "pass"
    # on a mostly-passing set looks good on raw agreement but scores ~0 here).
    if n:
        pe = ((tp + fp) * (tp + fn) + (fn + tn) * (fp + tn)) / (n * n)
        kappa = (agreement - pe) / (1 - pe) if pe < 1 else 1.0
    else:
        kappa = 0.0
    result = {
        "judge_model": judge_model, "n": n, "agreement": round(agreement, 4), "kappa": round(kappa, 4),
        "confusion": {"tp": tp, "tn": tn, "fp": fp, "fn": fn}, "rejudged": rejudge,
        "at": time.strftime("%Y-%m-%d %H:%M:%S"), "disagreements": disagreements[:50],
    }
    store.save_calibration(result)
    return result
