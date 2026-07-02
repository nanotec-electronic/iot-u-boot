#!/usr/bin/env python3
"""Class-B dashboard generator — parses .workflow/tasks.md (+ findings.md) and renders the site.

Same role as iot-device-admin's Class-A build.py, but the tracked task source is a Markdown
tasks.md instead of a state.yaml. ONE mechanism, no extra curated file: tasks.md is the single
source of truth. `## Open`/`## Planned` entries → pending, `## Closed` → done. done/total/percent
are derived exactly like device-admin's package logic (done = Closed count; total = Open + Planned
+ Closed; percent = round(100*done/total)). Deterministic: no network at build time except reading
git provenance; stable ordering; no Date.now/random.

tasks.md entry format (one line-1 header, optional trailer lines until the next blank line):

    ### <YYYY-MM-DD> · <domain> · <SEVERITY?> — <title>
    commit: <hash> · note: <free text>      # trailer, optional (mainly for Closed)

SEVERITY (HIGH/MEDIUM/LOW/…) is optional. The trailer's `commit:` and `note:` are optional.

Usage: python3 scripts/dashboard/build.py [--repo-root PATH] [--out docs] [--track NAME]
"""
from __future__ import annotations
import argparse, os, re, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from render import (  # noqa: E402
    render_index, render_blocker, render_history, render_report_viewer,
    render_findings_status, render_completed, render_pending, render_blocked,
)

SECTION_RE = re.compile(r"^##\s+(Open|Planned|Closed)\s*$", re.I)
ENTRY_RE = re.compile(r"^###\s+(.*)$")
# header:  <date> · <domain> · <SEV?> — <title>   (· separators, — before title)
HEADER_RE = re.compile(
    r"^\s*(?P<date>\d{4}-\d{2}-\d{2})?\s*·?\s*"
    r"(?P<domain>[^·—]+?)?\s*·?\s*"
    r"(?P<sev>HIGH|MEDIUM|LOW|INFO|CRITICAL)?\s*—\s*(?P<title>.+?)\s*$",
    re.I,
)
TRAILER_RE = re.compile(r"(?:^|·)\s*(commit|note|domain|id)\s*:\s*([^·]+)", re.I)


def _slug(text, i):
    base = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40]
    return f"{base or 'task'}-{i}"


def parse_tasks(path):
    """Return {'open': [...], 'planned': [...], 'closed': [...]} lists of task dicts."""
    out = {"open": [], "planned": [], "closed": []}
    if not os.path.exists(path):
        return out
    section = None
    cur = None
    idx = 0
    with open(path) as fh:
        for raw in fh:
            line = raw.rstrip("\n")
            m_sec = SECTION_RE.match(line)
            if m_sec:
                section = m_sec.group(1).lower()
                cur = None
                continue
            m_entry = ENTRY_RE.match(line)
            if m_entry and section:
                idx += 1
                head = m_entry.group(1)
                hm = HEADER_RE.match(head)
                if hm:
                    title = hm.group("title").strip()
                    domain = (hm.group("domain") or "").strip()
                    date = hm.group("date") or ""
                    sev = (hm.group("sev") or "").upper()
                else:  # header without the "—": treat whole thing as title
                    title, domain, date, sev = head.strip(), "", "", ""
                cur = {"id": _slug(title, idx), "title": title, "domain": domain,
                       "date": date, "sev": sev, "ref": "", "note": ""}
                out[section].append(cur)
                continue
            # trailer lines (commit:/note:/…) attach to the current entry
            if cur is not None and line.strip():
                for key, val in TRAILER_RE.findall(line):
                    key = key.lower(); val = val.strip()
                    if key == "commit":
                        cur["ref"] = val
                    elif key == "note":
                        cur["note"] = val
                    elif key == "domain" and not cur["domain"]:
                        cur["domain"] = val
                    elif key == "id":
                        cur["id"] = val
            elif not line.strip():
                cur = None
    return out


def build_track(name, tasks):
    done = len(tasks["closed"])
    pending_n = len(tasks["open"]) + len(tasks["planned"])
    total = done + pending_n
    pending = [{"id": t["id"], "domain": t["domain"] or "?", "title": t["title"],
                "note": (f'{t["sev"]} · ' if t["sev"] else "") + (t["note"] or "")}
               for t in tasks["open"] + tasks["planned"]]
    completed = [{"id": t["id"], "title": t["title"], "date": t["date"],
                  "ref": t["ref"], "note": t["note"]} for t in tasks["closed"]]
    return {
        "name": name,
        "done": done, "total": total,
        "percent": round(100 * done / total) if total else 0,
        "counts": {"completed": done, "pending": pending_n,
                   "in_progress": 0, "blocked": 0, "wont_do": 0},
        "blocked": [], "in_progress": [],
        "pending": pending, "completed": completed,
    }


def findings_counts(path):
    if not os.path.exists(path):
        return {"open": 0, "planned": 0, "closed": 0}
    c = {"open": 0, "planned": 0, "closed": 0}
    section = None
    for line in open(path):
        if line.startswith("## Open"):
            section = "open"
        elif line.startswith("## Planned"):
            section = "planned"
        elif line.startswith("## Closed"):
            section = "closed"
        elif line.startswith("### ") and section:
            c[section] += 1
    return c


def git_provenance(root):
    try:
        out = subprocess.check_output(
            ["git", "-C", root, "log", "-1", "--pretty=format:%h|%cI"], text=True)
        h, iso = out.split("|", 1)
        return {"commit": h, "committed_at": iso}
    except Exception:
        return {"commit": "unknown", "committed_at": ""}


def _find_workflow_dir(root):
    for cand in (os.path.join(root, "backend/.workflow"), os.path.join(root, ".workflow")):
        if os.path.exists(os.path.join(cand, "tasks.md")):
            return cand
    return os.path.join(root, ".workflow")


def build_status(root, track_name):
    wf = _find_workflow_dir(root)
    tasks = parse_tasks(os.path.join(wf, "tasks.md"))
    findings_md = os.path.join(wf, "findings.md")
    return {
        "schema_version": 1,
        "repo": os.path.basename(os.path.abspath(root)),
        "provenance": git_provenance(root),
        "tracks": [build_track(track_name, tasks)],
        "findings": findings_counts(findings_md),
        # so the Findings tile can link the committed findings.md via the report viewer
        "has_findings_md": os.path.exists(findings_md),
        "_findings_md_path": findings_md if os.path.exists(findings_md) else None,
        "audit": None,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", default=os.getcwd())
    ap.add_argument("--out", default=None, help="output dir (default: <repo-root>/docs)")
    ap.add_argument("--track", default=None, help="track name (default: repo basename)")
    ap.add_argument("--index-name", default="index.html",
                    help="filename for this repo's main page (default index.html; iot-master uses "
                         "repo.html so the aggregator can own index.html)")
    args = ap.parse_args()
    root = os.path.abspath(args.repo_root)
    out = args.out or os.path.join(root, "docs")
    track = args.track or os.path.basename(root).replace("iot-", "").replace("-snap", "")
    os.makedirs(out, exist_ok=True)
    open(os.path.join(out, ".nojekyll"), "a").close()

    import json
    status = build_status(root, track)
    with open(os.path.join(out, "status.json"), "w") as fh:
        json.dump(status, fh, indent=2)
    with open(os.path.join(out, args.index_name), "w") as fh:
        fh.write(render_index(status))
    with open(os.path.join(out, "reviews.html"), "w") as fh:
        fh.write(render_history(status))
    if any(t.get("completed") for t in status["tracks"]):
        with open(os.path.join(out, "completed.html"), "w") as fh:
            fh.write(render_completed(status))
    if any(t.get("pending") for t in status["tracks"]):
        with open(os.path.join(out, "pending.html"), "w") as fh:
            fh.write(render_pending(status))
    if any(t.get("blocked") for t in status["tracks"]):
        with open(os.path.join(out, "blocked.html"), "w") as fh:
            fh.write(render_blocked(status))
    for t in status["tracks"]:
        for b in t["blocked"]:
            with open(os.path.join(out, f"blocker-{b['id']}.html"), "w") as fh:
                fh.write(render_blocker(status, b))
    # Copy the committed findings.md into docs/reports/ + emit the marked.js viewer so the
    # Findings tile's "Open findings.md →" link (report.html?f=findings.md) renders it
    # in-browser — same mechanism device-admin uses for review reports (viewer fetches
    # reports/<f>). No secondary render — the committed source IS the page.
    if status.get("_findings_md_path"):
        reports_dst = os.path.join(out, "reports")
        os.makedirs(reports_dst, exist_ok=True)
        with open(status["_findings_md_path"]) as r, \
                open(os.path.join(reports_dst, "findings.md"), "w") as w:
            w.write(r.read())
        with open(os.path.join(out, "report.html"), "w") as fh:
            fh.write(render_report_viewer())

    print(f"built {status['repo']} dashboard ({status['tracks'][0]['done']}/"
          f"{status['tracks'][0]['total']} = {status['tracks'][0]['percent']}%) -> {out}")


if __name__ == "__main__":
    main()
