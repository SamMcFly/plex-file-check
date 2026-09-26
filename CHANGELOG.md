# 0.2.0 — 2026-09-26

- Make the default scan focus on file errors, strong warning signs, and a few conditional device-support limitations. Standard scans keep short video/audio decode samples and early HDR metadata checks.
- Print a compact report with FILE ERRORS, REVIEW, DEVICE SUPPORT and INCOMPLETE groups. Group repeated observations and keep raw evidence out of the everyday view.
- Move broad packet/bitrate heuristics, visual/cadence analysis, subtitle content checks and indexing preferences into `--advanced`. `--details` shows existing evidence without adding scans.
- Stop treating unrequested optional checks as incomplete. Actual failed or unfinished requested checks still affect the result. Device-support notes alone do not produce a warning exit code.
- Retain lower-confidence observations, including controlled threading-dependent PPS diagnostics, in JSON and detailed output. They are not treated as major file problems in focused mode.
- JSON schema 2 adds the scan profile, finding categories and a separate `diagnostics` list. Focused counts and exit codes use the active `findings` list; metrics retain detailed evidence.

# 0.1.2 — 2026-09-25

- Preserve recognized decoder diagnostics and perform one controlled single-thread retry for isolated PPS-change warnings in short samples. A successful retry retains a warning and both attempts' evidence.
- Use the whole-file timeout for embedded text-subtitle extraction, with clearer progress and timeout advice for large/network files.
- Inspect distinct HDR mastering records with provenance; distinguish omitted optional fields from invalid or conflicting values.
- Show explicit severity counts, scan time, exact file size and modification time in text reports. Informational entries are labeled as context, not failures.

# 0.1.1 — 2026-09-25

Documentation update to keep the guides focused on local media-file analysis. Refreshed downloads and checksums. No diagnostic behavior changes.

# 0.1.0 — 2026-09-25

Initial public release under the MIT license. Read-only Python CLI, text/JSON reports, optional full decode and HDR/loudness inspection, explicit coverage/inconclusive results, and portable tool discovery. Adapted from a personal conversion workflow without changing that converter.

Includes beginner setup for Windows, macOS and Linux, an advanced usage guide, troubleshooting/glossary, a check inventory and explicit distinctions between measured errors, reported observations and unconfirmed heuristics. Source, regression tests and a single-file Python download are provided. See VALIDATION.md for tested scope and remaining gaps.
