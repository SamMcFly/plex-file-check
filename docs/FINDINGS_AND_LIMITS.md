# What the results mean

The default checker focuses on file errors and useful compatibility clues. It is not a guarantee of playback on every device, and a finding does not by itself explain a particular buffering event.

## The four result groups

| Group | Examples | How to use it |
|---|---|---|
| **FILE ERRORS** | The input cannot be read; video/audio decoding reports a failure | Investigate first. Read the evidence: a decoder limitation or access problem can also prevent a clean test. |
| **REVIEW** | Conflicting or invalid HDR values, unresolved decoder diagnostics, major duration differences, or suspicious full-file timestamps | A meaningful clue, not proof of the playback cause. Compare with the source and the symptom. |
| **DEVICE SUPPORT** | Dolby Vision Profiles 5 and 7; less common AVC/HEVC bit-depth or color-sampling formats; image-based subtitles | Check the actual player and selected tracks. These notes do not mean the file is damaged and do not make the scan fail. |
| **INCOMPLETE** | A requested check timed out or its required tool was unavailable | No conclusion from that check. Optional work you did not request is not an incomplete warning. |

Ordinary 10-bit HEVC, Dolby Vision Profile 8, multiple tracks, missing optional HDR values, and language/default-track preferences are not major findings merely because they are present. Profile 7 appears as a conditional device-support note, not a warning. The checker does not know the capabilities of your particular Plex client.

Plex documents that selected unsupported subtitles can require video burn-in, while unsupported audio can require audio conversion alone; actual handling depends on the client. See [Plex Direct Play and Direct Stream](https://support.plex.tv/articles/200250387-streaming-media-direct-play-and-direct-stream/).

Dolby Vision Profile 5 has no HDR10-compatible base layer. Profile 7 does have one, but full Dolby Vision playback depends on support for its enhancement layer and the player's playback path. See [Dolby's profile table](https://ott.dolby.com/OnDelKits/Dolby_Vision_Online_Delivery_Kit/v1/Documentation/Specs/Visio_Profiles/help_files/topics/c_dovi_profiles_public.html). The checker does **not** distinguish MEL (minimum enhancement layer) from FEL (full enhancement layer), including when `--dovi` is used. A Profile 7 note alone does not mean conversion is needed.

Support for less common AVC/HEVC formats also changes with hardware and software; [NVIDIA's decoder support table](https://docs.nvidia.com/video-technologies/video-codec-sdk/13.1/nvdec-application-note/index.html) is one example, not a compatibility list for every Plex client.

A sampled scan can miss a problem elsewhere. `--deep` expands timeline and decode coverage; a clean software decode still does not certify hardware-decoder support, picture quality, perceived lip-sync, or network performance.

## Two observations that need careful wording

**HDR metadata disagreement:** the checker compares supplied mastering-display records and color signaling. A conflict is worth reviewing, particularly after a conversion, but metadata is not a measurement of the picture or a reason to invent replacement brightness values. Missing optional fields are different from malformed or conflicting values. Stream/frame probe locations alone do not prove where a demuxer obtained a value.

**Thread-dependent PPS diagnostic:** if a short decode sample reports only `PPS changed between slices`, it receives one controlled retry with one decoder thread. A clean retry with the same positive frame count is retained as `DECODE_THREAD_DEPENDENT` in additional diagnostics, not a major focused finding. This is weaker evidence than a reproducible decode failure. Different counts, additional errors, and failed retries remain visible for review. Both attempts are preserved in JSON and detailed output.

## Additional diagnostics are optional

The project began by adapting checks from the author's conversion workflow. Some of those rules are useful when investigating a specific problem, but they are **not official Plex requirements and may not predict playback trouble**. They now belong in `--advanced` or individually requested analysis.

| Observation or rule | Why it is not a default major-issue verdict |
|---|---|
| Video burst thresholds, long keyframe gaps, separated audio/video bytes | Project thresholds have not been established as universal client or network limits. |
| Matroska index location or MP4 faststart layout | Valid files can use different layouts; a possible optimization is not corruption. |
| Interlace/telecine detector scores | Detectors can misclassify content. Watching moving scenes is still necessary. |
| Sparse or zero HDR10+ scene values | Personal scene-quality rules are not HDR10+ conformance validation. |
| Loudness outside the author's preferred target | A listening preference, not a Plex playback requirement. |
| Language, default-track, subtitle-credit, and retained-track choices | Library preferences and conversion context, not universal file defects. |

`--details` shows extra observations from the checks already run. `--advanced` actually runs the broader diagnostic set and exposes its heuristic warnings. Explicit bandwidth, expected-runtime, and reference comparisons are available when you have a relevant value or source. [CHECKS.md](../CHECKS.md) preserves the technical inventory and thresholds.

## The author's HEVC/QSV observation

The author's [published test report](https://forums.plex.tv/t/hevc-hardware-transcodes-ignore-the-bitrate-limit-intel-qsv-measured-and-a-workaround/941228) describes a Plex HEVC transcoding bitrate overshoot on Windows with Intel Arc/QSV. That is an observation in the reported setup, not a claim that every HEVC file or Plex version has the problem. It is not an official Plex diagnosis or a statement about current fix status.

**This checker cannot detect that server behavior by inspecting a movie.** Diagnosis requires evidence from the affected transcode session and its generated output. A high source bitrate or an HEVC source codec does not establish it.

## Original sources and processed files

Checks apply to either an original file or a processed output. A finding already present before MCEBuddy is not evidence that MCEBuddy introduced it. Compare separate reports from the exact source/output pair; another download or a file with the same movie title may contain different tracks, metadata, or video. File size and modification time help identify a copy but do not prove identical content or source history.

## What to do with a finding

1. Start with file errors, then review findings. Keep the original file.
2. Match the finding to the actual symptom and selected tracks. A device-support note needs the player/app details.
3. Use `--deep` if damage outside the samples is suspected; use `--details` if someone needs evidence.
4. Test one change at a time. Do not convert a working movie merely to clear a report.
5. Report false positives too. A file that triggers a finding but plays correctly helps improve the checker.

The checker is read-only. It does not repair files or change Plex settings.
