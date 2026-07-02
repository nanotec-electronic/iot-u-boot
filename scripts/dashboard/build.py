#!/usr/bin/env python3
"""Canonical repo-status generator — reads .workflow/state.yaml and renders the dashboard.

ONE source of truth, exactly like iot-device-admin (THE template): every repo's dashboard is
derived from `.workflow/state.yaml` (a `packages:` list with per-package `status`:
completed / pending / in_progress / blocked / wont_do). No tasks.md, no progress.yaml.

Single-track by convention: this kit build.py renders ONE track from the repo-root
`.workflow/state.yaml` (+ optional `.workflow/state.archive.yaml`). iot-device-admin is the ONLY
multi-track repo (backend + frontend, split for size) and keeps its OWN build.py — do not
overwrite it with this one. Everything else uses this file verbatim.

Deterministic: stable ordering, no Date.now/random (only git provenance touches the outside).
Usage: python3 scripts/dashboard/build.py [--repo-root PATH] [--out docs] [--track NAME]
"""
from __future__ import annotations
import argparse, json, os, subprocess, sys
from collections import Counter

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from render import (  # noqa: E402
    render_index, render_blocker, render_history, render_report_viewer,
    render_completed, render_pending, render_blocked,
)

SCHEMA_VERSION = 1
COUNTED = ("completed", "pending", "in_progress", "blocked")  # wont_do excluded from denominator


def load_pkgs(path):
    if not os.path.exists(path):
        return []
    doc = yaml.safe_load(open(path)) or {}
    return doc.get("packages", []) or []


def track_stats(name, active_path, archive_path):
    """One track from state.yaml (+ optional archive of completed pkgs)."""
    active = load_pkgs(active_path)
    archived = load_pkgs(archive_path)
    counts = Counter(p.get("status") for p in active)
    done = len(archived) + counts.get("completed", 0)
    remaining = sum(counts.get(s, 0) for s in COUNTED if s != "completed")
    total = done + remaining
    blocked = [
        {"id": p["id"], "reason": p.get("note", "") or p.get("last_error", "") or p.get("wont_do_reason", ""),
         "depends_on": p.get("depends_on", []), "domain": str(p.get("spec_section", "?"))}
        for p in active if p.get("status") == "blocked"
    ]
    in_progress = [p["id"] for p in active if p.get("status") == "in_progress"]
    pending = [
        {"id": p["id"], "domain": str(p.get("spec_section", "?")),
         "title": p.get("title", p["id"]), "note": p.get("note", "")}
        for p in active if p.get("status") == "pending"
    ]
    completed = [
        {"id": p["id"], "title": p.get("title", p["id"]), "date": p.get("date", ""),
         "ref": p.get("commit", ""), "note": p.get("note", "")}
        for p in (list(reversed(archived)) + [q for q in active if q.get("status") == "completed"])
    ]
    return {
        "name": name,
        "done": done, "total": total,
        "percent": round(100 * done / total) if total else 0,
        "counts": {
            "completed": done,
            "pending": counts.get("pending", 0),
            "in_progress": counts.get("in_progress", 0),
            "blocked": counts.get("blocked", 0),
            "wont_do": counts.get("wont_do", 0),
        },
        "blocked": blocked, "in_progress": in_progress,
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


def build_status(root, track_name):
    wf = os.path.join(root, ".workflow")
    findings_md = os.path.join(wf, "findings.md")
    return {
        "schema_version": SCHEMA_VERSION,
        "repo": os.path.basename(os.path.abspath(root)),
        "provenance": git_provenance(root),
        "tracks": [track_stats(track_name,
                               os.path.join(wf, "state.yaml"),
                               os.path.join(wf, "state.archive.yaml"))],
        "findings": findings_counts(findings_md),
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
                    help="main-page filename (iot-master uses repo.html so the aggregator owns index.html)")
    args = ap.parse_args()
    root = os.path.abspath(args.repo_root)
    out = args.out or os.path.join(root, "docs")
    track = args.track or os.path.basename(root).replace("iot-", "").replace("-snap", "")
    os.makedirs(out, exist_ok=True)
    open(os.path.join(out, ".nojekyll"), "a").close()

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
    if status.get("_findings_md_path"):
        reports_dst = os.path.join(out, "reports")
        os.makedirs(reports_dst, exist_ok=True)
        with open(status["_findings_md_path"]) as r, \
                open(os.path.join(reports_dst, "findings.md"), "w") as w:
            w.write(r.read())
        with open(os.path.join(out, "report.html"), "w") as fh:
            fh.write(render_report_viewer())

    t = status["tracks"][0]
    print(f"built {status['repo']} ({t['done']}/{t['total']} = {t['percent']}%) -> {out}")


if __name__ == "__main__":
    main()
