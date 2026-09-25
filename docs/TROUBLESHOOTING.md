# Troubleshooting

Start with the exact message you see. Re-running a scan does not alter the media. Use a new `--report` name when a previous report already exists.

| Symptom | What to do |
|---|---|
| `py` or `python3` is not recognized / command not found | Install Python, open a new terminal, and run its version command. On Windows, try `python --version`; if it works, use `python` instead of `py -3`. See [Setup](SETUP.md). |
| Python opens a `>>>` prompt | You opened Python interactively. Type `exit()` and press Enter, then run the complete checker command at the terminal prompt. |
| `can't open file` mentioning the checker | Your terminal is in the wrong folder, the ZIP is not extracted, or you downloaded source instead of the release. Use `dir` on Windows or `ls` on macOS/Linux to inspect the current folder. Source downloads use `plex_check.py`; releases include `plex-file-check.pyz`. |
| The input is missing or unreadable | Check the movie's full path, quotes, drive/mount availability and read permissions. A folder or streaming URL is not a media-file input. |
| `FFprobe was not found` | Install a build containing both FFmpeg and ffprobe. Put executables in `tools` beside the checker, on PATH, or supply `--ffprobe` and `--ffmpeg` with their full paths. |
| Quick scan works but decode checks are incomplete | ffprobe may be available while FFmpeg is missing or lacks a decoder. Verify both tools and use a recent complete build. |
| Permission denied / executable format error | Check executable permissions and download a build for your OS and CPU. Do not run the scan as administrator just to bypass a setup problem. |
| Unknown option/filter/decoder from FFmpeg | Tool versions/build features differ. Update a matched FFmpeg/ffprobe pair and retry. An unsupported operation is not evidence of media damage. |
| A report with that name already exists | Change `--report first-check` to `--report second-check`. The checker intentionally refuses to replace reports. |
| Unable to run/save, exit code 3 | Check tool paths, input/report permissions and free temporary/report disk space. Try a writable report folder. |
| Scan is slow or appears idle | Deep decode, loudness and HDR extraction may take a long time, particularly on network storage. Per-operation timeouts are not a total scan timer. Ctrl+C cancels without editing media. |
| Warnings on a file that plays correctly | That can be a false positive, a harmless property, or a client-dependent concern. Read [findings and limitations](FINDINGS_AND_LIMITS.md); do not convert solely to remove warnings. |
| Exit code 1, but no error | It also means warnings or incomplete checks. Look at coverage; deliberately omitted checks can explain it. |
| No warnings, but Plex still buffers | Check the actual client's selected tracks, delivery path, network and server output. A file scan cannot diagnose all playback causes. |
| Results differ between machines | Compare tool versions, modes, selected file bytes and optional tools. Decoder/metadata support differs by build. |

## Getting help or reporting a false positive

Open an [issue](https://github.com/SamMcFly/plex-file-check/issues) and include:

- Operating system, Python/checker versions, and FFmpeg/ffprobe versions (the report lists detected tools).
- The command and scan mode, with private paths replaced by placeholders.
- The finding code, a reviewed report made with `--redact-name`, and whether the result repeats.
- The actual symptom, or explicitly that playback is fine. If relevant, include Plex server/client versions, Direct Play/Direct Stream/transcode status, selected tracks and local/remote playback.

Share the smallest useful evidence. Do not post access tokens, private server URLs, full unreviewed logs, or copyrighted movie files. Synthetic/reproducible examples are preferred. Please separate what you measured from what you think caused it.

## Plain-language glossary

| Term | Meaning here |
|---|---|
| Container | The file wrapper, such as MKV or MP4, holding video, audio, subtitles and timing. |
| Codec | How a track is encoded, such as H.264/AVC, H.265/HEVC, AV1 or AAC. |
| Track / stream | One video, audio or subtitle component inside the file. |
| Metadata | Information describing tracks, timing, color and other properties; it can be missing or inaccurate. |
| Decode | Turn compressed media into pictures/sound for analysis. The checker discards that output instead of writing a converted movie. |
| Direct Play / Direct Stream / transcode | Plex may send the original media, change its packaging, or convert tracks for a client. These paths can behave differently. |
| Bitrate / Mbps | Data per second; Mbps means millions of bits per second, not megabytes. Brief peaks differ from a whole-file average. |
| Keyframe / GOP | A keyframe helps decoding restart; a GOP is a group of pictures between such reference points. Long gaps can affect seeking. |
| PTS / DTS | Timestamps for showing and decoding media. Different ordering can be normal for compressed video. |
| Interleaving / index | How track data is arranged in the file, and information used to locate it. |
| HDR / Dolby Vision / HDR10+ | Color/brightness formats and related metadata; device and playback support vary. |
| QSV | Intel's hardware media-processing path. Seeing HEVC as a source codec does not tell you which encoder Plex used. |
| Heuristic / false positive | A practical rule of thumb / a warning that does not indicate the suspected problem in that case. |
| PATH | Folders your system searches when you type a program name. |
| Exit code | A number returned when a command finishes; useful for scripts, but not a playback verdict. |

Return to [Setup](SETUP.md), [Usage](USAGE.md), or the [project overview](../README.md).
