"""Apple-designed self-contained HTML report for one evaluation run (`runs/<run_id>/report.html`)."""

from __future__ import annotations

import html
from typing import Any

CSS = """
:root{
  --bg:#f5f5f7;--fg:#1d1d1f;--fg2:#424245;--muted:#86868b;--card:#ffffff;
  --line:rgba(15,23,42,0.08);--blue:#0071e3;--blue-soft:rgba(0,113,227,0.10);
  --good:#1d9d5f;--good-soft:rgba(29,157,95,0.12);
  --bad:#e11d48;--bad-soft:rgba(225,29,72,0.11);
  --warn:#d97706;--warn-soft:rgba(217,119,6,0.12);
  --chip:#f1f5f9;--shadow:0 12px 32px rgba(15,23,42,0.06),0 2px 6px rgba(15,23,42,0.03);
}
@media (prefers-color-scheme:dark){
  :root{
    --bg:#0b0f19;--fg:#f5f5f7;--fg2:#cbd5e1;--muted:#94a3b8;--card:#151c2c;
    --line:rgba(255,255,255,0.09);--blue:#3b82f6;--blue-soft:rgba(59,130,246,0.18);
    --good:#34d399;--good-soft:rgba(52,211,153,0.16);
    --bad:#fb7185;--bad-soft:rgba(251,113,133,0.16);
    --warn:#fbbf24;--warn-soft:rgba(251,191,36,0.16);
    --chip:#1e293b;--shadow:0 16px 40px rgba(0,0,0,0.35);
  }
}
*{box-sizing:border-box}
body{
  margin:0;background:radial-gradient(1000px 520px at 15% -8%,var(--blue-soft),transparent 65%),var(--bg);
  color:var(--fg);font:14.5px/1.5 -apple-system,BlinkMacSystemFont,"SF Pro Display","SF Pro Text","Segoe UI",sans-serif;
  -webkit-font-smoothing:antialiased;
}
.topbar{
  position:sticky;top:0;z-index:50;padding:14px 32px;display:flex;align-items:center;justify-content:space-between;
  background:color-mix(in srgb,var(--card) 78%,transparent);backdrop-filter:saturate(180%) blur(22px);
  -webkit-backdrop-filter:saturate(180%) blur(22px);border-bottom:1px solid var(--line);
}
.brand{display:flex;align-items:center;gap:12px;font-weight:700;font-size:18px;letter-spacing:-0.02em}
main{max-width:1320px;margin:0 auto;padding:28px 28px 72px}
h1{font-size:30px;font-weight:800;letter-spacing:-0.026em;margin:0 0 6px}
h2{font-size:19px;font-weight:700;letter-spacing:-0.015em;margin:30px 0 12px}
.muted{color:var(--muted)}
.mono{font-family:"SF Mono",ui-monospace,SFMono-Regular,Menlo,monospace}
.banners{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:14px;margin:18px 0}
.banner{padding:14px 18px;border-radius:16px;border:1px solid var(--line);background:var(--card);box-shadow:var(--shadow);display:flex;align-items:center;justify-content:space-between;gap:12px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:16px;margin:18px 0}
.tile{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:18px 20px;box-shadow:var(--shadow);display:flex;align-items:center;justify-content:space-between;gap:12px}
.tile .lbl{font-size:11.5px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;color:var(--muted)}
.tile b{display:block;font-size:28px;font-weight:800;letter-spacing:-0.025em;margin-top:4px}
.good{color:var(--good)}.bad{color:var(--bad)}.warn{color:var(--warn)}
.pill{display:inline-flex;align-items:center;padding:3px 10px;border-radius:999px;font-size:12px;font-weight:700}
.p-good{background:var(--good-soft);color:var(--good)}
.p-bad{background:var(--bad-soft);color:var(--bad)}
.p-warn{background:var(--warn-soft);color:var(--warn)}
.p-blue{background:var(--blue-soft);color:var(--blue)}
.chip{display:inline-block;background:var(--chip);border-radius:8px;padding:2px 8px;margin:2px;font-size:12px;font-weight:600}
.card-wrap{background:var(--card);border:1px solid var(--line);border-radius:20px;box-shadow:var(--shadow);overflow-x:auto}
table{width:100%;border-collapse:collapse}
th,td{text-align:left;padding:12px 16px;border-bottom:1px solid var(--line);vertical-align:middle}
th{font-weight:700;color:var(--muted);font-size:11.5px;letter-spacing:0.08em;text-transform:uppercase}
tr:last-child td{border-bottom:none}
.toolbar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;justify-content:space-between;margin:28px 0 12px}
.seg{display:inline-flex;gap:4px;padding:4px;border-radius:12px;background:var(--chip)}
.seg button{border:none;background:transparent;color:var(--fg2);padding:6px 13px;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer}
.seg button.active{background:var(--card);color:var(--blue);box-shadow:0 2px 6px rgba(0,0,0,0.08)}
details.trial-card{background:var(--card);border:1px solid var(--line);border-radius:16px;margin:10px 0;padding:12px 18px;box-shadow:var(--shadow)}
summary{cursor:pointer;font-weight:600;display:flex;align-items:center;justify-content:space-between;gap:12px;list-style:none}
summary::-webkit-details-marker{display:none}
pre{white-space:pre-wrap;word-break:break-word;font:12.5px/1.55 "SF Mono",ui-monospace,Menlo,monospace;background:var(--chip);padding:10px 12px;border-radius:10px;max-height:300px;overflow:auto;margin:6px 0 0}
a{color:var(--blue);text-decoration:none;font-weight:600}
a:hover{text-decoration:underline}
"""


def _e(x: Any) -> str:
    return html.escape(str(x))


def _pct(x: float | None) -> str:
    return "–" if x is None else f"{x:.0%}"


def _ring(pct: float, color: str) -> str:
    r = 22
    c = 2 * 3.14159265 * r
    clamped = max(0.0, min(1.0, pct))
    off = c * (1.0 - clamped)
    return (
        f"<svg width='54' height='54' viewBox='0 0 54 54'>"
        f"<circle cx='27' cy='27' r='{r}' fill='none' stroke='var(--line)' stroke-width='6'/>"
        f"<circle cx='27' cy='27' r='{r}' fill='none' stroke='{color}' stroke-width='6' stroke-linecap='round' "
        f"stroke-dasharray='{c:.1f}' stroke-dashoffset='{off:.1f}' transform='rotate(-90 27 27)'/></svg>"
    )


def render(summary: dict[str, Any], results: list[dict[str, Any]], cmp: dict[str, Any] | None,
           calibration: dict[str, Any] | None) -> str:
    meta = summary["meta"]
    out = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>",
        f"<title>AutoHarness Run · {_e(summary['run_id'])}</title><style>{CSS}</style></head><body>",
        "<header class='topbar'><div class='brand'>",
        "<svg width='28' height='28' viewBox='0 0 128 128'><rect x='6' y='6' width='116' height='116' rx='28' fill='#0f172a'/>"
        "<path d='M 64 24 A 40 40 0 0 1 101 49' fill='none' stroke='#4285F4' stroke-width='8' stroke-linecap='round'/>"
        "<path d='M 101 79 A 40 40 0 0 1 64 104' fill='none' stroke='#34A853' stroke-width='8' stroke-linecap='round'/>"
        "<path d='M 64 104 A 40 40 0 0 1 27 79' fill='none' stroke='#FBBC04' stroke-width='8' stroke-linecap='round'/>"
        "<path d='M 27 49 A 40 40 0 0 1 64 24' fill='none' stroke='#EA4335' stroke-width='8' stroke-linecap='round'/>"
        "<polygon points='64,51 77,64 64,77 51,64' fill='#4285F4'/></svg>",
        f"<span>Auto<span style='color:var(--blue)'>Harness</span> Report</span></div>"
        f"<span class='pill p-blue mono'>{_e(summary['run_id'])}</span></header><main>",
        f"<h1>Evaluation Run · {_e(summary['run_id'])}</h1>",
        f"<div class='muted'>Substrate <b>{_e(meta['agent'].get('type'))}</b> ({_e(meta['agent'].get('model', 'cli'))}) · "
        f"Harness <span class='mono'>{_e(meta.get('harness_fingerprint') or '–')}</span> · "
        f"Judge <b>{_e(meta['judge']['model'])}</b> · {meta['n_cases']} cases, {meta['n_trials']} trials · {summary['wall_time_s']}s</div>",
        "<div class='banners'>",
    ]

    if cmp:
        pcls = "p-good" if cmp["gate_passed"] else "p-bad"
        msg = "GATE: PASS" if cmp["gate_passed"] else "GATE: FAIL — " + "; ".join(cmp["gate_reasons"])
        out.append(
            f"<div class='banner'><div><div class='muted' style='font-size:12px;font-weight:700;text-transform:uppercase'>Regression Gate</div>"
            f"<b>{_e(msg)}</b> <span class='muted'>vs {_e(cmp['baseline'])}</span></div>"
            f"<span class='pill {pcls}'>{'✓ Safe to Ship' if cmp['gate_passed'] else '✗ Blocked'}</span></div>"
        )

    tcls = "p-good" if (meta["judge_trusted"] or summary["review_queue"] == 0) else "p-warn"
    tlabel = "Judge Trusted / Auto-Certified" if (meta["judge_trusted"] or summary["review_queue"] == 0) else "Judge Awaiting Calibration"
    out.append(
        f"<div class='banner'><div><div class='muted' style='font-size:12px;font-weight:700;text-transform:uppercase'>Judge Verification</div>"
        f"<b>{tlabel}</b> <div class='muted' style='font-size:12.5px'>{_e(meta['judge_trust_reason'])}</div></div>"
        f"<span class='pill {tcls}'>{summary['review_queue']} in queue</span></div></div>"
    )

    def delta(key: str, fmt) -> str:
        if not cmp:
            return ""
        v = cmp[key]
        cls = "good" if v > 0 else "bad" if v < 0 else "muted"
        if key == "token_delta_pct":
            cls = "bad" if v > 0 else "good" if v < 0 else "muted"
        return f" <span class='{cls}' style='font-size:15px'>{fmt(v)}</span>"

    tiles = [
        ("Pass Rate", _pct(summary["pass_rate"]) + delta("suite_delta", lambda v: f"{v:+.0%}"), _ring(summary["pass_rate"], "var(--blue)")),
        ("Every-Trial (pass^k)", _pct(summary["all_trials_pass_rate"]), _ring(summary["all_trials_pass_rate"], "var(--good)")),
        ("Mean Score", f"{summary['mean_score']:.2f}" + delta("score_delta", lambda v: f"{v:+.2f}"), _ring(summary["mean_score"], "var(--blue)")),
        ("Review Queue", f"<span class='{'warn' if summary['review_queue'] else 'good'}'>{summary['review_queue']}</span>", ""),
        ("Total Tokens", f"{(summary['input_tokens'] + summary['output_tokens']):,}" + delta("token_delta_pct", lambda v: f"{v:+.0f}%"), ""),
        ("Est. Run Cost", f"${summary['est_agent_cost_usd']:.2f}", ""),
    ]
    out.append(
        "<div class='tiles'>"
        + "".join(f"<div class='tile'><div><span class='lbl'>{k}</span><b>{v}</b></div>{ring}</div>" for k, v, ring in tiles)
        + "</div>"
    )

    rows_by_case = {r["case"]: r for r in (cmp["rows"] if cmp else [])}
    out.append(
        "<h2>Case Summary</h2><div class='card-wrap'><table>"
        "<tr><th>Case</th><th>Split</th><th>Pass Rate</th><th>vs Baseline</th><th>Score</th><th>Turns</th><th>Tokens</th><th>Failing Checks</th></tr>"
    )
    for cid, row in sorted(summary["cases"].items(), key=lambda kv: (kv[1]["pass_rate"], kv[0])):
        cr = rows_by_case.get(cid)
        vs = "<span class='muted'>–</span>"
        if cr:
            if cr["status"] == "new":
                vs = "<span class='pill p-blue'>new</span>"
            elif cr["delta"]:
                vs = f"<span class='{'good' if cr['delta'] > 0 else 'bad'}'><b>{cr['delta']:+.0%}</b></span>"
                if cr["status"] == "regressed":
                    vs += " <span class='pill p-bad'>regressed</span>"
        pr_cls = "p-good" if row["pass_rate"] == 1 else "p-bad" if row["pass_rate"] == 0 else "p-warn"
        split_val = row.get("split", "dev")
        sp_cls = "p-blue" if split_val == "dev" else "p-warn"
        fails = "".join(f"<span class='chip'>{_e(k)} ×{v}</span>" for k, v in row["failed_checks"].items()) or "<span class='muted'>none</span>"
        review = f" <span class='pill p-warn'>{row['needs_review']} review</span>" if row["needs_review"] else ""
        out.append(
            f"<tr><td><a href='#c-{_e(cid)}'>{_e(cid)}</a>{review}<div class='muted' style='font-size:12.5px'>{_e(row['description'])}</div></td>"
            f"<td><span class='pill {sp_cls}'>{_e(split_val)}</span></td>"
            f"<td><span class='pill {pr_cls}'>{_pct(row['pass_rate'])}</span></td><td>{vs}</td>"
            f"<td class='mono'>{row['mean_score']:.2f}</td><td class='mono'>{row['mean_turns']}</td>"
            f"<td class='mono'>{row['mean_tokens']:,}</td><td>{fails}</td></tr>"
        )
    out.append("</table></div>")

    out.append(
        "<div class='toolbar'><h2 style='margin:0'>Trial Forensics</h2>"
        "<div class='seg' id='flt'>"
        "<button class='active' onclick=\"flt('all',this)\">All</button>"
        "<button onclick=\"flt('fail',this)\">Failing</button>"
        "<button onclick=\"flt('review',this)\">Needs Review</button>"
        "</div></div>"
    )

    for r in sorted(results, key=lambda r: (r["case_id"], r["trial"])):
        mark = "<span class='pill p-good'>PASS</span>" if r["passed"] else "<span class='pill p-bad'>FAIL</span>"
        rev = " <span class='pill p-warn'>needs review</span>" if r["needs_review"] else ""
        d_pass = "1" if r["passed"] else "0"
        d_rev = "1" if r["needs_review"] else "0"
        out.append(
            f"<details class='trial-card' id='c-{_e(r['case_id'])}' data-pass='{d_pass}' data-rev='{d_rev}'>"
            f"<summary><div>{mark} <span style='margin-left:8px'>{_e(r['case_id'])}</span> · "
            f"<span class='muted mono'>trial {r['trial']}</span>{rev}</div>"
            f"<div class='muted mono' style='font-size:13px'>score {r['score']:.2f} · {r['turns']} turns · {r['tokens']:,} tok</div></summary>"
        )
        if r.get("error"):
            out.append(f"<pre class='bad'>{_e(r['error'])}</pre>")
        for reason in r.get("review_reasons", []):
            out.append(f"<div class='warn' style='margin:8px 0;font-weight:600'>⚠ {_e(reason)}</div>")
        out.append("<div class='card-wrap' style='margin-top:12px;box-shadow:none'><table><tr><th>Check</th><th>Layer</th><th>Result</th><th>Detail</th></tr>")
        for c in r["checks"]:
            res = "skipped" if c["skipped"] else ("pass" if c["passed"] else "fail")
            cls = "p-warn" if c["skipped"] else ("p-good" if c["passed"] else "p-bad")
            conf = f" ({c['confidence']})" if c.get("confidence") is not None else ""
            out.append(
                f"<tr><td><b>{_e(c['name'])}</b>{'' if c['required'] else ' <span class=muted>(optional)</span>'}</td>"
                f"<td><span class='chip'>{_e(c['layer'])}</span></td>"
                f"<td><span class='pill {cls}'>{res}{_e(conf)}</span></td>"
                f"<td><pre>{_e(c['detail'])}</pre></td></tr>"
            )
        out.append("</table></div></details>")

    if calibration:
        out.append("<h2>Judge Calibration</h2>")
        cm = calibration["confusion"]
        out.append(
            f"<div class='banner'><div>Agreement <b>{_pct(calibration['agreement'])}</b> · Cohen's κ <b>{calibration['kappa']:.2f}</b> · "
            f"{calibration['n']} human labels · lenient {cm['fp']} · strict {cm['fn']}</div>"
            f"<span class='muted mono'>{_e(calibration['at'])}</span></div>"
        )

    out.append(
        "<script>"
        "function flt(m,btn){document.querySelectorAll('#flt button').forEach(b=>b.classList.remove('active'));btn.classList.add('active');"
        "document.querySelectorAll('details.trial-card').forEach(d=>{"
        "const show=m==='all'||(m==='fail'&&d.dataset.pass==='0')||(m==='review'&&d.dataset.rev==='1');"
        "d.style.display=show?'block':'none';});}"
        "</script></main></body></html>"
    )
    return "\n".join(out)
