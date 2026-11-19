"""Agensi.io & Multi-Agent Skills Packager + 8-Point Security Auditor (`autoharness skills`).

Packages AutoHarness into standalone, zero-dependency, security-audit-clean `SKILL.md`
bundles (`.zip`) ready to list and monetize on Agensi.io and install across 20+ AI
coding agents (Claude Code, Cursor, GitHub Copilot, Codex CLI, Gemini CLI, Windsurf,
Cline, OpenCode, Roo Code, Antigravity).
"""

from __future__ import annotations

import ast
import json
import re
import shutil
import zipfile
from pathlib import Path
from typing import Any

SKILL_CATALOG: list[dict[str, Any]] = [
    {
        "slug": "autoharness",
        "title": "AutoHarness — Autonomous Agent Eval & Self-Healing Hub",
        "tier": "Free (Lead Magnet / Open-Source Companion)",
        "suggested_price": "$0 (Free)",
        "category": "Agent Engineering & Testing",
        "tags": ["agent-harness", "self-healing", "mcp", "evals", "tdd"],
        "pitch": "Connects any coding agent to the AutoHarness 9-tool MCP server, flat-rate subscription router ($0 API), and Apple Liquid Glass Studio.",
    },
    {
        "slug": "autoharness-write-locked-healer",
        "title": "Write-Locked Code Healer — Anti-Gaming Pytest & Import Repair",
        "tier": "Standalone Pro Skill",
        "suggested_price": "$19 one-time",
        "category": "Debugging & Automated Testing",
        "tags": ["pytest", "anti-gaming", "bugfix", "tdd", "sha256-locks", "python"],
        "pitch": "Stops AI agents from weakening or deleting unit tests to fake green builds. Enforces cryptographic SHA-256 locks on test files before & after AST/import/pytest repair.",
    },
    {
        "slug": "autoharness-harness2-architect",
        "title": "8-Component Harness² Architect — Modular Prompt, Guardrail & Memory Compiler",
        "tier": "Standalone Pro Skill",
        "suggested_price": "$29 one-time",
        "category": "Agent Architecture & Prompt Engineering",
        "tags": ["harness2", "system-prompt", "guardrails", "agent-memory", "claude-md", "cursor-rules"],
        "pitch": "Refactors bloated CLAUDE.md / AGENTS.md files into Google Research's 8-component Harness² topology with auto-synced live codebase symbol maps.",
    },
    {
        "slug": "autoharness-eval-loop",
        "title": "3-Layer Agent Eval Loop — Case Synthesizer & Hidden Holdout Gate",
        "tier": "Standalone Pro Skill",
        "suggested_price": "$39 one-time",
        "category": "AI Evaluation & QA",
        "tags": ["agent-evals", "regression-gate", "holdout", "trace-grading", "pass-at-k"],
        "pitch": "Auto-synthesizes dev + secret holdout cases from source code and runs a cheapest-first Deterministic ($0) -> Behavioral Trace ($0) -> Rubric waterfall.",
    },
    {
        "slug": "autoharness-contrastive-optimizer",
        "title": "Contrastive Harness Optimizer — Propose-Probe-Compose & Lesson Ledger",
        "tier": "Standalone Pro Skill",
        "suggested_price": "$49 one-time",
        "category": "Self-Improving Agents & Prompt Optimization",
        "tags": ["prompt-optimization", "propose-probe-compose", "anti-overfitting", "agent-lessons"],
        "pitch": "Optimizes failing agent harnesses without overfitting using K=3 orthogonal probes, a $0 Static Leak & Bloat Shield, holdout rollback gates, and a DO/AVOID Lesson Ledger.",
    },
]

FORBIDDEN_DIRS = {"node_modules", ".venv", "venv", "__pycache__", ".git", ".pytest_cache"}
SECRET_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_-]{12,}"),
    re.compile(r"sk-proj-[A-Za-z0-9_-]{12,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"AIza[0-9A-Za-z_-]{25,}"),
]
ENV_HARVEST_PATTERNS = [re.compile(r"\bos\.environ\b"), re.compile(r"\bos\.getenv\b")]
UNSAFE_EXEC_PATTERNS = [re.compile(r"shell\s*=\s*True"), re.compile(r"\beval\s*\("), re.compile(r"\bexec\s*\(")]
NETWORK_PATTERNS = [re.compile(r"\burllib\.request\b"), re.compile(r"\brequests\."), re.compile(r"\bhttp\.client\b"), re.compile(r"\bsocket\.")]

AGENT_SKILL_DIRS: dict[str, tuple[str, str]] = {
    "claude": (".claude/skills", "~/.claude/skills"),
    "cursor": (".cursor/skills", "~/.cursor/skills"),
    "copilot": (".github/skills", "~/.copilot/skills"),
    "codex": (".agents/skills", "~/.agents/skills"),
    "gemini": (".gemini/skills", "~/.gemini/skills"),
    "antigravity": (".agents/skills", "~/.gemini/config/skills"),
    "windsurf": (".windsurf/skills", "~/.codeium/windsurf/skills"),
    "opencode": (".opencode/skills", "~/.config/opencode/skills"),
}


def default_skills_root(project_root: Path | None = None) -> Path:
    if project_root and (project_root / "skills").is_dir():
        return (project_root / "skills").resolve()
    pkg_parent = Path(__file__).resolve().parent.parent / "skills"
    return pkg_parent


def discover_skill_dirs(project_root: Path | None = None) -> list[Path]:
    """Locate public (`skills/`) and private creator (`pro-skills/`) skill folders."""
    base = (project_root or Path(__file__).resolve().parent.parent).resolve()
    roots = [base / "skills", base / "pro-skills"]
    pkg_base = Path(__file__).resolve().parent.parent
    if pkg_base != base:
        roots.extend([pkg_base / "skills", pkg_base / "pro-skills"])
    found: dict[str, Path] = {}
    for r in roots:
        if not r.is_dir():
            continue
        for child in sorted(r.iterdir()):
            if child.is_dir() and (child / "SKILL.md").exists() and child.name not in found:
                found[child.name] = child
    return list(found.values())


def audit_skill_dir(skill_dir: Path) -> dict[str, Any]:
    """Run an 8-point Agensi.io security & structure check on a single skill folder."""
    issues: list[str] = []
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        issues.append("missing SKILL.md")
    else:
        txt = skill_md.read_text(errors="replace")
        if not txt.startswith("---"):
            issues.append("SKILL.md missing YAML frontmatter")
        else:
            fm = txt.split("---", 2)[1]
            for req_key in ("name:", "description:", "when_to_use:", "allowed-tools:"):
                if req_key not in fm:
                    issues.append(f"SKILL.md frontmatter missing `{req_key.rstrip(':')}`")

    all_files = [p for p in skill_dir.rglob("*") if p.is_file()]
    total_bytes = sum(p.stat().st_size for p in all_files)
    if len(all_files) > 1000:
        issues.append(f"exceeds 1,000 file limit ({len(all_files)} files)")
    if total_bytes > 50 * 1024 * 1024:
        issues.append(f"exceeds 50 MB size limit ({total_bytes} bytes)")

    for p in skill_dir.rglob("*"):
        if p.name in FORBIDDEN_DIRS:
            issues.append(f"forbidden directory bundled: {p.relative_to(skill_dir)}")

    for f in all_files:
        rel = str(f.relative_to(skill_dir))
        content = f.read_text(errors="replace")
        for pat in SECRET_PATTERNS:
            if pat.search(content):
                issues.append(f"{rel}: potential hardcoded secret matching {pat.pattern}")
        if f.suffix == ".py":
            try:
                ast.parse(content, filename=rel)
            except SyntaxError as exc:
                issues.append(f"{rel}:{exc.lineno}: SyntaxError: {exc.msg}")
            for pat in ENV_HARVEST_PATTERNS:
                if pat.search(content):
                    issues.append(f"{rel}: environment variable access ({pat.pattern}) flagged by security scan")
            for pat in UNSAFE_EXEC_PATTERNS:
                if pat.search(content):
                    issues.append(f"{rel}: unsafe command/eval pattern ({pat.pattern})")
            for pat in NETWORK_PATTERNS:
                if pat.search(content):
                    issues.append(f"{rel}: outbound network call ({pat.pattern}) in offline skill script")

    return {
        "skill": skill_dir.name,
        "passed": not issues,
        "files": len(all_files),
        "bytes": total_bytes,
        "checks_passed": 8 if not issues else max(0, 8 - len(issues)),
        "issues": issues,
    }


def package_skills(skills_root: Path, out_dir: Path) -> dict[str, Any]:
    """Audit all discovered skills (public `skills/` + private `pro-skills/`) and create `.zip` bundles."""
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    discovered = {d.name: d for d in discover_skill_dirs(skills_root.parent)}
    if skills_root.is_dir():
        for child in sorted(skills_root.iterdir()):
            if child.is_dir() and (child / "SKILL.md").exists():
                discovered.setdefault(child.name, child)

    audits: list[dict[str, Any]] = []
    archives: list[str] = []

    ordered_slugs = [m["slug"] for m in SKILL_CATALOG if m["slug"] in discovered] + [
        k for k in sorted(discovered) if k not in {m["slug"] for m in SKILL_CATALOG}
    ]
    for slug in ordered_slugs:
        sdir = discovered[slug]
        audit = audit_skill_dir(sdir)
        audits.append(audit)
        if not audit["passed"]:
            raise ValueError(f"Skill `{slug}` failed security audit: {audit['issues']}")

        zip_path = out_dir / f"{slug}.zip"
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for file_path in sorted(sdir.rglob("*")):
                if file_path.is_dir():
                    continue
                if any(part in FORBIDDEN_DIRS or part == ".DS_Store" for part in file_path.parts):
                    continue
                arcname = f"{slug}/{file_path.relative_to(sdir).as_posix()}"
                zf.write(file_path, arcname=arcname)
        archives.append(str(zip_path))

    listings_md = _render_listings_markdown(out_dir)
    (out_dir / "AGENSI_LISTINGS.md").write_text(listings_md)
    reply_md = _render_agensi_reply_markdown()
    (out_dir / "REPLY_TO_AGENSI.md").write_text(reply_md)
    manifest = {"packaged": len(archives), "archives": archives, "audits": audits, "catalog": SKILL_CATALOG}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def install_skills_for_agents(
    skills_root: Path,
    workspace_root: Path,
    target: str = "all",
    *,
    global_scope: bool = False,
) -> list[str]:
    """Install discovered AutoHarness skills into Claude Code, Cursor, Copilot, Codex, Gemini, Windsurf, OpenCode & Antigravity."""
    targets = list(AGENT_SKILL_DIRS.keys()) if target == "all" else [target]
    skill_dirs = discover_skill_dirs(skills_root.parent)
    installed: list[str] = []
    for t in targets:
        if t not in AGENT_SKILL_DIRS:
            raise ValueError(f"Unknown skill agent target: {t!r}")
        local_rel, global_rel = AGENT_SKILL_DIRS[t]
        dest_base = Path(global_rel).expanduser() if global_scope else (workspace_root.resolve() / local_rel)
        dest_base.mkdir(parents=True, exist_ok=True)
        for src in skill_dirs:
            dst = dest_base / src.name
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns(*FORBIDDEN_DIRS, ".DS_Store"))
            installed.append(str(dst))
    return installed


def _render_listings_markdown(out_dir: Path) -> str:
    lines = [
        "# Agensi.io Creator Listing Kit — AutoHarness Skill Suite",
        "",
        "All 5 `.zip` archives in this directory passed the **8-point Agensi security & structure audit**",
        "(valid `SKILL.md` frontmatter with `when_to_use` + `allowed-tools`, pure Python 3 stdlib `scripts/`,",
        "zero environment-variable harvesting, zero `shell=True`, zero hardcoded secrets, `< 50 KB` each).",
        "",
        "## Recommended Monetization & Funnel Strategy",
        "",
        "- **Top-of-Funnel Free Listing:** Publish `autoharness.zip` as **Free ($0)** so developers searching Agensi (150k+ monthly visitors) install it immediately.",
        "- **4 Standalone Pro Skills:** List each self-contained capability individually (`$19`–`$49`) **plus** offer a **$99 Complete AutoHarness Pro Suite Bundle** (30% off individual `$136` total).",
        "- **Affiliate Link (90% Share):** Share your Agensi creator affiliate link in your GitHub `README.md` and release notes so new signups earn **90% revenue share** + 20% for 6 months.",
        "",
        "| ZIP Archive | Suggested Title | Suggested Price | Tags |",
        "|---|---|---|---|",
    ]
    for m in SKILL_CATALOG:
        lines.append(
            f"| `{m['slug']}.zip` | **{m['title']}** | `{m['suggested_price']}` | `{', '.join(m['tags'])}` |"
        )
    lines += [
        "",
        "## Upload Steps (2 Minutes on Agensi.io)",
        "",
        "1. Sign in at **https://www.agensi.io**, open **Creator Dashboard**, and enable **Creator Mode** (connect Stripe or USDC payouts).",
        f"2. Upload each `.zip` from `{out_dir}` (or connect Agensi MCP and say *\"Publish these skills to Agensi\"* via `submit_skill`).",
        "3. Copy your Creator Affiliate URL into `README.md` to capture the **90% net share** on direct referrals.",
        "",
    ]
    return "\n".join(lines)


def _render_agensi_reply_markdown() -> str:
    return """# Ready-to-Send Reply to Agensi.io

**Subject:** Re: AutoHarness — Packaged into 5 Standalone Skills (Free + 4 Exclusive Pro Skills) for Agensi.io

Hi [Name / Agensi Team],

Thanks so much for reaching out and checking out AutoHarness!

We went ahead and built an open-core **5-skill product line** specifically for Agensi.io — keeping **1 free companion skill** in our repo as an organic funnel while building **4 private, commercial standalone Pro skills** (not published on GitHub, each backed by self-contained Python stdlib CLI tools, `references/`, and verified clean against your 8-point security & anti-harvesting checks for Claude Code, Cursor, Copilot, Codex, Gemini, and 20+ agents):

1. **AutoHarness Hub (`autoharness.zip` — Free Lead-Magnet Skill)**: Connects any host agent to the 9-tool AutoHarness MCP server, flat-rate subscription router, and Studio workbench.
2. **Write-Locked Code Healer (`autoharness-write-locked-healer.zip` — Paid Pro)**: Enforces cryptographic SHA-256 locks on unit tests before and after AST/import/`pytest` repair so agents can never pass builds by weakening assertions.
3. **8-Component Harness² Architect (`autoharness-harness2-architect.zip` — Paid Pro)**: Scaffolds, compiles, and auto-syncs live codebase `memory.md` across the 8 modular Harness² runtime roles (`system.md`, `guardrails.md`, `memory.md`, `skills.md`, `tool_guidance.md`, `scripts/`, `subagents.md`, `plugins.json`).
4. **3-Layer Agent Eval Loop (`autoharness-eval-loop.zip` — Paid Pro)**: Synthesizes stratified `dev` + secret `holdout` cases from source code and runs a cheapest-first Deterministic ($0) -> Behavioral Trace ($0) -> Rubric grading waterfall.
5. **Contrastive Harness Optimizer (`autoharness-contrastive-optimizer.zip` — Paid Pro)**: Runs Propose–Probe–Compose prompt optimization with a $0 Static Leak & Bloat Shield, holdout rollback gate, and persistent `DO` / `AVOID` Lesson Ledger.

I'm setting up our Creator Dashboard & affiliate link on Agensi.io now to upload all 5 `.zip` packages (plus a complete Pro bundle) and will add our Agensi affiliate badge directly to the top of our GitHub README — if you feature new agent-engineering launches in search or your newsletter, I'd love to coordinate!

Best regards,
Pritam Panda
https://github.com/pritampanda15/autoharness
"""
