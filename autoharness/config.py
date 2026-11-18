"""autoharness.yaml: agent substrate, 8-component Harness² config, autonomous evaluator & continuous loop."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULTS: dict[str, Any] = {
    "cases": ["cases/golden"],
    "code_paths": [],  # source code dirs/files watched & healed automatically when code changes
    "runs_dir": "runs",
    "trials": 3,
    "workers": 4,
    "agent": {
        "type": "claude",  # claude | gemini | openai | command | scripted
        "model": "claude-opus-5-5",
        "effort": "medium",
        "system_prompt_file": "harnesses/default/system.md",  # file OR 8-component Harness² directory
        "max_turns": 20,
        "max_tokens": 16000,
        "bash_timeout_s": 60,
    },
    "judge": {
        "backend": "api",  # claude-cli | api
        "model": "claude-opus-5-5",
        "effort": "high",
        "samples": 3,  # self-consistency votes per judge check
        "min_confidence": 0.7,  # below this -> human review queue
        "skip_if_deterministic_fails": True,
        "autonomous": False,  # auto-certify unanimous evidence-grounded verdicts without manual labels
    },
    "calibration": {
        "labels_file": "labels/calibration.jsonl",
        "min_agreement": 0.85,  # judge must agree with humans this often to be trusted
        "min_labels": 20,
    },
    "fixer": {
        "backend": "claude-cli",  # claude-cli | api | gemini | openai
        "model": "claude-opus-5-5",
        "effort": "high",
        "editable": [],  # harness files OR Harness² component directories the fixer may edit
        "candidates": 3,  # diverse candidate edits proposed per attempt (invariant / skill / guardrail)
        "parallel": 3,  # candidates probed simultaneously in isolated workspaces
        "compose": True,  # contrastive composition across probed candidates
        "reflect": True,  # distil each session into the persistent lesson ledger
        "lessons_file": "lessons/lessons.json",
        "max_lessons": 25,
        "workspace_include": [],  # extra files/dirs a probe workspace needs
        "max_iters": 3,  # fix attempts per trigger
        "max_usd_per_trigger": 5.0,
        "max_token_increase_pct": 30,
        "max_growth_pct": 100,
    },
    "healer": {
        "enabled": True,  # auto-heal broken code in `code_paths` during `watch` / `heal`
        "max_attempts": 2,
        "sync_memory": True,  # keep Harness² `memory.md` synchronized with codebase architecture
        "synthesize_cases": False,  # auto-generate `cases/auto/` YAML specs from code_paths
    },
    "loop": {
        "watch": [],  # defaults to editable + cases + code_paths
        "interval_s": 3,
        "debounce_s": 5,
        "autofix": True,
        "notify": True,
    },
    "gate": {
        "max_pass_rate_drop": 0.05,
        "max_case_drop": 0.34,
    },
}


@dataclass
class Config:
    raw: dict[str, Any] = field(default_factory=dict)
    root: Path = Path(".")

    def __getitem__(self, key: str) -> Any:
        return self.raw[key]

    def path(self, rel: str) -> Path:
        p = Path(rel)
        return p if p.is_absolute() else self.root / p


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = _merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


def load_config(path: str | Path = "autoharness.yaml") -> Config:
    p = Path(path)
    data = yaml.safe_load(p.read_text()) if p.exists() else {}
    return Config(raw=_merge(DEFAULTS, data or {}), root=p.resolve().parent)


def claude_cli(explicit: str | None = None) -> str:
    """Which `claude` binary to use: explicit config, then `$AUTOHARNESS_CLAUDE` /
    `$CLAUDE_CODE_EXECPATH`, then whatever is on PATH."""
    import os
    import shutil

    if explicit:
        return explicit
    env = os.environ.get("AUTOHARNESS_CLAUDE") or os.environ.get("CLAUDE_CODE_EXECPATH")
    if env and os.path.exists(env):
        return env
    return shutil.which("claude") or "claude"
