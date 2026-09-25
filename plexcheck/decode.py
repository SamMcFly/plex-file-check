"""Conservative decode diagnostics and one bounded, controlled sample retry."""
import math
import re

from .common import finding
from .process import run_text


# Public patterns are also used by packet-probe diagnostics. Report labels below
# are constants: never copy arbitrary tool output, paths or tokens into reports.
_CORRUPTION_PATTERNS = (
    ("Invalid NAL unit size", r"Invalid NAL unit size"),
    ("Error splitting the input into NAL units", r"Error splitting the input into NAL units"),
    ("Corrupt frame, macroblock or packet", r"corrupt(?:ed)? (?:frame|macroblock|packet)"),
    ("decode_slice_header error", r"decode_slice_header error"),
    ("Concealing decoding errors", r"concealing \d+ errors"),
    ("Failed to decode picture or frame", r"Failed to decode (?:picture|frame)"),
    ("Error while decoding", r"Error while decoding"),
    ("Invalid data found", r"Invalid data found"),
    ("Missing picture", r"missing picture"),
    ("Could not find reference picture", r"Could not find ref with POC"),
    ("Invalid NAL unit", r"Invalid NAL unit(?! size)"),
    ("Packet corrupt", r"Packet corrupt"),
)
_UNSUPPORTED_PATTERNS = (
    ("Unknown decoder", r"Unknown decoder"),
    ("Decoder not found", r"Decoder .* not found"),
    ("No decoder available", r"Decoding requested, but no decoder"),
    ("No such filter", r"No such filter"),
    ("Feature not currently supported", r"not currently supported"),
    ("Feature not yet implemented", r"not yet implemented"),
)
CORRUPTION = re.compile("|".join(pattern for _, pattern in _CORRUPTION_PATTERNS), re.I)
UNSUPPORTED = re.compile("|".join(pattern for _, pattern in _UNSUPPORTED_PATTERNS), re.I)
_KNOWN_PATTERNS = tuple((label, re.compile(pattern, re.I))
                        for label, pattern in _CORRUPTION_PATTERNS + _UNSUPPORTED_PATTERNS)
_PPS_LABEL = "PPS changed between slices"
_PPS = re.compile(r"PPS changed between slices\.?", re.I)
_PREFIX = re.compile(r"^\[[A-Za-z0-9_]+(?: @ (?:0x)?[0-9a-fA-F]+)?\]\s*")
_REPEATED = re.compile(r"Last message repeated \d+ times?", re.I)


def _message(line):
    line = line.strip()
    while _PREFIX.match(line):
        line = _PREFIX.sub("", line, count=1)
    return line


def _diagnostics(result):
    text = result.get("stderr") or ""
    visible_bytes = len(text.encode("utf-8", errors="replace"))
    recorded_bytes = result.get("stderr_bytes")
    byte_count = max(visible_bytes, recorded_bytes if isinstance(recorded_bytes, int) else 0)
    truncated = byte_count > visible_bytes or "[diagnostics truncated]" in text
    counts = {}
    unknown = False
    pps_seen = False
    pps_only = True
    for line in text.splitlines():
        message = _message(line)
        if not message:
            continue
        if _PPS.fullmatch(message):
            pps_seen = True
            counts[_PPS_LABEL] = counts.get(_PPS_LABEL, 0) + 1
        elif _REPEATED.fullmatch(message):
            # Repetition counts cannot reliably be attributed to a particular
            # message; retain only the count of visible diagnostic lines.
            continue
        else:
            pps_only = False
            known = False
            for label, pattern in _KNOWN_PATTERNS:
                occurrences = len(pattern.findall(message))
                if occurrences:
                    known = True
                    counts[label] = counts.get(label, 0) + occurrences
            if not known:
                unknown = True
    return dict(stderr_bytes=byte_count, diagnostic_types=sorted(counts),
                diagnostic_counts=counts, unrecognized_diagnostics=unknown or truncated,
                diagnostics_truncated=truncated), pps_seen, pps_seen and pps_only and not truncated


def check_decode_result(result, offset=None):
    """Classify one run, retaining canonical evidence without raw diagnostics."""
    text = result.get("stderr") or ""
    counters = re.findall(r"^frame=\s*(\d+)\s*$", result.get("stdout") or "", re.M)
    frames = int(counters[-1]) if counters else None
    diagnostics, _, _ = _diagnostics(result)
    metric = dict(start_seconds=offset, decoded_video_frames=frames,
                  exit_code=result.get("returncode"), timed_out=bool(result.get("timed_out")),
                  progress_truncated=bool(result.get("output_truncated")), **diagnostics)
    incomplete = []
    if metric["timed_out"] or metric["progress_truncated"] or UNSUPPORTED.search(text):
        incomplete = [finding("DECODE_INCOMPLETE", "skipped", "Decode check incomplete",
                              "The run timed out, its progress output was truncated, or FFmpeg lacks a required decoder/filter. This does not establish file corruption.",
                              **metric)]
    if CORRUPTION.search(text):
        error_types = [label for label, pattern in _CORRUPTION_PATTERNS if re.search(pattern, text, re.I)]
        return [finding("DECODE_ERRORS", "error", "Decoder reported media errors",
                        "FFmpeg reported structural or decoding errors. Some streams depend on features this decoder may not fully support; verify with a current build and the intended player.",
                        "Inspect the indicated part of the file or compare another copy before attempting a repair.",
                        error_types=sorted(set(error_types)), **metric)] + incomplete, metric
    if incomplete:
        return incomplete, metric
    if metric["exit_code"] != 0 or text.strip() or diagnostics["stderr_bytes"]:
        return [finding("DECODE_FAILED", "warning", "Decode check did not complete cleanly",
                        "FFmpeg returned diagnostics that could indicate a media or decoder limitation. Recognized messages are listed in the evidence; the presence of unrecognized diagnostics is recorded without publishing raw logs. No clean decode claim is made.",
                        **metric)], metric
    if not frames:
        return [finding("DECODE_EMPTY", "warning", "No video frames decoded",
                        "The requested region returned no video frames. Check runtime/timestamps and decoder support.",
                        **metric)], metric
    metric["clean"] = True
    return [], metric


def _retry_args(args):
    """Only a short, single-input sample with an explicit two-thread decoder."""
    args = list(args)
    try:
        input_index = args.index("-i")
        thread_index = args.index("-threads")
        duration_index = args.index("-t")
        seconds = float(args[duration_index + 1])
        if (args.count("-i") != 1 or thread_index >= input_index or
                args[thread_index + 1] != "2" or duration_index <= input_index or
                not math.isfinite(seconds) or not 0 < seconds <= 10):
            return None
    except (ValueError, IndexError, TypeError):
        return None
    args[thread_index + 1] = "1"
    return args


def run_decode_sample(args, timeout, offset=None, progress=lambda s: None):
    """Return (findings, metrics); retry only the known PPS sample diagnostic.

    A matching clean retry produces a warning, never an unconditional clean
    result. Both attempts remain in the metrics, including their original
    findings and frame counts. Full-file decodes must use check_decode_result.
    """
    initial = run_text(args, timeout)
    initial_findings, initial_metric = check_decode_result(initial, offset)
    _, pps_seen, isolated_pps = _diagnostics(initial)
    retry_args = _retry_args(args)
    if (not pps_seen or retry_args is None or initial.get("timed_out") or
            initial.get("output_truncated")):
        return initial_findings, initial_metric

    progress("Repeating the short decode sample with one decoder thread...")
    metric = dict(initial_metric)
    metric.pop("clean", None)
    metric["attempts"] = [dict(decoder_threads=2, metrics=initial_metric, findings=initial_findings)]
    try:
        retry = run_text(retry_args, timeout)
    except OSError:
        metric["attempts"].append(dict(decoder_threads=1, outcome="tool_unavailable",
                                       decoded_video_frames=None))
        metric["thread_retry"] = dict(outcome="tool_unavailable", same_frame_count=False,
                                      single_thread_clean=False)
        return initial_findings + [finding("DECODE_RETRY_UNAVAILABLE", "skipped",
            "Controlled decode retry unavailable",
            "The one-thread sample retry could not be started. The original decode finding remains unresolved.",
            start_seconds=offset)], metric

    retry_findings, retry_metric = check_decode_result(retry, offset)
    metric["attempts"].append(dict(decoder_threads=1, metrics=retry_metric, findings=retry_findings))
    first_frames, retry_frames = initial_metric["decoded_video_frames"], retry_metric["decoded_video_frames"]
    same_frames = bool(first_frames and first_frames == retry_frames)
    retry_clean = bool(retry_metric.get("clean"))
    controlled_success = (isolated_pps and initial_metric["exit_code"] == 0 and
                          retry_metric["exit_code"] == 0 and same_frames and retry_clean)
    outcome = ("thread_dependent" if controlled_success else
               "timed_out" if retry_metric["timed_out"] else
               "frame_count_mismatch" if retry_clean and not same_frames else
               "mixed_initial_diagnostics" if retry_clean and not isolated_pps else
               "initial_run_failed" if retry_clean and initial_metric["exit_code"] != 0 else
               "diagnostics_remain")
    metric["thread_retry"] = dict(outcome=outcome, same_frame_count=same_frames,
                                  single_thread_clean=retry_clean)
    evidence = dict(start_seconds=offset, initial_decoded_video_frames=first_frames,
                    retry_decoded_video_frames=retry_frames,
                    initial_exit_code=initial_metric["exit_code"], retry_exit_code=retry_metric["exit_code"],
                    retry_timed_out=retry_metric["timed_out"], retry_outcome=outcome,
                    initial_diagnostic_types=initial_metric["diagnostic_types"],
                    initial_unrecognized_diagnostics=initial_metric["unrecognized_diagnostics"])
    if controlled_success:
        return [finding("DECODE_THREAD_DEPENDENT", "warning", "Decode diagnostic depends on decoder threading",
            "FFmpeg reported 'PPS changed between slices' with two decoder threads. The controlled one-thread retry exited successfully without diagnostics and decoded the same positive number of video frames. This suggests a decoder-threading interaction; it does not confirm file corruption or establish that every player will decode cleanly.",
            "Compare playback with the intended player and a current FFmpeg build. Both decode attempts remain in the report metrics.",
            **evidence)], metric
    # Do not hide an initial error or a newly observed retry error. Attach an
    # attempt label to the retry evidence so duplicate codes remain explainable.
    for item in retry_findings:
        item["evidence"]["attempt"] = "single_thread_retry"
    return initial_findings + retry_findings + [finding("DECODE_RETRY_UNRESOLVED", "warning",
        "Controlled decode retry did not resolve the finding",
        "The retry did not meet every requirement for the isolated threading-dependent result. The original findings remain; review both attempts and their frame counts.",
        **evidence)], metric
