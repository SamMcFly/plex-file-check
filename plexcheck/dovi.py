"""Optional read-only whole-stream Dolby Vision payload inspection.

The primary HEVC stream is copied to a pipe, never to a full raw-video file.
Only the extracted RPU and tool diagnostics occupy temporary disk space.
There is one deadline for both extraction processes and the summary process.
"""
from contextlib import contextmanager
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import uuid

from .common import finding, number, video_streams
from .visual import analyze_dovi_summary


_DIAGNOSTIC_PATTERNS = {
    "container_structure": re.compile(
        r"moov atom not found|invalid as first byte of an EBML number|"
        r"Element.*exceeds containing master element|File ended prematurely|"
        r"File extends beyond end of segment", re.I),
    "bitstream_structure": re.compile(
        r"Invalid NAL unit size|Error splitting the input into NAL units|"
        r"corrupt(?:ed)? (?:frame|macroblock|packet)|decode_slice_header error|"
        r"Failed to read access unit|Error applying bitstream filters", re.I),
    "reference_or_dv_layer": re.compile(
        r"Could not find ref with POC|Missing reference picture|"
        r"Error constructing the frame RPS|Skipping invalid undecodable NALU|"
        r"Failed to parse header of NALU", re.I),
    "invalid_data": re.compile(r"Invalid data found when processing input|"
                               r"invalid RPU|failed to parse.*RPU|error parsing.*RPU", re.I),
}


@contextmanager
def _scratch_directory():
    # Python's explicit Windows 0700 ACL can exclude the restricted runtime
    # token. Inherit the user's temp-directory ACL on Windows; keep Unix 0700.
    base = Path(tempfile.gettempdir()).resolve()
    name = "plexcheck-dovi-" + uuid.uuid4().hex
    directory = base / name
    directory.mkdir(mode=0o777 if os.name == "nt" else 0o700)
    try:
        yield directory
    finally:
        # Delete only this newly-created, scoped directory, never a computed
        # ancestor or symlink substituted for it.
        if directory.is_symlink() or directory.parent.resolve() != base or directory.name != name:
            raise OSError("scratch directory identity changed")
        shutil.rmtree(str(directory))


def _read_diagnostics(handle):
    """Scan all log lines, retaining counts only (no private paths or raw logs)."""
    handle.flush()
    handle.seek(0)
    counts = {key: 0 for key in _DIAGNOSTIC_PATTERNS}
    for raw in handle:
        line = raw.decode("utf-8", errors="replace")
        for key, pattern in _DIAGNOSTIC_PATTERNS.items():
            if pattern.search(line):
                counts[key] += 1
    handle.seek(0, os.SEEK_END)
    return {"bytes": handle.tell(), "matches": counts}


def _stop_all(processes):
    """Kill live processes before waiting, so neither side keeps the pipe alive."""
    for process in processes:
        try:
            if process.poll() is None:
                process.kill()
        except OSError:
            pass
    for process in processes:
        try:
            process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            # A kill request can fail at OS level. Avoid an unbounded cleanup wait.
            pass


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise subprocess.TimeoutExpired("Dolby Vision inspection", 0)
    return remaining


def _profile_info(stream):
    profile, signaled = None, False
    for data in stream.get("side_data_list") or []:
        if not isinstance(data, dict):
            continue
        kind = str(data.get("side_data_type", "")).lower()
        if "dovi" in kind or "dolby vision" in kind:
            signaled = True
            value = number(data.get("dv_profile"))
            if value is not None and value > 0 and value.is_integer():
                profile = int(value)
    return profile, signaled


def _diagnostic_findings(diagnostics):
    findings = []
    for tool, diagnostic in diagnostics.items():
        counts = diagnostic["matches"]
        if counts["container_structure"] or counts["bitstream_structure"]:
            findings.append(finding(
                "dovi.structure_diagnostics", "warning", "Structural diagnostics during DV inspection",
                "The {} process reported container or bitstream structural errors. "
                "These remain relevant even if an RPU summary was produced.".format(tool),
                "Review the full decode results and obtain another source if damage is confirmed.",
                tool=tool, container_messages=counts["container_structure"],
                bitstream_messages=counts["bitstream_structure"]
            ))
        if counts["reference_or_dv_layer"]:
            findings.append(finding(
                "dovi.layer_diagnostics", "warning", "Reference or DV-layer diagnostics observed",
                "The {} process reported reference/NAL-layer warnings. They may reflect "
                "decoder handling of Dolby Vision layers; they were not silently discarded "
                "or treated as confirmed file corruption.".format(tool),
                "Correlate with software-decode results and playback at the affected time.",
                tool=tool, messages=counts["reference_or_dv_layer"]
            ))
        if counts["invalid_data"]:
            findings.append(finding(
                "dovi.invalid_data_diagnostics", "warning", "Invalid-data diagnostics during DV inspection",
                "The {} process reported invalid data or an RPU parsing problem. "
                "This can reflect payload damage or unsupported stream handling.".format(tool),
                "Keep this result distinct from ordinary profile/device compatibility.",
                tool=tool, messages=counts["invalid_data"]
            ))
    return findings


def _scan_dovi(path, probe, ffmpeg, dovi_tool, timeout=7200, expected_frames=None):
    """Return (findings, metrics), using optional FFmpeg and dovi_tool binaries.

    expected_frames, when supplied, must be the actual complete decoded count
    for the same primary video stream. Equal counts are necessary but do not
    prove temporal alignment or metadata preservation against another file.
    """
    findings = []
    metrics = {"measured": False, "scope": "whole primary video stream",
               "extraction_complete": False, "summary_parsed": False,
               "frame_alignment_verified": False,
               "source_preservation_verified": False, "timed_out": False}
    videos = video_streams(probe or {})
    if not videos or str(videos[0].get("codec_name", "")).lower() not in {"hevc", "h265"}:
        return [finding("dovi.non_hevc", "skipped", "Full RPU check not applicable",
                        "The primary non-cover video stream is not HEVC.")], metrics
    stream = videos[0]
    profile, signaled = _profile_info(stream)
    metrics.update(signaled_profile=profile, dolby_vision_signaled=signaled)
    if not ffmpeg or not dovi_tool:
        return [finding("dovi.tools_missing", "skipped", "Full RPU check unavailable",
                        "Both FFmpeg and dovi_tool are required for this optional inspection.",
                        "Provide the missing local tools to enable the whole-stream check.",
                        ffmpeg_available=bool(ffmpeg), dovi_tool_available=bool(dovi_tool))], metrics
    limit = number(timeout)
    if limit is None or limit <= 0:
        return [finding("dovi.timeout_invalid", "skipped", "Full RPU check unavailable",
                        "The inspection requires a positive finite timeout.")], metrics
    try:
        source = Path(path).expanduser().resolve(strict=True)
        if not source.is_file():
            raise OSError("not a regular file")
    except (OSError, ValueError, TypeError):
        return [finding("dovi.input_unavailable", "skipped", "Full RPU check unavailable",
                        "The local input file could not be opened for inspection.")], metrics

    expected = number(expected_frames)
    if expected is not None and expected > 0 and expected.is_integer():
        expected = int(expected)
    else:
        expected = None
    metrics["expected_decoded_frames"] = expected
    stream_index = number(stream.get("index"))
    selector = "0:{}".format(int(stream_index)) if (
        stream_index is not None and stream_index >= 0 and stream_index.is_integer()) else "0:V:0"
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    process_options = {"shell": False, "creationflags": flags}
    deadline = time.monotonic() + limit
    summary = None
    summary_complete = False
    diagnostics = {}
    processes = []

    with _scratch_directory() as scratch:
        rpu = scratch / "payload.bin"
        with tempfile.TemporaryFile(mode="w+b") as ff_err, \
                tempfile.TemporaryFile(mode="w+b") as dv_out, \
                tempfile.TemporaryFile(mode="w+b") as dv_err, \
                tempfile.TemporaryFile(mode="w+b") as info_out, \
                tempfile.TemporaryFile(mode="w+b") as info_err:
            producer = extractor = info = None
            try:
                _remaining(deadline)
                producer = subprocess.Popen(
                    [str(ffmpeg), "-hide_banner", "-nostdin", "-nostats", "-v", "error",
                     "-protocol_whitelist", "file,pipe", "-i", str(source),
                     "-map", selector, "-c:v", "copy", "-an", "-sn", "-dn",
                     "-bsf:v", "hevc_mp4toannexb", "-f", "hevc", "-"],
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=ff_err,
                    **process_options)
                processes.append(producer)
                try:
                    _remaining(deadline)
                    extractor = subprocess.Popen(
                        [str(dovi_tool), "extract-rpu", "-i", "-", "-o", str(rpu)],
                        stdin=producer.stdout, stdout=dv_out, stderr=dv_err, **process_options)
                    processes.append(extractor)
                finally:
                    # The child owns its duplicate; the parent must close its pipe end.
                    if producer.stdout is not None:
                        producer.stdout.close()
                extractor.wait(timeout=_remaining(deadline))
                producer.wait(timeout=_remaining(deadline))
                metrics.update(ffmpeg_returncode=producer.returncode,
                               extraction_returncode=extractor.returncode,
                               extraction_complete=(producer.returncode == 0 and extractor.returncode == 0))
                if not metrics["extraction_complete"]:
                    findings.append(finding(
                        "dovi.extraction_incomplete", "warning", "Full RPU extraction did not complete cleanly",
                        "One or both inspection processes exited unsuccessfully; any extracted "
                        "metadata is partial or unverified, rather than a successful full-stream result.",
                        "Review decoder/tool availability and the accompanying structural diagnostics.",
                        ffmpeg_returncode=producer.returncode, extraction_returncode=extractor.returncode
                    ))
                metrics["rpu_bytes"] = rpu.stat().st_size if rpu.is_file() else 0
                if metrics["rpu_bytes"] > 0:
                    _remaining(deadline)
                    info = subprocess.Popen(
                        [str(dovi_tool), "info", "-s", "-i", str(rpu)],
                        stdin=subprocess.DEVNULL, stdout=info_out, stderr=info_err, **process_options)
                    processes.append(info)
                    info.wait(timeout=_remaining(deadline))
                    metrics["summary_returncode"] = info.returncode
                    summary_complete = info.returncode == 0
                    info_out.seek(0)
                    info_err.seek(0)
                    # Summary is normally small. An unexpectedly huge result is not trusted.
                    raw_out, raw_err = info_out.read(1_000_001), info_err.read(1_000_001)
                    if len(raw_out) > 1_000_000 or len(raw_err) > 1_000_000:
                        summary_complete = False
                        findings.append(finding(
                            "dovi.summary_oversize", "skipped", "RPU summary was inconclusive",
                            "The summary exceeded the bounded parser size; no summary verdict was accepted."
                        ))
                    else:
                        summary = raw_out.decode("utf-8", errors="replace") + "\n" + raw_err.decode("utf-8", errors="replace")
                    if info.returncode:
                        findings.append(finding(
                            "dovi.summary_failed", "warning", "RPU summary tool failed",
                            "dovi_tool could not complete its payload summary successfully.",
                            returncode=info.returncode
                        ))
                else:
                    findings.append(finding(
                        "dovi.payload_missing", "warning" if signaled else "skipped",
                        "No nonempty RPU payload was extracted",
                        "The stream signals Dolby Vision, but its RPU payload was not verified."
                        if signaled else
                        "No Dolby Vision was signaled and extraction produced no payload. "
                        "This is an inconclusive or inapplicable RPU check, not proof of corruption.",
                        "Check tool diagnostics before treating absence as a damaged source.",
                        dolby_vision_signaled=signaled
                    ))
            except subprocess.TimeoutExpired:
                metrics["timed_out"] = True
                findings.append(finding(
                    "dovi.timeout", "skipped", "Full RPU inspection timed out",
                    "The shared time limit expired; no complete payload verdict can be given.",
                    "Use a longer timeout if a whole-stream RPU inspection is needed.", timeout_seconds=limit
                ))
            except OSError as exc:
                findings.append(finding(
                    "dovi.tool_unavailable", "skipped", "Full RPU inspection could not run",
                    "A local inspection process or its temporary storage could not be used.",
                    "Check tool paths, execution permissions and available temporary disk space.",
                    error_type=type(exc).__name__, os_error=getattr(exc, "errno", None)
                ))
            finally:
                _stop_all(processes)
                diagnostics["ffmpeg"] = _read_diagnostics(ff_err)
                diagnostics["dovi_extract_stdout"] = _read_diagnostics(dv_out)
                diagnostics["dovi_extract_stderr"] = _read_diagnostics(dv_err)
                diagnostics["dovi_info_stdout"] = _read_diagnostics(info_out)
                diagnostics["dovi_info_stderr"] = _read_diagnostics(info_err)
    metrics["diagnostics"] = diagnostics
    findings.extend(_diagnostic_findings(diagnostics))
    if summary is not None and summary_complete and not metrics["timed_out"]:
        parsed_findings, parsed = analyze_dovi_summary(summary)
        findings.extend(parsed_findings)
        metrics.update(parsed)
        # Parsing a partial extraction is not a whole-stream measurement.
        metrics["summary_parsed"] = bool(parsed.get("measured"))
        metrics["measured"] = bool(parsed.get("measured") and metrics["extraction_complete"])
        metrics["scope"] = "whole primary video stream" if metrics["extraction_complete"] else "partial or unverified payload"
        parsed_profile = parsed.get("profile")
        if parsed.get("measured") and profile is not None and parsed_profile != profile:
            findings.append(finding(
                "dovi.profile_mismatch", "warning", "Signaled and extracted DV profiles differ",
                "The container's Dolby Vision profile differs from the extracted RPU summary. "
                "Review the configuration record and payload together before changing metadata.",
                signaled_profile=profile, payload_profile=parsed_profile
            ))
        if metrics["measured"] and expected is not None:
            matched = parsed["rpu_frames"] == expected
            metrics["rpu_frame_count_matches"] = matched
            findings.append(finding(
                "dovi.frame_count_match" if matched else "dovi.frame_count_mismatch",
                "info" if matched else "warning",
                "RPU count matches decoded video" if matched else "RPU and decoded frame counts differ",
                "The RPU entry count equals the supplied whole-stream decoded frame count. "
                "Equal counts alone do not prove per-frame alignment or source preservation."
                if matched else
                "The RPU entry count differs from the supplied complete decoded frame count. "
                "Metadata alignment or missing entries warrant review; decoding loss can also cause this difference.",
                rpu_frames=parsed["rpu_frames"], decoded_frames=expected
            ))
        elif metrics["measured"]:
            findings.append(finding(
                "dovi.frame_comparison_unavailable", "skipped", "RPU/video frame comparison unavailable",
                "No complete decoded primary-video frame count was supplied. "
                "The RPU count was measured but its correspondence to video remains unverified."
            ))
    return findings, metrics


def scan_dovi(path, probe, ffmpeg, dovi_tool, timeout=7200, expected_frames=None):
    """Inspect one local HEVC file, returning findings and path-free metrics.

    ``expected_frames`` is optional and must describe the complete decoded
    primary video stream. The timeout is shared by extraction and summary.
    Temporary-storage failures are explicit incomplete results.
    """
    try:
        return _scan_dovi(path, probe, ffmpeg, dovi_tool, timeout, expected_frames)
    except OSError as exc:
        return [finding(
            "dovi.storage_unavailable", "skipped", "Full RPU inspection storage unavailable",
            "Temporary inspection storage could not be created, read or cleaned up. "
            "No complete payload verdict is available.",
            "Check temporary-directory permissions and free disk space.",
            error_type=type(exc).__name__, os_error=getattr(exc, "errno", None)
        )], {"measured": False, "scope": "whole primary video stream", "extraction_complete": False}
