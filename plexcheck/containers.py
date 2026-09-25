"""Bounded, read-only container header and matching SRT sidecar checks.

Payloads are skipped by declared lengths; signatures inside media payloads or
Matroska SeekHead references are never treated as actual elements.
"""
import re
import math
from pathlib import Path

from .common import finding


MAX_ELEMENTS = 100000
MAX_SRT_BYTES = 8 * 1024 * 1024
MAX_SIDECARS = 100
EBML = 0x1A45DFA3
SEGMENT = 0x18538067
CLUSTER = 0x1F43B675
CUES = 0x1C53BB6B
SEGMENT_CHILDREN = {
    0x114D9B74, 0x1549A966, 0x1654AE6B, CLUSTER, CUES,
    0x1941A469, 0x1043A770, 0x1254C367,
}


class _InvalidStructure(Exception):
    pass


class _ScanLimit(Exception):
    pass


def _read_exact(handle, size, end):
    if handle.tell() + size > end:
        raise _InvalidStructure("An element header extends beyond its parent or the file.")
    data = handle.read(size)
    if len(data) != size:
        raise _InvalidStructure("The file ended inside an element header.")
    return data


def _vint(handle, end, identifier=False):
    first = _read_exact(handle, 1, end)[0]
    if first == 0:
        raise _InvalidStructure("An EBML variable-length integer has an invalid leading byte.")
    length = 1
    mask = 0x80
    while not first & mask:
        length += 1
        mask >>= 1
    if length > (4 if identifier else 8):
        raise _InvalidStructure("An EBML integer exceeds the permitted encoded length.")
    value = first if identifier else first & (mask - 1)
    for byte in _read_exact(handle, length - 1, end):
        value = (value << 8) | byte
    if not identifier and value == (1 << (7 * length)) - 1:
        return None
    return value


def _ebml_element(handle, end, budget):
    budget[0] += 1
    if budget[0] > MAX_ELEMENTS:
        raise _ScanLimit("The container element inspection limit was reached.")
    offset = handle.tell()
    element_id = _vint(handle, end, identifier=True)
    size = _vint(handle, end)
    payload = handle.tell()
    if size is not None and payload + size > end:
        raise _InvalidStructure("An EBML element declares data beyond its parent or the file.")
    return element_id, offset, payload, size


def _skip_unknown_cluster(handle, end, budget):
    # Cluster blocks have finite sizes. Looking only at their sibling headers
    # allows a following level-one element to terminate an unknown-sized Cluster.
    while handle.tell() < end:
        element_id, offset, payload, size = _ebml_element(handle, end, budget)
        if element_id in SEGMENT_CHILDREN:
            handle.seek(offset)
            return
        if size is None:
            raise _ScanLimit("An unknown-sized nested Cluster element prevents bounded traversal.")
        handle.seek(payload + size)


def _inspect_matroska(handle, file_size):
    stats = {"kind": "matroska", "complete": False,
             "cluster_count": 0, "cues_count": 0, "seekhead_count": 0}
    findings = []
    budget = [0]
    segments = 0
    first = True
    try:
        while handle.tell() < file_size:
            element_id, offset, payload, size = _ebml_element(handle, file_size, budget)
            if first and element_id != EBML:
                raise _InvalidStructure("The expected EBML header is missing at the beginning of the file.")
            first = False
            if element_id != SEGMENT:
                if size is None:
                    raise _ScanLimit("An unknown-sized element outside Segment prevents bounded traversal.")
                handle.seek(payload + size)
                continue
            segments += 1
            end = payload + size if size is not None else file_size
            while handle.tell() < end:
                child, child_offset, child_payload, child_size = _ebml_element(handle, end, budget)
                if child == CLUSTER:
                    stats["cluster_count"] += 1
                    stats.setdefault("first_cluster_offset", child_offset)
                elif child == CUES:
                    stats["cues_count"] += 1
                    stats.setdefault("first_cues_offset", child_offset)
                elif child == 0x114D9B74:
                    stats["seekhead_count"] += 1
                if child_size is None:
                    if child != CLUSTER:
                        raise _ScanLimit("An unknown-sized Segment child prevents bounded traversal.")
                    _skip_unknown_cluster(handle, end, budget)
                else:
                    handle.seek(child_payload + child_size)
        if not segments:
            raise _InvalidStructure("The file contains no Matroska Segment.")
        stats["complete"] = True
        if stats["cues_count"] and stats["cluster_count"]:
            front = stats["first_cues_offset"] < stats["first_cluster_offset"]
            stats["cues_before_clusters"] = front
            if not front:
                findings.append(finding(
                    "matroska.cues_at_end", "info", "Matroska Cues follow media data",
                    "An actual Cues element was found after the first Cluster. This layout is valid; remote seeking may need an additional read.",
                    "Consider remuxing only if seeking is slow.",
                    cues_offset=stats["first_cues_offset"], cluster_offset=stats["first_cluster_offset"]))
        elif not stats["cues_count"]:
            findings.append(finding(
                "matroska.cues_absent", "warning", "No Matroska Cues element found",
                "A complete top-level traversal found no Cues. Sequential playback may work, but seeking may be slower. SeekHead references alone are not Cues.",
                "If seeking is unreliable, a lossless remux can rebuild the index."))
        if not stats["cluster_count"]:
            findings.append(finding("matroska.clusters_absent", "warning", "No Matroska media Clusters found",
                                    "The inspected Segment contains no media Cluster elements."))
    except _InvalidStructure as exc:
        findings.append(finding("container.structure_invalid", "error", "Invalid or truncated container structure", str(exc)))
    except _ScanLimit as exc:
        findings.append(finding("container.structure_incomplete", "skipped", "Container structure scan is incomplete", str(exc)))
    stats["elements_inspected"] = budget[0]
    stats["segments"] = segments
    return findings, stats


def _mp4_box(handle, end, budget):
    budget[0] += 1
    if budget[0] > MAX_ELEMENTS:
        raise _ScanLimit("The container box inspection limit was reached.")
    offset = handle.tell()
    header = _read_exact(handle, 8, end)
    size = int.from_bytes(header[:4], "big")
    kind = header[4:8]
    header_size = 8
    if size == 1:
        size = int.from_bytes(_read_exact(handle, 8, end), "big")
        header_size = 16
    elif size == 0:
        size = end - offset
    if kind == b"uuid":
        _read_exact(handle, 16, end)
        header_size += 16
    if size < header_size:
        raise _InvalidStructure("An MP4 box is smaller than its own header.")
    if offset + size > end:
        raise _InvalidStructure("An MP4 box declares data beyond its parent or the file.")
    return kind, offset, handle.tell(), offset + size


def _inspect_mp4(handle, file_size):
    findings = []
    stats = {"kind": "mp4", "complete": False, "fragmented": False,
             "moov_count": 0, "mdat_count": 0, "moof_count": 0}
    budget = [0]
    try:
        if file_size == 0:
            raise _InvalidStructure("The file is empty.")
        while handle.tell() < file_size:
            kind, offset, payload, end = _mp4_box(handle, file_size, budget)
            if kind == b"moov":
                stats["moov_count"] += 1
                stats.setdefault("first_moov_offset", offset)
                while handle.tell() < end:
                    child, _, _, child_end = _mp4_box(handle, end, budget)
                    if child == b"mvex":
                        stats["fragmented"] = True
                    handle.seek(child_end)
            elif kind == b"mdat":
                stats["mdat_count"] += 1
                stats.setdefault("first_mdat_offset", offset)
            elif kind == b"moof":
                stats["moof_count"] += 1
                stats["fragmented"] = True
            handle.seek(end)
        stats["complete"] = True
        if not stats["moov_count"]:
            findings.append(finding("mp4.moov_absent", "warning", "No MP4 movie metadata box found",
                                    "No top-level moov box was found. A standalone movie normally needs one; a separate fragment may rely on an initialization segment."))
        if stats["moov_count"] and stats["mdat_count"]:
            stats["moov_before_mdat"] = stats["first_moov_offset"] < stats["first_mdat_offset"]
            if not stats["moov_before_mdat"] and not stats["fragmented"]:
                findings.append(finding("mp4.faststart_absent", "warning", "MP4 movie metadata follows media data",
                                        "The moov box is after the first mdat box. This valid layout can delay playback over a network.",
                                        "A lossless faststart remux can move the metadata to the beginning."))
        if stats["fragmented"]:
            findings.append(finding("mp4.fragmented", "info", "Fragmented MP4 detected",
                                    "A moof box or moov/mvex marker is present. Ordinary moov-before-mdat faststart checks do not establish fragment seekability."))
    except _InvalidStructure as exc:
        findings.append(finding("container.structure_invalid", "error", "Invalid or truncated container structure", str(exc)))
    except _ScanLimit as exc:
        findings.append(finding("container.structure_incomplete", "skipped", "Container structure scan is incomplete", str(exc)))
    stats["boxes_inspected"] = budget[0]
    return findings, stats


def inspect_container(path: Path, format_name: str):
    """Inspect recognized container structure without decoding or modifying it."""
    path = Path(path)
    formats = {part.strip().lower() for part in (format_name or "").split(",")}
    kind = None
    if formats & {"matroska", "webm"}:
        kind = "matroska"
    elif formats & {"mov", "mp4", "m4a", "3gp", "3g2", "mj2"}:
        kind = "mp4"
    if kind is None:
        return [finding("container.structure_unsupported", "skipped", "Container structure check unavailable",
                        "The structural scanner supports Matroska/WebM and MP4/MOV containers.")], {"container_structure": {"kind": "unsupported", "complete": False}}
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            file_size = handle.tell()
            handle.seek(0)
            findings, stats = (_inspect_matroska if kind == "matroska" else _inspect_mp4)(handle, file_size)
            stats["file_bytes"] = file_size
            return findings, {"container_structure": stats}
    except OSError as exc:
        return [finding("container.read_unavailable", "skipped", "Container structure could not be read",
                        "The input file could not be read for structural inspection.", error_type=type(exc).__name__)], {"container_structure": {"kind": kind, "complete": False}}


_TIMESTAMP = re.compile(r"^(\d{2,}):([0-5]\d):([0-5]\d)[,.](\d{3})\s*-->\s*(\d{2,}):([0-5]\d):([0-5]\d)[,.](\d{3})(?:\s+.*)?$")
_CONTROLS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_DECOR = re.compile(r"(:{2,}|={3,}|~{3,}|>{2,}|<{2,}|\*{3,}|-{3,}|#{2,}|_{3,}|[■►▬●▲])")
_CREDIT_ALWAYS = re.compile(r"www\.|https?://|t\.me/|@\w{2,}|\b\w+\.(?:com|org|net|info|me|tv|in)\b|addic7ed|opensubtitles|subscene|yifysubtitles|licdom|1icdom|l1cdom|lic\s?dom|eztv|yts\.(?:mx|am|lt)|yify|rarbg|torrentgalaxy|tgx\.rs|mkvking|pahe\.(?:in|ph)|psarips|stuttershit|raffyone|idn_crew", re.I)
_CREDIT_SOFT = re.compile(r"release\b|rip(?:ped)?\s+by|encoded\s+by|sync(?:ed|hroniz\w+)?\b.{0,25}\bby\b|subs?\s+by|subtitles?\s+by|translat(?:ed|ion)|correct(?:ed|ions?)\b.{0,25}\bby\b|resync|traduzione|sottotitoli|\bpresents\b|a\s+\w+\s+release|telegram|nahom", re.I)


def _credit_candidate(text):
    text = re.sub(r"<[^>]+>", "", text).strip()
    if not text:
        return False
    if _CREDIT_ALWAYS.search(text):
        return True
    decorated = bool(_DECOR.search(text))
    shape = len(text.split()) <= 6 or decorated or (bool(re.search(r"[A-Z]", text)) and not re.search(r"[a-z]", text))
    if shape and _CREDIT_SOFT.search(text):
        return True
    symbols = sum(not (ch.isalpha() or ch.isdecimal() or ch.isspace()) for ch in text)
    return decorated and symbols / len(text) >= 0.20


def _milliseconds(groups):
    hour, minute, second, milli = map(int, groups)
    return ((hour * 60 + minute) * 60 + second) * 1000 + milli


def validate_srt(path: Path, duration=None):
    """Validate SRT without editing it or returning cue text in the report."""
    path = Path(path)
    try:
        duration = float(duration)
        if not math.isfinite(duration) or duration <= 0:
            duration = None
    except (TypeError, ValueError, OverflowError):
        duration = None
    findings = []
    report = {"name": path.name, "complete": False, "cue_count": 0}

    def add(code, severity, title, detail, **evidence):
        findings.append(finding(code, severity, title, detail, sidecar=path.name, **evidence))

    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_SRT_BYTES + 1)
        report["bytes_read"] = len(raw)
        if len(raw) > MAX_SRT_BYTES:
            add("srt.size_limit", "skipped", "Subtitle inspection size limit reached",
                "This SRT exceeds the bounded text inspection limit.", limit_bytes=MAX_SRT_BYTES)
            return findings, report
        if raw.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
            encoding = "utf-32"
        elif raw.startswith((b"\xff\xfe", b"\xfe\xff")):
            encoding = "utf-16"
        else:
            encoding = "utf-8-sig"
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            # Historical subtitles use legacy encodings. Do not label unknown
            # encoding as content corruption or expose raw text in diagnostics.
            add("srt.encoding_unknown", "warning", "Subtitle text encoding needs review",
                "The SRT is not valid in its BOM-indicated encoding or UTF-8. Its text could use a legacy encoding.")
            return findings, report
        report["encoding"] = encoding
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        if not text.strip():
            add("srt.empty", "error", "Subtitle file contains no cues", "The SRT is empty or contains only whitespace/BOM.")
            report["complete"] = True
            return findings, report
        if _CONTROLS.search(text) or "\ufffd" in text:
            add("srt.invalid_characters", "error", "Subtitle contains invalid text characters",
                "Binary control characters or encoding replacement characters were detected.")
        blocks = re.split(r"\n[ \t]*\n+", text.strip())
        invalid = empty = backwards = unordered = overlaps = bad_sequences = past_end = 0
        previous_start = None
        greatest_end = None
        cue_texts = []
        for block in blocks:
            lines = block.split("\n")
            timing_index = 1 if lines and re.fullmatch(r"\d+", lines[0].strip()) else 0
            if timing_index == 0:
                bad_sequences += 1
            match = _TIMESTAMP.fullmatch(lines[timing_index].strip()) if len(lines) > timing_index else None
            if not match:
                invalid += 1
                continue
            if any(_TIMESTAMP.fullmatch(line.strip()) for line in lines[timing_index + 1:]):
                # Multiple timing lines in one block usually mean a missing
                # blank separator; do not silently absorb a later cue as text.
                invalid += 1
            start = _milliseconds(match.groups()[:4])
            end = _milliseconds(match.groups()[4:])
            report["cue_count"] += 1
            body = "\n".join(lines[timing_index + 1:]).strip()
            cue_texts.append(body)
            if not re.sub(r"<[^>]+>", "", body).strip():
                empty += 1
            if end <= start:
                backwards += 1
            if duration is not None and end > (duration + 5.0) * 1000:
                past_end += 1
            if previous_start is not None and start < previous_start:
                unordered += 1
            if greatest_end is not None and start < greatest_end:
                overlaps += 1
            previous_start = start
            greatest_end = max(greatest_end or 0, end)
        for count, code, title, detail in (
            (invalid, "srt.invalid_blocks", "Malformed subtitle cue blocks", "Cue blocks have missing, invalid or duplicate timestamp lines; minutes/seconds must be 00 through 59 and timestamps nonnegative."),
            (empty, "srt.empty_cues", "Subtitle cues have no text", "One or more timestamped cues contain no displayable text."),
            (backwards, "srt.nonpositive_duration", "Subtitle cues have invalid durations", "Cue end timestamps must be strictly after their start timestamps."),
            (unordered, "srt.out_of_order", "Subtitle cues are out of time order", "Cue start timestamps decrease within the subtitle file."),
            (past_end, "srt.past_runtime", "Subtitle cues extend past the media runtime", "Cue end timestamps exceed the supplied media duration plus the five-second tolerance."),
        ):
            if count:
                add(code, "error", title, detail, count=count)
        if bad_sequences:
            add("srt.sequence_missing", "warning", "Subtitle sequence numbers are missing",
                "Some cue blocks do not begin with a numeric sequence line.", count=bad_sequences)
        if overlaps:
            add("srt.overlap", "warning", "Subtitle cues overlap",
                "Overlapping cues can be intentional for signs or multiple speakers; review only if rendering is poor.", count=overlaps)
        scan = min(3, len(cue_texts) // 2 + 1)
        positions = set(range(min(scan, len(cue_texts)))) | set(range(max(0, len(cue_texts) - scan), len(cue_texts)))
        credits = sum(_credit_candidate(cue_texts[index]) for index in positions)
        if credits:
            add("srt.credit_candidates", "info", "Possible subtitle credit cues",
                "The first or last few cues match release-credit heuristics. This may include legitimate dialogue; no content was changed.", count=credits)
        report.update(complete=True, invalid_blocks=invalid, empty_cues=empty,
                      nonpositive_durations=backwards, out_of_order=unordered,
                      overlaps=overlaps, credit_candidates=credits, past_runtime=past_end)
    except OSError as exc:
        add("srt.read_unavailable", "skipped", "Subtitle file could not be read",
            "The matching SRT sidecar was unavailable during inspection.", error_type=type(exc).__name__)
    return findings, report


def inspect_sidecars(path: Path):
    """Inspect exact-stem or stem.language SRTs in the media file's directory."""
    path = Path(path)
    findings = []
    reports = []
    matcher = re.compile(re.escape(path.stem) + r"(?:\.[a-z]{2,3}(?:[-_][a-z]{2,4})?)?\.srt$", re.I)
    try:
        count = 0
        for candidate in path.parent.iterdir():
            if not matcher.fullmatch(candidate.name) or not candidate.is_file():
                continue
            if count >= MAX_SIDECARS:
                findings.append(finding("srt.sidecar_limit", "skipped", "Subtitle sidecar count limit reached",
                                        "Additional matching sidecars were not inspected.", limit=MAX_SIDECARS))
                break
            count += 1
            results, report = validate_srt(candidate)
            findings.extend(results)
            reports.append(report)
    except OSError as exc:
        findings.append(finding("srt.directory_unavailable", "skipped", "Subtitle sidecar discovery unavailable",
                                "The media directory could not be inspected.", error_type=type(exc).__name__))
    reports.sort(key=lambda entry: entry["name"].casefold())
    return findings, {"sidecar_srt": {"files": len(reports), "reports": reports}}
