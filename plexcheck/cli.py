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
    depth.add_argument("--quick", action="store_true", help="metadata, container indexing, and sidecar subtitles only")
    depth.add_argument("--deep", action="store_true", help="also scan every packet and fully decode primary video/all audio (can take hours)")
    parser.add_argument("--no-visual", action="store_true", help="skip interlace/cadence and signal-level samples")
    parser.add_argument("--loudness", action="store_true", help="measure each whole audio track (slow; does not change its volume)")
    parser.add_argument("--dovi", action="store_true", help="inspect embedded HEVC Dolby Vision RPUs with optional dovi_tool (not certification)")
    parser.add_argument("--hdr10plus", action="store_true", help="inspect whole-stream HDR10+ scene metadata with optional hdr10plus_tool")
    parser.add_argument("--reference", type=Path, help="optional original file for metadata/runtime comparison, not VMAF")
    parser.add_argument("--expected-runtime", type=positive, metavar="SECONDS", help="known runtime; flag files more than 15%% shorter")
    parser.add_argument("--bandwidth-mbps", type=positive, help="optional sustained connection budget to compare with file video bitrate")
    parser.add_argument("--timeout", type=positive, default=120, help="seconds allowed per short tool invocation (default 120)")
    parser.add_argument("--full-timeout", type=positive, default=7200, help="seconds allowed per full scan (default 7200)")
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
    for item in report["findings"]:
        key = json.dumps(item, sort_keys=True, ensure_ascii=True)
        if key not in seen:
            seen.add(key)
            unique.append(item)
    report["findings"] = unique
    counts = Counter(f["severity"] for f in unique)
    report["counts"] = dict(counts)
    report["assessment"] = ("Errors detected; investigate" if counts["error"] else
                            "Potential playback concerns" if counts["warning"] else
                            "No problems detected in completed checks")
    report["exit_code"] = 2 if counts["error"] else 1 if counts["warning"] or counts["skipped"] else 0
    report["interpretation"] = "Advisory file analysis, not Plex certification. Warnings may be heuristics, not confirmed defects. Skipped checks are not passes."
    return report


def text_report(report):
    lines = ["PLEX FILE CHECK " + __version__, "File: " + report["file"],
             "Mode: " + report["mode"], "Assessment: " + report["assessment"], report["interpretation"], ""]
    meta = report["metrics"].get("metadata", {})
    if report["metrics"].get("file_size_bytes"):
        lines.append("File size: {:.2f} MiB".format(report["metrics"]["file_size_bytes"]/1048576))
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
    for severity, label in (("error", "ERROR"), ("warning", "WARNING"), ("skipped", "NOT CHECKED / INCOMPLETE"), ("info", "INFO")):
        items = [f for f in report["findings"] if f["severity"] == severity]
        if items:
            lines += ["", label]
        for item in items:
            lines += ["- [{}] {}".format(item["code"], item["title"]), "  " + item["detail"]]
            if item.get("evidence"):
                lines.append("  Evidence: " + json.dumps(item["evidence"], ensure_ascii=True, sort_keys=True))
            if item.get("advice"):
                lines.append("  Next step: " + item["advice"])
    lines += ["", "COVERAGE"]
    for item in report["coverage"]:
        lines.append("- {}: {}".format(item["check"], item["status"]))
    lines += ["", "TOOLS"]
    for tool, version in report["tools"].items():
        lines.append("- {}: {}".format(tool, version))
    lines += ["", "Exit code: {} (0=no flags; 1=warnings or incomplete checks; 2=errors; 3=unable to run/save)".format(report["exit_code"])]
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
                      progress=progress, redact_name=args.redact_name, loudness=args.loudness, hdr10plus=args.hdr10plus)
        if args.redact_name:
            report = redact(report, args.file)
        finalize(report)
        text = text_report(report)
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
