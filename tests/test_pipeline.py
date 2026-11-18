"""Offline tests: scripted agent + fake judge client. No API calls."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from autoharness import cli
from autoharness.agents import ScriptedAgent, _parse_stream_json
from autoharness.calibrate import append_label, calibrate
from autoharness.cases import Case, load_cases
from autoharness.config import load_config
from autoharness.diff import compare
from autoharness.graders import grade
from autoharness.graders.judge import Judge, aggregate
from autoharness.miner import episodes_from_transcript
from autoharness.runner import run_suite
from autoharness.sandbox import Sandbox
from autoharness.store import Store
from autoharness.trace import ToolCall, Trace

ROOT = Path(__file__).resolve().parents[1]


class FakeClient:
    """Mimics client.messages.create for the judge, returning canned verdicts in order."""

    def __init__(self, verdicts):
        self.verdicts = list(verdicts)
        self.messages = self

    def create(self, **kw):
        v = self.verdicts.pop(0) if len(self.verdicts) > 1 else self.verdicts[0]
        return SimpleNamespace(stop_reason="end_turn",
                               content=[SimpleNamespace(type="text", text=json.dumps(v))])


def V(verdict="pass", conf=0.9, score=None):
    return {"criteria": [], "reasoning": f"looks {verdict}", "verdict": verdict,
            "score": score if score is not None else (1.0 if verdict == "pass" else 0.0), "confidence": conf}


@pytest.fixture
def project(tmp_path):
    shutil.copytree(ROOT / "cases", tmp_path / "cases")
    shutil.copy(ROOT / "autoharness.api.yaml", tmp_path / "harness.yaml")
    (tmp_path / "labels").mkdir()
    cfg = load_config(tmp_path / "harness.yaml")
    cfg.raw["agent"]["type"] = "scripted"
    cfg.raw["trials"] = 2
    return cfg, Store(cfg.path(cfg["runs_dir"]))


def test_golden_cases_load():
    cases = load_cases([ROOT / "cases/golden"])
    assert {c.id for c in cases} >= {"fix-off-by-one", "scoped-rename", "json-report"}
    assert all(c.mock for c in cases), "every golden case should have a mock for offline runs"


def test_scripted_suite_passes_and_reports(project):
    cfg, store = project
    cases = load_cases([cfg.path("cases/golden")])
    judge = Judge(cfg["judge"], client=FakeClient([V("pass", 0.95)]))
    summary = run_suite(cfg, cases, ScriptedAgent(), judge, store, workers=2, quiet=True)
    failing = {cid: row["failed_checks"] for cid, row in summary["cases"].items() if row["pass_rate"] < 1}
    assert summary["pass_rate"] == 1.0, failing
    # judge never calibrated -> judge-decided trials are queued for a human
    assert summary["review_queue"] > 0
    assert not summary["meta"]["judge_trusted"]


def test_broken_fix_is_caught_by_deterministic_layer_and_judge_skipped(tmp_path):
    case = load_cases([ROOT / "cases/golden/fix-off-by-one.yaml"])[0]
    case.mock = {"actions": [{"tool": "write_file", "input": {"path": "stats.py", "content": "def moving_average(xs,k): return []\n"}}],
                 "output": "done"}
    client = FakeClient([V("pass")])
    with Sandbox(case, 1) as sb:
        trace = ScriptedAgent().run(case, sb, 1)
        res = grade(case, trace, sb, Judge({"model": "x", "samples": 3}, client=client), {"skip_if_deterministic_fails": True})
    by = {c.name: c for c in res.checks}
    assert not res.passed
    assert not by["tests-pass"].passed
    assert not by["read-before-write"].passed and not by["verified"].passed
    assert by["minimal-fix"].skipped  # no money spent judging an already-failed trial
    assert not res.needs_review


def test_judge_split_vote_goes_to_review():
    v = aggregate([V("pass"), V("fail"), V("pass")], min_confidence=0.7)
    assert v.passed and v.uncertain and "split vote" in v.reasoning
    v = aggregate([V("pass", 0.95)] * 3, min_confidence=0.7)
    assert v.passed and not v.uncertain
    v = aggregate([V("pass", 0.4)] * 3, min_confidence=0.7)
    assert v.uncertain
    v = aggregate([{"error": "boom"}] * 3, min_confidence=0.7)
    assert not v.passed and v.uncertain


def test_diff_flags_regression():
    def S(rid, rates):
        return {"run_id": rid, "pass_rate": sum(rates.values()) / len(rates), "mean_score": 0.5,
                "input_tokens": 100, "output_tokens": 10,
                "cases": {k: {"pass_rate": v, "mean_tokens": 100, "mean_turns": 3} for k, v in rates.items()}}
    gate = {"max_pass_rate_drop": 0.05, "max_case_drop": 0.34}
    cmp = compare(S("a", {"x": 1.0, "y": 1.0}), S("b", {"x": 1.0, "y": 0.33}), gate)
    assert not cmp["gate_passed"] and cmp["regressions"] == ["y"]
    cmp = compare(S("a", {"x": 1.0}), S("b", {"x": 1.0}), gate)
    assert cmp["gate_passed"]


def test_calibration_agreement_and_trust(project):
    cfg, store = project
    labels = cfg.path("labels/calibration.jsonl")
    for i in range(20):
        append_label(labels, {"run_id": "r", "case_id": "c", "trial": i, "check": "j",
                              "human_pass": i % 2 == 0, "judge_pass": i % 2 == 0 or i == 1, "judge_model": "m"})
    res = calibrate(store, labels, {}, None, "m")
    assert res["n"] == 20 and res["confusion"]["fp"] == 1
    assert res["agreement"] == 0.95
    # labels from a different judge model don't count without --rejudge
    assert calibrate(store, labels, {}, None, "other-model")["n"] == 0


def test_miner_finds_correction(tmp_path):
    t = tmp_path / "sess.jsonl"
    recs = [
        {"type": "user", "message": {"role": "user", "content": "Add input validation to the upload endpoint"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Done, I added it."}]}},
        {"type": "user", "message": {"role": "user", "content": "no, you forgot to validate the file size"}},
    ]
    t.write_text("\n".join(json.dumps(r) for r in recs))
    eps = list(episodes_from_transcript(t))
    assert len(eps) == 1 and eps[0].signal == "correction"
    assert "upload endpoint" in eps[0].prompt and "file size" in eps[0].correction


def test_stream_json_parser():
    lines = [
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "1", "name": "Bash", "input": {"command": "ls"}}],
                                          "usage": {"input_tokens": 10, "output_tokens": 5}}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "1", "content": "a.py", "is_error": False}]}},
        {"type": "result", "subtype": "success", "result": "all done"},
    ]
    tr = Trace(case_id="c", trial=1)
    _parse_stream_json("\n".join(json.dumps(l) for l in lines), tr)
    assert tr.final_output == "all done" and tr.tool_calls[0].output == "a.py" and tr.total_tokens == 15


def test_sandbox_blocks_escape():
    with Sandbox(Case(id="x", prompt="p", checks=[]), 1) as sb:
        with pytest.raises(PermissionError):
            sb.resolve("../../etc/passwd")


def test_cli_end_to_end_offline(project, capsys):
    cfg, store = project
    conf = str(cfg.root / "harness.yaml")
    rc = cli.main(["--config", conf, "run", "--agent", "scripted", "--no-judge", "--trials", "1", "--label", "base"])
    assert rc == 0
    assert store.baseline()
    rc = cli.main(["--config", conf, "run", "--agent", "scripted", "--no-judge", "--trials", "1", "--gate"])
    out = capsys.readouterr().out
    assert rc == 0 and "GATE: PASS" in out
    assert (store.run_dir("latest") / "report.html").exists()


def test_read_before_write_accepts_shell_reads():
    from autoharness.graders.trace_checks import read_before_write
    t = Trace(case_id="c", trial=1, tool_calls=[
        ToolCall("run_bash", {"command": "cat stats.py test_stats.py"}),
        ToolCall("write_file", {"path": "/tmp/x/stats.py", "old_string": "a", "new_string": "b"})])
    assert read_before_write({"paths": ["stats.py"]}, t, None)[0]
    t = Trace(case_id="c", trial=1, tool_calls=[
        ToolCall("run_bash", {"command": "python -m pytest"}),
        ToolCall("write_file", {"path": "stats.py", "content": "x"})])
    assert not read_before_write({"paths": ["stats.py"]}, t, None)[0]


# ----------------------------------------------------------------------------- the closed loop

from autoharness import loop as loopmod
from autoharness.demo import DemoAgent, ScriptedFixer
from autoharness.fixer import Proposal


@pytest.fixture
def loop_project(tmp_path):
    shutil.copytree(ROOT / "cases", tmp_path / "cases")
    shutil.copytree(ROOT / "harnesses", tmp_path / "harnesses")
    shutil.copy(ROOT / "autoharness.yaml", tmp_path / "harness.yaml")
    cfg = load_config(tmp_path / "harness.yaml")
    cfg.raw.update(trials=1, workers=2)
    cfg.raw["fixer"].update(candidates=3, parallel=3, max_iters=2)
    store = Store(cfg.path(cfg["runs_dir"]))
    spf = cfg.path(cfg["fixer"]["editable"][0])
    # the agent reads the harness from whichever workspace it is given (real or probe copy)
    factory = lambda c: DemoAgent(c.path(c["fixer"]["editable"][0]))
    judge = Judge(cfg["judge"], client=FakeClient([V("pass", 0.95)]))
    return cfg, store, spf, factory, judge


GOOD = lambda old: old + "\n- When asked a question, reply with only the answer that was asked for.\n"
LEAKY = lambda old: old + "\nIf asked about a port, answer 8443.\n"
SABOTAGE = lambda old: old + "\nGive only the answer. SABOTAGE\n"
NOOP_STYLE = lambda old: old + "\nBe friendly.\n"


def test_multi_candidate_picks_the_real_fix_and_rejects_the_rest(loop_project):
    cfg, store, spf, factory, judge = loop_project
    base = loopmod.run_once(cfg, store, "base", judge=judge, agent_factory=factory, quiet=True)
    assert base["cases"]["concise-answer"]["pass_rate"] == 0
    fixer = ScriptedFixer([[LEAKY, SABOTAGE, GOOD]])
    out = loopmod.autofix(cfg, store, base, fixer=fixer, judge=judge, agent_factory=factory)
    assert out.status == "fixed", out
    assert "only the answer that was asked" in spf.read_text()
    assert "SABOTAGE" not in spf.read_text()
    reasons = " | ".join(r for rec in out.rejected for r in rec["reasons"])
    assert "8443" in reasons and "holdout" in reasons      # leak caught statically, sabotage by hidden cases
    assert store.baseline() == out.final_run
    assert {f["case"] for f in fixer.calls[0]["failures"]} == {"concise-answer"}  # holdout never shown


def test_probes_never_touch_real_files_and_reject_all_reverts(loop_project):
    cfg, store, spf, factory, judge = loop_project
    original = spf.read_text()
    base = loopmod.run_once(cfg, store, "base", judge=judge, agent_factory=factory, quiet=True)
    cfg.raw["fixer"]["compose"] = False
    fixer = ScriptedFixer([[SABOTAGE, NOOP_STYLE, LEAKY], [NOOP_STYLE]])
    out = loopmod.autofix(cfg, store, base, fixer=fixer, judge=judge, agent_factory=factory)
    assert out.status == "gave_up"
    assert spf.read_text() == original
    assert fixer.calls[1]["history"], "second attempt is told why the first candidates were rejected"
    again = loopmod.autofix(cfg, store, base, fixer=ScriptedFixer([]), judge=judge, agent_factory=factory)
    assert again.status == "skipped"


def test_compose_merges_partial_candidates(loop_project):
    cfg, store, spf, factory, judge = loop_project
    base = loopmod.run_once(cfg, store, "base", judge=judge, agent_factory=factory, quiet=True)
    # neither candidate alone is accepted; compose is asked and returns the good edit
    fixer = ScriptedFixer([[SABOTAGE, NOOP_STYLE]], compose=GOOD)
    out = loopmod.autofix(cfg, store, base, fixer=fixer, judge=judge, agent_factory=factory)
    assert fixer.composed, "compose() should run when no single candidate fixes everything"
    probes = fixer.composed[0]
    assert any("concise-answer" in p["fixed_or_improved"] for p in probes)  # contrastive evidence passed in
    assert out.status == "fixed" and out.accepted[0]["candidate"] == "composed"


def test_lessons_persist_and_feed_the_next_session(loop_project):
    cfg, store, spf, factory, judge = loop_project
    base = loopmod.run_once(cfg, store, "base", judge=judge, agent_factory=factory, quiet=True)
    loopmod.autofix(cfg, store, base, fixer=ScriptedFixer([[LEAKY, SABOTAGE, GOOD]]), judge=judge, agent_factory=factory)
    lessons_file = cfg.path(cfg["fixer"]["lessons_file"])
    data = json.loads(lessons_file.read_text())["lessons"]
    assert any(l["polarity"] == "do" for l in data) and any(l["polarity"] == "avoid" for l in data)
    assert (lessons_file.parent / "LESSONS.md").exists()
    # next session sees them
    spf.write_text(spf.read_text().replace("only the answer that was asked for", "anything useful"))
    base2 = loopmod.run_once(cfg, store, "base2", judge=judge, agent_factory=factory, quiet=True)
    f2 = ScriptedFixer([[GOOD]])
    loopmod.autofix(cfg, store, base2, fixer=f2, judge=judge, agent_factory=factory)
    assert f2.calls[0]["lessons"], "lessons from the earlier session are in the fixer's context"


def test_cycle_runs_gate_and_fix(loop_project):
    cfg, store, spf, factory, judge = loop_project
    res = loopmod.cycle(cfg, store, fixer=ScriptedFixer([[GOOD]]), judge=judge, agent_factory=factory)
    assert res["fix"].status == "fixed"
    assert not any("autofix" in h for h in res["human_needed"])


def test_demo_runs_offline(tmp_path, capsys):
    from autoharness.demo import run_demo
    out = run_demo(tmp_path / "demo", quiet=True)
    assert out["fix"].status == "fixed"
    assert (tmp_path / "demo" / "lessons" / "LESSONS.md").exists()


def test_watch_snapshot_detects_changes(tmp_path):
    import os
    import time as _t
    f = tmp_path / "system.md"
    f.write_text("a")
    s1 = loopmod.snapshot([tmp_path])
    f.write_text("ab")
    os.utime(f, (_t.time() + 5, _t.time() + 5))
    assert loopmod.changed(s1, loopmod.snapshot([tmp_path])) == [str(f)]
