"""Small shared types; no third-party Python dependencies."""
import math
from fractions import Fraction


def number(value, default=None):
    try:
        result = float(Fraction(str(value)))
        return result if math.isfinite(result) else default
    except (ValueError, TypeError, ZeroDivisionError, OverflowError):
        return default


def finding(code, severity, title, detail, advice="", **evidence):
    return dict(code=code, severity=severity, title=title, detail=detail,
                advice=advice, evidence=evidence)


def video_streams(probe):
    return [s for s in probe.get("streams", []) if s.get("codec_type") == "video"
            and not s.get("disposition", {}).get("attached_pic")]


def duration_of(probe):
    videos = video_streams(probe)
    value = number(videos[0].get("duration")) if videos else None
    if value and value > 0:
        return value
    value = number(probe.get("format", {}).get("duration"))
    return value if value and value > 0 else None


DEFAULT_LIMITS = {
    "packet_gap": 5.0, "av_start": 2.0, "sample_av_delta": 5.0,
    "interleave_mib": 75.0, "keyframe_warn": 12.0, "keyframe_high": 30.0,
    "peak_hd_mbps": 50.0, "peak_4k_mbps": 160.0, "peak_ratio": 12.0,
}
