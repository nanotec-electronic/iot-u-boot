#!/usr/bin/env python3
"""Shared HTML renderer for the Nanotec repo-status dashboard (multi-page).

Pages:
  render_index(status)          -> index.html   (tracks + domain % + review tile)
  render_blocker(status, blk)   -> blocker-<id>.html  (problem + issue link)
  render_history(status)        -> reviews.html  (list of past reviews -> reports)
  render_report_viewer()        -> report.html   (renders reports/<f>.md via marked.js)

Portable: no dependency on state.yaml or repo layout. Copy verbatim into Class B
repos; build.py drives it with a status dict derived from the tracked task source
(Class A: state.yaml; Class B: .workflow/tasks.md).
Security detail lives ONLY in the linked full reports (internal host); the tiles
show numbers only.
"""
from __future__ import annotations
import html, json, re, sys

CSS = """
  :root { --be:#2563eb; --fe:#7c3aed; --t2:#0891b2; --t3:#ca8a04; --t4:#dc2626;
    --bg:#0f172a; --card:#1e293b; --card2:#243044; --txt:#e2e8f0; --mut:#94a3b8;
    --grn:#86efac; --amb:#fcd34d; --red:#fca5a5; }
  * { box-sizing:border-box; }
  body { font-family:ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
    background:var(--bg); color:var(--txt); margin:0; padding:2rem; line-height:1.5; }
  a { color:#93c5fd; text-decoration:none; } a:hover { text-decoration:underline; }
  .wrap { max-width:960px; margin:0 auto; }
  header { border-bottom:1px solid #334155; padding-bottom:1rem; margin-bottom:1.5rem; }
  h1 { margin:0 0 .25rem; font-size:1.5rem; } h2 { font-size:1.15rem; margin:0 0 .6rem; }
  h2 small { color:var(--mut); font-weight:400; font-size:.85rem; }
  .prov { color:var(--mut); font-size:.8rem; }
  .back { font-size:.85rem; color:var(--mut); display:inline-block; margin-bottom:1rem; }
  .card, .track { background:var(--card); border-radius:10px; padding:1.2rem 1.4rem;
    margin-bottom:1.2rem; border-left:4px solid #64748b; }
  .track.be { border-left-color:var(--be); } .track.fe { border-left-color:var(--fe); }
  .track.t2 { border-left-color:var(--t2); } .track.t3 { border-left-color:var(--t3); }
  .track.t4 { border-left-color:var(--t4); }
  .bar { position:relative; background:#334155; border-radius:6px; height:24px; overflow:hidden; margin:.3rem 0 .5rem; }
  .bar .fill { height:100%; border-radius:6px; background:#64748b; }
  .bar.be .fill { background:var(--be); } .bar.fe .fill { background:var(--fe); }
  .bar.t2 .fill { background:var(--t2); } .bar.t3 .fill { background:var(--t3); }
  .bar.t4 .fill { background:var(--t4); }
  .bar .lbl { position:absolute; right:10px; top:1px; font-size:.82rem; font-weight:600; }
  .counts { font-size:.9rem; color:var(--mut); }
  .blocker-line a { color:var(--red); font-weight:600; }
  .inprog { color:#93c5fd; margin-top:.3rem; font-size:.9rem; }
  .domains { margin-top:1rem; }
  .dom-row { display:grid; grid-template-columns:150px 1fr 48px; gap:.7rem; align-items:center; margin:.35rem 0; font-size:.88rem; }
  .dom-row .dbar { background:#334155; border-radius:5px; height:16px; overflow:hidden; }
  .dom-row .dbar > div { height:100%; background:var(--be); border-radius:5px; }
  .dom-row .pct { text-align:right; color:var(--mut); }
  table { width:100%; border-collapse:collapse; margin-top:.6rem; font-size:.85rem; }
  th,td { text-align:left; padding:.4rem .5rem; border-bottom:1px solid #334155; vertical-align:top; }
  th { color:var(--mut); font-weight:500; }
  .grid { display:grid; grid-template-columns:1fr 1fr; gap:1.2rem; }
  .tile { background:var(--card); border-radius:10px; padding:1.2rem 1.4rem; }
  .tile.review { border-left:4px solid var(--t2); }
  .ampel { font-weight:700; font-size:1.05rem; }
  .ampel.green { color:var(--grn); } .ampel.red { color:var(--red); } .ampel.amber { color:var(--amb); }
  .sev { display:flex; gap:.6rem; flex-wrap:wrap; margin:.5rem 0; font-size:.85rem; }
  .sev span { background:var(--card2); padding:.15rem .5rem; border-radius:5px; }
  .muted { color:var(--mut); font-size:.85rem; }
  .findings b { font-size:1.35rem; }
  .rev-item { display:grid; grid-template-columns:96px 80px 1fr auto; gap:.8rem; align-items:center;
    padding:.6rem .5rem; border-bottom:1px solid #334155; font-size:.9rem; }
  .badge { font-size:.72rem; padding:.1rem .45rem; border-radius:4px; background:var(--card2); color:var(--mut); }
  .badge.audit { color:#fca5a5; } .badge.security { color:#fcd34d; } .badge.review { color:#93c5fd; }
  footer { margin-top:2rem; color:var(--mut); font-size:.78rem; border-top:1px solid #334155; padding-top:1rem; }
  .report-body { background:var(--card); border-radius:10px; padding:1.5rem 2rem; }
  .report-body h1,.report-body h2,.report-body h3 { border-bottom:1px solid #334155; padding-bottom:.3rem; }
  .report-body table { font-size:.82rem; } .report-body code { background:#0b1220; padding:.1rem .3rem; border-radius:4px; }
  .report-body pre { background:#0b1220; padding:1rem; border-radius:8px; overflow:auto; }
  .curated-note { background:#3b2f14; border:1px solid #78621f; color:#fde68a;
    border-radius:8px; padding:.6rem .8rem; margin:.6rem 0; font-size:.85rem; }
  .tag { font-size:.68rem; padding:.05rem .35rem; border-radius:4px; vertical-align:middle; }
  .tag.done { background:#14532d; color:#86efac; } .tag.prog { background:#1e3a5f; color:#93c5fd; }
  .tag.plan { background:#3f3f46; color:#d4d4d8; } .tag.untr { background:#4c1d1d; color:#fca5a5; }
  .explain { background:var(--card2); border-radius:10px; padding:1rem 1.4rem; margin-bottom:1.2rem; }
  .explain h3 { margin:0 0 .5rem; font-size:1rem; } .explain ul { margin:0; padding-left:1.1rem; }
  .explain li { margin:.35rem 0; font-size:.88rem; } .explain b { color:var(--txt); }
  .fs-res { color:#86efac; } .fs-plan { color:#93c5fd; } .fs-trk { color:#d4d4d8; }
  .fs-acc { color:#a1a1aa; } .fs-open { color:var(--mut); }
  .badge.fs-res { background:#14532d; } .badge.fs-plan { background:#1e3a5f; }
  .badge.fs-trk { background:#3f3f46; } .badge.fs-acc { background:#292524; } .badge.fs-open { background:#1f2937; }
  td.sevcell { color:var(--mut); text-transform:uppercase; font-size:.72rem; }
"""


def _e(s):
    return html.escape(str(s))


def _page(title, body):
    return (f"<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            f"<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            f"<title>{_e(title)}</title><style>{CSS}</style></head>"
            f"<body><div class=\"wrap\">{body}</div></body></html>")


def _bar(pct, cls=""):
    return (f'<div class="bar {cls}"><div class="fill" style="width:{pct}%"></div>'
            f'<span class="lbl">{pct}%</span></div>')


# Shown under every progress bar. The % is a plain COUNT of issues/tasks (done ÷ total), NOT
# effort-weighted — a one-line task and a multi-week task count the same. Honest framing so the
# bar is never read as "% of the work done".
PCT_DISCLAIMER = ('<div class="muted" style="font-size:.78rem;margin:-.2rem 0 .5rem">'
                  'ℹ️ Unweighted — counts issues/tasks (done ÷ total), not effort. '
                  'Individual items can differ greatly in size.</div>')


# Per-track color class so EVERY repo shares ONE visual line identical to iot-device-admin:
# the first track is blue (--be), the second purple (--fe) — exactly like device-admin's
# backend/frontend — and any further tracks fall back to the remaining palette. The
# device-admin names keep their fixed colors regardless of position. So a single-track
# Class B repo (gadget, kernel, master) looks exactly like device-admin's primary track: blue.
_BY_POSITION = ("be", "fe", "t2", "t3", "t4")


def _track_cls(name, index=0):
    if name == "backend":
        return "be"
    if name == "frontend":
        return "fe"
    return _BY_POSITION[index] if index < len(_BY_POSITION) else "t4"


_STATUS_TAG = {
    "done": '<span class="tag done">done</span>',
    "in_progress": '<span class="tag prog">in progress</span>',
    "planned": '<span class="tag plan">planned</span>',
    "untracked": '<span class="tag untr">not yet tracked</span>',
}


def _domains_block(domains, curated=False):
    if not domains:
        return ""
    rows = ""
    for d in domains:
        # curated domains use "name"; derived ones use "domain"
        name = d.get("name", d.get("domain", "?"))
        tag = _STATUS_TAG.get(d.get("status", ""), "")
        rows += (f'<div class="dom-row"><span>{_e(name)} {tag}</span>'
                 f'<div class="dbar"><div style="width:{d["percent"]}%"></div></div>'
                 f'<span class="pct">{d["percent"]}%</span></div>')
    label = ("Per domain (curated — tracking incomplete)" if curated
             else "Per domain (package-based)")
    return (f'<div class="domains"><h3 style="font-size:.95rem;margin:.6rem 0 .2rem">'
            f'{label}</h3>{rows}</div>')


def _fstatus_summary(s):
    fs = s.get("findings_status")
    if not fs:
        return ""
    t = fs.get("tally", {})
    return (f'<div style="margin-top:.5rem"><a href="findings.html">'
            f'Implementation status: ✅ {t.get("resolved",0)} · 🗓️ {t.get("planned",0)} · '
            f'📋 {t.get("tracked",0)} · 🚫 {t.get("accepted",0)} · ⚪ {t.get("open",0)} →</a></div>')


def render_index(s):
    prov = s.get("provenance", {}) or {}
    tracks = ""
    for i, t in enumerate(s.get("tracks", [])):
        cls = _track_cls(t["name"], i)
        blk = ""
        for b in t.get("blocked", []):
            blk += (f'<div class="blocker-line">🔴 blocked: '
                    f'<a href="blocker-{_e(b["id"])}.html">{_e(b["id"])}</a></div>')
        inprog = (f'<div class="inprog">🔵 in progress: {_e(", ".join(t["in_progress"]))}</div>'
                  if t.get("in_progress") else "")
        c = t.get("counts", {})
        # Each count links to its detail page when the track carries that detail list —
        # one shared behavior across done / pending / blocked (any repo/class that provides it).
        nm = _e(t["name"])
        done_n, pend_n, blk_n = c.get("completed", 0), c.get("pending", 0), c.get("blocked", 0)
        done_html = (f'<a href="completed.html#{nm}">✅ {done_n} done</a>'
                     if t.get("completed") else f'✅ {done_n} done')
        pend_html = (f'<a href="pending.html#{nm}">🟡 {pend_n} pending</a>'
                     if t.get("pending") else f'🟡 {pend_n} pending')
        blk_html = (f'<a href="blocked.html#{nm}">🔴 {blk_n} blocked</a>'
                    if t.get("blocked") else f'🔴 {blk_n} blocked')
        counts = (f'{done_html} · {pend_html} · {blk_html} · '
                  f'⏸ {c.get("wont_do",0)} won\'t-do')
        # curated % note (frontend): show the correction transparently
        curated = t.get("percent_curated", False)
        pct_note = ""
        if curated:
            raw = t.get("percent_source")
            pct_note = (f'<div class="curated-note">⚠️ Curated estimate — package '
                        f'tracking is incomplete for this track'
                        f'{f" (raw package count would read {raw}%)" if raw is not None else ""}. '
                        f'{_e(t.get("note",""))}</div>')
        tracks += (f'<section class="track {cls}"><h2>{_e(t["name"].capitalize())} '
                   f'<small>{t["done"]}/{t["total"]} tracked packages</small></h2>'
                   f'{_bar(t["percent"], cls)}{PCT_DISCLAIMER}<div class="counts">{counts}</div>'
                   f'{pct_note}{blk}{inprog}{_domains_block(t.get("domains"), curated)}</section>')

    f = s.get("findings", {}) or {}
    findings_tile = (
        f'<div class="tile"><h2>Findings</h2><div class="findings">'
        f'<b>{f.get("open",0)}</b> open · <b>{f.get("planned",0)}</b> planned · '
        f'<b>{f.get("closed",0)}</b> closed</div>'
        f'<div class="muted" style="margin-top:.5rem">Findings = review observations '
        f'(bugs, drift, cleanup) awaiting a decision — distinct from packages below.</div>'
        + (f'<div style="margin-top:.6rem"><a href="report.html?f=findings.md">'
           f'Open findings.md →</a></div>' if s.get("has_findings_md") else "")
        + '</div>')

    audit = s.get("audit")
    if audit:
        sev = audit.get("severity", {}) or {}
        dot = "🟢" if audit["security_ampel"] == "green" else "🔴"
        sevhtml = "".join(f'<span>{v} {k}</span>' for k, v in sev.items()) if sev else ""
        review_tile = (
            f'<div class="tile review"><h2>Review / Audit</h2>'
            f'<div class="ampel {audit["security_ampel"]}">{dot} '
            f'{audit["critical_or_high"]} critical/high · {audit["total_findings"]} findings total</div>'
            f'<div class="sev">{sevhtml}</div>'
            f'<div class="muted">Method: {_e(audit["method"])}<br>Latest: {_e(audit["date"])}</div>'
            f'{_fstatus_summary(s)}'
            f'<div style="margin-top:.6rem"><a href="reviews.html">All reviews →</a> · '
            f'<a href="report.html?f={_e(audit["report"])}">Open latest report →</a></div></div>')
    else:
        review_tile = ('<div class="tile review"><h2>Review / Audit</h2>'
                       '<div class="muted">No audit yet.</div></div>')

    body = (
        f'<header><h1>{_e(s.get("repo","repo"))} — Repo Status</h1>'
        f'<div class="prov">commit <code>{_e(prov.get("commit","?"))}</code> · '
        f'{_e(str(prov.get("committed_at",""))[:16].replace("T"," "))} · '
        f'tracks shown separately (overall project progress lives in the product repo)</div></header>'
        f'{tracks}<div class="grid">{findings_tile}{review_tile}</div>'
        f'<div class="explain"><h3>What am I looking at?</h3><ul>'
        f'<li><b>Packages (State)</b> — units of planned work tracked in the autonomous '
        f'workflow (<code>state.yaml</code>). Their status (done / pending / blocked) drives '
        f'the progress bars. This is <i>what is built</i>.</li>'
        f'<li><b>Findings</b> — observations from code reviews &amp; audits (bugs, spec-drift, '
        f'cleanup, security notes). A finding is a <i>thing noticed that may need action</i>; '
        f'it is not a work package until it is scheduled as one. This is <i>what was noticed</i>.</li>'
        f'<li><b>Review / Audit</b> — the periodic health check that produces findings. The tile '
        f'shows the headline numbers; the linked reports carry the detail.</li></ul></div>'
        f'<footer>Schema v{s.get("schema_version","?")} · machine-readable '
        f'<code>status.json</code> is the dock point for the product-repo dashboard. '
        f'Domain % is package-based unless marked curated; security detail lives only in the linked reports.</footer>')
    return _page(f'{s.get("repo","repo")} — Status', body)


def render_completed(s):
    """Detail page for past/completed tasks — one section per track, linked from the index
    '✅ N done' count. Each track supplies a completed[] list of
    {id, title, date?, ref?, note?}; Class A fills it from state.archive.yaml, Class B from
    the ## Closed section of .workflow/tasks.md. render.py just renders what it is given."""
    sections = ""
    total = 0
    for t in s.get("tracks", []):
        done = t.get("completed", []) or []
        total += len(done)
        rows = ""
        for it in done:
            date = _e(str(it.get("date", "") or ""))
            ref = it.get("ref", "") or ""
            # linkify a commit hash or URL ref, else show plain
            if re.fullmatch(r"[0-9a-f]{7,40}", str(ref)):
                ref_html = f'<code>{_e(ref)}</code>'
            elif re.match(r"https?://", str(ref)):
                ref_html = f'<a href="{_e(ref)}">{_e(ref)}</a>'
            else:
                ref_html = _e(ref)
            note = f'<div class="muted">{_e(it.get("note",""))}</div>' if it.get("note") else ""
            rows += (f'<tr><td class="muted">{date}</td>'
                     f'<td><b>{_e(it.get("title", it.get("id","(untitled)")))}</b>{note}</td>'
                     f'<td>{ref_html}</td></tr>')
        if not rows:
            rows = '<tr><td colspan="3" class="muted">No completed items recorded for this track.</td></tr>'
        sections += (f'<section class="card" id="{_e(t["name"])}"><h2>{_e(t["name"].capitalize())} '
                     f'<small>{len(done)} completed</small></h2>'
                     f'<table><thead><tr><th>Date</th><th>Task</th><th>Ref</th></tr></thead>'
                     f'<tbody>{rows}</tbody></table></section>')
    body = (
        f'<a class="back" href="index.html">← Status</a>'
        f'<header><h1>{_e(s.get("repo","repo"))} — Completed tasks</h1>'
        f'<div class="prov">{total} completed across {len(s.get("tracks", []))} track(s) · '
        f'newest first per track</div></header>'
        f'{sections}'
        f'<footer>Completed detail comes from tracked inputs (Class A: state.archive.yaml · '
        f'Class B: .workflow/tasks.md). Generated artifact — do not edit by hand.</footer>')
    return _page(f'{s.get("repo","repo")} — Completed', body)


def render_pending(s):
    """Detail page for pending (planned/not-yet-started) tasks — one section per track,
    linked from the index '🟡 N pending' count. Each track supplies a pending[] list of
    {id, domain?, title?, note?}. Same shape/behavior as render_completed (one line)."""
    sections = ""
    total = 0
    for t in s.get("tracks", []):
        pend = t.get("pending", []) or []
        total += len(pend)
        rows = ""
        for it in pend:
            note = f'<div class="muted">{_e(it.get("note",""))}</div>' if it.get("note") else ""
            rows += (f'<tr><td class="muted">{_e(it.get("domain","") or "")}</td>'
                     f'<td><b>{_e(it.get("title", it.get("id","(untitled)")))}</b>{note}</td></tr>')
        if not rows:
            rows = '<tr><td colspan="2" class="muted">No pending items recorded for this track.</td></tr>'
        sections += (f'<section class="card" id="{_e(t["name"])}"><h2>{_e(t["name"].capitalize())} '
                     f'<small>{len(pend)} pending</small></h2>'
                     f'<table><thead><tr><th>Domain</th><th>Task</th></tr></thead>'
                     f'<tbody>{rows}</tbody></table></section>')
    body = (
        f'<a class="back" href="index.html">← Status</a>'
        f'<header><h1>{_e(s.get("repo","repo"))} — Pending tasks</h1>'
        f'<div class="prov">{total} pending across {len(s.get("tracks", []))} track(s) · '
        f'planned / not yet started</div></header>'
        f'{sections}'
        f'<footer>Pending detail comes from tracked inputs (Class A: state.yaml · '
        f'Class B: .workflow/tasks.md). Generated artifact — do not edit by hand.</footer>')
    return _page(f'{s.get("repo","repo")} — Pending', body)


def render_blocked(s):
    """Detail page for blocked tasks — one section per track, linked from the index
    '🔴 N blocked' count. Each blocker is {id, domain?, reason?, depends_on?} and its row
    links to the existing per-blocker detail page (blocker-<id>.html). One line with the rest."""
    sections = ""
    total = 0
    for t in s.get("tracks", []):
        blk = t.get("blocked", []) or []
        total += len(blk)
        rows = ""
        for b in blk:
            deps = ", ".join(b.get("depends_on", [])) or "—"
            reason = _e((b.get("reason", "") or "").split("\n")[0][:160])
            rows += (f'<tr><td class="muted">{_e(b.get("domain","") or "")}</td>'
                     f'<td><a href="blocker-{_e(b["id"])}.html"><b>{_e(b["id"])}</b></a>'
                     f'<div class="muted">{reason}</div></td>'
                     f'<td class="muted">{_e(deps)}</td></tr>')
        if not rows:
            rows = '<tr><td colspan="3" class="muted">No blockers for this track. 🎉</td></tr>'
        sections += (f'<section class="card" id="{_e(t["name"])}"><h2>{_e(t["name"].capitalize())} '
                     f'<small>{len(blk)} blocked</small></h2>'
                     f'<table><thead><tr><th>Domain</th><th>Blocker</th><th>Depends on</th></tr></thead>'
                     f'<tbody>{rows}</tbody></table></section>')
    body = (
        f'<a class="back" href="index.html">← Status</a>'
        f'<header><h1>{_e(s.get("repo","repo"))} — Blocked tasks</h1>'
        f'<div class="prov">{total} blocked across {len(s.get("tracks", []))} track(s) · '
        f'each links to its full blocker page</div></header>'
        f'{sections}'
        f'<footer>Blocker detail comes from tracked inputs. Generated artifact — do not edit by hand.</footer>')
    return _page(f'{s.get("repo","repo")} — Blocked', body)


def render_blocker(s, b):
    deps = ", ".join(b.get("depends_on", [])) or "—"
    reason = _e(b.get("reason", "") or "No description recorded.")
    # linkify LP#NNNN and launchpad URLs
    reason = re.sub(r"LP#(\d+)",
                    r'<a href="https://launchpad.net/bugs/\1">LP#\1</a>', reason)
    reason = re.sub(r"(?<!\")(https?://[^\s)]+)", r'<a href="\1">\1</a>', reason)
    body = (
        f'<a class="back" href="index.html">← Status</a>'
        f'<header><h1>🔴 Blocker: {_e(b["id"])}</h1>'
        f'<div class="prov">domain {_e(b.get("domain","?"))} · depends on {_e(deps)}</div></header>'
        f'<div class="card"><h2>Problem</h2><p>{reason}</p></div>'
        f'<div class="card"><h2>Status</h2><p class="muted">Blocked pending the linked '
        f'upstream fix. Stays in this state until the dependency is resolved. '
        f'See the issue link above for progress.</p></div>')
    return _page(f'Blocker: {b["id"]}', body)


def render_history(s):
    rows = ""
    for r in s.get("review_history", []):
        fnd = f'{r["findings"]} findings' if r["findings"] is not None else "—"
        rows += (f'<div class="rev-item"><span class="muted">{_e(r["date"])}</span>'
                 f'<span class="badge {r["kind"]}">{_e(r["kind"])}</span>'
                 f'<a href="report.html?f={_e(r["file"])}">{_e(r["title"])}</a>'
                 f'<span class="muted">{_e(fnd)}</span></div>')
    body = (
        f'<a class="back" href="index.html">← Status</a>'
        f'<header><h1>Review & Audit History</h1>'
        f'<div class="prov">{len(s.get("review_history", []))} report(s) · '
        f'produced by the project-audit / spec-review workflows</div></header>'
        f'<div class="card">{rows or "<p class=muted>No reports.</p>"}</div>')
    return _page("Review History", body)


def render_report_viewer():
    # Renders reports/<f>.md client-side. marked.js from CDN (needs network at view
    # time; fine for an internally-hosted dashboard).
    return (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<title>Report</title><style>' + CSS + '</style>'
        '<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script></head>'
        '<body><div class="wrap"><a class="back" href="reviews.html">← Review History</a>'
        '<div id="body" class="report-body">Loading…</div></div><script>'
        'const f=new URLSearchParams(location.search).get("f");'
        'if(!f){document.getElementById("body").textContent="No report specified.";}'
        'else{fetch("reports/"+f).then(r=>r.ok?r.text():Promise.reject(r.status))'
        '.then(md=>{document.getElementById("body").innerHTML=marked.parse(md);})'
        '.catch(e=>{document.getElementById("body").textContent="Could not load report: "+e;});}'
        '</script></body></html>')


_FSTATUS = {
    "resolved": ("✅", "resolved", "fs-res"),
    "planned": ("🗓️", "planned", "fs-plan"),
    "tracked": ("📋", "tracked in findings.md", "fs-trk"),
    "accepted": ("🚫", "accepted — no action", "fs-acc"),
    "open": ("⚪", "open", "fs-open"),
}


def _fref_link(ref):
    if not ref:
        return ""
    if ref.startswith("commit:"):
        h = ref.split(":", 1)[1]
        return f'<a href="report.html?f={_e(_LATEST_REPORT)}"><code>{_e(h)}</code></a>' if False else f'<code>{_e(h)}</code>'
    if ref.startswith("pkg:"):
        return f'<span class="muted">→ package <code>{_e(ref.split(":",1)[1])}</code></span>'
    if ref.startswith("findings.md:"):
        return f'<span class="muted">→ {_e(ref.split(":",1)[1])}</span>'
    return _e(ref)


_LATEST_REPORT = ""


def render_findings_status(s):
    fs = s.get("findings_status") or {}
    items = fs.get("items", [])
    tally = fs.get("tally", {})
    addressed, listed = fs.get("addressed", 0), fs.get("listed", 0)
    accepted, opencnt = fs.get("accepted", 0), fs.get("open", 0)
    decided = fs.get("decided", addressed + accepted)
    pct = round(100 * decided / listed) if listed else 0

    # progress bar: decided (addressed + accepted) vs listed; open is what remains
    prog = (f'<div class="bar"><div class="fill" style="width:{pct}%;background:#22c55e"></div>'
            f'<span class="lbl">{decided}/{listed} decided · {opencnt} open</span></div>')
    tally_line = (f'<div class="sev"><span class="fs-res">✅ {tally.get("resolved",0)} resolved</span>'
                  f'<span class="fs-plan">🗓️ {tally.get("planned",0)} planned</span>'
                  f'<span class="fs-trk">📋 {tally.get("tracked",0)} tracked</span>'
                  f'<span class="fs-acc">🚫 {tally.get("accepted",0)} accepted</span>'
                  f'<span class="fs-open">⚪ {tally.get("open",0)} open</span></div>')

    order = {"resolved": 0, "planned": 1, "tracked": 2, "accepted": 3, "open": 4}
    sev_order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    items_sorted = sorted(items, key=lambda f: (order.get(f.get("status"), 9),
                                                sev_order.get(f.get("severity"), 9)))
    rows = ""
    for f in items_sorted:
        icon, label, cls = _FSTATUS.get(f.get("status", "open"), _FSTATUS["open"])
        note = f' <span class="muted">— {_e(f["note"])}</span>' if f.get("note") else ""
        rows += (f'<tr><td><span class="badge {cls}">{icon} {label}</span></td>'
                 f'<td class="sevcell">{_e(f.get("severity",""))}</td>'
                 f'<td>{_e(f.get("title",""))}{note}</td>'
                 f'<td>{_fref_link(f.get("ref",""))}</td></tr>')

    body = (
        f'<a class="back" href="index.html">← Status</a>'
        f'<header><h1>Findings — implementation status</h1>'
        f'<div class="prov">from {_e(fs.get("source_report",""))} · '
        f'{_e(fs.get("generated_note",""))}</div></header>'
        f'<div class="card">{prog}{tally_line}'
        f'<div class="muted" style="margin-top:.4rem">{_e(fs.get("totals_note",""))}</div></div>'
        f'<div class="card"><table><thead><tr><th>Status</th><th>Sev</th>'
        f'<th>Finding</th><th>Ref</th></tr></thead><tbody>{rows}</tbody></table></div>')
    return _page("Findings Status", body)


# Back-compat: single-page renderer used by earlier callers / Class B minimal use.
def render_html(s):
    return render_index(s)


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else "status.json"
    print(render_index(json.load(open(src))))
