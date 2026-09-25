"""Paired raw/IVTC samples; analysis only, no output media."""
import re
from .common import finding, number, video_streams, duration_of
from .process import run_text
from .visual import analyze_idet


def assess_pair(raw_frames, ivtc_frames, interlaced_percent):
    if not raw_frames or not ivtc_frames or interlaced_percent is None:
        return None, None
    ratio = ivtc_frames/raw_frames
    return .75 < ratio < .85 and interlaced_percent <= 5, ratio


def scan_telecine(path, probe, ffmpeg, timeout=120):
    video = video_streams(probe)[0]
    fps = number(video.get("avg_frame_rate")) or number(video.get("r_frame_rate"))
    # Classic 2:3 pulldown is a ~30fps signal. Decimation of arbitrary rates
    # can manufacture an 0.8 ratio, so do not use that as evidence.
    if not fps or abs(fps-30) > .1:
        return [finding("TELECINE_NOT_APPLICABLE", "info", "Classic 2:3 telecine test not applied",
                        "The reported frame rate is not near 29.97/30 fps. Interlace detector results alone do not establish telecine.")], {"applicable": False}
    duration = duration_of(probe) or 0
    starts = [duration*.3, duration*.6] if duration > 600 else [duration*.4] if duration > 120 else [0]
    out, pairs = [], []
    for start in starts:
        samples = []
        for transform in ("", "fieldmatch,decimate,"):
            result = run_text([ffmpeg, "-hide_banner", "-nostdin", "-v", "info", "-threads", "2",
                "-protocol_whitelist", "file,pipe", "-ss", str(max(0,start-30)), "-i", str(path),
                "-map", "0:"+str(video["index"]), "-an", "-sn", "-dn", "-vf",
                "trim=start={}:duration=20,setpts=PTS-STARTPTS,{}idet".format(min(start,30),transform),
                "-t", "20", "-fps_mode", "passthrough", "-enc_time_base:v", "1:1000000",
                "-progress", "pipe:1", "-nostats", "-f", "null", "-"], timeout)
            if result["returncode"] or result["timed_out"]:
                samples.append(None)
                continue
            _, m = analyze_idet(result["stderr"])
            counts = re.findall(r"^frame=(\d+)\s*$", result["stdout"], re.M)
            samples.append((int(counts[-1]), m) if counts and m.get("measured") else None)
        if not all(samples):
            out.append(finding("TELECINE_INCOMPLETE", "skipped", "Paired telecine sample incomplete",
                               "Both raw and inverse-telecine samples must succeed; an unmatched sample is not a result.", start_seconds=start))
            continue
        likely, ratio = assess_pair(samples[0][0], samples[1][0], samples[1][1].get("interlaced_percent"))
        pairs.append(dict(start_seconds=start, raw_frames=samples[0][0], ivtc_frames=samples[1][0],
                          ratio=ratio, post_ivtc_interlaced_percent=samples[1][1].get("interlaced_percent"), candidate=likely))
        if likely:
            out.append(finding("TELECINE_CANDIDATE", "info", "Sample fits the pipeline's 2:3 telecine heuristic",
                               "Inverse telecine reduced frame count to about 80% and left at most 5% interlace classifications. Visual confirmation is still needed before changing a file.", start_seconds=start, frame_ratio=ratio))
    return out, {"applicable": True, "paired_samples": pairs, "complete": len(pairs)==len(starts)}
