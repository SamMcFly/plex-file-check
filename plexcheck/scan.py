"""Read-only orchestration of independent diagnostics."""
import datetime
import io
import json
import os
from pathlib import Path
import re
import tempfile
from . import __version__
from .common import finding, number, duration_of, video_streams
from .process import probe_json, run_file, run_text
from .packets import sample_starts, parse_packets, analyze_packets
from .deep import analyze_full_packets
from .decode import CORRUPTION, UNSUPPORTED, check_decode_result, run_decode_sample


def _failure(result):
    if result.get("timed_out"):
        return "The tool exceeded the configured time limit."
    if result.get("output_truncated"):
        return "The probe output exceeded the size limit."
    return "The tool failed or did not return usable data (exit {}).".format(result.get("returncode"))


def scan(path, tools, mode="standard", timeout=120, full_timeout=7200,
         bandwidth_mbps=None, reference=None, expected_runtime=None, visual=True,
         dovi=False, progress=lambda s: None, redact_name=False, loudness=False, hdr10plus=False):
    from .metadata import analyze_metadata
    from .containers import inspect_container, inspect_sidecars
    from .visual import analyze_idet, analyze_signalstats, analyze_dovi_summary
    path = Path(path).expanduser().resolve()
    result = dict(schema_version=1, checker_version=__version__,
                  generated_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  file=("media" + path.suffix if redact_name else path.name),
                  mode=mode, findings=[], metrics={}, coverage=[], tools={})
    findings, metrics, coverage = result["findings"], result["metrics"], result["coverage"]
    def add(items, data=None, name=None):
        findings.extend(items)
        if data is not None and name:
            metrics[name] = data
    if not path.is_file():
        findings.append(finding("FILE_MISSING", "error", "Input is not a regular file", "Select a readable local media file."))
        return result
    before = path.stat()
    metrics["file_size_bytes"] = before.st_size
    metrics["input_identity"] = {
        "size_bytes": before.st_size,
        "modified_utc": datetime.datetime.fromtimestamp(before.st_mtime, datetime.timezone.utc).isoformat(),
    }
    if not before.st_size:
        findings.append(finding("FILE_EMPTY", "error", "Input file is empty", "There is no media data to inspect."))
        return result
    for name in ("ffprobe", "ffmpeg"):
        if tools.get(name):
            vr = run_text([tools[name], "-version"], min(timeout, 15), 8192)
            result["tools"][name] = vr["stdout"].splitlines()[0] if vr["stdout"] else "version unavailable"
    progress("Reading media metadata...")
    probe, status = probe_json(tools["ffprobe"], path, timeout=timeout)
    if not probe or not probe.get("streams"):
        findings.append(finding("PROBE_FAILED", "error", "Media metadata could not be read", _failure(status),
                                "Confirm that the file is complete and readable, and that FFprobe supports its format."))
        return result
    coverage.append({"check": "metadata", "status": "completed"})
    reference_probe = None
    if reference:
        reference_probe, rs = probe_json(tools["ffprobe"], reference, timeout=timeout)
        if not reference_probe:
            findings.append(finding("REFERENCE_UNREADABLE", "skipped", "Reference comparison unavailable", _failure(rs)))
    frames = []
    videos = video_streams(probe)
    if videos and mode != "quick":
        progress("Reading frame color and Dolby Vision metadata...")
        fp, fs = probe_json(tools["ffprobe"], path,
                           ["-select_streams", str(videos[0]["index"]), "-read_intervals", "%+#48",
                            "-show_frames", "-show_entries",
                            "frame=stream_index,pts_time,color_range,color_space,color_primaries,color_transfer,interlaced_frame,top_field_first,pict_type:frame_side_data"], timeout)
        frames = fp.get("frames", []) if fp else []
        coverage.append({"check": "frame metadata (first 48 packets)", "status": "completed" if frames else "incomplete"})
        if not frames:
            findings.append(finding("FRAME_METADATA_UNMEASURED", "skipped", "Frame metadata unavailable",
                                    "Stream metadata was checked, but early decoded-frame HDR metadata was unavailable."))
    mf, mm = analyze_metadata(probe, frames=frames, reference=reference_probe)
    add(mf, mm, "metadata")
    fmt = probe.get("format", {}).get("format_name", "")
    progress("Inspecting container layout (many small reads; network storage can be slow)...")
    cf, cm = inspect_container(path, fmt)
    add(cf, cm, "container")
    sf, sm = inspect_sidecars(path)
    add(sf, sm, "subtitles")
    coverage += [{"check": "container indexing", "status": "completed" if cm.get("container_structure", {}).get("complete") else "incomplete"},
                 {"check": "sidecar SRT validation", "status": "completed" if not any(f["severity"] == "skipped" for f in sf) else "incomplete"}]
    dur = duration_of(probe)
    if expected_runtime and dur:
        delta = dur - expected_runtime
        metrics["expected_runtime"] = {"expected_seconds": expected_runtime, "difference_seconds": round(delta, 3)}
        if dur < expected_runtime * .85:
            findings.append(finding("RUNTIME_SHORT", "warning", "Runtime is substantially shorter than expected",
                                    "The file is more than 15% shorter than the supplied runtime. Different cuts or editions are another explanation.",
                                    expected_seconds=expected_runtime, observed_seconds=dur))
    if mode == "quick":
        findings.append(finding("QUICK_SCOPE", "skipped", "Content scans were not run",
                                "Quick mode inspects metadata, container structure and sidecar subtitles only. Run standard or deep mode for packet and decode checks."))
    elif videos:
        windows = []
        starts = sample_starts(dur)
        origin = number(probe.get("format", {}).get("start_time"), 0)
        for start in starts:
            progress("Sampling packets at {:.0f} seconds...".format(start))
            packet_probe, ps = probe_json(tools["ffprobe"], path,
                ["-show_packets", "-show_entries", "packet=stream_index,pts_time,dts_time,duration_time,size,pos,flags",
                 "-read_intervals", "{:.6f}%+20".format(origin+start)], timeout)
            if packet_probe is None:
                findings.append(finding("PACKET_SAMPLE_FAILED", "skipped", "Packet sample unavailable", _failure(ps), start_seconds=start))
            else:
                windows.append(packet_probe.get("packets", []))
                if ps.get("stderr", "").strip():
                    findings.append(finding("PACKET_PROBE_DIAGNOSTICS", "warning", "Packet probe reported errors",
                                            "The packet probe returned data together with error diagnostics; its data does not establish clean demuxing.", start_seconds=start,
                                            error_types=sorted(set(CORRUPTION.findall(ps["stderr"])))))
        pf, pm = analyze_packets(windows, probe, bandwidth_mbps=bandwidth_mbps)
        add(pf, pm, "packets")
        coverage.append({"check": "packet samples", "status": "completed" if len(windows)==len(starts) and all(windows) and not any(f["severity"] == "skipped" for f in pf) else "incomplete", "requested_starts_seconds": starts, "window_seconds": 20})
        decode_starts = sorted(set((0.0, max(0, (dur or 2)/2-1), max(0, (dur or 3)-3))))
        if not tools.get("ffmpeg"):
            findings.append(finding("FFMPEG_MISSING", "skipped", "Decode and visual checks unavailable", "Install FFmpeg or supply --ffmpeg."))
        else:
            decodes = []
            for start in decode_starts:
                progress("Decoding video and all audio tracks at {:.1f} seconds...".format(start))
                args = [tools["ffmpeg"], "-hide_banner", "-nostdin", "-v", "error", "-threads", "2",
                        "-protocol_whitelist", "file,pipe", "-ss", str(start), "-i", str(path),
                        "-map", "0:"+str(videos[0]["index"]), "-map", "0:a?", "-sn", "-dn", "-t", "2",
                        "-fps_mode", "passthrough", "-enc_time_base:v", "1:1000000", "-progress", "pipe:1", "-nostats", "-f", "null", "-"]
                df, dm = run_decode_sample(args, timeout, start, progress=progress)
                add(df)
                decodes.append(dm)
            metrics["decode_samples"] = decodes
            coverage.append({"check": "software video + all audio decode", "status": "completed" if all(d.get("clean") for d in decodes) else "incomplete", "window_seconds": 2, "starts_seconds": decode_starts})
            if visual:
                visual_samples = []
                visual_starts = [0.0] if not dur or dur < 60 else sorted(set(round(dur*f, 3) for f in (.1, .3, .5, .7, .9)))
                for start in visual_starts:
                    progress("Checking interlace/cadence and signal levels at {:.1f} seconds...".format(start))
                    args = [tools["ffmpeg"], "-hide_banner", "-nostdin", "-v", "info", "-threads", "2",
                            "-protocol_whitelist", "file,pipe", "-ss", str(max(0, start-30)), "-i", str(path),
                            "-map", "0:"+str(videos[0]["index"]), "-an", "-sn", "-dn",
                            "-vf", "trim=start={}:duration=10,setpts=PTS-STARTPTS,idet,signalstats,metadata=print:file=-".format(min(start,30)),
                            "-t", "10", "-enc_time_base:v", "1:1000000", "-f", "null", "-"]
                    vs = run_text(args, timeout)
                    if vs["timed_out"] or vs["returncode"]:
                        findings.append(finding("VISUAL_UNMEASURED", "skipped", "Visual sample unavailable", _failure(vs), start_seconds=start))
                        continue
                    vf, vm = analyze_idet(vs["stderr"], field_order=videos[0].get("field_order", "unknown"))
                    for f in vf:
                        f["evidence"]["start_seconds"] = start
                    add(vf)
                    bitdepth = mm.get("bit_depth") or number(videos[0].get("bits_per_raw_sample"))
                    if not bitdepth:
                        match = re.search(r"(?:p|gray|gbrp)(10|12|16)", videos[0].get("pix_fmt", ""))
                        bitdepth = int(match[1]) if match else 8
                    lf, lm = analyze_signalstats(vs["stdout"], bit_depth=int(bitdepth))
                    add(lf)
                    visual_samples.append(dict(start_seconds=start, idet=vm, signal=lm))
                metrics["visual_samples"] = visual_samples
                coverage.append({"check": "interlace/cadence/signal samples", "status": "completed" if len(visual_samples)==len(visual_starts) and all(s["idet"].get("measured") and s["signal"].get("measured") for s in visual_samples) else "incomplete"})
                if any(s["idet"].get("combing_risk") for s in visual_samples):
                    from .cadence import scan_telecine
                    progress("Checking whether the interlace signal fits a 2:3 telecine pattern...")
                    cf, cdm = scan_telecine(path, probe, tools["ffmpeg"], timeout=timeout)
                    add(cf, cdm, "telecine")
            else:
                findings.append(finding("VISUAL_SKIPPED", "skipped", "Visual analysis disabled", "Run without --no-visual to sample interlacing and code levels."))
        if mode == "deep":
            progress("Scanning the full packet timeline (may take several minutes)...")
            args = [tools["ffprobe"], "-v", "error", "-protocol_whitelist", "file,pipe", "-show_packets",
                    "-show_entries", "packet=stream_index,pts_time,dts_time,duration_time,size,flags", "-of", "compact=p=0:nk=0", str(path)]
            with run_file(args, full_timeout) as (handle, ts):
                if ts["returncode"] or ts["timed_out"]:
                    findings.append(finding("FULL_PACKETS_FAILED", "skipped", "Whole-file packet scan incomplete", _failure(ts)))
                else:
                    tf, tm = analyze_full_packets(parse_packets(io.TextIOWrapper(handle, encoding="utf-8", errors="replace")), probe)
                    add(tf, tm, "full_packets")
                    coverage.append({"check": "whole-file packet timeline", "status": "completed" if not any(f["severity"] == "skipped" for f in tf) else "incomplete"})
                    if ts.get("stderr", "").strip():
                        findings.append(finding("FULL_PROBE_DIAGNOSTICS", "warning", "Whole-file probe reported errors",
                            "FFprobe emitted error diagnostics during the full scan. Packet counts alone do not establish clean demuxing.",
                            error_types=sorted(set(CORRUPTION.findall(ts["stderr"])))))
                    if bandwidth_mbps and tm.get("video_peak_1s_mbps", 0) > bandwidth_mbps:
                        findings.append(finding("FULL_BANDWIDTH_HEADROOM", "warning", "Whole-file video peak exceeds the connection budget",
                            "Buffering can absorb bursts; this comparison alone does not establish a playback failure.",
                            peak_mbps=tm["video_peak_1s_mbps"], budget_mbps=bandwidth_mbps))
            if tools.get("ffmpeg"):
                progress("Decoding the entire primary video and every audio track (may take a long time)...")
                ds = run_text([tools["ffmpeg"], "-hide_banner", "-nostdin", "-v", "error", "-threads", "2",
                    "-protocol_whitelist", "file,pipe", "-i", str(path), "-map", "0:"+str(videos[0]["index"]),
                    "-map", "0:a?", "-sn", "-dn", "-fps_mode", "passthrough", "-enc_time_base:v", "1:1000000", "-progress", "pipe:1", "-nostats", "-f", "null", "-"], full_timeout)
                df, dm = check_decode_result(ds)
                add(df, dm, "full_decode")
                coverage.append({"check": "whole-file software decode", "status": "completed" if dm.get("clean") else "incomplete"})
                primary = metrics.get("full_packets", {}).get("streams", {}).get(str(videos[0]["index"]), {})
                count, frames_count = primary.get("packets"), dm.get("decoded_video_frames")
                if count and frames_count and abs(count-frames_count) > 2:
                    findings.append(finding("PACKET_FRAME_DIFFERENCE", "warning", "Packet and decoded-frame counts differ",
                        "The pipeline's two-frame tolerance was exceeded. Packet-to-frame mapping is codec-dependent, so this does not by itself prove lost frames.", packets=count, decoded_frames=frames_count))
                span = primary.get("span_seconds")
                declared = number(videos[0].get("avg_frame_rate")) or number(videos[0].get("r_frame_rate"))
                if dm.get("clean") and span and frames_count and declared:
                    measured = frames_count/span
                    metrics["measured_average_fps"] = round(measured, 5)
                    if abs(measured-declared)/declared > .02:
                        findings.append(finding("MEASURED_FPS_MISMATCH", "warning", "Measured average frame rate differs from metadata",
                            "Decoded frames divided by packet runtime differ by more than 2%. Variable frame rate and timeline edits are possible explanations.", measured_fps=round(measured, 4), declared_fps=declared))
        else:
            findings.append(finding("DEEP_NOT_RUN", "skipped", "Whole-file integrity was not checked",
                                    "Samples cannot rule out damage elsewhere. Use --deep to scan the full packet timeline and decode every primary-video/audio frame."))
    if tools.get("ffmpeg") and mode != "quick":
        from .extras import scan_embedded_subtitles
        progress("Checking embedded text subtitles (whole-file reads; limit {} seconds per track)...".format(full_timeout))
        ef, em = scan_embedded_subtitles(path, probe, tools["ffmpeg"], timeout=full_timeout, progress=progress)
        add(ef, em, "embedded_subtitles")
        coverage.append({"check": "embedded text subtitles", "status": "completed" if em.get("embedded_subtitles", {}).get("complete") else "incomplete"})
    if loudness:
        from .extras import scan_loudness
        lf, lm = scan_loudness(path, probe, tools.get("ffmpeg"), timeout=full_timeout, progress=progress)
        add(lf, lm, "loudness")
        coverage.append({"check": "audio loudness", "status": "completed" if lm.get("loudness", {}).get("complete") else "incomplete"})
    else:
        findings.append(finding("LOUDNESS_NOT_RUN", "skipped", "Whole-track loudness was not measured",
                                "Use --loudness for optional integrated loudness/true-peak analysis. Volume is not a codec compatibility test."))
    if dovi:
        from .dovi import scan_dovi
        progress("Checking embedded Dolby Vision RPUs...")
        dvf, dvm = scan_dovi(path, probe, tools.get("ffmpeg"), tools.get("dovi_tool"), timeout=full_timeout,
                            expected_frames=metrics.get("full_decode", {}).get("decoded_video_frames")
                            if metrics.get("full_decode", {}).get("clean") else None)
        add(dvf, dvm, "dolby_vision_rpu")
        coverage.append({"check": "Dolby Vision RPU validation", "status": "completed" if dvm.get("measured") and not any(f["severity"] == "skipped" for f in dvf) else "incomplete"})
    else:
        findings.append(finding("RPU_SCOPE", "info", "Dolby Vision checks cover reported metadata",
                                "An RPU-for-every-frame integrity check and conversion-preservation checks require extracted bitstreams/reference data."))
    if hdr10plus:
        from .hdr10plus import scan_hdr10plus
        progress("Checking whole-stream HDR10+ scene metadata...")
        hf, hm = scan_hdr10plus(path, probe, tools.get("ffmpeg"), tools.get("hdr10plus_tool"), timeout=full_timeout)
        add(hf, hm, "hdr10plus_scenes")
        coverage.append({"check": "HDR10+ scene metadata", "status": "completed" if hm.get("measured") and hm.get("extraction_complete") else "incomplete"})
    elif mm.get("hdr10plus_observed"):
        findings.append(finding("HDR10PLUS_SCOPE", "skipped", "HDR10+ payload was not fully inspected",
                                "HDR10+ metadata was observed in early frames. Use --hdr10plus with hdr10plus_tool for the full scene heuristic."))
    findings.append(finding("PLAYBACK_SCOPE", "info", "A file check cannot guarantee Plex playback",
                            "Client codec support, subtitles selected, server transcoding, network speed and Plex bugs are outside this file-only assessment. This does not test Plex HEVC transcode bitrate overshoot."))
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        findings.append(finding("INPUT_CHANGED", "warning", "Input changed during the scan",
                                "Size or modification time changed; rerun after the download/conversion finishes."))
    # Raw probe data, file paths, tags, tool stderr, and command lines are never
    # embedded in the report. It can be shared without local folder names.
    return result
