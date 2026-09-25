# Plex File Check

Check a local movie or episode for file errors and possible playback concerns, then save a report you can share. The checker reads your file without changing it. It works independently of Plex and needs no Plex account.

**A warning is a clue to investigate, not proof that your file is broken or that it causes Plex buffering.** Some checks measure errors; others use thresholds from the author's own conversion workflow. Those thresholds have not been established as universal Plex requirements. Read [what the findings actually mean](docs/FINDINGS_AND_LIMITS.md) before deciding to change a file.

An unofficial community project; not affiliated with or endorsed by Plex. Designed for Windows, macOS, and Linux. See [validation and platform status](VALIDATION.md) for what has actually been tested.

## Get started

**New to scripts? Follow the [step-by-step setup guide](docs/SETUP.md).** It explains what to download, how to open a terminal, and exactly what to type on each operating system. No coding knowledge, Git, or Python packages from pip are needed.

You need Python 3.9 or newer (a currently supported version is recommended), plus a recent FFmpeg installation that includes ffprobe. Download **Plex-File-Check-v0.1.2.zip** from [Releases](https://github.com/SamMcFly/plex-file-check/releases/latest), extract it, and open a terminal in the extracted `Plex-File-Check` folder.

Windows:

```powershell
py -3 plex-file-check.pyz "D:\Movies\Example.mkv" --redact-name --report first-check
```

macOS:

```sh
python3 plex-file-check.pyz "/Users/yourname/Movies/Example.mkv" --redact-name --report first-check
```

Linux:

```sh
python3 plex-file-check.pyz "/home/yourname/Videos/Example.mkv" --redact-name --report first-check
```

Replace the quoted example path with your own file's path. The command saves **first-check.txt** (open this to read the results) and **first-check.json** (more detail for troubleshooting) in the current folder. Use a different report name for your next scan; existing reports are never overwritten. The `.pyz` is the complete checker in one file, but still needs Python and FFmpeg installed.

If you downloaded GitHub's **Code → Download ZIP** instead of the release, use `plex_check.py` in place of `plex-file-check.pyz` and keep the `plexcheck` folder beside it.

## Choose a scan

| Choice | What it does | When to use it |
|---|---|---|
| `--quick` | Reads track information, container layout, and matching external SRT subtitles | First look; often seconds |
| No mode option | Adds short packet, decode, image-pattern and embedded text-subtitle checks | Recommended starting scan; seconds to minutes |
| `--deep` | Also reads the full packet timeline and decodes the primary video and all audio | Investigate damage missed by samples; may take hours |

Deep mode does not automatically enable the separate `--loudness`, `--dovi`, or `--hdr10plus` checks. Short scans can miss problems elsewhere in a file. A completed software decode does not guarantee playback on every device.

## Understand the report

| Label | Meaning |
|---|---|
| **ERROR** | A concrete read, structure, or decode problem was reported. Investigate the evidence; it does not automatically prove a Plex defect. |
| **WARNING** | A possible concern, inconsistent measurement, or heuristic threshold needs attention. It may be harmless for your setup. |
| **INFO** | Context, compatibility information, or a preference; usually no action is required. |
| **NOT CHECKED / INCOMPLETE** | No usable conclusion. The check may be optional, unavailable, timed out, or inconclusive. This is not a pass. |

Start with the summary counts, then the finding's explanation and suggested next step, and the **COVERAGE** section. Informational entries are not failures. The report shows its scan time, exact file size and file modification time to help distinguish copies; these are not a content hash or proof of the file's origin. A normal scan can exit with code 1 simply because it leaves some checks incomplete; that does not mean the program crashed. [The usage guide](docs/USAGE.md) explains every option and exit code.

## What this can and cannot tell you

It can examine timestamps, bitrate bursts, stream metadata, subtitle structure, decode errors, HDR signaling, and other properties of a local file. It cannot establish the cause of buffering from a file alone. Device capabilities, selected tracks, network conditions, and the server's actual output also matter.

The author's reported **HEVC/QSV transcoding bitrate overshoot** is documented separately in [findings and limitations](docs/FINDINGS_AND_LIMITS.md#the-authors-hevcqsv-observation). Scanning an HEVC source does not establish that it has that server-side issue.

The checker does not convert, repair, remux, rename, delete, or upload media. Temporary subtitle/HDR data is cleaned up. Reports hide the media name when `--redact-name` is used; still review a report before posting it. No third-party tool binaries or media are bundled.

## More help

- [Setup for Windows, macOS, and Linux](docs/SETUP.md)
- [Usage, optional checks, and advanced options](docs/USAGE.md)
- [Measured findings, author observations, and unconfirmed assumptions](docs/FINDINGS_AND_LIMITS.md)
- [Troubleshooting and glossary](docs/TROUBLESHOOTING.md)
- [Technical check inventory and thresholds](CHECKS.md)
- [Test coverage and known validation gaps](VALIDATION.md)
- [Contributing and building a release](CONTRIBUTING.md)

Licensed under the [MIT license](LICENSE). To report a suspected checker problem or false positive, use [GitHub Issues](https://github.com/SamMcFly/plex-file-check/issues) with a reviewed, redacted report and the steps to reproduce it.
