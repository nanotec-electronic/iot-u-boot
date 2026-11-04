# External bug reports

> This document was produced with AI assistance and reviewed carefully. Every claim and every recommendation in it was verified personally, and the
> technical conclusions are the author's own. AI was used as an assistant for drafting,
> cross-checking and structuring — not as a substitute for engineering judgement or for the
> work itself. Minor editorial errors nevertheless cannot be excluded; where a statement is
> decision-relevant, the underlying source is cited so that it can be checked independently.

Bug reports to upstream projects and vendors that concern this repository. Each file carries
front matter (`target`, `channel`, `tracker`, `status`, `reported`, `affected`, `fixed_in`,
`workaround`, `package`, `last_checked`), the report text as sent or drafted, and a dated
`## Status log`. The report texts themselves carry no AI-assistance note (vendor-report
exception). Each report is a tracked package in `.workflow/state.yaml` with
`category: ext-coord`; its note starts with `Upstream: <target> · <tracker> · <status>`.

Status mapping: drafted → `pending`; sent, or fixed upstream but not released → `blocked`;
fixed and released → `completed`; documented, not reported → `wont_do`.

| File | Target | Status | Package |
|---|---|---|---|
| [2026-09-18-rpi-utils-rpi-eeprom-ab-retry.md](2026-09-18-rpi-utils-rpi-eeprom-ab-retry.md) | raspberrypi/utils (rpi-eeprom-ab) | documented, not reported | `report-rpi-eeprom-ab-retry-upstream` (wont_do) |
