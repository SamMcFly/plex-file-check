# Usage and advanced options

Start with [Setup](SETUP.md) if the version checks do not work. These examples assume your terminal is in the extracted release folder. On **Windows**, replace `python3` below with `py -3`. Replace quoted media paths with real paths. For a source checkout, replace `plex-file-check.pyz` with `plex_check.py`.

## Everyday commands

```sh
python3 plex-file-check.pyz "Example.mkv" --redact-name --report first-check
python3 plex-file-check.pyz "Example.mkv" --quick --report quick-check
python3 plex-file-check.pyz "Example.mkv" --deep --report deep-check
```

One command checks one local file. It can read a file on mounted storage if your account has access, but slow/network storage can increase scan time. URLs, Plex libraries, folders and wildcard batches are not accepted as single media inputs. The first non-cover video in ffprobe's stream order is selected for content analysis; there is no video-track selector. Other video tracks receive only basic inventory checks, not full HDR, timeline or decode analysis. All audio tracks are examined.

External subtitle discovery is limited to matching SRT names such as `Movie.srt`, `Movie.en.srt` and `Movie.en-US.srt` beside `Movie.mkv`. Extra suffixes such as `Movie.en.forced.srt`, `.sdh.srt` or `.default.srt` are not all supported by this matcher. A report is not a promise that every Plex sidecar naming pattern was inspected. Embedded text subtitles are examined during standard/deep scans; bitmap subtitles are not OCR-checked.

`--report PREFIX` saves `PREFIX.txt` and `PREFIX.json`. Do not add `.txt` to the prefix unless you want a filename ending in `.txt.txt`. A relative prefix saves in your terminal's current folder. You can specify a writable full path. Existing reports are not replaced. Without `--report`, results appear only in the terminal.

## Optional analysis

| Option | Purpose and limitation |
|---|---|
| `--bandwidth-mbps 20` | Compare measured **file video** bitrate with a sustained 20 Mbps budget. This is not a speed test, includes no complete audio/protocol budget, and cannot inspect live Plex output. |
| `--loudness` | Decode every full audio track and measure loudness. Does not change volume; the target comparison is a preference. |
| `--dovi` | Inspect HEVC Dolby Vision metadata using optional `dovi_tool`. Add `--deep` for actual decoded-frame count comparison. Equal counts do not prove correct frame alignment. |
| `--hdr10plus` | Inspect HEVC HDR10+ scene metadata using optional `hdr10plus_tool`. The quality rules are heuristics, not a standard's validation suite. |
| `--reference "Original.mkv"` | Compare stream counts and runtime metadata against an original of the same edition. Does not compare pictures, run VMAF, or prove preservation. |
| `--expected-runtime 5400` | Compare with an independently known length in **seconds** (5400 = 90 minutes). Flags files more than 15% shorter; does not look up movie titles. |
| `--no-visual` | Skip image-pattern, interlace/cadence and signal-level samples. Useful when investigating timing or decode concerns. |

Examples:

```sh
python3 plex-file-check.pyz "Example.mkv" --deep --dovi --report dv-check
python3 plex-file-check.pyz "Example.mkv" --hdr10plus --report hdr-check
python3 plex-file-check.pyz "Example.mkv" --loudness --report audio-check
python3 plex-file-check.pyz "Example.mkv" --bandwidth-mbps 20 --report bandwidth-check
python3 plex-file-check.pyz "Converted.mkv" --reference "Original.mkv" --report comparison
```

Optional tools: download builds for your operating system from [dovi_tool releases](https://github.com/quietvoid/dovi_tool/releases) or [hdr10plus_tool releases](https://github.com/quietvoid/hdr10plus_tool/releases). Extract and place the executable in `tools`, beside the checker, or on PATH. On Windows, names are `dovi_tool.exe` and `hdr10plus_tool.exe`. The checker does not download them. Missing/inapplicable tools produce incomplete results, not successful validations.

These options can make a scan slow. Even with `--quick`, explicitly requested loudness or HDR payload checks still run. `--quick` and `--deep` cannot be combined.

## Tool locations

Automatic discovery checks `tools` beside the checker, then the checker's folder, then PATH. Explicit options override automatic discovery. Windows example:

```powershell
py -3 plex-file-check.pyz "D:\Movies\Example.mkv" --ffmpeg "C:\Tools\ffmpeg.exe" --ffprobe "C:\Tools\ffprobe.exe"
```

macOS/Linux example (adjust actual paths):

```sh
python3 plex-file-check.pyz "Example.mkv" --ffmpeg "/opt/tools/ffmpeg" --ffprobe "/opt/tools/ffprobe"
```

Use `--dovi-tool PATH` and `--hdr10plus-tool PATH` for explicit optional-tool locations. Give the path to the executable, not its containing folder. Keep ffprobe and FFmpeg from the same build when practical.

## Time, resources, and output

| Option | Default and behavior |
|---|---|
| `--timeout SECONDS` | 120 per short tool call, not a limit on the entire scan |
| `--full-timeout SECONDS` | 7200 per whole-file operation; multiple operations can each use this much time |
| `--redact-name` | Replace media and matching subtitle names in results; review before sharing |
| `--quiet` | Hide progress messages, keeping the result |
| `--json` | Print structured JSON instead of the text report; progress normally goes to stderr |
| `--version` | Print checker version and exit |
| `--help` | Show current command syntax and all options |

Full scans use software decoding and may take longer than playback. Packet scans keep compact timestamp arrays in RAM and spool tool output to temporary disk files. Long/high-frame-rate files, detailed HDR extraction, or very noisy decoder logs can require significant resources. A timeout gives an incomplete result. To allow four hours for each full-file operation, use `--full-timeout 14400`. Press Ctrl+C to cancel.

Report JSON includes measurements, findings with codes and evidence, coverage, and detected tool versions. The schema may evolve while the project is at version 0.x; integrations should record `checker_version` and `schema_version` and tolerate new fields/codes. The report does not deliberately include full input paths, raw logs, command lines, or arbitrary title tags.

## Exit codes for scripts

| Code | Meaning |
|---|---|
| 0 | No errors, warnings, or incomplete findings were reported; still not Plex certification |
| 1 | Warnings **or** incomplete checks; normal for many intentionally limited scans |
| 2 | Error findings; also used by Python's argument parser for invalid command syntax |
| 3 | Unable to run or save the report, such as missing ffprobe or an existing report name |
| 130 | Cancelled with Ctrl+C during scanning |

Do not use `exit != 0` as an automatic instruction to delete or re-encode a file. In automation, inspect `findings`, `counts` and `coverage` in valid report JSON. Failure to produce JSON is a separate execution/input problem. Example for advanced users:

```sh
python3 plex-file-check.pyz "Example.mkv" --json --quiet > result.json
```

Shell redirection can overwrite `result.json`; use the safer `--report` option when you want the checker's refusal to overwrite existing files. See [Findings and limitations](FINDINGS_AND_LIMITS.md) before making automated decisions.
