"""Whole-file packet accounting, with compact timestamp storage."""
from array import array
from collections import defaultdict
import math
from .common import DEFAULT_LIMITS, finding, number, video_streams


def analyze_full_packets(packets, probe):
    streams = {int(s["index"]): s for s in probe.get("streams", [])
               if s.get("codec_type") in ("video", "audio") and not s.get("disposition", {}).get("attached_pic")}
    data = {i: dict(count=0, missing=0, missing_dts=0, backwards=0, last_dts=None,
                    pts=array("d"), duration=array("d"), bytes=0, keys=array("d")) for i in streams}
    bins = defaultdict(int)
    videos = video_streams(probe)
    primary = int(videos[0]["index"]) if videos else None
    for p in packets:
        idx = number(p.get("stream_index"))
        if idx not in data:
            continue
        d = data[idx]
        d["count"] += 1
        size = number(p.get("size"), 0)
        d["bytes"] += max(0, size)
        pts = number(p.get("pts_time"))
        if pts is None:
            d["missing"] += 1
        else:
            d["pts"].append(pts)
            d["duration"].append(max(0, number(p.get("duration_time"), 0)))
            if idx == primary:
                bins[math.floor(pts)] += max(0, size)
            if "K" in p.get("flags", ""):
                d["keys"].append(pts)
        dts = number(p.get("dts_time"))
        if dts is None:
            d["missing_dts"] += 1
        else:
            if d["last_dts"] is not None and dts < d["last_dts"] - .001:
                d["backwards"] += 1
            d["last_dts"] = dts
    out, metrics = [], {"scope": "whole file", "streams": {}}
    for idx, d in data.items():
        m = {"packets": d["count"], "missing_pts": d["missing"], "missing_dts": d["missing_dts"],
             "dts_backwards": d["backwards"], "payload_bytes": int(d["bytes"])}
        if len(d["pts"]) < 2:
            out.append(finding("FULL_TIMELINE_UNMEASURED", "skipped", "Whole-file timeline unavailable",
                               "Fewer than two usable presentation timestamps were found.", stream=idx))
        else:
            order = sorted(range(len(d["pts"])), key=d["pts"].__getitem__)
            start = d["pts"][order[0]]
            end = max(d["pts"][n] + d["duration"][n] for n in order)
            max_gap, location = 0.0, None
            for a, b in zip(order, order[1:]):
                gap = d["pts"][b] - d["pts"][a] - d["duration"][a]
                if gap > max_gap:
                    max_gap, location = gap, [d["pts"][a], d["pts"][b]]
            m.update(start_seconds=start, end_seconds=end, span_seconds=end-start,
                     max_uncovered_gap_seconds=round(max_gap, 4), gap_location=location)
            if max_gap > DEFAULT_LIMITS["packet_gap"]:
                out.append(finding("FULL_PACKET_GAP", "warning", "Whole-file scan found a timestamp gap",
                                   "A gap exceeds five seconds beyond the preceding packet's duration. It can be intentional, but warrants checking playback there.",
                                   stream=idx, gap_seconds=round(max_gap, 3), times=location))
            if idx == primary:
                m["packet_rate_per_second"] = round(d["count"]/(end-start), 5) if end > start else None
                first_second, last_second = math.ceil(start), math.floor(d["pts"][order[-1]])
                complete_count = max(0, last_second-first_second)
                vals = [size*8/1e6 for s, size in bins.items() if first_second <= s < last_second]
                if vals and complete_count:
                    metrics["video_average_complete_seconds_mbps"] = round(sum(vals)/complete_count, 4)
                    metrics["video_peak_1s_mbps"] = round(max(vals), 4)
                    metrics["complete_seconds"] = complete_count
                    is_4k = number(videos[0].get("height"), 0) >= 2160 or number(videos[0].get("width"), 0) >= 3840
                    threshold = DEFAULT_LIMITS["peak_4k_mbps"] if is_4k else DEFAULT_LIMITS["peak_hd_mbps"]
                    if max(vals) > threshold:
                        out.append(finding("FULL_BITRATE_BURST", "warning", "Whole-file scan found a high bitrate second",
                            "Video packet bytes per media second exceed an advisory streaming threshold. This is not a measurement of Plex transcoder output.",
                            peak_mbps=round(max(vals), 4), threshold_mbps=threshold))
                keys = sorted(set(d["keys"]))
                if len(keys) > 1:
                    keygap = max(b-a for a, b in zip(keys, keys[1:]))
                    m["max_keyframe_gap_seconds"] = round(keygap, 4)
                    if keygap > DEFAULT_LIMITS["keyframe_warn"]:
                        out.append(finding("FULL_KEYFRAMES_SPARSE", "warning", "Whole-file scan found a long GOP",
                                           "Long keyframe intervals may slow seeking or startup.", gap_seconds=round(keygap, 3)))
                declared = number(streams[idx].get("duration"))
                if declared and abs(declared-(end-start)) > max(2, declared*.005):
                    out.append(finding("DURATION_PACKET_MISMATCH", "warning", "Declared runtime differs from packet timeline",
                                       "The stream duration and the observed packet span disagree. Edit lists and timestamp conventions can also cause this.",
                                       declared_seconds=declared, measured_seconds=round(end-start, 3)))
        if d["missing"]:
            out.append(finding("FULL_PTS_MISSING", "warning", "Whole-file scan found missing timestamps",
                               "Missing PTS leaves part of the timeline unverified.", stream=idx, missing=d["missing"]))
        if d["backwards"]:
            out.append(finding("FULL_DTS_BACKWARDS", "warning", "Decode timestamps move backwards",
                               "Detected across the full demuxed stream, excluding ordinary PTS reordering.",
                               stream=idx, count=d["backwards"]))
        metrics["streams"][str(idx)] = m
    primary_stats = metrics["streams"].get(str(primary), {})
    video_span = primary_stats.get("span_seconds")
    if video_span and video_span > 0:
        for idx, stream in streams.items():
            if stream.get("codec_type") != "audio":
                continue
            audio = metrics["streams"][str(idx)]
            span = audio.get("span_seconds")
            if span and abs(video_span-span)/video_span > .02:
                out.append(finding("FULL_AUDIO_DURATION", "warning", "Audio and video packet runtimes differ",
                    "A whole-file packet scan found more than a 2% runtime difference. An alternate/partial track can be intentional.",
                    stream=idx, video_seconds=round(video_span, 3), audio_seconds=round(span, 3)))
    return out, metrics
