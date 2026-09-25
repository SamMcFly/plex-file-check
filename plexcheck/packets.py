"""Packet-based measurements. PTS is sorted; reordered B-frames are valid."""
from collections import defaultdict
import math
import statistics
from .common import DEFAULT_LIMITS, finding, number, video_streams


def sample_starts(duration, window=20):
    if not duration or duration <= window:
        return [0.0]
    return sorted(set(float(int(max(0, p))) for p in
                      (0, duration * .25, duration * .5, duration * .75,
                       duration - window - .5)))


def parse_packets(lines):
    for line in lines:
        row = dict(part.split("=", 1) for part in line.strip().split("|") if "=" in part)
        if "stream_index" in row and ("pts_time" in row or "size" in row):
            yield row


def timeline_gap(packets):
    points = sorted((number(p.get("pts_time")), number(p.get("duration_time"), 0))
                    for p in packets if number(p.get("pts_time")) is not None)
    maximum, location = 0.0, None
    for (left, duration), (right, _) in zip(points, points[1:]):
        # An intentionally long-duration still frame is not missing media.
        gap = right - left - max(0, duration)
        if gap > maximum:
            maximum, location = gap, [left, right]
    return maximum, location


def analyze_packets(windows, probe, limits=None, bandwidth_mbps=None):
    limits = dict(DEFAULT_LIMITS, **(limits or {}))
    out, metrics = [], {"packet_windows": len(windows), "scope": "sampled"}
    videos = video_streams(probe)
    if not videos:
        return out, metrics
    video_index = int(videos[0]["index"])
    audios = [int(s["index"]) for s in probe.get("streams", []) if s.get("codec_type") == "audio"]
    active = [video_index] + audios
    bins = defaultdict(dict)
    full_intervals = []
    max_interleave, max_key_gap = 0.0, 0.0
    sparse = 0
    sample_counts = defaultdict(int)
    missing_pts = defaultdict(int)
    missing_dts = defaultdict(int)
    nonkeys, keysizes = [], []
    audio_bitrates = []
    for wi, packets in enumerate(windows):
        grouped = defaultdict(list)
        for p in packets:
            idx = number(p.get("stream_index"))
            if idx is not None:
                grouped[int(idx)].append(p)
        vp = grouped[video_index]
        if len(vp) < 2:
            out.append(finding("PACKETS_INCOMPLETE", "skipped", "Too few video packets",
                               "The packet window could not be evaluated.", window=wi))
            continue
        vpts = [number(p.get("pts_time")) for p in vp if number(p.get("pts_time")) is not None]
        vpos = [number(p.get("pos")) for p in vp if number(p.get("pos"), -1) >= 0]
        if vpts:
            # Discard boundary seconds. Seeking often lands before the requested point.
            # Never compare timestamps across separate sample windows.
            full_intervals.append((math.ceil(min(vpts)), math.floor(max(vpts))))
        for idx in active:
            sp = grouped[idx]
            sample_counts[idx] += len(sp)
            if not sp:
                out.append(finding("STREAM_PACKETS_MISSING", "warning", "No packets for a track in a sample",
                                   "This can indicate a sparse, short, or incomplete track; inspect it before assuming corruption.",
                                   stream=idx, window=wi))
                continue
            missing_pts[idx] += sum(number(p.get("pts_time")) is None for p in sp)
            missing_dts[idx] += sum(number(p.get("dts_time")) is None for p in sp)
            gap, loc = timeline_gap(sp)
            if gap > limits["packet_gap"]:
                out.append(finding("PACKET_GAP", "warning", "Gap in media timestamps",
                                   "Packet presentation timestamps leave a gap beyond the preceding packet duration. This may interrupt playback.",
                                   "Inspect playback around these times; intentional timeline edits can also cause gaps.",
                                   stream=idx, gap_seconds=round(gap, 3), times=loc, window=wi))
            prev = None
            for p in sp:
                dts = number(p.get("dts_time"))
                if dts is not None:
                    if prev is not None and dts < prev - .001:
                        out.append(finding("DTS_BACKWARDS", "warning", "Decode timestamps move backwards",
                                           "DTS decreased in demux order. PTS reordering alone is normal for B-frames and is not flagged.",
                                           "Check for a timestamp discontinuity or a damaged mux.", stream=idx, window=wi))
                        break
                    prev = dts
        kt = sorted(set(number(p.get("pts_time")) for p in vp
                        if "K" in p.get("flags", "") and number(p.get("pts_time")) is not None))
        if len(kt) > 1:
            max_key_gap = max(max_key_gap, max(b-a for a, b in zip(kt, kt[1:])))
        elif vpts and max(vpts)-min(vpts) > limits["keyframe_warn"]:
            sparse += 1
        for p in vp:
            pts, size = number(p.get("pts_time")), number(p.get("size"), 0)
            if pts is None or size <= 0:
                continue
            # A repeated window must not double-count the same packet.
            ident = (p.get("pos"), p.get("pts_time"), p.get("dts_time"), p.get("size"))
            second = math.floor(pts)
            bins[second][ident] = size
            (keysizes if "K" in p.get("flags", "") else nonkeys).append(size)
        for idx in audios:
            ap = grouped[idx]
            apos = [number(p.get("pos")) for p in ap if number(p.get("pos"), -1) >= 0]
            apts = [number(p.get("pts_time")) for p in ap if number(p.get("pts_time")) is not None]
            if apts:
                end = max(number(p.get("pts_time"), min(apts)) + max(0, number(p.get("duration_time"), 0)) for p in ap)
                span = end - min(apts)
                if span > 0 and len(ap) >= 2:
                    audio_bitrates.append({"stream": idx, "window": wi, "measured_seconds": round(span, 4),
                        "kbps": round(sum(max(0, number(p.get("size"), 0)) for p in ap)*8/span/1000, 3)})
            if vpts and apts and abs(min(vpts)-min(apts)) > limits["sample_av_delta"]:
                out.append(finding("AV_SAMPLE_OFFSET", "warning", "Audio/video sample timestamps differ",
                                   "The earliest audio and video timestamps in a packet window differ by more than the sampling tolerance. Seeking and long GOPs can affect this measurement.",
                                   stream=idx, delta_seconds=round(abs(min(vpts)-min(apts)), 3), window=wi))
            if vpos and apos:
                gap_mib = max(0, min(apos)-max(vpos), min(vpos)-max(apos)) / 1048576
                max_interleave = max(max_interleave, gap_mib)
                if gap_mib > limits["interleave_mib"]:
                    out.append(finding("AV_INTERLEAVE", "warning", "Audio and video are stored far apart",
                                       "Time-window audio/video byte ranges are separated. This can increase remote range requests and startup delays.",
                                       "A remux may improve interleaving; verify a copy before replacing the source.",
                                       stream=idx, gap_mib=round(gap_mib, 2), window=wi))
            elif ap:
                out.append(finding("INTERLEAVE_UNMEASURED", "skipped", "Byte interleaving could not be measured",
                                   "The demuxer did not expose sufficient file positions.", stream=idx))
    metrics.update(max_interleave_mib=round(max_interleave, 3), max_keyframe_gap_seconds=round(max_key_gap, 3),
                   sparse_keyframe_windows=sparse, packet_counts=dict(sample_counts),
                   missing_pts=dict(missing_pts), missing_dts=dict(missing_dts))
    metrics["audio_sample_bitrates"] = audio_bitrates
    if any(missing_pts.values()):
        out.append(finding("PTS_MISSING", "warning", "Some packets lack presentation timestamps",
                           "Timeline coverage is incomplete; this is not automatically corruption.", counts=dict(missing_pts)))
    if any(missing_dts.values()):
        out.append(finding("DTS_INCOMPLETE", "info", "Some decode timestamps are unavailable",
                           "This occurs with some codecs and containers; those DTS values were not tested.", counts=dict(missing_dts)))
    if sparse or max_key_gap > limits["keyframe_warn"]:
        out.append(finding("KEYFRAMES_SPARSE", "warning", "Long gaps between keyframes",
                           "Long GOPs can increase seek and startup time; they do not prove a damaged file.",
                           max_gap_seconds=round(max_key_gap, 3), sparse_windows=sparse,
                           exceeds_30_seconds=max_key_gap > limits["keyframe_high"]))
    values = [sum(bins[s].values()) * 8 / 1e6 for s in sorted(bins)
              if any(a <= s < b for a, b in full_intervals)]
    if values:
        avg, peak = statistics.mean(values), max(values)
        ratio = peak / avg if avg else 0
        is_4k = number(videos[0].get("height"), 0) >= 2160 or number(videos[0].get("width"), 0) >= 3840
        threshold = limits["peak_4k_mbps"] if is_4k else limits["peak_hd_mbps"]
        metrics.update(sampled_video_average_mbps=round(avg, 3), sampled_video_peak_1s_mbps=round(peak, 3),
                       peak_to_average_ratio=round(ratio, 3), complete_seconds_measured=len(values))
        if peak > threshold:
            out.append(finding("BITRATE_BURST", "warning", "High one-second video bitrate",
                               "A packet-based video bitrate measurement exceeds the pipeline's advisory threshold. This is a file measurement, not a network-speed measurement or evidence of the Plex HEVC encoder bug.",
                               "Allow headroom for audio, overhead, and buffering; test the intended client/connection.",
                               peak_mbps=round(peak, 3), average_mbps=round(avg, 3), ratio=round(ratio, 2),
                               threshold_mbps=threshold, extreme_ratio=ratio > limits["peak_ratio"]))
        if bandwidth_mbps and peak > bandwidth_mbps:
            out.append(finding("BANDWIDTH_HEADROOM", "warning", "Video bitrate exceeds the supplied connection budget",
                               "One or more sampled seconds exceed the budget. Player buffering can absorb short bursts, so this alone does not predict a stall.",
                               budget_mbps=bandwidth_mbps, sampled_video_peak_mbps=round(peak, 3)))
    else:
        out.append(finding("BITRATE_UNMEASURED", "skipped", "Insufficient complete seconds for bitrate measurement",
                           "Very short clips, missing timestamps, or failed sampling can prevent this check."))
    if len(nonkeys) >= 10:
        avg = statistics.mean(nonkeys)
        metrics["non_key_packet_size_cv"] = round(statistics.pstdev(nonkeys) / avg, 3)
        metrics["non_key_packet_size_range_ratio"] = round(max(nonkeys)/min(nonkeys), 3)
        # Packet size cannot tell P-frames from B-frames. Keep the old heuristic as
        # descriptive evidence only; the time-based bitrate check drives findings.
    return out, metrics
