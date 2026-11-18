"""Self-contained HTML report for one run (optionally diffed against the baseline)."""

from __future__ import annotations

import html
from typing import Any

CSS = """
:root{--bg:#fafaf9;--fg:#1c1917;--muted:#78716c;--card:#fff;--line:#e7e5e4;--good:#15803d;--bad:#b91c1c;--warn:#b45309;--chip:#f5f5f4}
@media (prefers-color-scheme:dark){:root{--bg:#0c0a09;--fg:#e7e5e4;--muted:#a8a29e;--card:#1c1917;--line:#292524;--good:#4ade80;--bad:#f87171;--warn:#fbbf24;--chip:#292524}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
main{max-width:1100px;margin:0 auto;padding:24px 16px 64px}h1{font-size:20px;margin:0 0 4px}h2{font-size:15px;margin:32px 0 8px}
.muted{color:var(--muted)}.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:16px 0}
.tile{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px}.tile b{display:block;font-size:22px}
.good{color:var(--good)}.bad{color:var(--bad)}.warn{color:var(--warn)}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}th{font-weight:600;color:var(--muted);font-size:12px}
.chip{display:inline-block;background:var(--chip);border-radius:6px;padding:0 6px;margin:1px;font-size:12px}
details{background:var(--card);border:1px solid var(--line);border-radius:10px;margin:8px 0;padding:8px 12px}
summary{cursor:pointer}pre{white-space:pre-wrap;word-break:break-word;font-size:12px;background:var(--chip);padding:8px;border-radius:6px;max-height:320px;overflow:auto}
.banner{padding:10px 12px;border-radius:10px;border:1px solid var(--line);background:var(--card);margin:12px 0}
.wrap{overflow-x:auto}
"""


def _e(x: Any) -> str:
    return html.escape(str(x))


def _pct(x: float | None) -> str:
    return "–" if x is None else f"{x:.0%}"


def render(summary: dict[str, Any], results: list[dict[str, Any]], cmp: dict[str, Any] | None,
           calibration: dict[str, Any] | None) -> str:
    meta = summary["meta"]
    out = [f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
           f"<title>Harness Eval Run</title><style>{CSS}</style></head><body><main>"]
    out.append(f"<h1>Harness eval · {_e(summary['run_id'])}</h1>")
    out.append(f"<div class='muted'>agent {_e(meta['agent'].get('type'))} / {_e(meta['agent'].get('model', meta['agent'].get('command', '')))}"
               f" · harness {_e(meta.get('harness_fingerprint') or '–')} · judge {_e(meta['judge']['model'])}"
               f" · {meta['n_cases']} cases, {meta['n_trials']} trials · {summary['wall_time_s']}s</div>")

    if cmp:
        cls = "good" if cmp["gate_passed"] else "bad"
        msg = "Gate passed" if cmp["gate_passed"] else "Gate failed: " + "; ".join(cmp["gate_reasons"])
        out.append(f"<div class='banner'><b class='{cls}'>{_e(msg)}</b> <span class='muted'>vs baseline {_e(cmp['baseline'])}</span></div>")

    trust_cls = "good" if meta["judge_trusted"] else "warn"
    out.append(f"<div class='banner'><b class='{trust_cls}'>Judge {'trusted' if meta['judge_trusted'] else 'not yet trusted'}</b>"
               f" <span class='muted'>{_e(meta['judge_trust_reason'])}</span></div>")

    def delta(key, fmt):
        if not cmp:
            return ""
        v = cmp[key]
        cls = "good" if v > 0 else "bad" if v < 0 else "muted"
        if key == "token_delta_pct":
            cls = "bad" if v > 0 else "good" if v < 0 else "muted"
        return f" <span class='{cls}'>{fmt(v)}</span>"

    tiles = [
        ("Pass rate", _pct(summary["pass_rate"]) + delta("suite_delta", lambda v: f"{v:+.0%}")),
        ("Passes every trial", _pct(summary["all_trials_pass_rate"])),
        ("Mean score", f"{summary['mean_score']:.2f}" + delta("score_delta", lambda v: f"{v:+.2f}")),
        ("Needs human review", f"<span class='{'warn' if summary['review_queue'] else 'good'}'>{summary['review_queue']}</span>"),
        ("Tokens", f"{(summary['input_tokens'] + summary['output_tokens']):,}" + delta("token_delta_pct", lambda v: f"{v:+.0f}%")),
        ("Est. agent cost", f"${summary['est_agent_cost_usd']:.2f}"),
    ]
    out.append("<div class='tiles'>" + "".join(f"<div class='tile'><span class='muted'>{k}</span><b>{v}</b></div>" for k, v in tiles) + "</div>")

    rows_by_case = {r["case"]: r for r in (cmp["rows"] if cmp else [])}
    out.append("<h2>Cases</h2><div class='wrap'><table><tr><th>Case</th><th>Pass rate</th><th>vs baseline</th><th>Score</th>"
               "<th>Turns</th><th>Tokens</th><th>Failing checks</th></tr>")
    for cid, row in sorted(summary["cases"].items(), key=lambda kv: (kv[1]["pass_rate"], kv[0])):
        cr = rows_by_case.get(cid)
        vs = ""
        if cr:
            if cr["status"] == "new":
                vs = "<span class='chip'>new</span>"
            elif cr["delta"]:
                vs = f"<span class='{'good' if cr['delta'] > 0 else 'bad'}'>{cr['delta']:+.0%}</span>"
                if cr["status"] == "regressed":
                    vs += " <span class='chip bad'>regressed</span>"
        pr_cls = "good" if row["pass_rate"] == 1 else "bad" if row["pass_rate"] == 0 else "warn"
        fails = "".join(f"<span class='chip'>{_e(k)} ×{v}</span>" for k, v in row["failed_checks"].items())
        review = f" <span class='chip warn'>{row['needs_review']} review</span>" if row["needs_review"] else ""
        out.append(f"<tr><td><a href='#c-{_e(cid)}'>{_e(cid)}</a>{review}<div class='muted'>{_e(row['description'])}</div></td>"
                   f"<td class='{pr_cls}'>{_pct(row['pass_rate'])}</td><td>{vs}</td><td>{row['mean_score']:.2f}</td>"
                   f"<td>{row['mean_turns']}</td><td>{row['mean_tokens']:,}</td><td>{fails}</td></tr>")
    out.append("</table></div>")

    out.append("<h2>Trials</h2>")
    for r in sorted(results, key=lambda r: (r["case_id"], r["trial"])):
        mark = "<b class='good'>PASS</b>" if r["passed"] else "<b class='bad'>FAIL</b>"
        rev = " <span class='chip warn'>needs review</span>" if r["needs_review"] else ""
        out.append(f"<details id='c-{_e(r['case_id'])}'><summary>{mark} {_e(r['case_id'])} · trial {r['trial']} · "
                   f"score {r['score']:.2f} · {r['turns']} turns · {r['tokens']:,} tok{rev}</summary>")
        if r.get("error"):
            out.append(f"<pre class='bad'>{_e(r['error'])}</pre>")
        for reason in r.get("review_reasons", []):
            out.append(f"<div class='warn'>review: {_e(reason)}</div>")
        out.append("<table><tr><th>Check</th><th>Layer</th><th>Result</th><th>Detail</th></tr>")
        for c in r["checks"]:
            res = "skipped" if c["skipped"] else ("pass" if c["passed"] else "fail")
            cls = "muted" if c["skipped"] else ("good" if c["passed"] else "bad")
            conf = f" (conf {c['confidence']})" if c.get("confidence") is not None else ""
            out.append(f"<tr><td>{_e(c['name'])}{'' if c['required'] else ' <span class=muted>(optional)</span>'}</td>"
                       f"<td>{_e(c['layer'])}</td><td class='{cls}'>{res}{_e(conf)}</td><td><pre>{_e(c['detail'])}</pre></td></tr>")
        out.append("</table></details>")

    if calibration:
        out.append("<h2>Judge calibration</h2>")
        cm = calibration["confusion"]
        out.append(f"<div class='banner'>agreement <b>{_pct(calibration['agreement'])}</b> · kappa {calibration['kappa']:.2f} · "
                   f"{calibration['n']} labels · judge too lenient {cm['fp']} · too strict {cm['fn']} · {_e(calibration['at'])}</div>")
    out.append("</main></body></html>")
    return "\n".join(out)
