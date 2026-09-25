"""Optional read-only HDR10+ extraction and advisory scene heuristics.

The scene thresholds reproduce Test-HDR10MetadataQuality in canonical
09-EncoderBackends.ps1:2721-2945. They are pipeline heuristics, not an HDR10+
conformance test: dark frames, zero targets and absent curves can be legitimate.
"""
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid

from .common import finding, number, video_streams


MAX_JSON_BYTES = 128 * 1024 * 1024
MAX_SCENES = 1000000
_PATTERNS = {
    "container_or_bitstream": re.compile(
        r"moov atom not found|invalid as first byte of an EBML number|"
        r"File ended prematurely|Invalid NAL unit size|"
        r"Error splitting the input into NAL units|Error applying bitstream filters", re.I),
    "invalid_data": re.compile(r"invalid data|failed to parse|error parsing|invalid metadata", re.I),
}


def _numeric(value):
    # bool is not a measured luminance, even though bool subclasses int.
    return None if isinstance(value, bool) else number(value)


def analyze_hdr10plus(data):
    """Return advisory findings and aggregate metrics, never raw scene text."""
    findings = []
    metrics = {"measured": False, "advisory_only": True,
               "conformance_verified": False, "pipeline_viable": None,
               "minimum_viable_percent": 10.0, "minimum_scenes": 10}
    scenes = data.get("SceneInfo") if isinstance(data, dict) else None
    if not isinstance(scenes, list) or not scenes:
        return [finding("hdr10plus.scenes_unavailable", "skipped", "HDR10+ scene assessment unavailable",
                        "The extracted document has no nonempty SceneInfo list. No payload quality verdict was made.")], metrics
    if len(scenes) > MAX_SCENES:
        return [finding("hdr10plus.scene_limit", "skipped", "HDR10+ scene assessment limit reached",
                        "The scene array exceeds the bounded assessment limit.", limit=MAX_SCENES)], metrics

    stats = {"TotalScenes": len(scenes), "GoodScenes": 0, "BadScenes": 0,
             "ZeroMaxSclScenes": 0, "ZeroAverageRgbScenes": 0,
             "InvalidDistributionScenes": 0, "UnusualTargetLuminanceScenes": 0,
             "AverageMaxSclWhenValid": 0.0, "HasValidToneMapping": False,
             "PercentageViable": 0.0}
    malformed = 0
    max_sum = 0.0
    max_count = 0
    target_column_present = any(isinstance(scene, dict) and "TargetedSystemDisplayMaximumLuminance" in scene for scene in scenes)
    for scene in scenes:
        if not isinstance(scene, dict):
            malformed += 1
            continue
        params = scene.get("LuminanceParameters")
        if not isinstance(params, dict):
            malformed += 1
            continue
        incomplete = False
        raw_max = params.get("MaxScl")
        max_values = [_numeric(value) for value in raw_max] if isinstance(raw_max, list) else []
        if any(value is None or value < 0 for value in max_values):
            incomplete = True
        zero_max = not isinstance(raw_max, list) or not raw_max or all(value == 0 for value in max_values)
        if zero_max:
            stats["ZeroMaxSclScenes"] += 1
        for value in max_values:
            if value is not None and value > 0:
                max_sum += value
                max_count += 1

        average = _numeric(params.get("AverageRGB"))
        if average is None or average < 0:
            incomplete = True
        zero_average = average == 0
        if zero_average:
            stats["ZeroAverageRgbScenes"] += 1

        distribution = params.get("LuminanceDistributions")
        raw_values = distribution.get("DistributionValues") if isinstance(distribution, dict) else None
        values = [_numeric(value) for value in raw_values] if isinstance(raw_values, list) else []
        if any(value is None or value < 0 for value in values):
            incomplete = True
        invalid_distribution = not values or sum(value is not None and value > 0 for value in values) <= 2
        if invalid_distribution:
            stats["InvalidDistributionScenes"] += 1

        # Match canonical default: when the target column is entirely absent,
        # every target defaults to zero; missing entries in a present column do
        # not compare less than 800. It is diagnostic only, never a bad-scene gate.
        target = _numeric(scene.get("TargetedSystemDisplayMaximumLuminance")) if target_column_present else 0.0
        if target is not None and target < 800:
            stats["UnusualTargetLuminanceScenes"] += 1
        bezier = scene.get("BezierCurveData")
        if isinstance(bezier, dict):
            x, y = _numeric(bezier.get("KneePointX")), _numeric(bezier.get("KneePointY"))
            if (x is not None and x > 0) or (y is not None and y > 0):
                stats["HasValidToneMapping"] = True
        if incomplete:
            malformed += 1
        elif zero_max or zero_average or invalid_distribution:
            stats["BadScenes"] += 1
        else:
            stats["GoodScenes"] += 1

    stats["PercentageViable"] = stats["GoodScenes"] / stats["TotalScenes"] * 100
    stats["AverageMaxSclWhenValid"] = max_sum / max_count if max_count else 0.0
    metrics.update(quality_stats=stats, malformed_scenes=malformed, measured=not malformed)
    if malformed:
        findings.append(finding("hdr10plus.scene_fields_unknown", "warning", "Some HDR10+ scene fields could not be evaluated",
                                "Missing or nonnumeric fields prevent a complete reproduction of the pipeline heuristic. Unknown scenes were not counted as healthy.", count=malformed))
    else:
        metrics["pipeline_viable"] = stats["PercentageViable"] >= 10.0 and stats["GoodScenes"] > 0 and stats["TotalScenes"] >= 10
    if stats["PercentageViable"] < 10.0:
        findings.append(finding("hdr10plus.low_heuristic_viability", "warning", "Few HDR10+ entries meet the pipeline scene heuristic",
                                "Fewer than 10% of entries avoid all three zero-MaxScl, zero-AverageRGB and sparse-distribution conditions. This advisory does not certify damaged metadata.",
                                viable_percent=stats["PercentageViable"], threshold_percent=10.0))
    for key, ratio, code, title in (
        ("ZeroMaxSclScenes", 0.8, "zero_maxscl", "Most HDR10+ entries have zero or absent MaxScl"),
        ("ZeroAverageRgbScenes", 0.8, "zero_average_rgb", "Most HDR10+ entries have zero AverageRGB"),
        ("InvalidDistributionScenes", 0.5, "sparse_distributions", "Many HDR10+ entries have sparse luminance distributions"),
    ):
        if stats[key] > stats["TotalScenes"] * ratio:
            findings.append(finding("hdr10plus." + code, "warning", title,
                                    "The canonical pipeline threshold was exceeded. Review the metadata in context; black frames or authoring choices can trigger this heuristic.",
                                    count=stats[key], total=stats["TotalScenes"], threshold_fraction=ratio))
    if not stats["HasValidToneMapping"]:
        findings.append(finding("hdr10plus.tone_curve_absent", "info", "No positive HDR10+ tone-curve knee points found",
                                "No BezierCurveData KneePointX or KneePointY was positive. Tone-curve absence alone does not make HDR10+ invalid."))
    if stats["UnusualTargetLuminanceScenes"]:
        findings.append(finding("hdr10plus.low_target_luminance", "info", "HDR10+ entries have low or unspecified display targets",
                                "The canonical heuristic counts target luminance below 800, defaulting an entirely absent target field to zero. This condition does not affect its good-scene count.",
                                count=stats["UnusualTargetLuminanceScenes"], threshold=800))
    if stats["TotalScenes"] < 10:
        findings.append(finding("hdr10plus.small_sample", "info", "HDR10+ scene sample is small",
                                "The pipeline viability rule requires at least ten entries. Fewer entries alone do not establish invalid HDR10+.", count=stats["TotalScenes"]))
    findings.append(finding("hdr10plus.heuristic_scope", "info", "HDR10+ assessment is advisory",
                            "Scene heuristics cannot establish HDR10+ conformance, picture quality, metadata-to-frame alignment or preservation against the original source.",
                            pipeline_viable=metrics["pipeline_viable"], total_entries=stats["TotalScenes"]))
    return findings, metrics


class _DiagnosticDrain:
    """Drain an entire pipe with bounded line storage; retain counts only."""
    def __init__(self, stream):
        self.stream = stream
        self.bytes = 0
        self.counts = {key: 0 for key in _PATTERNS}
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _line(self, raw):
        text = raw.decode("utf-8", errors="replace")
        for key, pattern in _PATTERNS.items():
            if pattern.search(text):
                self.counts[key] += 1

    def _run(self):
        pending = b""
        try:
            while True:
                chunk = self.stream.read(4096)
                if not chunk:
                    break
                self.bytes += len(chunk)
                pending += chunk
                lines = pending.split(b"\n")
                pending = lines.pop()
                for line in lines:
                    self._line(line)
                if len(pending) > 8192:
                    self._line(pending[:-256])
                    pending = pending[-256:]
            if pending:
                self._line(pending)
        except (OSError, ValueError):
            pass
        finally:
            self.stream.close()

    def finish(self):
        self.thread.join(timeout=5)
        return {"bytes": self.bytes, "matches": dict(self.counts), "drain_complete": not self.thread.is_alive()}


def _stop_all(processes):
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
            pass


def scan_hdr10plus(path, probe, ffmpeg, hdr10plus_tool, timeout=7200):
    """Copy primary HEVC to hdr10plus_tool through a pipe; inspect temporary JSON."""
    metrics = {"measured": False, "advisory_only": True, "extraction_complete": False,
               "timed_out": False, "scope": "whole primary video stream"}
    findings = []
    videos = video_streams(probe or {})
    if not videos or str(videos[0].get("codec_name", "")).lower() not in {"hevc", "h265"}:
        return [finding("hdr10plus.non_hevc", "skipped", "HDR10+ extraction not applicable",
                        "The primary non-cover video stream is not HEVC.")], metrics
    if not ffmpeg or not hdr10plus_tool:
        return [finding("hdr10plus.tools_missing", "skipped", "HDR10+ extraction unavailable",
                        "This optional whole-stream check requires local FFmpeg and hdr10plus_tool binaries.",
                        ffmpeg_available=bool(ffmpeg), hdr10plus_tool_available=bool(hdr10plus_tool))], metrics
    limit = number(timeout)
    if limit is None or limit <= 0:
        return [finding("hdr10plus.timeout_invalid", "skipped", "HDR10+ extraction unavailable",
                        "A positive finite timeout is required.")], metrics
    try:
        source = Path(path).expanduser().resolve(strict=True)
        if not source.is_file():
            raise OSError("not a regular file")
    except (OSError, ValueError, TypeError):
        return [finding("hdr10plus.input_unavailable", "skipped", "HDR10+ input unavailable",
                        "The local input file could not be opened for inspection.")], metrics
    index = number(videos[0].get("index"))
    selector = "0:{}".format(int(index)) if index is not None and index >= 0 and index.is_integer() else "0:V:0"
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    deadline = time.monotonic() + limit
    processes = []
    drains = {}
    scratch = None
    extracted = None
    try:
        scratch_root = Path(tempfile.gettempdir()).resolve()
        scratch = scratch_root / ("plexcheck-hdr10plus-" + uuid.uuid4().hex)
        scratch.mkdir(mode=0o777 if os.name == "nt" else 0o700)
        payload = scratch / "metadata.json"
        producer = subprocess.Popen(
            [str(ffmpeg), "-hide_banner", "-nostdin", "-nostats", "-v", "error",
             "-protocol_whitelist", "file,pipe", "-i", str(source), "-map", selector,
             "-c:v", "copy", "-an", "-sn", "-dn", "-bsf:v", "hevc_mp4toannexb", "-f", "hevc", "-"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            shell=False, creationflags=flags)
        processes.append(producer)
        drains["ffmpeg_stderr"] = _DiagnosticDrain(producer.stderr)
        try:
            extractor = subprocess.Popen(
                [str(hdr10plus_tool), "extract", "-i", "-", "-o", str(payload)],
                stdin=producer.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                shell=False, creationflags=flags)
            processes.append(extractor)
        finally:
            producer.stdout.close()
        drains["extract_stdout"] = _DiagnosticDrain(extractor.stdout)
        drains["extract_stderr"] = _DiagnosticDrain(extractor.stderr)
        while any(process.poll() is None for process in processes):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired("HDR10+ extraction", limit)
            if payload.exists() and payload.stat().st_size > MAX_JSON_BYTES:
                findings.append(finding("hdr10plus.json_limit", "skipped", "HDR10+ extraction size limit reached",
                                        "The extracted metadata exceeded the bounded JSON inspection limit; extraction was stopped.", limit_bytes=MAX_JSON_BYTES))
                return findings, metrics
            time.sleep(min(0.05, remaining))
        metrics.update(ffmpeg_returncode=producer.returncode, extraction_returncode=extractor.returncode,
                       extraction_complete=producer.returncode == 0 and extractor.returncode == 0)
        if not metrics["extraction_complete"]:
            findings.append(finding("hdr10plus.extraction_incomplete", "warning", "HDR10+ extraction did not complete cleanly",
                                    "One or both local tools returned unsuccessfully. Absence of a parsed payload is inconclusive and can also mean this HEVC stream has no HDR10+.",
                                    ffmpeg_returncode=producer.returncode, extraction_returncode=extractor.returncode))
            return findings, metrics
        if not payload.is_file() or payload.stat().st_size == 0:
            findings.append(finding("hdr10plus.payload_absent", "skipped", "No HDR10+ payload was extracted",
                                    "The completed extraction produced no nonempty JSON payload. This does not by itself establish media corruption."))
            return findings, metrics
        with payload.open("rb") as handle:
            raw = handle.read(MAX_JSON_BYTES + 1)
        metrics["json_bytes"] = len(raw)
        if len(raw) > MAX_JSON_BYTES:
            findings.append(finding("hdr10plus.json_limit", "skipped", "HDR10+ JSON assessment size limit reached",
                                    "The extracted document exceeds the bounded JSON parser limit.", limit_bytes=MAX_JSON_BYTES))
            return findings, metrics
        try:
            extracted = json.loads(raw.decode("utf-8-sig"))
        except (ValueError, UnicodeDecodeError, RecursionError):
            findings.append(finding("hdr10plus.json_unreadable", "warning", "HDR10+ extraction produced unreadable JSON",
                                    "The extractor's output could not be parsed as a metadata document. No scene assessment was accepted."))
    except subprocess.TimeoutExpired:
        metrics["timed_out"] = True
        findings.append(finding("hdr10plus.timeout", "skipped", "HDR10+ extraction timed out",
                                "The shared extraction deadline expired. No whole-stream result was accepted.", timeout_seconds=limit))
    except OSError as exc:
        findings.append(finding("hdr10plus.tool_unavailable", "skipped", "HDR10+ extraction could not run",
                                "A local tool or temporary storage was unavailable. Check executable paths and temporary disk access.", error_type=type(exc).__name__))
    finally:
        _stop_all(processes)
        metrics["diagnostics"] = {name: drain.finish() for name, drain in drains.items()}
        for name, report in metrics["diagnostics"].items():
            if any(report["matches"].values()):
                findings.append(finding("hdr10plus.tool_diagnostics", "warning", "Diagnostics occurred during HDR10+ extraction",
                                        "The local tools reported structural or parsing diagnostics. They were retained as aggregate counts even if JSON was produced.",
                                        tool=name, counts=report["matches"]))
        if scratch is not None:
            # Remove only this invocation's verified, unique temporary directory.
            try:
                if scratch.resolve().parent == scratch_root and scratch.resolve().name == scratch.name and not scratch.is_symlink():
                    shutil.rmtree(scratch)
            except OSError:
                findings.append(finding("hdr10plus.temp_cleanup", "warning", "HDR10+ temporary cleanup was incomplete",
                                        "The inspection could not remove all of its temporary metadata files."))
    if extracted is not None and metrics["extraction_complete"]:
        results, scene_metrics = analyze_hdr10plus(extracted)
        findings.extend(results)
        metrics.update(scene_metrics)
    return findings, metrics
