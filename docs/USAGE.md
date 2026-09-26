# Usage

Start with [Setup](SETUP.md) if Python or FFmpeg is not ready. These commands run in the extracted checker folder. On **Windows**, replace `python3` with `py -3`. In a source checkout, replace `plex-file-check.pyz` with `plex_check.py`.

## The normal scan

```sh
python3 plex-file-check.pyz "Example.mkv" --redact-name --report first-check
```

This checks track/HDR metadata, basic container structure, and short video/audio decode samples. Open **first-check.txt** for the result. The compact report groups relevant findings as **FILE ERRORS**, **REVIEW**, **DEVICE SUPPORT**, and **INCOMPLETE**. Unrequested optional work does not create incomplete warnings.

One command checks one local file, including files on mounted storage that your account can read. It does not scan a folder or Plex library. The first video track that is not a cover image is selected for content analysis; all audio tracks are examined. Other video tracks receive basic inventory checks only.

`--report PREFIX` saves `PREFIX.txt` and `PREFIX.json`. Use a new prefix for each scan. Relative names save in the terminal's current folder; a writable full path also works. Do not add `.txt` to the prefix. Without `--report`, results appear only in the terminal.

## When to do more

| Option | Use it when… |
|---|---|
| `--quick` | You only want metadata, basic container structure, and compatibility notes. It does not test decoding. |
| `--deep` | You suspect damage outside the short samples. It adds full audio/video timelines and whole-file decoding of the primary video and all audio. It can take hours. |
| `--details` | You need the evidence, finding codes, extra observations, and coverage behind the result. This changes the text report, not which checks run. |
| `--advanced` | You want the broader diagnostic set: sampled packet/bitrate rules, image-pattern analysis, and subtitle checks, with layout/index observations also included in the detailed result. |

```sh
python3 plex-file-check.pyz "Example.mkv" --deep --report deep-check
python3 plex-file-check.pyz "Example.mkv" --details --report detailed-check
python3 plex-file-check.pyz "Example.mkv" --advanced --report advanced-check
```

`--quick` and `--deep` cannot be combined. `--advanced` can be combined with either mode. Advanced quick mode still omits the decode/image/embedded-subtitle work that requires a standard or deep scan. `--no-visual` skips image-pattern, interlace/cadence, and signal-level samples during advanced scans.

Advanced rules include personal thresholds inherited from a conversion workflow. A threshold crossing does not establish a Plex defect. They are described in [CHECKS.md](../CHECKS.md), not required steps for a first scan.

## Specific optional checks

These run only when requested; neither `--deep` nor `--advanced` enables them automatically.

| Option | Purpose and limitation |
|---|---|
| `--bandwidth-mbps 20` | Compare measured file-video bitrate with a budget you supply. It is not a speed test and does not include the complete audio/protocol budget or live Plex output. |
| `--dovi` | Inspect HEVC Dolby Vision payloads using `dovi_tool`. Add `--deep` for a decoded-frame count comparison. Matching counts do not prove alignment or player support. |
| `--hdr10plus` | Inspect HEVC HDR10+ payloads using `hdr10plus_tool`. Scene-quality rules are heuristics, not a conformance test. |
| `--loudness` | Measure every full audio track. Its target comparison is a listening preference, not a Plex requirement. |
| `--reference "Original.mkv"` | Compare track counts and runtime metadata against the same edition. It does not compare picture quality or prove preservation. |
| `--expected-runtime 5400` | Compare with a known length in seconds (5400 = 90 minutes). It does not look up the title or determine the correct edition. |

For example:

```sh
python3 plex-file-check.pyz "Example.mkv" --deep --dovi --report dv-check
```

Download optional executables from [dovi_tool releases](https://github.com/quietvoid/dovi_tool/releases) or [hdr10plus_tool releases](https://github.com/quietvoid/hdr10plus_tool/releases) only if you need these checks. Put them in `tools` beside the checker, beside the checker itself, or on PATH. The Windows names end in `.exe`. The checker does not download them. Missing tools or failed extraction leave a requested check incomplete. These options can read the whole file even with `--quick`.

## Tool locations

The checker searches `tools` beside the checker, the checker's folder, then PATH. Explicit paths override discovery:

```powershell
py -3 plex-file-check.pyz "D:\Movies\Example.mkv" --ffmpeg "C:\Tools\ffmpeg.exe" --ffprobe "C:\Tools\ffprobe.exe"
```

Use `--dovi-tool PATH` and `--hdr10plus-tool PATH` for optional tools. Supply the executable's full path, not its folder. Keep FFmpeg and ffprobe from the same build when practical.

## Time and output options

| Option | Behavior |
|---|---|
| `--timeout SECONDS` | Short tool-call limit; default 120 seconds per call, not per scan |
| `--full-timeout SECONDS` | Whole-file operation limit; default 7200 seconds per operation |
| `--redact-name` | Replace media and matching subtitle names; still review reports before posting |
| `--quiet` | Hide progress messages |
| `--json` | Print structured JSON instead of text |
| `--version` | Print checker version and exit |
| `--help` | Show all command options |

Press **Ctrl+C** to cancel. No media is changed. Deep decoding can take longer than playback; advanced subtitle extraction can read the whole movie. Slow storage, long files, detailed HDR extraction, and noisy decoder logs can require substantial time, RAM, and temporary disk space. A timeout is incomplete coverage, not a bad-file verdict. For four hours per whole-file operation, use `--full-timeout 14400`.

JSON keeps finding codes, measurements, evidence, coverage, and tool versions even when the text is compact. With schema version 2, `findings` contains the active results, and `diagnostics` preserves observations left out of the focused result. `--details` displays these observations without promoting them into major findings. Advanced mode includes the broader heuristic findings in its results.

Record `checker_version` and `schema_version` in integrations and tolerate new fields while the project is at version 0.x. File size, modification time, and scan time help distinguish copies but are not a content fingerprint or source history. Raw logs, full input paths, command lines, and arbitrary title tags are not deliberately included.

## Exit codes for automation

| Code | Meaning |
|---|---|
| 0 | No error, review finding, or incomplete requested check; still not playback certification |
| 1 | Review needed or a requested check is incomplete; advanced mode also includes its broader warnings |
| 2 | Error findings; also used for invalid command syntax |
| 3 | Unable to run or save, such as missing ffprobe or an existing report name |
| 130 | Cancelled with Ctrl+C during scanning |

Device-support notes alone return 0. Do not automatically delete or convert a file because the exit code is nonzero. Inspect valid JSON findings and coverage. Failure to produce JSON is a separate execution/input problem.

## Advanced subtitle scope

With `--advanced`, sidecar discovery covers matching SRT names such as `Movie.srt`, `Movie.en.srt`, and `Movie.en-US.srt` beside `Movie.mkv`. Other suffix combinations are not all supported. Standard/deep advanced scans also temporarily extract embedded text subtitles; bitmap subtitles are not OCR-checked. Extraction can lose styling or hide issues in the original representation, so it is not a complete subtitle-format validation.
