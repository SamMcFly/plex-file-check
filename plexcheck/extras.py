"""Optional read-only audio loudness and embedded text-subtitle inspection.

FFmpeg writes only a null sink or a private temporary subtitle. Extraction can
change subtitle representation and therefore cannot certify source preservation.
"""
import json
from contextlib import contextmanager
import os
from pathlib import Path
import re
import shutil
import tempfile
import uuid

from .common import duration_of, finding, number
from .process import run_text


_TEXT_SUBTITLE_CODECS = {"subrip", "srt", "mov_text", "ass", "ssa", "webvtt"}
_LOUDNESS_FIELDS = {"input_i": "integrated_lufs", "input_tp": "true_peak_dbtp",
                    "input_lra": "loudness_range_lu", "input_thresh": "threshold_lufs"}
_AUDIO_ERRORS = re.compile(r"Error while decoding|Invalid frame header|invalid bitstream|"
                           r"File ended prematurely|corrupt(?:ed)? (?:frame|packet)|"
                           r"buffer underflow|packet too small", re.I)


@contextmanager
def _subtitle_directory():
    # Python's restrictive Windows TemporaryDirectory ACL can exclude a
    # sandbox's process token. Inherit the user's temp-directory ACL there;
    # retain normal private mode-0700 directories on POSIX.
    if os.name != "nt":
        with tempfile.TemporaryDirectory(prefix="plexcheck-sub-") as directory:
            yield Path(directory)
        return
    root = Path(tempfile.gettempdir()).resolve()
    directory = root / ("plexcheck-sub-" + uuid.uuid4().hex)
    directory.mkdir(mode=0o777)
    try:
        yield directory
    finally:
        # Resolve and constrain this exact generated child before recursive
        # cleanup. Never follow an unexpected replacement outside the temp root.
        resolved = directory.resolve()
        if resolved.parent == root and resolved.name == directory.name:
            shutil.rmtree(directory)


def _streams(probe, kind):
    return [s for s in (probe.get("streams") or [])
            if isinstance(s, dict) and s.get("codec_type") == kind]


def _index(stream):
    value = number(stream.get("index"))
    return int(value) if value is not None and value >= 0 and value.is_integer() else None


def _incomplete(code, title, index, result=None, error=None):
    evidence = {"stream": index}
    if result:
        evidence.update(exit_code=result.get("returncode"), timed_out=bool(result.get("timed_out")))
    if error:
        evidence["error_type"] = type(error).__name__
    return finding(code, "skipped", title,
                   "The analysis did not complete. Tool availability, format support and the time limit can affect this; no clean-content verdict is made.", **evidence)


def _loudness_json(text):
    # Parse candidates independently: FFmpeg can print unrelated messages or
    # multiple measurement blocks. Do not greedily consume from the first brace.
    decoder = json.JSONDecoder()
    chosen = None
    for offset, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text, offset)
        except ValueError:
            continue
        if isinstance(value, dict) and "input_i" in value:
            chosen = value
    return chosen


def scan_loudness(path, probe, ffmpeg, timeout=7200, progress=lambda text: None):
    """Measure every audio stream at -23 LUFS/-2 dBTP/7 LU; write no audio.

    Targets are an optional pipeline reference, not a file-validity standard.
    A null output discards the filter output; source media is never modified.
    """
    findings, tracks = [], []
    audios = _streams(probe, "audio")
    metrics = {"loudness": {"complete": False, "target_lufs": -23,
                            "target_true_peak_dbtp": -2, "target_lra_lu": 7,
                            "tracks": tracks}}
    if not audios:
        findings.append(finding("loudness.no_audio", "skipped", "No audio available for loudness analysis",
                                "The metadata contains no audio stream."))
        return findings, metrics
    if not ffmpeg:
        findings.append(finding("loudness.tool_missing", "skipped", "Loudness analysis requires FFmpeg",
                                "Install FFmpeg with the loudnorm filter to measure audio."))
        return findings, metrics
    source = str(Path(path).expanduser().resolve())
    for stream in audios:
        index = _index(stream)
        track = {"stream": index, "complete": False}
        tracks.append(track)
        if index is None:
            findings.append(_incomplete("loudness.index_missing", "Audio stream index unavailable", None))
            continue
        progress("Measuring full-track loudness for audio stream {}...".format(index))
        args = [ffmpeg, "-hide_banner", "-nostdin", "-v", "info",
                "-protocol_whitelist", "file,pipe", "-i", source, "-map", "0:{}".format(index),
                "-vn", "-sn", "-dn", "-af", "loudnorm=I=-23:TP=-2:LRA=7:print_format=json",
                "-f", "null", "-"]
        try:
            result = run_text(args, timeout=timeout)
        except (OSError, ValueError) as error:
            findings.append(_incomplete("loudness.unavailable", "Loudness analysis unavailable", index, error=error))
            continue
        if result.get("timed_out") or result.get("returncode") or result.get("output_truncated"):
            findings.append(_incomplete("loudness.incomplete", "Loudness analysis incomplete", index, result))
            continue
        measurement = _loudness_json(result.get("stderr", ""))
        if measurement is None:
            findings.append(_incomplete("loudness.measurement_missing", "Loudness measurement unavailable", index, result))
            continue
        raw_i = str(measurement.get("input_i", "")).strip().lower()
        raw_tp = str(measurement.get("input_tp", "")).strip().lower()
        decode_diagnostics = bool(_AUDIO_ERRORS.search(result.get("stderr", "")))
        if decode_diagnostics:
            findings.append(finding("loudness.decode_diagnostics", "warning", "Loudness pass reported decode diagnostics",
                                    "The pass returned a measurement but also reported decoding problems. The measurement may exclude unrecovered audio and does not establish complete-track integrity.", stream=index))
        if raw_i in {"-inf", "-infinity"} and raw_tp in {"-inf", "-infinity"}:
            track.update(complete=not decode_diagnostics, silence_reported=True)
            findings.append(finding("loudness.silence", "info", "Audio measurement reported silence",
                                    "FFmpeg reported negative-infinite integrated loudness and peak for this stream. Silence can be intentional.", stream=index))
            continue
        values = {label: number(measurement.get(key)) for key, label in _LOUDNESS_FIELDS.items()}
        if any(value is None for value in values.values()) or values["loudness_range_lu"] < 0:
            findings.append(finding("loudness.nonfinite", "skipped", "Loudness result is not a complete finite measurement",
                                    "One or more loudness values are missing, nonnumeric, nonfinite, or internally invalid. This is not proof of damaged audio.", stream=index))
            continue
        within = (abs(values["integrated_lufs"] + 23) <= 1.5 and values["true_peak_dbtp"] <= -2
                  and values["loudness_range_lu"] <= 8)
        track.update(values, complete=not decode_diagnostics, silence_reported=False, within_reference_targets=within)
        findings.append(finding("loudness.measurement", "info", "Audio loudness measured",
                                "Measured the whole stream. The -23 LUFS/-2 dBTP/7 LU targets are an optional consistency reference; differing from them is not corruption.",
                                stream=index, **values, within_reference_targets=within))
    metrics["loudness"]["complete"] = bool(tracks) and all(t["complete"] for t in tracks)
    return findings, metrics


def scan_embedded_subtitles(path, probe, ffmpeg, timeout=7200, progress=lambda text: None):
    """Extract supported text subtitles to private temporary SRTs and inspect.

    Bitmap subtitle OCR is not attempted. FFmpeg text conversion may remove
    styling/representation errors, so a clean converted SRT is a limited result.
    """
    from .containers import validate_srt, MAX_SRT_BYTES
    findings, tracks = [], []
    subtitles = _streams(probe, "subtitle")
    metrics = {"embedded_subtitles": {"complete": False, "tracks": tracks, "timeout_seconds_per_track": timeout,
                                      "conversion_preservation_checked": False}}
    if not subtitles:
        metrics["embedded_subtitles"].update(complete=True, applicable=False)
        return findings, metrics
    metrics["embedded_subtitles"]["applicable"] = True
    if not ffmpeg:
        findings.append(finding("subtitle.ffmpeg_missing", "skipped", "Embedded subtitle inspection requires FFmpeg",
                                "Only available stream metadata and sidecar files could be inspected."))
        return findings, metrics
    source = str(Path(path).expanduser().resolve())
    duration = duration_of(probe)
    for stream in subtitles:
        index = _index(stream)
        codec = str(stream.get("codec_name") or "").lower()
        track = {"stream": index, "codec": codec or None, "complete": False}
        tracks.append(track)
        if codec not in _TEXT_SUBTITLE_CODECS:
            findings.append(finding("subtitle.content_not_inspected", "skipped", "Embedded subtitle content was not inspected",
                                    "This codec is outside the supported text extraction formats. Bitmap subtitles require separate inspection; OCR was not run.", stream=index, codec=codec or None))
            continue
        if index is None:
            findings.append(_incomplete("subtitle.index_missing", "Subtitle stream index unavailable", None))
            continue
        progress("Inspecting embedded text subtitle stream {}...".format(index))
        try:
            with _subtitle_directory() as directory:
                output = directory / "subtitle.srt"
                # Use input-start-relative timestamps, retaining cue spacing.
                # This is an analysis copy and cannot certify original styling.
                args = [ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-n",
                        "-protocol_whitelist", "file,pipe", "-copyts", "-start_at_zero", "-i", source,
                        "-map", "0:{}".format(index), "-vn", "-an", "-dn", "-c:s", "srt",
                        "-avoid_negative_ts", "disabled", "-fs", str(MAX_SRT_BYTES + 1), "-f", "srt", str(output)]
                result = run_text(args, timeout=timeout)
                if result.get("timed_out") or result.get("returncode") or result.get("output_truncated"):
                    note = _incomplete("subtitle.extraction_incomplete", "Embedded subtitle extraction incomplete", index, result)
                    note["evidence"]["timeout_seconds"] = timeout
                    if result.get("timed_out"):
                        note["detail"] = "Whole-track subtitle extraction exceeded its time limit. It may need to read the entire media file, especially on network storage. This does not establish damaged subtitles."
                        note["advice"] = "Increase --full-timeout for a longer per-track limit, or inspect a local copy."
                    findings.append(note)
                    continue
                if not output.is_file():
                    findings.append(_incomplete("subtitle.output_missing", "No extracted subtitle was produced", index, result))
                    continue
                if output.stat().st_size >= MAX_SRT_BYTES:
                    findings.append(finding("subtitle.extraction_size_limit", "skipped", "Embedded subtitle inspection limit reached",
                                            "The extracted subtitle reached the analysis size limit; the full stream was not validated.", stream=index, limit_bytes=MAX_SRT_BYTES))
                    continue
                results, report = validate_srt(output, duration=duration)
                # Replace generic sidecar identity with the source stream index.
                # Neither the temporary directory nor any source filename is returned.
                for item in results:
                    item = dict(item)
                    evidence = dict(item.get("evidence") or {})
                    evidence.pop("sidecar", None)
                    evidence.update(embedded_stream=index, converted_to_srt=True)
                    item["evidence"] = evidence
                    findings.append(item)
                safe_report = {key: value for key, value in report.items() if key not in {"name", "path", "filename"}}
                track.update(safe_report, converted_to_srt=True, timestamps="relative to input start")
                if result.get("stderr", "").strip():
                    track["complete"] = False
                    findings.append(finding("subtitle.extraction_diagnostics", "warning", "Subtitle extraction reported diagnostics",
                                            "FFmpeg emitted errors even though extraction exited successfully. The converted text cannot establish that every original cue was recovered.", stream=index))
        except (OSError, ValueError) as error:
            findings.append(_incomplete("subtitle.inspection_unavailable", "Embedded subtitle inspection unavailable", index, error=error))
    metrics["embedded_subtitles"]["complete"] = bool(tracks) and all(t["complete"] for t in tracks)
    if any(t.get("converted_to_srt") for t in tracks):
        findings.append(finding("subtitle.conversion_scope", "info", "Embedded subtitle checks used temporary text copies",
                                "Text subtitles were converted to SRT for syntax/timing inspection. This does not certify original styling, translation, completeness, or preservation through conversion. No source subtitle was changed."))
    return findings, metrics
