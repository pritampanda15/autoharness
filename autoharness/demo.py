"""`autoharness demo`: full offline demonstration of Code Self-Healing + Harness² Propose-Probe-Compose.

Runs completely offline with zero API keys in seconds:
1. Scaffolds an 8-component Harness² tree + a sample codebase with a buggy module (`src/pricing.py`).
2. Runs Autonomous Code Self-Healing (`heal_codebase`): detects the failing test, repairs `src/pricing.py`
   inside an isolated copy while guarding `tests/test_pricing.py` against tampering, and verifies zero regressions.
3. Runs Harness² Contrastive Refinement (`cycle` -> `autofix`): proposes 3 diverse candidate harness edits
   (one leaks a case-specific answer, one breaks hidden holdout cases, one states a general rule),
   probes survivors in parallel isolated workspaces, keeps the general improvement, and updates `LESSONS.md`.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from .agents import ScriptedAgent, execute_tool
from .config import load_config
from .fixer import Proposal
from .graders.judge import Judge
from .healer import heal_codebase
from .lessons import LessonStore
from .store import Store
from .trace import ToolCall, Trace
from .tree import HarnessTree

EXAMPLES = Path(__file__).parent / "examples"

BUGGY_PRICING = '''"""Order discount calculator."""


def apply_discount(subtotal: float, pct: float) -> float:
    """Return subtotal after subtracting `pct` percent (0..100)."""
    # BUG: subtracts raw pct instead of percentage of subtotal
    return round(subtotal - pct, 2)
'''

FIXED_PRICING = '''"""Order discount calculator."""


def apply_discount(subtotal: float, pct: float) -> float:
    """Return subtotal after subtracting `pct` percent (0..100)."""
    return round(subtotal * (1 - pct / 100.0), 2)
'''

PRICING_TEST = '''from src.pricing import apply_discount


def test_twenty_percent_off():
    assert apply_discount(200.0, 20.0) == 160.0


def test_zero_discount():
    assert apply_discount(49.99, 0.0) == 49.99
'''


class DemoAgent:
    """Scripted agent whose behavior responds to the harness text AND can repair `src/pricing.py`."""

    def __init__(self, prompt_file: Path):
        p = Path(prompt_file)
        self.text = HarnessTree.load(p).compile_prompt() if p.exists() else ""

    def run(self, case, sb, trial):
        if case.id == "heal-pricing":
            trace = Trace(case_id=case.id, trial=trial, workdir=str(sb.dir))
            for tool, inp in [
                ("read_file", {"path": "src/pricing.py"}),
                ("read_file", {"path": "tests/test_pricing.py"}),
                ("write_file", {"path": "src/pricing.py", "content": FIXED_PRICING}),
                ("run_bash", {"command": "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q tests/test_pricing.py"}),
            ]:
                out, err = execute_tool(sb, tool, inp)
                trace.tool_calls.append(ToolCall(tool, inp, out, err))
            trace.final_output = "Fixed percentage calculation in apply_discount and verified with pytest."
            trace.turns = 4
            return trace

        t = ScriptedAgent().run(case, sb, trial)
        if case.id == "concise-answer" and "only the answer" not in self.text.lower():
            t.final_output += " The separate metrics endpoint uses port 9100."
        if "SABOTAGE" in self.text and case.split == "holdout":
            t.final_output = "no idea"
        return t


class _PassingJudgeClient:
    """Offline stand-in for the judge client: returns grounded high-confidence pass verdicts."""

    def __init__(self):
        self.messages = self

    def create(self, **kw):
        prompt_txt = str((kw.get("messages") or [{}])[0].get("content", ""))
        snippet = "moving_average range(len(xs) - k + 1)" if "stats.py" in prompt_txt else "before deleting anything data/"
        v = {
            "criteria": [{"criterion": "rubric", "met": True, "evidence": snippet}],
            "reasoning": "meets the rubric",
            "verdict": "pass",
            "score": 1.0,
            "confidence": 0.95,
        }
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps(v))])


Edit = Callable[[str], str]


class ScriptedFixer:
    """Fixer with canned candidates: attempts[i] is the list of edits proposed on attempt i."""

    def __init__(self, attempts: list[list[Edit]], compose: Edit | None = None):
        self.attempts = list(attempts)
        self.compose_edit = compose
        self.calls: list[dict[str, Any]] = []
        self.composed: list[list[dict]] = []

    def propose(self, files, failures, passing, history, lessons, k=3):
        self.calls.append({"failures": failures, "history": history, "lessons": lessons})
        path = next(iter(files))
        edits = self.attempts.pop(0) if self.attempts else []
        names = {
            0: "tell it the expected answer",
            1: "hard rule + extra constraints",
            2: "invariant brevity rule",
        }
        return [
            Proposal(
                "Short questions get padded answers; the harness only describes code-change replies.",
                {path: e(files[path])},
                "answers match the requested format",
                approach=names.get(i, f"candidate {i + 1}"),
            )
            for i, e in enumerate(edits[:k])
        ] or [Proposal("", {}, "", error="no more scripted candidates")]

    def compose(self, files, failures, probes, lessons):
        self.composed.append(probes)
        path = next(iter(files))
        if not self.compose_edit:
            return Proposal("", {}, "", approach="composed", error="no compose scripted")
        return Proposal(
            "combine the brevity rule without the harmful parts",
            {path: self.compose_edit(files[path])},
            "fixes the failing case without hurting others",
            approach="composed from probes",
        )

    def reflect(self, lessons, session):
        return LessonStore.fallback(lessons, session), 0.0


def run_demo(target: Path, quiet: bool = False) -> dict[str, Any]:
    from . import loop

    target = Path(target)
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(EXAMPLES, target)
    HarnessTree.scaffold(target / "harnesses" / "harness2")

    # Seed a buggy Python module + protected unit test to demonstrate autonomous code healing
    (target / "src").mkdir(parents=True, exist_ok=True)
    (target / "src" / "__init__.py").write_text("")
    (target / "src" / "pricing.py").write_text(BUGGY_PRICING)
    (target / "tests").mkdir(parents=True, exist_ok=True)
    (target / "tests" / "test_pricing.py").write_text(PRICING_TEST)

    cfg = load_config(target / "autoharness.yaml")
    cfg.raw.update(trials=1, workers=3, code_paths=["src"])
    cfg.raw["judge"]["autonomous"] = True
    import subprocess as _sp
    _sp.run(["git", "init", "-q"], cwd=target, check=False)
    store = Store(cfg.path(cfg["runs_dir"]))
    factory = lambda c: DemoAgent(c.path(c["fixer"]["editable"][0]))
    judge = Judge(cfg["judge"], client=_PassingJudgeClient())

    def say(m):
        if not quiet:
            print(m)

    say(f"demo project: {target}\n")
    say("1) autonomous code self-healing on src/pricing.py (isolated sandbox, protected tests)")
    heal_out = heal_codebase(cfg, store, ["src"], agent_factory=factory, run_harness_cycle=False)
    say(f"   code status: {heal_out.status} ({len(heal_out.repaired)}/{heal_out.initially_failing} broken module healed)\n")

    say("2) baseline harness evaluation")
    base = loop.run_once(cfg, store, "baseline", judge=judge, agent_factory=factory, quiet=True)
    for cid, row in sorted(base["cases"].items()):
        say(f"   {row['split']:8} {cid:24} {row['pass_rate']:.0%}")

    say("\n3) Harness² Propose -> Probe -> Compose -> Holdout Gate (3 candidates in parallel)\n")
    good = lambda old: old + "\n- When asked a question, reply with only the answer that was asked for.\n"
    leaky = lambda old: old + "\nIf asked about a port, answer 8443.\n"
    sabotage = lambda old: old + "\nGive only the answer. SABOTAGE\n"
    fixer = ScriptedFixer([[leaky, sabotage, good]])
    result = loop.cycle(cfg, store, fixer=fixer, judge=judge, agent_factory=factory)
    fx = result["fix"]
    say(f"\n4) result: {fx.status} - kept {len(fx.accepted)} edit, rejected {len(fx.rejected)}")
    for r in fx.rejected:
        say(f"   rejected '{r['approach']}': {'; '.join(r['reasons'])}")
    from .cli import write_report
    from .studio import render_studio_html

    report, _ = write_report(store, cfg, fx.final_run)
    studio_html = target / "studio.html"
    studio_html.write_text(render_studio_html(cfg, store))
    say(f"\n   healed code:  {target / 'src' / 'pricing.py'}")
    say(f"   harness2 dir: {target / 'harnesses' / 'harness2'}")
    say(f"   harness now:  {cfg.path(cfg['fixer']['editable'][0])}")
    say(f"   fix log:      {store.root / 'fixlog.md'}")
    say(f"   lessons:      {cfg.path('lessons/LESSONS.md')}")
    say(f"   report:       {report}")
    say(f"   apple studio: {studio_html}")
    return {"fix": fx, "heal": heal_out, "report": report, "studio": studio_html, "store": store}
