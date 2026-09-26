# Plex File Check

Check a movie or episode for file errors, suspicious HDR signaling, and formats that need particular playback support. The default scan gives a short report with the findings most worth investigating.

The checker reads your file without changing it. It needs no Plex account and does not upload media. It is an unofficial community project, designed for Windows, macOS, and Linux. See [validation status](VALIDATION.md) for what has actually been tested.

## Start here

**New to scripts? Follow the [step-by-step setup guide](docs/SETUP.md).** You need Python 3.9 or newer and FFmpeg with ffprobe. No coding knowledge or Python packages from pip are needed.

Download **Plex-File-Check-v0.2.0.zip** from [Releases](https://github.com/SamMcFly/plex-file-check/releases/latest), extract it, and open a terminal in the extracted `Plex-File-Check` folder.

On Windows:

```powershell
py -3 plex-file-check.pyz "D:\Movies\Example.mkv" --redact-name --report first-check
```

On macOS or Linux:

```sh
python3 plex-file-check.pyz "/path/to/Example.mkv" --redact-name --report first-check
```

Replace the quoted path with your own movie's path. Open **first-check.txt** when the scan finishes. **first-check.json** contains the technical details if someone needs to help you. Use a new report name for each run; existing reports are never overwritten.

If you downloaded **Code → Download ZIP**, use `plex_check.py` instead of `plex-file-check.pyz` and keep the `plexcheck` folder beside it.

## Read the result

The normal report shows only relevant groups:

| Group | What it means |
|---|---|
| **FILE ERRORS** | The file could not be read or decoded cleanly. Investigate these first; tool limitations can also cause failures. |
| **REVIEW** | A strong clue needs checking, such as conflicting HDR metadata or suspicious timing. It is not a confirmed explanation for your playback symptom. |
| **DEVICE SUPPORT** | A format may need a compatible player or Plex conversion. This does not mean the file is broken. |
| **INCOMPLETE** | A requested check could not finish, for example because of a timeout or missing tool. |

Optional checks you did not request do not create an incomplete warning. A clean sampled result means **no major issue was found in the checks performed**; it does not certify every byte or every Plex device.

## Pick a scan

| Command option | What runs |
|---|---|
| No mode option | **Recommended:** file/track metadata, basic container structure, HDR checks, and short video/audio decode samples |
| `--quick` | Metadata and basic container structure; no video/audio decode test |
| `--deep` | Also checks the full audio/video packet timelines and decodes the primary video and all audio; may take hours |

Start with the default. If playback still fails or you suspect damage outside the samples, run again with `--deep` and a new report name.

For help with one finding, add **`--details`** to show its evidence and the coverage information. For the old, broader conversion-style diagnostics, use **`--advanced`**. This adds checks such as bitrate heuristics, image-pattern analysis, and subtitle extraction, with a detailed report that also includes layout/index observations. Those extra rules are not universal Plex requirements. See [Usage](docs/USAGE.md).

## What this can tell you

An unreadable file or repeatable decoder failure is useful evidence. Device-support notes focus on Dolby Vision Profile 5, less common AVC/HEVC bit-depth or color-sampling formats, and image-based subtitles. Compare them with the actual player's support and selected tracks. **No format is declared broken simply because some devices cannot play it directly.**

The author's conversion-workflow observations are documented separately in [findings and limitations](docs/FINDINGS_AND_LIMITS.md), including rules whose connection to playback remains unproven. A file scan cannot establish the author's reported Plex HEVC/QSV transcode bitrate problem or diagnose every cause of buffering.

The checker does not convert, repair, rename, delete, or upload your media. Review reports before sharing them, even with `--redact-name`. No third-party tool binaries or media are bundled.

## Help

- [Setup for Windows, macOS, and Linux](docs/SETUP.md)
- [Usage and optional diagnostics](docs/USAGE.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md)
- [What the findings do and do not prove](docs/FINDINGS_AND_LIMITS.md)
- [Technical inventory and advanced thresholds](CHECKS.md)
- [Tests and validation gaps](VALIDATION.md)
- [Contributing](CONTRIBUTING.md)

Licensed under [MIT](LICENSE). Report suspected checker bugs or false positives in [GitHub Issues](https://github.com/SamMcFly/plex-file-check/issues) with a reviewed, redacted report.
