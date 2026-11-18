"""Apple Liquid Glass self-contained HTML report for one evaluation run (`runs/<run_id>/report.html`)."""

from __future__ import annotations

import html
import math
from typing import Any

CSS = """
:root{
  --bg:#eef2f9;--fg:#0f172a;--fg2:#334155;--muted:#64748b;
  --glass:linear-gradient(145deg,rgba(255,255,255,0.74) 0%,rgba(255,255,255,0.44) 100%);
  --glass-rim:rgba(255,255,255,0.86);--line:rgba(15,23,42,0.06);
  --blue:#007aff;--blue-soft:rgba(0,122,255,0.12);
  --good:#16a34a;--good-soft:rgba(52,199,89,0.14);
  --bad:#e11d48;--bad-soft:rgba(255,59,48,0.13);
  --warn:#d97706;--warn-soft:rgba(245,158,11,0.14);
  --chip:rgba(255,255,255,0.58);
  --shadow:inset 0 1px 0.5px rgba(255,255,255,0.95),0 20px 50px -15px rgba(15,23,42,0.08),0 4px 14px -2px rgba(15,23,42,0.03);
}
*{box-sizing:border-box}
body{
  margin:0;background:var(--bg);color:var(--fg);
  font:14px/1.52 -apple-system,BlinkMacSystemFont,"SF Pro Display","SF Pro Text","Helvetica Neue",sans-serif;
  -webkit-font-smoothing:antialiased;overflow-x:hidden;
}
.aurora{position:fixed;inset:0;z-index:0;pointer-events:none;overflow:hidden;background:linear-gradient(180deg,#f3f6fc 0%,#e9eff8 55%,#f4f7fc 100%)}
.orb{position:absolute;border-radius:50%;filter:blur(76px);opacity:0.6}
.o1{width:640px;height:640px;left:-120px;top:-160px;background:radial-gradient(circle,rgba(0,122,255,0.26),transparent 70%);animation:d1 20s ease-in-out infinite alternate}
.o2{width:600px;height:600px;right:-120px;top:-80px;background:radial-gradient(circle,rgba(175,82,222,0.22),transparent 70%);animation:d2 24s ease-in-out infinite alternate}
.o3{width:680px;height:520px;left:28%;bottom:-180px;background:radial-gradient(circle,rgba(52,199,89,0.18),transparent 70%);animation:d1 22s ease-in-out infinite alternate-reverse}
@keyframes d1{from{transform:translate3d(0,0,0)}to{transform:translate3d(90px,65px,0) scale(1.08)}}
@keyframes d2{from{transform:translate3d(0,0,0)}to{transform:translate3d(-100px,80px,0) scale(0.94)}}
.dock-wrap{position:sticky;top:14px;z-index:60;padding:0 24px}
.topbar{
  max-width:1320px;margin:0 auto;padding:10px 18px;border-radius:999px;display:flex;align-items:center;justify-content:space-between;
  background:linear-gradient(145deg,rgba(255,255,255,0.78) 0%,rgba(255,255,255,0.52) 100%);
  backdrop-filter:blur(46px) saturate(210%);-webkit-backdrop-filter:blur(46px) saturate(210%);
  border:1px solid var(--glass-rim);box-shadow:var(--shadow);
}
.brand{display:flex;align-items:center;gap:11px;font-weight:700;font-size:17.5px;letter-spacing:-0.022em}
main{position:relative;z-index:2;max-width:1320px;margin:0 auto;padding:28px 28px 76px}
h1{font-size:31px;font-weight:700;letter-spacing:-0.028em;margin:0 0 6px}
h2{font-size:19px;font-weight:700;letter-spacing:-0.018em;margin:30px 0 12px}
.muted{color:var(--muted)}
.mono{font-family:"SF Mono",ui-monospace,SFMono-Regular,Menlo,monospace}
.banners{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:16px;margin:18px 0}
.banner,.tile,.card-wrap,details.trial-card,.fix-alert{
  background:var(--glass);backdrop-filter:blur(40px) saturate(210%);-webkit-backdrop-filter:blur(40px) saturate(210%);
  border:1px solid var(--glass-rim);box-shadow:var(--shadow);
}
.banner{padding:16px 20px;border-radius:22px;display:flex;align-items:center;justify-content:space-between;gap:12px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:16px;margin:18px 0}
.tile{border-radius:22px;padding:18px 20px;display:flex;align-items:center;justify-content:space-between;gap:12px}
.tile .lbl{font-size:12px;font-weight:600;color:var(--muted)}
.tile b{display:block;font-size:29px;font-weight:700;letter-spacing:-0.03em;margin-top:4px}
.good{color:var(--good)}.bad{color:var(--bad)}.warn{color:var(--warn)}
.pill{display:inline-flex;align-items:center;padding:4px 11px;border-radius:999px;font-size:11.5px;font-weight:600;border:1px solid rgba(255,255,255,0.72)}
.p-good{background:var(--good-soft);color:#15803d}
.p-bad{background:var(--bad-soft);color:#be123c}
.p-warn{background:var(--warn-soft);color:#b45309}
.p-blue{background:var(--blue-soft);color:#0062cc}
.chip{display:inline-block;background:var(--chip);border:1px solid rgba(255,255,255,0.8);border-radius:999px;padding:2px 9px;margin:2px;font-size:11.5px;font-weight:600}
.card-wrap{border-radius:24px;overflow-x:auto}
table{width:100%;border-collapse:collapse}
th,td{text-align:left;padding:12px 16px;border-bottom:1px solid var(--line);vertical-align:middle}
th{font-weight:600;color:var(--muted);font-size:11.5px}
tr:last-child td{border-bottom:none}
.toolbar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;justify-content:space-between;margin:28px 0 12px}
.seg{display:inline-flex;gap:3px;padding:4px;border-radius:999px;background:rgba(15,23,42,0.05)}
.seg button{border:none;background:transparent;color:var(--fg2);padding:6px 14px;border-radius:999px;font-size:12.5px;font-weight:600;cursor:pointer}
.seg button.active{background:rgba(255,255,255,0.92);color:var(--blue);box-shadow:0 2px 8px rgba(15,23,42,0.08)}
details.trial-card{border-radius:20px;margin:10px 0;padding:13px 18px}
summary{cursor:pointer;font-weight:600;display:flex;align-items:center;justify-content:space-between;gap:12px;list-style:none}
summary::-webkit-details-marker{display:none}
pre{white-space:pre-wrap;word-break:break-word;font:12px/1.55 "SF Mono",ui-monospace,Menlo,monospace;background:rgba(255,255,255,0.55);border:1px solid rgba(255,255,255,0.8);padding:10px 12px;border-radius:12px;max-height:300px;overflow:auto;margin:6px 0 0}
a{color:var(--blue);text-decoration:none;font-weight:600}
a:hover{text-decoration:underline}
.btn-fix{border:1px solid rgba(255,255,255,0.42);cursor:pointer;display:inline-flex;align-items:center;gap:6px;padding:8px 16px;border-radius:999px;font-size:13px;font-weight:600;background:linear-gradient(180deg,rgba(255,69,58,0.96) 0%,rgba(225,29,72,0.96) 100%);color:#fff;box-shadow:inset 0 1px 0 rgba(255,255,255,0.45),0 8px 20px -4px rgba(225,29,72,0.36)}
.btn-fix-sm{border:1px solid rgba(255,255,255,0.42);cursor:pointer;display:inline-flex;align-items:center;gap:4px;padding:4px 11px;border-radius:999px;font-size:11.5px;font-weight:600;background:linear-gradient(180deg,rgba(255,69,58,0.95) 0%,rgba(225,29,72,0.95) 100%);color:#fff}
.fix-alert{padding:16px 22px;border-radius:22px;border-color:rgba(255,59,48,0.28);display:flex;align-items:center;justify-content:space-between;gap:14px;margin:16px 0}
"""

BOLT_SVG = "<svg width='14' height='14' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><path d='M13 2L3 14h9l-1 8 10-12h-9l1-8z'/></svg>"


def _e(x: Any) -> str:
    return html.escape(str(x))


def _pct(x: float | None) -> str:
    return "–" if x is None else f"{x:.0%}"


def _ring(pct: float, color: str, gid: str) -> str:
    r = 22
    c = 2 * math.pi * r
    clamped = max(0.0, min(1.0, pct))
    off = c * (1.0 - clamped)
    return (
        f"<svg width='56' height='56' viewBox='0 0 56 56'>"
        f"<defs><linearGradient id='{gid}' x1='0' y1='0' x2='1' y2='1'><stop offset='0%' stop-color='{color}'/><stop offset='100%' stop-color='#38bdf8'/></linearGradient></defs>"
        f"<circle cx='28' cy='28' r='{r}' fill='none' stroke='rgba(15,23,42,0.065)' stroke-width='5.8'/>"
        f"<circle cx='28' cy='28' r='{r}' fill='none' stroke='url(#{gid})' stroke-width='5.8' stroke-linecap='round' "
        f"stroke-dasharray='{c:.1f}' stroke-dashoffset='{off:.1f}' transform='rotate(-90 28 28)'/></svg>"
    )


def render(summary: dict[str, Any], results: list[dict[str, Any]], cmp: dict[str, Any] | None,
           calibration: dict[str, Any] | None) -> str:
    meta = summary["meta"]
    failing_cases = [cid for cid, r in summary.get("cases", {}).items() if r.get("pass_rate", 1.0) < 1.0]
    top_fix_btn = (
        f"<button class='btn-fix' onclick=\"reportAutoFix('')\">{BOLT_SVG}<span>Auto-Fix Failures ({len(failing_cases)})</span></button>"
        if failing_cases else ""
    )
    out = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>",
        f"<title>AutoHarness Run · {_e(summary['run_id'])}</title><style>{CSS}</style></head><body>",
        "<div class='aurora'><div class='orb o1'></div><div class='orb o2'></div><div class='orb o3'></div></div>",
        "<div class='dock-wrap'><header class='topbar'><div class='brand'>",
        "<svg width='28' height='28' viewBox='0 0 128 128'><rect x='6' y='6' width='116' height='116' rx='28' fill='#0f172a'/>"
        "<path d='M 64 24 A 40 40 0 0 1 101 49' fill='none' stroke='#4285F4' stroke-width='8' stroke-linecap='round'/>"
        "<path d='M 101 79 A 40 40 0 0 1 64 104' fill='none' stroke='#34A853' stroke-width='8' stroke-linecap='round'/>"
        "<path d='M 64 104 A 40 40 0 0 1 27 79' fill='none' stroke='#FBBC04' stroke-width='8' stroke-linecap='round'/>"
        "<path d='M 27 49 A 40 40 0 0 1 64 24' fill='none' stroke='#EA4335' stroke-width='8' stroke-linecap='round'/>"
        "<polygon points='64,51 77,64 64,77 51,64' fill='#4285F4'/></svg>",
        f"<span>Auto<span style='color:var(--blue)'>Harness</span> Report</span></div>"
        f"<div style='display:flex;align-items:center;gap:10px'>{top_fix_btn}<span class='pill p-blue mono'>{_e(summary['run_id'])}</span></div></header></div><main>",
        f"<h1>Evaluation Run · {_e(summary['run_id'])}</h1>",
        f"<div class='muted'>Substrate <b>{_e(meta['agent'].get('type'))}</b> ({_e(meta['agent'].get('model', 'cli'))}) · "
        f"Harness <span class='mono'>{_e(meta.get('harness_fingerprint') or '–')}</span> · "
        f"Judge <b>{_e(meta['judge']['model'])}</b> · {meta['n_cases']} cases, {meta['n_trials']} trials · {summary['wall_time_s']}s</div>",
    ]
    if failing_cases:
        out.append(
            "<div class='fix-alert' id='fix-status-box'><div>"
            f"<b>{len(failing_cases)} Failing Case(s) Ready for Automatic Repair:</b> "
            f"<span class='mono' style='font-size:12.5px'>{_e(', '.join(failing_cases[:8]))}</span>"
            "<div class='muted' style='font-size:12.5px;margin-top:2px'>Repairs broken modules with write-locked tests or runs Propose-Probe-Compose against hidden holdouts.</div>"
            "</div>"
            f"<button class='btn-fix' onclick=\"reportAutoFix('')\">{BOLT_SVG}<span>Auto-Fix Now</span></button></div>"
        )
    else:
        out.append("<div id='fix-status-box'></div>")
    out.append("<div class='banners'>")

    if cmp:
        pcls = "p-good" if cmp["gate_passed"] else "p-bad"
        msg = "GATE: PASS" if cmp["gate_passed"] else "GATE: FAIL — " + "; ".join(cmp["gate_reasons"])
        out.append(
            f"<div class='banner'><div><div class='muted' style='font-size:12px;font-weight:600'>Regression Gate</div>"
            f"<b>{_e(msg)}</b> <span class='muted'>vs {_e(cmp['baseline'])}</span></div>"
            f"<span class='pill {pcls}'>{'Safe to Ship' if cmp['gate_passed'] else 'Blocked'}</span></div>"
        )

    tcls = "p-good" if (meta["judge_trusted"] or summary["review_queue"] == 0) else "p-warn"
    tlabel = "Judge Auto-Certified" if (meta["judge_trusted"] or summary["review_queue"] == 0) else "Judge Awaiting Calibration"
    out.append(
        f"<div class='banner'><div><div class='muted' style='font-size:12px;font-weight:600'>Judge Verification</div>"
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
        ("Pass Rate", _pct(summary["pass_rate"]) + delta("suite_delta", lambda v: f"{v:+.0%}"), _ring(summary["pass_rate"], "#007aff", "rr1")),
        ("Every-Trial (pass^k)", _pct(summary["all_trials_pass_rate"]), _ring(summary["all_trials_pass_rate"], "#16a34a", "rr2")),
        ("Mean Score", f"{summary['mean_score']:.2f}" + delta("score_delta", lambda v: f"{v:+.2f}"), _ring(summary["mean_score"], "#9333ea", "rr3")),
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
        "<tr><th>Case</th><th>Split</th><th>Pass Rate</th><th>vs Baseline</th><th>Score</th><th>Turns</th><th>Tokens</th><th>Failing Checks</th><th>Action</th></tr>"
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
        act = (
            f"<button class='btn-fix-sm' onclick=\"reportAutoFix('{_e(cid)}')\">{BOLT_SVG}<span>Auto-Fix</span></button>"
            if row["pass_rate"] < 1 else "<span class='pill p-good'>Healthy</span>"
        )
        out.append(
            f"<tr><td><a href='#c-{_e(cid)}'>{_e(cid)}</a>{review}<div class='muted' style='font-size:12px'>{_e(row['description'])}</div></td>"
            f"<td><span class='pill {sp_cls}'>{_e(split_val)}</span></td>"
            f"<td><span class='pill {pr_cls}'>{_pct(row['pass_rate'])}</span></td><td>{vs}</td>"
            f"<td class='mono'>{row['mean_score']:.2f}</td><td class='mono'>{row['mean_turns']}</td>"
            f"<td class='mono'>{row['mean_tokens']:,}</td><td>{fails}</td><td>{act}</td></tr>"
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
            f"<div class='muted mono' style='font-size:12.5px'>score {r['score']:.2f} · {r['turns']} turns · {r['tokens']:,} tok</div></summary>"
        )
        if r.get("error"):
            out.append(f"<pre class='bad'>{_e(r['error'])}</pre>")
        for reason in r.get("review_reasons", []):
            out.append(f"<div class='warn' style='margin:8px 0;font-weight:600'>{_e(reason)}</div>")
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
        "async function reportAutoFix(caseId){"
        "const box=document.getElementById('fix-status-box');"
        "box.className='fix-alert';box.innerHTML='<div><b>Running Automatic Repair...</b><div class=muted style=font-size:12.5px>Isolating failure, locking tests, and probing repairs via AutoHarness Studio...</div></div>';"
        "for(const port of [8484,8485]){"
        "try{const r=await fetch('http://127.0.0.1:'+port+'/api/autofix',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({target_case:caseId||null})});"
        "if(r.ok){const d=await r.json();const aft=d.after_pass_rate!=null?Math.round(d.after_pass_rate*100)+'%':'done';"
        "box.innerHTML='<div><b class=good>Auto-Fix Verified ('+d.status+') · Updated Pass Rate: '+aft+'</b><div class=muted style=font-size:12.5px>Open <a href=http://127.0.0.1:'+port+' target=_blank>AutoHarness Studio</a> to inspect diffs & traces.</div></div>';return;}}catch(e){}}"
        "if(navigator.clipboard)navigator.clipboard.writeText('autoharness heal --with-harness');"
        "box.innerHTML='<div><b>Start Studio Server or Run CLI Fix</b><div class=muted style=font-size:12.5px>Copied <code>autoharness heal --with-harness</code> to clipboard (or run <code>autoharness studio</code> for live 1-click browser repairs).</div></div>';"
        "}"
        "</script></main></body></html>"
    )
    return "\n".join(out)
