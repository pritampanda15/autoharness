"""Tests for Harness² 8-component tree, autonomous self-evaluation, synthesizer, code healer & Git hooks."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

from autoharness import cli, loop as loopmod
from autoharness.agents import ScriptedAgent, execute_tool
from autoharness.cases import Case, load_cases
from autoharness.config import load_config
from autoharness.fixer import Proposal
from autoharness.graders import grade
from autoharness.graders.judge import Judge
from autoharness.healer import heal_codebase
from autoharness.hooks import changed_python_files, hook_status, install_hook, uninstall_hook
from autoharness.sandbox import Sandbox
from autoharness.store import Store
from autoharness.synthesizer import build_context_map, scan_codebase, synthesize_cases
from autoharness.trace import ToolCall, Trace
from autoharness.tree import COMPONENTS, HarnessTree, MiddlewareState


def test_harness_tree_scaffolds_8_components_and_enforces_middleware(tmp_path):
    hdir = tmp_path / "harnesses" / "h2"
    written = HarnessTree.scaffold(hdir)
    assert len(written) == 9  # 8 component files + scripts/verify.sh
    tree = HarnessTree.load(hdir)
    assert tree.is_directory
    assert len(tree.summary_rows()) == len(COMPONENTS)
    assert all(r["active"] for r in tree.summary_rows())

    compiled = tree.compile_prompt(extra_memory="Module `billing.py` handles invoices.")
    assert "<guardrails>" in compiled
    assert "<tool_guidance>" in compiled
    assert "<verification_protocol>" in compiled
    assert "billing.py" in compiled

    with Sandbox(Case(id="mw", prompt="test", checks=[]), 1) as sb:
        tree.stage_into_sandbox(sb)
        assert (sb.dir / ".harness" / "scripts" / "verify.sh").exists()
        mw = MiddlewareState()
        # Destructive command blocked deterministically by plugins.json middleware
        out, is_err = execute_tool(sb, "run_bash", {"command": "rm -rf /"}, tree=tree, mw_state=mw)
        assert is_err and "Blocked by harness guardrail plugin" in out
        # Post-write reminder appended by middleware
        out2, is_err2 = execute_tool(sb, "write_file", {"path": "a.py", "content": "x = 1\n"}, tree=tree, mw_state=mw)
        assert not is_err2 and "[harness-plugin]" in out2


def test_autonomous_evaluator_certifies_grounded_verdict_without_human_labels():
    case = Case(
        id="grounded-case",
        prompt="Create hello.py setting GREETING = 'hi'",
        checks=[{"name": "clean-code", "type": "judge", "include_files": ["hello.py"], "rubric": "GREETING is set to hi"}],
    )
    with Sandbox(case, 1) as sb:
        (sb.dir / "hello.py").write_text("GREETING = 'hi'\n")
        trace = Trace(
            case_id=case.id,
            trial=1,
            final_output="Created hello.py with GREETING = 'hi'.",
            tool_calls=[ToolCall("write_file", {"path": "hello.py"}, "wrote 16 chars")],
            artifacts={"hello.py": "GREETING = 'hi'\n"},
        )
        grounded_vote = {
            "criteria": [{"criterion": "GREETING set", "met": True, "evidence": "hello.py defines GREETING = 'hi'"}],
            "reasoning": "Artifact hello.py matches rubric.",
            "verdict": "pass",
            "score": 1.0,
            "confidence": 0.95,
        }
        client = SimpleNamespace(
            messages=SimpleNamespace(
                create=lambda **kw: SimpleNamespace(
                    stop_reason="end_turn",
                    content=[SimpleNamespace(type="text", text=json.dumps(grounded_vote))],
                )
            )
        )
        judge = Judge({"model": "m", "samples": 3, "min_confidence": 0.7}, client=client)
        # Uncalibrated (`judge_trusted=False`), WITHOUT autonomous mode -> requires human review
        res_manual = grade(case, trace, sb, judge, {"autonomous": False, "min_confidence": 0.7}, judge_trusted=False)
        assert res_manual.passed and res_manual.needs_review

        # Uncalibrated (`judge_trusted=False`), WITH autonomous grounding mode -> certified with 0 review needed!
        res_auto = grade(case, trace, sb, judge, {"autonomous": True, "min_confidence": 0.7}, judge_trusted=False)
        assert res_auto.passed and not res_auto.needs_review


def test_synthesizer_discovers_bugs_and_generates_cases(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "good.py").write_text('"""Healthy utility."""\n\ndef double(x: int) -> int:\n    return x * 2\n')
    (tmp_path / "pkg" / "broken.py").write_text("def oops(\n    return 1\n")  # SyntaxError

    modules = scan_codebase(tmp_path, ["pkg"], run_checks=True)
    by_rel = {m.rel_path: m for m in modules}
    assert by_rel["pkg/good.py"].healthy
    assert not by_rel["pkg/broken.py"].healthy and "SyntaxError" in (by_rel["pkg/broken.py"].syntax_error or "")

    cmap = build_context_map(modules)
    assert "pkg/good.py" in cmap and "`double()`" in cmap

    out_dir = tmp_path / "cases" / "auto"
    written = synthesize_cases(tmp_path, ["pkg"], out_dir=out_dir)
    assert len(written) == 2
    loaded = load_cases([out_dir])
    assert any("auto-heal-broken" in c.id for c in loaded)
    assert any("auto-guard-good" in c.id for c in loaded)


class _RepairAgent:
    """Repairs `app/math_ops.py` cleanly."""

    def run(self, case, sb, trial):
        trace = Trace(case_id=case.id, trial=trial, workdir=str(sb.dir))
        fixed = "def add_tax(amount: float, rate: float) -> float:\n    return round(amount * (1 + rate), 2)\n"
        out, err = execute_tool(sb, "write_file", {"path": "app/math_ops.py", "content": fixed})
        trace.tool_calls.append(ToolCall("write_file", {"path": "app/math_ops.py"}, out, err))
        trace.turns = 1
        return trace


class _CheatingAgent:
    """Attempts to pass tests by overwriting the test file instead of fixing the bug."""

    def run(self, case, sb, trial):
        trace = Trace(case_id=case.id, trial=trial, workdir=str(sb.dir))
        execute_tool(sb, "write_file", {"path": "tests/test_math_ops.py", "content": "def test_tax(): assert True\n"})
        execute_tool(sb, "write_file", {"path": "app/math_ops.py", "content": "def add_tax(a, r): return 0\n"})
        trace.turns = 2
        return trace


def test_code_healer_repairs_broken_code_and_blocks_test_tampering(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "__init__.py").write_text("")
    buggy = "def add_tax(amount: float, rate: float) -> float:\n    return round(amount - rate, 2)\n"
    (tmp_path / "app" / "math_ops.py").write_text(buggy)
    (tmp_path / "tests").mkdir()
    orig_test = "from app.math_ops import add_tax\n\ndef test_tax():\n    assert add_tax(100.0, 0.1) == 110.0\n"
    (tmp_path / "tests" / "test_math_ops.py").write_text(orig_test)
    HarnessTree.scaffold(tmp_path / "harnesses" / "harness2")

    (tmp_path / "autoharness.yaml").write_text(
        "code_paths: [app]\nagent:\n  type: scripted\n  system_prompt_file: harnesses/harness2\n"
    )
    cfg = load_config(tmp_path / "autoharness.yaml")
    store = Store(cfg.path("runs"))

    # 1. Cheating agent tries to tamper with tests -> REJECTED, real files unchanged!
    cheat_out = heal_codebase(cfg, store, ["app"], agent_factory=lambda _: _CheatingAgent())
    assert cheat_out.status == "failed"
    assert "protected test files" in cheat_out.unrepaired[0].reason
    assert (tmp_path / "app" / "math_ops.py").read_text() == buggy
    assert (tmp_path / "tests" / "test_math_ops.py").read_text() == orig_test

    # 2. Legitimate repair agent fixes `app/math_ops.py` -> ACCEPTED & memory.md synced!
    good_out = heal_codebase(cfg, store, ["app"], agent_factory=lambda _: _RepairAgent(), sync_memory=True)
    assert good_out.status == "healed"
    assert len(good_out.repaired) == 1
    assert "1 + rate" in (tmp_path / "app" / "math_ops.py").read_text()
    assert "app/math_ops.py" in (tmp_path / "harnesses" / "harness2" / "memory.md").read_text()


def test_autofix_supports_directory_harness2_editable(tmp_path):
    hdir = tmp_path / "harnesses" / "h2"
    HarnessTree.scaffold(hdir)
    # Remove the brevity rule from scaffolded system.md so concise-qa fails initially
    (hdir / "system.md").write_text("You are a helpful coding assistant.\n")
    (tmp_path / "cases").mkdir()
    (tmp_path / "cases" / "qa.yaml").write_text(
        "id: port-qa\nsplit: dev\nprompt: What port?\nchecks:\n"
        "  - {type: output_max_chars, value: 20}\nmock:\n  output: '8080'\n"
    )
    (tmp_path / "autoharness.yaml").write_text(
        "cases: [cases]\ntrials: 1\nagent:\n  type: scripted\n  system_prompt_file: harnesses/h2\n"
        "fixer:\n  editable: [harnesses/h2]\n  candidates: 1\n  compose: false\n"
    )
    cfg = load_config(tmp_path / "autoharness.yaml")
    store = Store(cfg.path("runs"))

    class _H2Agent:
        def __init__(self, c):
            self.prompt = HarnessTree.load(c.path("harnesses/h2")).compile_prompt()

        def run(self, case, sb, trial):
            t = ScriptedAgent().run(case, sb, trial)
            if "strict-terse-mode" not in self.prompt.lower():
                t.final_output = "The service listens on port 8080 as configured in the default profile."
            return t

    class _H2Fixer:
        def propose(self, files, failures, passing, history, lessons, k=1):
            target = "harnesses/h2/skills.md"
            assert target in files
            return [Proposal("Add strict terse Q&A skill", {target: files[target] + "\nEnable strict-terse-mode for direct Q&A.\n"}, "short answers", approach="skill edit")]

        def reflect(self, lessons, session):
            return [], 0.0

    base = loopmod.run_once(cfg, store, "base", agent_factory=_H2Agent, quiet=True)
    assert base["pass_rate"] == 0.0
    out = loopmod.autofix(cfg, store, base, fixer=_H2Fixer(), agent_factory=_H2Agent)
    assert out.status == "fixed"
    assert "strict-terse-mode" in (hdir / "skills.md").read_text()


def test_git_hooks_install_status_and_uninstall(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "hello.py").write_text("x = 1\n")
    assert changed_python_files(tmp_path) == [tmp_path / "hello.py"]

    hook = install_hook(tmp_path, kind="pre-commit")
    assert hook.exists()
    assert hook_status(tmp_path)["pre-commit"] == "autoharness"
    assert uninstall_hook(tmp_path, kind="pre-commit")
    assert hook_status(tmp_path)["pre-commit"] == "not installed"


def test_cli_new_commands(tmp_path, capsys):
    assert cli.main(["tree", "--init", str(tmp_path / "h2")]) == 0
    assert cli.main(["tree", str(tmp_path / "h2")]) == 0
    (tmp_path / "mod.py").write_text("def greet():\n    return 'hi'\n")
    (tmp_path / "autoharness.yaml").write_text("code_paths: ['.']\n")
    assert cli.main(["--config", str(tmp_path / "autoharness.yaml"), "synthesize", str(tmp_path / "mod.py"), "--out", str(tmp_path / "auto")]) == 0
    assert any((tmp_path / "auto").glob("*.yaml"))
    out = capsys.readouterr().out
    assert "8-component Harness²" in out and "synthesized 1 case" in out

    studio_out = tmp_path / "studio.html"
    assert cli.main(["--config", str(tmp_path / "autoharness.yaml"), "studio", "--export", str(studio_out), "--no-open"]) == 0
    html_txt = studio_out.read_text()
    assert studio_out.exists() and "AutoHarness Studio" in html_txt
    assert "triggerAutoFix" in html_txt and "btn-top-autofix" in html_txt


def test_studio_immediate_autofix_repairs_failing_test_and_module(tmp_path):
    from autoharness.studio import run_immediate_autofix
    from autoharness.trace import ToolCall, Trace

    hdir = tmp_path / "harnesses" / "h2"
    HarnessTree.scaffold(hdir)
    (hdir / "system.md").write_text("You are a coding assistant.\n")

    # Broken Python module + locked unit test
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "calc.py").write_text("def double(x: int) -> int:\n    return x * 3\n")
    (tmp_path / "tests").mkdir()
    locked_test = "from pkg.calc import double\n\ndef test_double():\n    assert double(4) == 8\n"
    (tmp_path / "tests" / "test_calc.py").write_text(locked_test)

    # Failing dev case in the evaluation suite
    (tmp_path / "cases").mkdir()
    (tmp_path / "cases" / "qa.yaml").write_text(
        "id: quick-qa\nsplit: dev\nprompt: Port?\nchecks:\n"
        "  - {type: output_max_chars, value: 15}\nmock:\n  output: '443'\n"
    )
    (tmp_path / "autoharness.yaml").write_text(
        "cases: [cases]\ncode_paths: [pkg]\ntrials: 1\n"
        "agent:\n  type: scripted\n  system_prompt_file: harnesses/h2\n"
        "fixer:\n  editable: [harnesses/h2]\n  candidates: 1\n  compose: false\n"
    )
    cfg = load_config(tmp_path / "autoharness.yaml")
    store = Store(cfg.path("runs"))

    class _DualAgent:
        def __init__(self, c):
            self.prompt = HarnessTree.load(c.path("harnesses/h2")).compile_prompt()

        def run(self, case, sb, trial):
            # If invoked inside heal_module_isolated, repair pkg/calc.py
            calc_file = sb.dir / "pkg" / "calc.py"
            if calc_file.exists():
                calc_file.write_text("def double(x: int) -> int:\n    return x * 2\n")
                return Trace(
                    case_id=case.id,
                    trial=trial,
                    workdir=str(sb.dir),
                    turns=1,
                    final_output="Fixed double()",
                    tool_calls=[ToolCall("write_file", {"path": "pkg/calc.py"}, "ok", False)],
                )
            t = ScriptedAgent().run(case, sb, trial)
            if "terse-answer-rule" not in self.prompt.lower():
                t.final_output = "The HTTPS port is 443 in the production configuration."
            return t

    class _OneClickFixer:
        def propose(self, files, failures, passing, history, lessons, k=1):
            target = "harnesses/h2/skills.md"
            return [Proposal("Add terse-answer-rule", {target: files[target] + "\nUse terse-answer-rule.\n"}, "short output", approach="terse skill")]

        def reflect(self, lessons, session):
            return [], 0.0

    # Seed failing run -> verify report.render includes immediate Auto-Fix button
    from autoharness import report as reportmod

    base = loopmod.run_once(cfg, store, "failing-seed", agent_factory=_DualAgent, quiet=True)
    assert base["pass_rate"] == 0.0
    report_html = reportmod.render(base, store.load_results(store.run_dir(base["run_id"])), None, None)
    assert "reportAutoFix" in report_html and "Auto-Fix Now" in report_html

    # Click the immediate Auto-Fix button!
    res = run_immediate_autofix(cfg, store, agent_factory=_DualAgent, fixer=_OneClickFixer())
    assert res["ok"] is True
    assert res["status"] == "fixed"
    assert res["before_pass_rate"] == 0.0
    assert res["after_pass_rate"] == 1.0
    assert len(res["healed_modules"]) == 1
    assert "x * 2" in (tmp_path / "pkg" / "calc.py").read_text()
    assert (tmp_path / "tests" / "test_calc.py").read_text() == locked_test


def test_universal_model_substrates_and_oss_tool_calls(tmp_path):
    from autoharness.agents import (
        AutoRouterAgent,
        CodexAgent,
        GeminiCliAgent,
        OllamaAgent,
        OpenAIAgent,
        _extract_oss_tool_calls,
        _parse_codex_jsonl,
        build_agent,
    )
    from autoharness.providers import extract_json_object
    from autoharness.trace import Trace

    # 1. All universal substrate types build cleanly
    for kind, cls in [
        ("codex", CodexAgent),
        ("gemini-cli", GeminiCliAgent),
        ("ollama", OllamaAgent),
        ("opensource", OllamaAgent),
        ("vllm", OpenAIAgent),
        ("lmstudio", OpenAIAgent),
        ("openrouter", OpenAIAgent),
        ("auto", AutoRouterAgent),
    ]:
        ag = build_agent({"type": kind, "model": "qwen2.5-coder:14b"}, tmp_path)
        assert isinstance(ag, cls)

    # 2. Open-source `<think>` + `<tool_call>` JSON extraction (DeepSeek-R1 / Qwen / Llama)
    oss_raw = '<think>Check file first</think>\n<tool_call>{"name": "read_file", "arguments": {"path": "app.py"}}</tool_call>'
    assert _extract_oss_tool_calls(oss_raw) == [("read_file", {"path": "app.py"})]
    assert extract_json_object('<think>reasoning</think>\n```json\n{"verdict": "pass", "score": 1.0}\n```') == {
        "verdict": "pass",
        "score": 1.0,
    }

    # 3. Codex JSONL trace parser
    tr = Trace(case_id="c1", trial=1, workdir=str(tmp_path))
    codex_out = (
        '{"type":"item.completed","item":{"type":"command_execution","command":"pytest -q","aggregated_output":"2 passed","exit_code":0}}\n'
        '{"type":"item.completed","item":{"type":"agent_message","text":"All tests pass."}}\n'
    )
    _parse_codex_jsonl(codex_out, tr)
    assert tr.final_output == "All tests pass."
    assert len(tr.tool_calls) == 1 and tr.tool_calls[0].name == "run_bash"

    # 4. CLI `autoharness models --use` switches autoharness.yaml across Codex, Gemini, Ollama & Auto
    cfg_file = tmp_path / "autoharness.yaml"
    cfg_file.write_text("cases: [cases]\n")
    assert cli.main(["--config", str(cfg_file), "models", "--use", "ollama-qwen", "--model", "deepseek-r1:14b"]) == 0
    reloaded = load_config(cfg_file)
    assert reloaded["agent"]["type"] == "ollama"
    assert reloaded["agent"]["model"] == "deepseek-r1:14b"
    assert reloaded["fixer"]["backend"] == "ollama"
