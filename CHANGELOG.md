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
