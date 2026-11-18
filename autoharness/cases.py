"""Eval cases: YAML files describing a task, its starting files, and its checks."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Case:
    id: str
    prompt: str
    checks: list[dict[str, Any]]
    description: str = ""
    tags: list[str] = field(default_factory=list)
    files: dict[str, str] = field(default_factory=dict)
    setup_commands: list[str] = field(default_factory=list)
    trials: int | None = None
    status: str = "active"  # active | draft (mined, awaiting promotion) | disabled
    split: str = "dev"  # dev | holdout (never shown to an automated fixer)
    mock: dict[str, Any] | None = None  # scripted agent behaviour, for offline runs
    source: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any], source: str = "") -> "Case":
        if "id" not in d or "prompt" not in d:
            raise ValueError(f"{source}: a case needs at least 'id' and 'prompt'")
        setup = d.get("setup") or {}
        return cls(
            id=str(d["id"]),
            prompt=d["prompt"],
            checks=d.get("checks") or [],
            description=d.get("description", ""),
            tags=d.get("tags") or [],
            files=setup.get("files") or {},
            setup_commands=setup.get("commands") or [],
            trials=d.get("trials"),
            status=d.get("status", "active"),
            split=d.get("split", "dev"),
            mock=d.get("mock"),
            source=source,
        )


def load_cases(
    paths: list[str | Path],
    tags: list[str] | None = None,
    ids: list[str] | None = None,
    include_drafts: bool = False,
    split: str | None = None,
) -> list[Case]:
    cases: list[Case] = []
    seen: dict[str, str] = {}
    for p in paths:
        p = Path(p)
        files = sorted(p.rglob("*.y*ml")) if p.is_dir() else [p]
        for f in files:
            docs = list(yaml.safe_load_all(f.read_text()))
            for doc in docs:
                if not doc:
                    continue
                for d in doc if isinstance(doc, list) else [doc]:
                    c = Case.from_dict(d, source=str(f))
                    if c.id in seen:
                        raise ValueError(f"duplicate case id {c.id!r} in {f} and {seen[c.id]}")
                    seen[c.id] = str(f)
                    cases.append(c)
    out = []
    for c in cases:
        if c.status == "disabled" or (c.status == "draft" and not include_drafts):
            continue
        if tags and not set(tags) & set(c.tags):
            continue
        if ids and c.id not in ids:
            continue
        if split and c.split != split:
            continue
        out.append(c)
    return out
