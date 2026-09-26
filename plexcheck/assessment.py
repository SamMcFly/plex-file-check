"""Keep the everyday result focused without discarding diagnostic evidence."""
from .common import number

# These observations alone are weak predictors of playback failure. They stay
# available in JSON/--details; --advanced also includes them in its assessment.
_DIAGNOSTIC_CODES = {
    "DECODE_THREAD_DEPENDENT", "QUICK_SCOPE", "DEEP_NOT_RUN", "VISUAL_SKIPPED",
    "LOUDNESS_NOT_RUN", "HDR10PLUS_SCOPE", "frame_rate_unavailable",
    "audio_channels_unknown", "container.structure_unsupported",
    "matroska.cues_absent", "matroska.cues_at_end", "mp4.faststart_absent",
    "hdr_transfer_missing", "hdr_primaries_unusual", "hdr_matrix_unusual",
    "BITRATE_BURST", "FULL_BITRATE_BURST", "KEYFRAMES_SPARSE", "FULL_KEYFRAMES_SPARSE",
    "AV_SAMPLE_OFFSET", "AV_INTERLEAVE", "INTERLEAVE_UNMEASURED",
    "STREAM_PACKETS_MISSING", "PACKET_FRAME_DIFFERENCE",
    "MEASURED_FPS_MISMATCH",
}


def category(item, advanced=False):
    """Classify a finding, never demoting an observed error."""
    code, severity = item["code"], item["severity"]
    evidence = item.get("evidence", {})
    if severity == "error":
        return "file_error"
    if code in {"subtitle_bitmap", "video_limited_hardware_support"}:
        return "device_support"
    if code == "dolby_vision_profile" and evidence.get("profile") in (5, 7):
        return "device_support"
    if not advanced:
        if code in _DIAGNOSTIC_CODES:
            return "diagnostic"
        if code == "audio_video_duration_mismatch" and not evidence.get("substantial"):
            return "diagnostic"
        if code == "audio_channels_unusual" and evidence.get("channels", 0) > 0:
            return "diagnostic"
        if code == "FULL_AUDIO_DURATION":
            video = number(evidence.get("video_seconds"))
            audio = number(evidence.get("audio_seconds"))
            if video and audio is not None and abs(video - audio) <= max(2, video * .2):
                return "diagnostic"
    if severity == "warning":
        return "review"
    if severity == "skipped":
        return "incomplete"
    return "diagnostic"


def split_findings(findings, advanced=False):
    active, diagnostics = [], []
    for original in findings:
        item = dict(original)
        item["category"] = category(item, advanced=advanced)
        # Advanced reports keep context too, but context still does not fail a scan.
        (active if advanced or item["category"] != "diagnostic" else diagnostics).append(item)
    return active, diagnostics


def scope_note(report):
    mode = report.get("mode", "standard")
    if mode == "quick":
        if any(c.get("check") not in {"metadata", "container structure"} for c in report.get("coverage", [])):
            return "Quick metadata/container scan plus explicitly requested checks; full video/audio integrity was not checked."
        return "Metadata and container checks only; video/audio content was not decoded."
    if mode == "deep":
        return "Requested: full primary-video/audio decode and packet timeline; see any incomplete checks below."
    return "Short primary-video/audio samples only; damage elsewhere in the file can be missed."
