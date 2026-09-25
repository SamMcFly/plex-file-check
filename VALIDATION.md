# Validation of 0.1.0

Verified on Windows with Python 3.12 and the installed FFmpeg build reported in each run.

- 94 automated regression tests passed before packaging.
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
- Positive Dolby Vision and HDR10+ parsing/count/profile cases are covered by synthetic tool-output/unit fixtures. A real positive Dolby Vision/HDR10+ movie was not used in these validation runs.

The shareable `tests/smoke_ffmpeg.py` also passed locally: synthetic H.264/AAC Matroska full decode (72 frames), MP4 faststart, source and `.pyz` entry points, Unicode paths, redaction, unchanged input hashes, refusal to overwrite reports, empty input and missing ffprobe. Fixtures are created by the script; no real movies are needed or included.

The inactive template `ci/check.yml` describes 94 unit tests and packaging on Windows, macOS and Linux with Python 3.12, plus unit tests/packaging on Linux with Python 3.9. Its Python 3.12 jobs also install FFmpeg and run the synthetic smoke checks. **This template has not run on GitHub**: the initial publishing credential lacked the separate workflow permission. [Contributing](CONTRIBUTING.md#optional-github-automation) explains how a maintainer can enable it. Configuration alone is not evidence of a successful run.

macOS and Linux have not yet been exercised on actual hosts. The source uses Python 3.9-compatible syntax and portable FFmpeg command arguments; OS-specific process/temp-directory branches are included. This release is a shareable initial version, not a certification of every Plex client or file format. The synthetic checks do not test Plex itself, client hardware decoders, playback over a real network, or positive Dolby Vision/HDR10+ movies.
