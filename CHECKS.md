# Check inventory and evidence

The default scan focuses on file errors, stronger clues, and a few important device-support conditions. Use [Setup](docs/SETUP.md) and [Usage](docs/USAGE.md) for the normal workflow; the long inventory below is for troubleshooting and contributors.

## What appears in the focused report

| Result group | Included evidence |
|---|---|
| **FILE ERRORS** | Errors reported while reading or decoding the file. Tool limitations and access failures still need to be distinguished from media damage. |
| **REVIEW** | Invalid/conflicting HDR signaling, unresolved decode diagnostics, unidentified codecs or invalid dimensions, substantial duration discrepancies, suspicious deep timeline findings, and explicitly requested comparisons. |
| **DEVICE SUPPORT** | Dolby Vision Profile 5, less common AVC/HEVC bit-depth or chroma formats, and image-based subtitles if selected. These are informational and do not change the exit code. |
| **INCOMPLETE** | A check that was requested but could not produce a complete result. |

The default mode reads metadata and basic container structure, probes early video-frame HDR metadata, and performs short video/audio decode samples. `--quick` reads metadata and basic container structure without decoding content. `--deep` adds full audio/video packet timelines and full decoding of the primary video and all audio.

Ordinary HEVC 10-bit, Dolby Vision Profile 7/8, audio-format inventory, missing optional HDR metadata, and track/language preferences are not major findings by themselves. A clean, matching single-thread retry of an isolated PPS-change diagnostic is additional context rather than a focused warning. All retained observations remain available through JSON or `--details`.

## Additional diagnostics

`--advanced` enables sampled packet/bitrate heuristics, image-pattern analysis, and subtitle validation, and displays a detailed report. Basic container structure is inspected in all modes; advanced mode also includes its layout/index heuristic findings in the assessment. This is the broader set inherited from the author's conversion workflow. An advanced quick scan only runs the parts applicable to quick mode.

Loudness, Dolby Vision payload extraction, HDR10+ payload extraction, a supplied bandwidth budget, and reference/expected-runtime comparisons remain separate opt-ins. `--details` changes reporting only; it does not enable extra analysis.

Many advanced thresholds are personal screening choices. They are **not Plex requirements, universal compatibility limits, or independently established explanations for buffering**. A result can accurately report a measured property while its playback impact remains uncertain. The checker never imports or runs the conversion pipeline's repair/encoding functions.

## Technical inventory: focused and advanced checks

**This is a technical appendix, not a first-scan checklist.** The function names below document the origin of the idea. They are provided for advanced readers; they are not commands beginners need to run. “Primary video” means the first video stream after excluding attached cover images. Declared metadata and detector outputs remain evidence with the limits described above.

| Original responsibility / functions | Standalone equivalent | Scope and limits |
|---|---|---|
| `Get-VideoMetadata`, `Show-StreamDiagnostics`, `Get-AudioStreamInfo`, `Get-SubtitleStreamInfo`, `Test-SubtitleStreams` | Stream inventory, codec/pixel format, dimensions, frame rates, duration/bitrate provenance, channels, sample rate, language/default/forced dispositions | All modes; every track's metadata, primary non-cover video for content analysis |
| `Get-HDRMasteringMetadata`, `Get-DoViMetadataFromFFProbe`, `Test-HDRContent`, `Test-HDRMetadataValidity`, color module | PQ/HLG/DV/HDR10+ signaling, ambiguous/missing color fields, chromaticity and luminance bounds, light-level consistency, DV profile/base-layer compatibility | Stream metadata + early decoded-frame metadata from the first 48 input packets in standard/deep. Absence from this sample does not prove absence throughout the bitstream. |
| `Invoke-FinalDecodeSpotCheck`, `Confirm-AudioStreamUsability`, `Test-AudioStreamIntegrity` | Three short software decode samples; recognized media diagnostics are retained even on successful process exit; missing decoders/timeouts are inconclusive | Primary video + every audio track, 2 s at start/middle/end. Decoder limitations can also produce errors. |
| `Invoke-SourceDecodePass`, `Test-SourceDecodeIntegrity`, `Test-4KSourceIntegrity` | Whole-file software decode, packet/frame-count comparison, measured average rate vs declared rate | `--deep` for whole-file decoding; packet/frame-count and measured-rate heuristics require `--advanced --deep`. Count difference > 2 frames is not proof of corruption. Packet-to-frame mapping depends on the codec. |
| `Test-VideoPacketTimeline`, final-media packet gates | Sorted PTS gap analysis; DTS regression; missing timestamp coverage; packet-duration-aware gaps | Full A/V timelines with `--deep`; sampled timelines with `--advanced` or an explicit bandwidth comparison |
| `Test-AudioStreamDuration`, final-media runtime/offset gates | Audio/video metadata durations, earliest timestamps in packet samples, whole-file packet spans, optional reference runtime | Focused metadata-duration review requires a substantial mismatch (>20%); the >2% / >2 s threshold is an additional diagnostic. Advanced sampled A/V timestamp separation >5 s. These do not measure audible lip-sync. |
| `Test-PassthroughStreamSafety`, `Test-FinalMediaHealth` | Five packet windows for byte interleaving, timeline holes, keyframes and bitrate | `--advanced` or explicit bandwidth comparison; up to five 20 s windows at 0%, 25%, 50%, 75%, and near the end; duplicate starts are removed. Seeking can land earlier. |
| `Test-BitrateVariation`, `Get-AudioBitrateFromPackets` | Measured video bytes/media-second, peaks, ratios, non-key packet size variation; audio packet bitrate using actual sampled span | `--advanced` or explicit bandwidth comparison for sampled analysis; deep collects whole primary-video peaks as extra context; no inferred P/B type from packet size |
| `Test-MatroskaFrontCues` | Actual top-level EBML Cues/Cluster inspection; SeekHead pointers cannot masquerade as Cues | All modes; read/seek-only parsing with bounded element count. Layout/index preferences are additional diagnostics in focused mode. Missing Cues may affect seeking; Cues after media are a valid layout. Unknown traversal is reported. |
| Container streaming layout | MP4 `moov`/`mdat` order, fragmentation notes, malformed box lengths | All modes; layout preferences are additional diagnostics in focused mode. Metadata after media is valid and may delay network startup; that layout alone is not corruption. |
| `Test-InterlacedContent`, `Test-CombingArtifacts` | `idet` classifications with unknown/skipped states and progressive-flag mismatch | `--advanced`; up to five 10 s samples; one sample for clips shorter than 60 s. Percentages are detector observations, not visual confirmation. |
| `Test-TelecinePattern` | Paired raw and `fieldmatch,decimate,idet` samples, emitted-frame ratios | `--advanced`; triggered by combing evidence, restricted to reported rates within 0.1 fps of 30 fps. A matching classic 2:3 cadence is a candidate requiring visual confirmation. |
| `Get-HDR10LightLevels` | `signalstats` code-level measurements | `--advanced`; same visual samples; does not misrepresent luma proxy as physical mastering luminance |
| `Measure-AudioLoudness`, `Test-LoudnormSkipEligibility` | Integrated LUFS, true peak and LRA, silence/incomplete states, optional reference-target comparison | `--loudness`; every whole audio track. −23 LUFS / −2 dBTP / 7 LU is an optional consistency preference, not a Plex requirement. Volume is unchanged. |
| `Test-SrtFileValid`, subtitle extraction validation, `Test-IsSubtitleCredit` | Structural/timestamp/text validation of matching external SRT and temporarily extracted embedded text tracks; credit candidates advisory | `--advanced`; no OCR or deletion; runtime + 5 s bound for extracted subtitles. Conversion may lose styling or mask original representation problems. Bitmap support remains client-specific. |
| `Test-DVHEVCRPUIntegrity`, `ConvertFrom-DoviToolSummary`, `Get-DVRPUStaticMetadata` | Stream copy piped to dovi_tool, RPU summary/profile and decoded-frame count comparison | `--dovi`; use `--deep` for actual video-frame count. Successful parsing or equal counts do not establish alignment, source preservation, or player support. |
| `Test-HDR10MetadataQuality`, `Test-HDR10ProcessingViability` | Whole-stream HDR10+ extraction and scene heuristics | `--hdr10plus`; advisory scoring inherited from a personal pipeline, not an HDR10+ conformance test or proof of a Plex defect. No payload repairs. |
| `Test-SourceRuntime` | User-provided expected runtime comparison | `--expected-runtime`; >15% short warning; no external movie-database account or guessed title matching |
| Reference count/duration gates | Compare optional original-file metadata and retained audio/subtitle track counts | `--reference`; alternate editions/trims may explain differences |

## Thresholds and deliberate adaptations

These are **implementation defaults and screening choices**, mostly exposed through `--advanced` or explicitly requested analysis. Crossing a threshold usually means “inspect this,” not “this file is invalid.” The project has not established these numbers as universal limits for Plex clients, networks, or HDR authoring.

- **Packet timing:** gaps greater than **5 s beyond the preceding packet's declared duration** are flagged. PTS is sorted so normal B-frame reordering does not trigger false corruption. DTS regression threshold is **1 ms**. Missing/nonfinite timestamps are unknown; intentional holds or edits remain possible explanations for gaps.
- **Audio/video storage layout:** more than **75 MiB** between disjoint sampled byte ranges triggers a warning. This is a personal streaming heuristic, not a measurement of lip-sync, disk latency, or the client's required buffer.
- **Keyframes:** gaps greater than **12 s** are flagged; greater than **30 s** are also noted. Short samples may miss long GOPs; deep mode examines the whole primary timeline. A long GOP is not automatically damaged.
- **Video bursts:** greater than **50 Mbps** for non-UHD or **160 Mbps** for UHD triggers an advisory. UHD here means width **≥ 3840** or height **≥ 2160**. A ratio greater than **12×** the sampled average is recorded with a burst finding; the ratio alone does not create a warning. These are personal thresholds, not measured client limits. Video packet bytes omit audio/protocol overhead. Complete-second sample boundaries are used and overlapping packets are deduplicated.
- **Supplied bandwidth:** a video peak above `--bandwidth-mbps` is a comparison against a value supplied by the user, not a network-speed measurement. Buffering can absorb brief peaks; audio and overhead consume additional capacity.
- **Audio/video durations and timestamps:** metadata duration differences require **> 2% and > 2 s** for the broader diagnostic. Focused mode only promotes substantial metadata mismatches **>20%** to review. Earliest timestamps within a packet window require separation **> 5 s**. Short alternate tracks, inaccurate tags, seeking, and long GOPs can affect these findings. They do not establish perceived lip-sync.
- **Reference and expected runtime:** `--reference` flags runtime differences **> 2% and > 5 s**; `--expected-runtime` flags a file **more than 15% short**. Meaningful interpretation requires the same edition/cut and a reliable expected value. The checker does not identify the title or determine the correct runtime itself.
- **Packet/frame counts and frame rates:** an absolute count difference **> 2 frames** warns because the pipeline expects close agreement; codec-dependent mapping can explain it. Measured average frame rate differences **> 2%** warn; variable frame rate or timeline edits can explain those too.
- **Interlace classification:** **> 2%** suggests combing; **> 20%** is a stronger interlace signal. These are personal detector thresholds, not a visual diagnosis. Static scenes, telecine, and detector mistakes can influence results; undetermined frames are not counted as progressive. A telecine candidate requires a paired frame-count ratio strictly between **0.75 and 0.85** and post-filter classified interlace **≤ 5%**. Dropping one frame in five alone proves nothing about cadence.
- **Loudness:** the optional reference is **−23 LUFS / −2 dBTP / 7 LU**. Its comparison accepts integrated loudness within **1.5 LU**, true peak at or below **−2 dBTP**, and loudness range at or below **8 LU**. A different soundtrack level, wide dynamics, or intentional silence is not thereby defective.
- **HDR10+ entry scoring:** the inherited heuristic expects **at least 10 `SceneInfo` entries**, with **at least 10%** meeting its rules. The report's “scene” count is a count of extracted entries, not visually distinct shots. Absent/all-zero MaxScl, zero AverageRGB, or **2 or fewer** positive luminance-distribution values prevent an entry meeting the heuristic. Additional advisories appear when zero/absent MaxScl or zero AverageRGB occurs in **more than 80%** of entries, or sparse distributions in **more than 50%**. Unknown fields remain unknown. Legitimate black content or authoring choices can trigger these rules; zeros and absent tone curves do not automatically invalidate HDR10+.

The standalone code deliberately avoids several overstrict assumptions in the original pipeline:

- Zero minimum mastering luminance is accepted. Zero content-light values can mean unspecified. HDR rationals are evaluated as complete fractions, including denominators. Explicit SDR transfer signaling is not overridden merely because BT.2020 primaries and 10-bit video are present.
- All distinct supplied mastering-display records are inspected, with stream/frame probe locations retained. Optional omissions are informational when only partial records exist; a complete record elsewhere prevents the partial-record notice. Unreadable or out-of-range values in any record remain warnings. Conflicting numeric fields also remain visible; preferring a complete record for summary metrics never erases another record's problem. Frame provenance does not itself prove that data originated in the bitstream rather than being propagated by the demuxer.
- Mastering-record comparisons allow an absolute difference of **0.00002** in chromaticity or **0.0001 nit** in luminance, or a relative difference of **0.000001**. These are implementation tolerances for quantization/representation differences, not a certification standard. Individual invalid values are checked before applying comparison tolerances. Summary metrics select a complete record with consistent numbers, preferring a frame record on ties; fallback selection and the chosen record ID are included in JSON.
- Recognized decode diagnostics are stored as fixed labels, including **PPS changed between slices**, even on exit code 0. A short sample with that diagnostic can be retried once with one decoder thread. Only isolated PPS diagnostics, a clean retry and identical positive frame counts yield `DECODE_THREAD_DEPENDENT`; this stays in additional diagnostics in focused mode, with both attempts retained. Advanced mode exposes the original warning. Other problems are not dismissed, and full decode is not automatically repeated.
- Embedded text-subtitle extraction uses `--full-timeout` per track because it can require reading the whole file. A timeout is incomplete coverage, never a malformed-subtitle finding. File size, modification time and scan time help distinguish reports but do not establish file origin or content equality.
- Dolby Vision Profile 8 is not assumed to have an HDR10 base layer in every case; the reported compatibility identifier matters.
- Multiple audio tracks, absent subtitles, valid short SRT cues, Cues after media, and optional metadata are not condemned simply because the converter normally produces a particular arrangement.
- Failed or absent measurements are never silently treated as healthy. Missing tools and timeouts are explicit for requested work. Intentionally unrequested optional checks are recorded as scope, not incomplete findings.

## Checks requiring conversion context, or not diagnosing Plex compatibility

These were inventoried, but are not silently presented as completed single-file checks:

| Original check or action | Why it is not a standalone playback verdict |
|---|---|
| `Invoke-QualityValidation`, `Get-VmafQualityGateResult`, presentation-origin/alignment helpers | VMAF needs the untouched reference plus exact crop, IVTC, deinterlace, HDR normalization and frame alignment. `--reference` does metadata comparison only. An unaligned score would mislead. |
| `Test-DolbyVisionFileSizeChange`, `Invoke-PostEncodeBloatCheck`, post-remux duration/size gates | These judge a specific before/after transformation. Size shrink/growth alone cannot diagnose an arbitrary file; no conversion is performed. |
| `Invoke-SourceQualityAnalysis`, `Get-AdaptiveQualityThresholds` | Legacy PSNR/SSIM self-comparison functions have no active canonical call sites. Comparing a source to its own reconstruction cannot recover original visual quality. |
| `Get-ContentBasedQuality`, `Get-EnhancedFallbackQuality`, `Get-EncodingSettings`, BPP/quality clamps | Encoder-setting choices, not objective corruption/quality checks. Measured bitrates and packet variation remain available. |
| `Get-CropDetectFilter`, `Test-VideoWatermark` | Black bars, logos and release text are editorial/encoding preferences; they do not make a file incompatible with Plex. No crop/logo OCR is performed. |
| Audio/subtitle ranking, English filtering, target track counts, output flags, TV-specific volume targets | User/library preferences. The shared checker inventories every track and reports selection-relevant flags without deleting/reordering anything. |
| PGS-to-SRT OCR, release-credit removal, HDR10+ repair, Dolby Vision removal/injection, remux and encode | Transformations are excluded. It can flag bitmap subtitle presence and text credit candidates, but cannot certify OCR/translation accuracy. |
| Exact HDR/RPU/source-preservation and expected-profile gates | Some payload metadata is inspected, but unknown original metadata cannot be compared. No blanket Profile 8 requirement is imposed. Equal counts alone do not establish alignment. |
| GPU/QSV tests, MCEBuddy state, queues, temp-space reservations, locks, backup, Plex APIs | Runtime infrastructure and encoding prerequisites, not properties of the media file. |

The checker does not run the converter. A review finding or device-support note here is not an automatic instruction to reject a conversion or modify media.

## Small glossary

| Term | Meaning here |
|---|---|
| Container | The packaging, such as MKV or MP4, holding video, audio, subtitles, and metadata. |
| Codec | The compression format, such as H.264, HEVC, or AAC. Container and codec support are separate. |
| Packet | An encoded chunk of one track. It does not universally equal one decoded video frame. |
| PTS / DTS | Presentation time / decoding time. Their ordering can differ legitimately. |
| Keyframe / GOP | A decoding restart point / the group of pictures between such points. Longer gaps can affect seeking. |
| MiB / Mbps | MiB measures bytes of data; Mbps measures millions of bits per second. They are different units. |
| LUFS / dBTP / LU | Integrated perceived loudness / estimated true peak / loudness difference or range. |
| RPU | Dolby Vision dynamic metadata associated with video frames. Parsing it does not certify a player's display behavior. |
| Heuristic | A practical screening rule that can produce false positives and needs interpretation. |

## Tool and format references

These sources explain the tools and format behavior. They do **not** endorse the project's personal thresholds or establish a Plex defect:

- [FFprobe documentation](https://ffmpeg.org/ffprobe.html) for stream/packet/frame metadata and interval seeking.
- [FFmpeg filter documentation](https://ffmpeg.org/ffmpeg-filters.html) for idet, fieldmatch, decimate, signalstats and loudnorm.
- [Plex Direct Play and Direct Stream](https://support.plex.tv/articles/200250387-streaming-media-direct-play-and-direct-stream/) for client-dependent stream/subtitle behavior.
- [dovi_tool](https://github.com/quietvoid/dovi_tool) and [hdr10plus_tool](https://github.com/quietvoid/hdr10plus_tool) for optional metadata payload inspection.
