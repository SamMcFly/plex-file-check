# Validation

## Version 0.2.0

Verified on Windows with Python 3.12. The default scan and report now focus on file errors, stronger warning signs, and conditional device support. This changes presentation and scan scope; it does not certify playback on any Plex client.

- 166 automated regression tests passed, including focused/advanced scan boundaries, explicitly requested options, missing tools and timeouts, diagnostic retention, device-note grouping, substantial measured audio/video duration differences, and coverage labeling for controlled decoder retries.
- Native FFmpeg smoke checks passed for the source and packaged `.pyz`: clean focused results, compact and detailed text, advanced packet/subtitle scope, deep H.264/AAC Matroska decoding (72 frames), and MP4 structure.
- Synthetic input hashes stayed unchanged. Unicode filenames, report overwrite refusal, missing tools, and empty input handling passed.
- A focused scan of the current 44.8 GB HEVC/Dolby Vision Profile 8.1 file completed with no major findings and one grouped image-subtitle device note. Its observed HDR records agreed (1,000-nit mastering maximum). Two controlled threading-dependent PPS observations remained in additional diagnostics with both attempts retained. File size and modification time were unchanged during the scan; this was not a whole-file decode or content-hash comparison.
- The release builder checks Python 3.9 syntax. macOS and Linux still require actual-host validation; the inactive CI template is not evidence of a passed platform run.

## Earlier 0.1.x validation

Version 0.1.2 adds controlled decoder retries, mastering-record comparisons, longer whole-track subtitle limits and clearer report identity/summary information. Version 0.1.1 was a documentation-only update.

Verified on Windows with Python 3.12 and the installed FFmpeg build reported in each run.

- 135 automated regression tests passed for 0.1.2, including canonical diagnostic privacy, conditional retries, mixed errors/timeouts, frame-count differences, partial/conflicting HDR records, subtitle timeout propagation, and report identity/summary handling.
- A 0.1.2 standard scan of a 44.8 GB HEVC/Dolby Vision Profile 8.1 Matroska file on network storage completed with zero error findings, two threading-dependent decode warnings, and one mastering-record disagreement. Both one-thread retries returned the same frame counts as their initial samples (47 and 48) without diagnostics. All 1,611 extracted text-subtitle cues passed the implemented checks with a 900-second per-track limit; two bitmap tracks remained uninspected. File size and modification time were unchanged. This sampled run did not establish whole-file health or Plex playback compatibility.
- Real Matroska H.264: full timeline and full software decode returned 288 decoded frames, matching the generated source; whole-track AAC loudness measured.
- Real MP4: faststart box layout recognized; brackets, spaces and Unicode in the filename handled.
- Real HEVC: software full decode passed with reordered frames; unavailable initial DTS was not mislabeled as corruption.
- Embedded text subtitles: temporary extraction and two-cue validation succeeded.
- Truncated Matroska: structure/media concerns detected; exit code 2.
- Empty input: rejected; exit code 2.
- Missing FFprobe: actionable failure; exit code 3.
- Existing report/input paths: overwrite refused; content preserved.
- SHA-256 comparisons confirmed all media inputs were unchanged by the integration runs.
- Real FFmpeg-to-dovi_tool pipe: non-DV HEVC produced a missing/inapplicable RPU result, with no raw HEVC output file.
- Real FFmpeg-to-hdr10plus_tool pipe: non-HDR10+ HEVC produced an unsuccessful/inconclusive extraction result, not a healthy-payload claim.
- Positive Dolby Vision and HDR10+ payload-tool parsing/count/profile cases are covered by synthetic tool-output/unit fixtures. A complete real positive payload-tool validation remains untested. Separate real-file checks of Dolby Vision Profile 8.1 stream/frame metadata and short decoding samples do not establish whole-stream RPU integrity.

The shareable `tests/smoke_ffmpeg.py` also passed locally: synthetic H.264/AAC Matroska full decode (72 frames), MP4 faststart, source and `.pyz` entry points, Unicode paths, redaction, unchanged input hashes, refusal to overwrite reports, empty input and missing ffprobe. Fixtures are created by the script; no real movies are needed or included.

The inactive template `ci/check.yml` describes unit tests and packaging on Windows, macOS and Linux with Python 3.12, plus unit tests/packaging on Linux with Python 3.9. Its Python 3.12 jobs also install FFmpeg and run the synthetic smoke checks. **This template has not run on GitHub**: the initial publishing credential lacked the separate workflow permission. [Contributing](CONTRIBUTING.md#optional-github-automation) explains how a maintainer can enable it. Configuration alone is not evidence of a successful run.

macOS and Linux have not yet been exercised on actual hosts. The source uses Python 3.9-compatible syntax and portable FFmpeg command arguments; OS-specific process/temp-directory branches are included. This release is a shareable initial version, not a certification of every Plex client or file format. The synthetic checks do not test Plex itself, client hardware decoders, playback over a real network, or complete real positive Dolby Vision/HDR10+ payloads.
