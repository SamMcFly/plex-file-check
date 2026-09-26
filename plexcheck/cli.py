import argparse
from collections import Counter
import json
import math
from pathlib import Path
import sys
from . import __version__
from .common import finding
from .process import locate
from .scan import scan
from .assessment import split_findings, scope_note


def positive(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be a positive finite number")
    return number


def make_parser():
    parser = argparse.ArgumentParser(description="Read-only media diagnostics for Plex. No source changes or Plex account required.")
    parser.add_argument("file", type=Path, help="local media file to inspect")
    parser.add_argument("--version", action="version", version="Plex File Check " + __version__)
    depth = parser.add_mutually_exclusive_group()
    depth.add_argument("--quick", action="store_true", help="metadata and essential container checks only; no content decoding")
    depth.add_argument("--deep", action="store_true", help="also scan every packet and fully decode primary video/all audio (can take hours)")
    parser.add_argument("--advanced", action="store_true", help="run the broader diagnostic audit, including heuristics and subtitle content checks")
    parser.add_argument("--details", action="store_true", help="show evidence and additional diagnostics from the selected scan")
    parser.add_argument("--no-visual", action="store_true", help="skip image-pattern samples when using --advanced")
    parser.add_argument("--loudness", action="store_true", help="measure each whole audio track (slow; does not change its volume)")
    parser.add_argument("--dovi", action="store_true", help="inspect embedded HEVC Dolby Vision RPUs with optional dovi_tool (not certification)")
    parser.add_argument("--hdr10plus", action="store_true", help="inspect whole-stream HDR10+ scene metadata with optional hdr10plus_tool")
    parser.add_argument("--reference", type=Path, help="optional original file for metadata/runtime comparison, not VMAF")
    parser.add_argument("--expected-runtime", type=positive, metavar="SECONDS", help="known runtime; flag files more than 15%% shorter")
    parser.add_argument("--bandwidth-mbps", type=positive, help="optional sustained connection budget to compare with file video bitrate")
    parser.add_argument("--timeout", type=positive, default=120, help="seconds allowed per short tool invocation (default 120)")
    parser.add_argument("--full-timeout", type=positive, default=7200, help="seconds per whole-file operation, including subtitle extraction (default 7200)")
    for tool in ("ffprobe", "ffmpeg", "dovi-tool", "hdr10plus-tool"):
        parser.add_argument("--"+tool, help="explicit path to " + tool)
    parser.add_argument("--report", type=Path, metavar="PREFIX", help="save PREFIX.txt and PREFIX.json; refuses to replace existing files")
    parser.add_argument("--json", action="store_true", help="emit JSON to standard output")
    parser.add_argument("--redact-name", action="store_true", help="replace media and sidecar names in the shareable report")
    parser.add_argument("--quiet", action="store_true", help="suppress scan progress on standard error")
    return parser


def finalize(report):
    # Collapse identical findings repeated in several samples, without discarding
    # their per-window measurements in metrics. Different evidence remains visible.
    seen, unique = set(), []
    for item in report["findings"] + report.get("diagnostics", []):
        item = {k: v for k, v in item.items() if k != "category"}
        key = json.dumps(item, sort_keys=True, ensure_ascii=True)
        if key not in seen:
            seen.add(key)
            unique.append(item)
    focused = report.get("profile") == "focused"
    if report.get("profile"):
        unique, diagnostics = split_findings(unique, advanced=not focused)
        report["diagnostics"] = diagnostics
    report["findings"] = unique
    counts = Counter(f["severity"] for f in unique)
    report["counts"] = {key: counts[key] for key in ("error", "warning", "skipped", "info")}
    report["assessment"] = ("Errors detected; investigate" if counts["error"] else
                            "Warnings need review; file damage is not established" if counts["warning"] else
                            "No errors or warnings detected; some checks are incomplete" if counts["skipped"] else
                            "No problems detected in completed checks")
    report["exit_code"] = 2 if counts["error"] else 1 if counts["warning"] or counts["skipped"] else 0
    report["interpretation"] = "Advisory file analysis, not Plex certification. Warnings may be heuristics, not confirmed defects. Skipped checks are not passes."
    if focused:
        report["assessment"] = (
            "File errors detected; investigate before converting" if counts["error"] else
            "Potential playback issues found; review the items below" if counts["warning"] else
            "Check incomplete; no clean result" if counts["skipped"] else
            "No major problems found in metadata checks" if report.get("mode") == "quick" else
            "No major file problems found in completed checks")
        report["interpretation"] = "File errors and strong warning signs only. Device support notes are conditional, not file defects."
        report["scope"] = scope_note(report)
    return report


def _detailed_text_report(report):
    lines = ["PLEX FILE CHECK " + __version__, "File: " + report["file"],
             "Mode: " + report["mode"], "Assessment: " + report["assessment"], report["interpretation"], ""]
    counts = report.get("counts", {})
    lines.append("Summary: {} errors | {} warnings | {} not checked/incomplete | {} informational".format(
        counts.get("error", 0), counts.get("warning", 0), counts.get("skipped", 0), counts.get("info", 0)))
    if report.get("generated_utc"):
        lines.append("Scan started (UTC): " + report["generated_utc"])
    meta = report["metrics"].get("metadata", {})
    size = report["metrics"].get("file_size_bytes")
    if size is not None:
        lines.append("File size: {:,} bytes ({:.2f} MiB)".format(size, size/1048576))
    identity = report["metrics"].get("input_identity", {})
    if identity.get("modified_utc"):
        lines.append("File modified (UTC): " + identity["modified_utc"])
        lines.append("Size and modification time help identify a copy; they are not a content hash or source history.")
    if meta.get("video_codec"):
        lines.append("Video: {} | {} x {} | {} | {:.2f} seconds".format(meta["video_codec"],
                     meta.get("width", "?"), meta.get("height", "?"), meta.get("pixel_format", "unknown"),
                     meta.get("duration_seconds") or 0))
        lines.append("Tracks: {} video, {} audio, {} subtitle".format(meta.get("video_streams", 0),
                     meta.get("audio_streams", 0), meta.get("subtitle_streams", 0)))
    packet = report["metrics"].get("packets", {})
    if "sampled_video_peak_1s_mbps" in packet:
        lines.append("Sampled video bitrate: average {:.2f} Mbps; one-second peak {:.2f} Mbps".format(
            packet["sampled_video_average_mbps"], packet["sampled_video_peak_1s_mbps"]))
    for severity, label in (("error", "ERROR"), ("warning", "WARNING"), ("skipped", "NOT CHECKED / INCOMPLETE"), ("info", "INFO / CONTEXT (not failures)")):
        items = [f for f in report["findings"] if f["severity"] == severity]
        if items:
            lines += ["", label]
        for item in items:
            lines += ["- [{}] {}".format(item["code"], item["title"]), "  " + item["detail"]]
            if item.get("evidence"):
                lines.append("  Evidence: " + json.dumps(item["evidence"], ensure_ascii=True, sort_keys=True))
            if item.get("advice"):
                lines.append("  Next step: " + item["advice"])
    if report.get("diagnostics"):
        lines += ["", "ADDITIONAL DIAGNOSTICS (not counted in the focused result)"]
        for item in report["diagnostics"]:
            lines += ["- [{}] {}".format(item["code"], item["title"]), "  " + item["detail"]]
            if item.get("evidence"):
                lines.append("  Evidence: " + json.dumps(item["evidence"], ensure_ascii=True, sort_keys=True))
    if report.get("scope"):
        lines += ["", "Scope: " + report["scope"]]
    lines += ["", "COVERAGE"]
    for item in report["coverage"]:
        lines.append("- {}: {}".format(item["check"], item["status"]))
    lines += ["", "TOOLS"]
    for tool, version in report["tools"].items():
        lines.append("- {}: {}".format(tool, version))
    lines += ["", "Exit code: {} (0=no flags; 1=warnings or incomplete checks; 2=errors; 3=unable to run/save)".format(report["exit_code"])]
    return "\n".join(lines) + "\n"


_SHORT_TEXT = {
    "hdr_mastering_conflict": "HDR brightness/color metadata disagrees between records. Different players may interpret it differently; this does not prove buffering or damaged video.",
    "hdr_mastering_invalid": "HDR mastering metadata contains invalid or inconsistent values. Verify the intended values before changing brightness/color tags; this can also occur in an original source.",
    "hdr_light_levels_invalid": "HDR light-level values are inconsistent. Verify the intended values; this alone does not establish a playback failure or show which process introduced them.",
    "subtitle_bitmap": "If an image-based subtitle is selected and your player cannot display it, Plex may have to burn it into the video, requiring video transcoding. Try a text subtitle or turn subtitles off if playback struggles.",
}

_DV_PROFILE_TEXT = {
    5: "Dolby Vision Profile 5 has no HDR10-compatible base layer. Use a player with suitable Dolby Vision support or a supported conversion path.",
    7: "Dolby Vision Profile 7 uses a dual-layer format. Full Dolby Vision playback depends on the player's enhancement-layer support; its HDR10-compatible base may provide fallback. MEL/FEL type and player support are not verified. This note does not mean the file needs conversion.",
}


def text_report(report, details=False):
    if details or report.get("profile") != "focused":
        return _detailed_text_report(report)
    lines = ["PLEX FILE CHECK " + __version__, "File: " + report["file"],
             "Result: " + report["assessment"]]
    meta = report.get("metrics", {}).get("metadata", {})
    if meta.get("video_codec"):
        lines.append("Video: {} | {} x {} | {}".format(
            meta["video_codec"], meta.get("width", "?"), meta.get("height", "?"),
            meta.get("pixel_format") or "unknown pixel format"))
    counts = report.get("counts", {})
    lines.append("{} file errors | {} to review | {} incomplete".format(
        counts.get("error", 0), counts.get("warning", 0), counts.get("skipped", 0)))
    groups = (("file_error", "FILE ERRORS"), ("review", "REVIEW"),
              ("device_support", "DEVICE SUPPORT"), ("incomplete", "INCOMPLETE"))
    for category, label in groups:
        grouped = {}
        for item in report["findings"]:
            if item.get("category") == category:
                if item["code"] == "video_limited_hardware_support":
                    key = (item["code"], item.get("evidence", {}).get("stream"))
                elif item["code"] == "dolby_vision_profile":
                    key = (item["code"], item.get("evidence", {}).get("profile"))
                else:
                    key = item["code"]
                grouped.setdefault(key, []).append(item)
        if grouped:
            lines += ["", label]
        for items in grouped.values():
            first = items[0]
            code = first["code"]
            suffix = " ({} observations)".format(len(items)) if len(items) > 1 else ""
            if code == "video_limited_hardware_support":
                suffix += " (video stream {})".format(first.get("evidence", {}).get("stream", "?"))
            title = first["title"]
            detail = _SHORT_TEXT.get(code, first["detail"])
            if code == "dolby_vision_profile":
                profile = first.get("evidence", {}).get("profile")
                if profile in _DV_PROFILE_TEXT:
                    title = "Dolby Vision Profile {} compatibility".format(int(profile))
                    detail = _DV_PROFILE_TEXT[profile]
            lines.append("- " + title + suffix)
            lines.append("  " + detail)
            if first.get("advice") and code not in _SHORT_TEXT:
                lines.append("  Next step: " + first["advice"])
    lines += ["", "Scope: " + report.get("scope", scope_note(report)),
              "Playback also depends on the player and selected tracks.",
              "Use --details for evidence; --advanced runs the broader diagnostic audit."]
    return "\n".join(lines) + "\n"


def redact(report, media):
    replacements = {media.name: "media" + media.suffix}
    # No folder names should enter metrics, but scrub the resolved input as an
    # additional defense and redact matching subtitle names as requested.
    replacements[str(media.resolve())] = "media" + media.suffix
    def walk(value):
        if isinstance(value, str):
            for old, new in sorted(replacements.items(), key=lambda p: -len(p[0])):
                value = value.replace(old, new)
            if media.stem:
                value = value.replace(media.stem, "media")
            return value
        if isinstance(value, list):
            return [walk(v) for v in value]
        if isinstance(value, dict):
            return {k: walk(v) for k, v in value.items()}
        return value
    return walk(report)


def main(argv=None):
    args = make_parser().parse_args(argv)
    tools = {"ffprobe": locate("ffprobe", args.ffprobe), "ffmpeg": locate("ffmpeg", args.ffmpeg),
             "dovi_tool": locate("dovi_tool", args.dovi_tool),
             "hdr10plus_tool": locate("hdr10plus_tool", args.hdr10plus_tool)}
    if not tools["ffprobe"]:
        print("FFprobe was not found. Install FFmpeg (which includes FFprobe), place the binaries in tools/, or use --ffprobe PATH.", file=sys.stderr)
        return 3
    if args.reference and not args.reference.expanduser().is_file():
        print("The reference must be a readable local file.", file=sys.stderr)
        return 3
    outputs = []
    if args.report:
        prefix = args.report.expanduser().resolve()
        outputs = [Path(str(prefix)+".txt"), Path(str(prefix)+".json")]
        if any(p.exists() for p in outputs):
            print("A report with that name already exists; choose a new --report prefix.", file=sys.stderr)
            return 3
        if any(p == args.file.expanduser().resolve() or (args.reference and p == args.reference.expanduser().resolve()) for p in outputs):
            print("Report paths must differ from the media/reference files.", file=sys.stderr)
            return 3
    progress = (lambda s: None) if args.quiet else (lambda s: print(s, file=sys.stderr, flush=True))
    try:
        report = scan(args.file, tools, mode="quick" if args.quick else "deep" if args.deep else "standard",
                      timeout=args.timeout, full_timeout=args.full_timeout, bandwidth_mbps=args.bandwidth_mbps,
                      reference=args.reference.expanduser().resolve() if args.reference else None,
                      expected_runtime=args.expected_runtime, visual=not args.no_visual, dovi=args.dovi,
                      progress=progress, redact_name=args.redact_name, loudness=args.loudness, hdr10plus=args.hdr10plus,
                      advanced=args.advanced)
        if args.redact_name:
            report = redact(report, args.file)
        finalize(report)
        text = text_report(report, details=args.details or args.advanced)
        serialized = json.dumps(report, indent=2, ensure_ascii=True, allow_nan=False) + "\n"
        if outputs:
            for output in outputs:
                output.parent.mkdir(parents=True, exist_ok=True)
            # Exclusive create makes even a race with another scan non-destructive.
            for output, content in zip(outputs, (text, serialized)):
                with output.open("x", encoding="utf-8", newline="\n") as handle:
                    handle.write(content)
            progress("Reports saved to {} and {}".format(*outputs))
        print(serialized if args.json else text, end="")
        return report["exit_code"]
    except KeyboardInterrupt:
        print("Scan cancelled. The media file was not changed.", file=sys.stderr)
        return 130
    except (OSError, ValueError, RuntimeError) as exc:
        print("The scan could not complete ({}). Check file permissions, tool paths, and available disk space.".format(type(exc).__name__), file=sys.stderr)
        return 3
