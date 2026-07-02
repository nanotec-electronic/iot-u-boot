# Nanotec Repo-Status Schema (`status.json`)

The **single dock point** for the multi-repo project dashboard. Every platform repo
emits a `status.json` in this shape; the product repo aggregates them (`N × fetch + render`).
It does not matter whether the file was **generated** (Class A) or **hand-curated** (Class B) —
the schema is identical, so the aggregator sees no difference.

## Two repo classes

- **Class A — data-driven** (e.g. `iot-device-admin`): has `.workflow/state.yaml` per track.
  `scripts/dashboard/build.py` generates `status.json` deterministically. Auto, zero upkeep.
- **Class B — curated** (e.g. `iot-gadget-snap`, `iot-rpi-kernel-snap`, `iot-assertions`,
  `iot-master`): no machine-readable progress source. `status.json` is **hand-maintained**
  via the `update-repo-status` skill, which enforces this schema. Honest human-sourced progress,
  not fake-computed.

## Schema (v1)

```jsonc
{
  "schema_version": 1,                    // bump on breaking shape changes
  "repo": "<repo-name>",                  // e.g. "iot-device-admin"
  "provenance": {
    "commit": "<short-sha>",              // Class A: git; Class B: last edit commit or ""
    "committed_at": "<iso-8601>"          // when this status was last true
  },
  "tracks": [                             // one or more; SHOWN SEPARATELY, never a mixed %
    {
      "name": "<track>",                  // "backend", "frontend", "gadget", "kernel", ...
      "status_text": "<short prose>",     // optional one-liner; curated / agent-proposed-user-approved
      "done": 0,
      "total": 0,
      "percent": 0,                       // round(100*done/total)
      "counts": {                         // Class B may use just completed/pending
        "completed": 0, "pending": 0, "in_progress": 0, "blocked": 0, "wont_do": 0
      },
      "blocked": [],                      // list of blocker ids/labels
      "in_progress": [],
      "pending": [ { "id": "...", "domain": "..." } ]   // may be [] for Class B
    }
  ],
  "findings": { "open": 0, "planned": 0, "closed": 0 },  // 0/0/0 if N/A
  "audit": null                           // or { report, date, severity:{...},
                                          //      critical_or_high, security_ampel }
                                          // NUMBERS ONLY — never file:line detail.
}
```

## Hard rules (both classes)

1. **Tracks separate, no mixed overall %.** Overall project progress is the product repo's job.
2. **Security audit → ampel + counts only.** The file:line detail stays an internal document.
3. **`schema_version` is mandatory.** The aggregator branches on it.
4. **Provenance must say when the status was last true** — a stale curated file is worse than none.

## For Class B repos

Copy `scripts/dashboard/render.py` (HTML template) + the `update-repo-status` skill.
Do NOT copy `build.py` (it assumes `state.yaml`). Determine the repo's real progress source
(release tags, milestones, or a hand-kept `progress.yaml`) and document it. If unclear, ask —
never invent a computed progress number where none exists.
