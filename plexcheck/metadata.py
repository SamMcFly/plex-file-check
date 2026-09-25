"""Conservative, pure ffprobe metadata checks; no file or network access.

Absent metadata is different from invalid metadata. Compatibility notes describe
possible client requirements, never guarantee a Plex client will or will not play.
"""
import math
import re

from .common import number, finding, video_streams, duration_of


_UNKNOWN = {"", "unknown", "unspecified", "n/a", "none"}
_HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}
_BITMAP_SUBS = {"hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle", "xsub"}
_TEXT_SUBS = {"subrip", "srt", "ass", "ssa", "webvtt", "mov_text", "text",
              "ttml", "microdvd", "sami", "realtext", "subviewer", "subviewer1"}
_MASTERING_FIELDS = ("red_x", "red_y", "green_x", "green_y", "blue_x", "blue_y",
                     "white_point_x", "white_point_y", "max_luminance", "min_luminance")
# One unit of HEVC mastering metadata quantization, plus a small allowance for
# floating container representations. These are comparison tolerances, not
# permission to ignore a malformed or out-of-range value in an individual record.
_MASTERING_REL_TOLERANCE = 1e-6
_MASTERING_XY_TOLERANCE = 2e-5
_MASTERING_NITS_TOLERANCE = 1e-4


def _text(value):
    return str(value or "").strip().lower()


def _present(value):
    return value is not None and _text(value) not in _UNKNOWN


def _flag(stream, name):
    return number((stream.get("disposition") or {}).get(name), 0) == 1


def _tags(stream):
    return {str(k).lower(): v for k, v in (stream.get("tags") or {}).items()}


def _stream_duration(stream):
    """A stream/tag duration is not replaced by the container duration."""
    direct = number(stream.get("duration"))
    if direct is not None:
        return direct, "stream.duration"
    tags = _tags(stream)
    for key in ("duration", "duration-eng"):
        raw = tags.get(key)
        if raw is not None:
            match = re.fullmatch(r"(\d+):(\d{2}):(\d{2}(?:\.\d+)?)", str(raw).strip())
            if match:
                hours, minutes, seconds = map(float, match.groups())
                if minutes < 60 and seconds < 60:
                    return hours * 3600 + minutes * 60 + seconds, "tags." + key
    return None, None


def _bit_depth(stream):
    declared = number(stream.get("bits_per_raw_sample"))
    if declared is not None and declared > 0:
        return int(declared)
    fmt = _text(stream.get("pix_fmt"))
    match = re.search(r"(?:p0|p2|p4)(10|12|16)(?:le|be)?$", fmt)
    if not match:
        match = re.search(r"(?:p|gray|gbrp|gbrap)(9|10|12|14|16)(?:le|be)$", fmt)
    if match:
        return int(match.group(1))
    if fmt in {"nv12", "nv21", "yuv420p", "yuv422p", "yuv444p", "yuvj420p",
               "yuvj422p", "yuvj444p", "rgb24", "bgr24", "gray", "gbrp"}:
        return 8
    return None


def _color_class(stream):
    transfer = _text(stream.get("color_transfer"))
    depth = _bit_depth(stream)
    definite = transfer in _HDR_TRANSFERS
    candidate = (transfer in _UNKNOWN and "bt2020" in _text(stream.get("color_primaries"))
                 and depth is not None and depth >= 10)
    return definite, candidate, depth


def _side_data_with_sources(stream, frames):
    """Retain probe location; stream/frame does not prove container/SEI origin."""
    records = [(record, {"kind": "stream", "side_data_index": position})
               for position, record in enumerate(stream.get("side_data_list") or [])
               if isinstance(record, dict)]
    index = stream.get("index")
    for frame_index, frame in enumerate(frames or []):
        if not isinstance(frame, dict) or frame.get("media_type", "video") != "video":
            continue
        if index is not None and frame.get("stream_index") is not None and frame["stream_index"] != index:
            continue
        for position, record in enumerate(frame.get("side_data_list") or []):
            if isinstance(record, dict):
                source = {"kind": "frame", "frame_index": frame_index, "side_data_index": position}
                timestamp = number(frame.get("pts_time"))
                if timestamp is not None:
                    source["pts_seconds"] = timestamp
                records.append((record, source))
    return records


def _mastering_checks(entries, index, findings, metrics):
    """Check each distinct record; never merge partial records into a full one.

    Legacy max/min metrics describe one selected record, not every observation.
    Prefer a complete record with consistent numeric values, with frames winning
    ties. Otherwise choose the most numeric fields, then consistent values, then
    a frame. The selected record ID and all other records remain in metrics.
    """
    if not entries:
        return
    records, seen = [], {}
    for raw, source in entries:
        values = {key: number(raw[key]) for key in _MASTERING_FIELDS if key in raw}
        missing = [key for key in _MASTERING_FIELDS if key not in raw]
        unreadable = [key for key, value in values.items() if value is None]
        bad = list(unreadable)
        for key, value in values.items():
            if value is not None and key not in {"max_luminance", "min_luminance"} and not 0 <= value <= 1:
                bad.append(key)
        for prefix in ("red", "green", "blue", "white_point"):
            x, y = values.get(prefix + "_x"), values.get(prefix + "_y")
            if x is not None and y is not None and x + y > 1.00001:
                bad.append(prefix + "_xy_sum")
        maximum, minimum = values.get("max_luminance"), values.get("min_luminance")
        if maximum is not None and not 0 < maximum <= 10000:
            bad.append("max_luminance")
        if minimum is not None and (not 0 <= minimum <= 10000 or (maximum is not None and minimum > maximum)):
            bad.append("min_luminance")

        # Validate before deduplication. Equivalent rationals normalize exactly;
        # tolerances are deliberately reserved for comparisons below so they can
        # never conceal an invalid value near a range boundary.
        signature = (source["kind"], tuple((key in raw, values.get(key)) for key in _MASTERING_FIELDS),
                     tuple(sorted(set(bad))))
        if signature in seen:
            entry = records[seen[signature]]
            entry["occurrences"] += 1
            entry["last_source"] = source
            continue
        seen[signature] = len(records)
        records.append(dict(record=len(records) + 1, source=source, occurrences=1,
                            values=values, missing_fields=missing, unreadable_fields=unreadable,
                            invalid_fields=sorted(set(bad)), complete=not missing and not unreadable))

    def preference(record):
        numeric_count = sum(value is not None for value in record["values"].values())
        consistent = not record["invalid_fields"]
        return (record["complete"] and consistent, numeric_count, consistent,
                record["source"]["kind"] == "frame")

    selected = max(records, key=preference)
    metrics.update(mastering_records=records, mastering_record_count=len(records),
                   mastering_observation_count=sum(record["occurrences"] for record in records),
                   mastering_selected_record=selected["record"],
                   mastering_selection_policy="Prefer complete consistent records and frame provenance; otherwise prefer more numeric fields, then consistent values, then frame provenance. No fields are merged across records.",
                   mastering_max_nits=selected["values"].get("max_luminance"),
                   mastering_min_nits=selected["values"].get("min_luminance"))
    for record in records:
        if record["invalid_fields"]:
            findings.append(finding("hdr_mastering_invalid", "warning", "HDR mastering values need review",
                                    "A reported value is unreadable, outside the checker's expected HDR range, or inconsistent with another value in the same record. Other valid records do not resolve this observation.",
                                    "Compare the reported stream and frame metadata before changing the file.",
                                    stream=index, record=record["record"], source=record["source"],
                                    fields=record["invalid_fields"], unreadable_fields=record["unreadable_fields"],
                                    values=record["values"]))
    if not any(record["complete"] for record in records):
        findings.append(finding("hdr_mastering_incomplete", "info", "Only partial HDR mastering records were observed",
                                "No single supplied record contains every numeric mastering field. Stream and frame records may omit optional fields; this alone does not establish malformed metadata or an HDR playback problem.",
                                stream=index, records=[dict(record=record["record"], source=record["source"],
                                                           missing_fields=record["missing_fields"],
                                                           unreadable_fields=record["unreadable_fields"])
                                                       for record in records]))

    conflicts = {}
    for key in _MASTERING_FIELDS:
        comparable = [(record["values"][key], record) for record in records
                      if record["values"].get(key) is not None]
        if len(comparable) < 2:
            continue
        low, low_record = min(comparable, key=lambda pair: pair[0])
        high, high_record = max(comparable, key=lambda pair: pair[0])
        tolerance = _MASTERING_NITS_TOLERANCE if "luminance" in key else _MASTERING_XY_TOLERANCE
        if not math.isclose(low, high, rel_tol=_MASTERING_REL_TOLERANCE, abs_tol=tolerance):
            conflicts[key] = dict(minimum=low, maximum=high,
                                  minimum_record=low_record["record"], maximum_record=high_record["record"],
                                  minimum_source=low_record["source"], maximum_source=high_record["source"])
    metrics["mastering_conflicts"] = conflicts
    if conflicts:
        findings.append(finding("hdr_mastering_conflict", "warning", "HDR mastering records disagree",
                                "Comparable numeric fields differ across supplied stream or frame records beyond the comparison tolerance. This can reflect conflicting signaling or changes within the sample; it does not by itself establish corrupted video.",
                                "Review the source-specific records and intended playback before changing metadata.",
                                stream=index, fields=sorted(conflicts), differences=conflicts,
                                relative_tolerance=_MASTERING_REL_TOLERANCE,
                                chromaticity_absolute_tolerance=_MASTERING_XY_TOLERANCE,
                                luminance_absolute_tolerance_nits=_MASTERING_NITS_TOLERANCE))


def _hdr_checks(stream, frames, findings, metrics):
    index = stream.get("index")
    definite, candidate, depth = _color_class(stream)
    transfer = _text(stream.get("color_transfer"))
    primaries = _text(stream.get("color_primaries"))
    matrix = _text(stream.get("color_space"))
    entries = _side_data_with_sources(stream, frames)
    records = [record for record, source in entries]
    dovi = [s for s in records if "dovi configuration" in _text(s.get("side_data_type"))]
    dv_frames = [s for s in records if "dolby vision" in _text(s.get("side_data_type"))]
    masters = [s for s in records if "mastering display" in _text(s.get("side_data_type"))]
    lights = [s for s in records if "content light level" in _text(s.get("side_data_type"))]
    dynamic = [s for s in records if re.search(r"2094.40|hdr10\+|hdr dynamic metadata smpte2094-40",
                                             _text(s.get("side_data_type")))]
    metrics.update(hdr=definite, hdr_candidate=candidate, bit_depth=depth,
                   color_transfer=transfer or None, color_primaries=primaries or None,
                   color_space=matrix or None, dolby_vision=bool(dovi or dv_frames),
                   hdr10plus_observed=bool(dynamic), mastering_metadata_observed=bool(masters),
                   content_light_metadata_observed=bool(lights))
    if candidate:
        findings.append(finding("hdr_transfer_missing", "warning", "Possible HDR with missing transfer tag",
                                "BT.2020 primaries and at least 10-bit video suggest HDR, but do not establish its transfer curve.",
                                "Check the source/mastering information before changing color tags.", stream=index, bit_depth=depth))
    if definite:
        if depth is not None and depth < 10:
            findings.append(finding("hdr_low_bit_depth", "warning", "HDR is tagged on video below 10-bit",
                                    "This combination is unusual and may indicate a conversion or tagging problem.",
                                    stream=index, bit_depth=depth, transfer=transfer))
        for name, value, expected in (("primaries", primaries, {"bt2020"}),
                                      ("matrix", matrix, {"bt2020nc", "bt2020c", "ictcp", "rgb"})):
            if value in _UNKNOWN:
                findings.append(finding("hdr_" + name + "_missing", "warning", "HDR color tag is missing",
                                        "The HDR transfer tag is present, but the %s tag is unspecified." % name,
                                        stream=index, transfer=transfer))
            elif value not in expected:
                findings.append(finding("hdr_" + name + "_unusual", "warning", "Unusual HDR color combination",
                                        "The %s tag is unusual alongside this HDR transfer; verify the source rather than assuming the colors are wrong." % name,
                                        stream=index, value=value, transfer=transfer))
        if transfer == "smpte2084" and not masters:
            findings.append(finding("hdr_mastering_not_observed", "info", "HDR mastering metadata was not observed",
                                    "The supplied stream/frame sample contains no mastering-display record. This is not a full bitstream scan.",
                                    stream=index))
        if transfer == "smpte2084" and not lights:
            findings.append(finding("hdr_light_levels_not_observed", "info", "HDR light-level metadata was not observed",
                                    "MaxCLL/MaxFALL were not present in the supplied metadata sample; absence alone does not establish a bad file.",
                                    stream=index))
    elif masters or lights:
        findings.append(finding("hdr_side_data_without_transfer", "warning", "HDR side data and transfer tags need review",
                                "HDR static side data is present but the video does not explicitly declare PQ or HLG. The side data may be stale or the transfer tag may be missing.",
                                stream=index, transfer=transfer or None))

    _mastering_checks([(record, source) for record, source in entries
                       if "mastering display" in _text(record.get("side_data_type"))], index, findings, metrics)
    for record in lights[:1]:
        cll = number(record.get("max_content", record.get("max_content_light_level")))
        fall = number(record.get("max_average", record.get("max_pic_average_light_level")))
        metrics.update(max_cll=cll, max_fall=fall)
        if cll is None or fall is None:
            findings.append(finding("hdr_light_levels_incomplete", "info", "HDR light-level record is incomplete",
                                    "The record is present but both numeric light levels were not available.", stream=index))
        elif cll < 0 or fall < 0 or cll > 10000 or fall > 10000 or (cll > 0 and fall > cll):
            findings.append(finding("hdr_light_levels_invalid", "warning", "HDR light levels are inconsistent",
                                    "Light levels exceed the PQ range, are negative, or MaxFALL exceeds a known MaxCLL.",
                                    stream=index, max_cll=cll, max_fall=fall))
        elif cll == 0 or fall == 0:
            findings.append(finding("hdr_light_levels_unspecified", "info", "HDR light levels include unspecified values",
                                    "A zero light-level value is treated as unspecified, not as a measurement of black video.",
                                    stream=index, max_cll=cll, max_fall=fall))
    if dovi:
        dv = dovi[0]
        profile = number(dv.get("dv_profile"))
        compatibility = number(dv.get("dv_bl_signal_compatibility_id"))
        metrics.update(dv_profile=profile, dv_compatibility_id=compatibility)
        if profile == 5:
            note = "Dolby Vision Profile 5 has no HDR10-compatible base layer; playback depends on Dolby Vision support or correct tone mapping."
        elif profile == 7:
            note = "Dolby Vision Profile 7 uses a dual-layer format. Support varies by player and device."
        elif profile == 8:
            base = {1: "HDR10", 2: "SDR", 4: "HLG"}.get(compatibility, "unspecified")
            note = "Dolby Vision Profile 8 declares %s base-layer compatibility; support still depends on the player." % base
        else:
            note = "Dolby Vision configuration is present; verify profile support on the intended player."
        findings.append(finding("dolby_vision_profile", "info", "Dolby Vision playback context", note,
                                stream=index, profile=profile, compatibility_id=compatibility))
        if number(dv.get("rpu_present_flag")) == 0:
            findings.append(finding("dolby_vision_rpu_flag", "warning", "Dolby Vision RPU flag needs review",
                                    "A Dolby Vision configuration record declares no RPU metadata. Confirm with a bitstream scan.", stream=index))


def analyze_metadata(probe, frames=None, reference=None):
    """Return (findings, metrics) from ffprobe JSON and optional reference JSON."""
    findings, metrics = [], {}
    if not isinstance(probe, dict):
        return [finding("probe_invalid", "error", "No usable media probe", "Expected a ffprobe JSON object.")], metrics
    streams = [s for s in (probe.get("streams") or []) if isinstance(s, dict)]
    videos = video_streams(dict(probe, streams=streams))
    audios = [s for s in streams if s.get("codec_type") == "audio"]
    subtitles = [s for s in streams if s.get("codec_type") == "subtitle"]
    container = probe.get("format") or {}
    metrics.update(video_streams=len(videos), audio_streams=len(audios), subtitle_streams=len(subtitles),
                   attached_pictures=sum(s.get("codec_type") == "video" and _flag(s, "attached_pic") for s in streams),
                   container=container.get("format_name"), duration_seconds=duration_of(dict(probe, streams=streams)))
    size = number(container.get("size"))
    if size is not None and size <= 0:
        findings.append(finding("file_size_invalid", "error", "Container reports an empty file", "The declared byte size is zero or negative.", bytes=size))
    if not videos:
        findings.append(finding("video_missing", "error", "No playable video stream found", "No video stream remains after excluding attached cover images."))
    elif len(videos) > 1:
        findings.append(finding("multiple_video_streams", "info", "Multiple video streams", "The container has multiple non-cover video streams; confirm the intended default stream.", count=len(videos)))
    duration = metrics["duration_seconds"]
    if duration is None:
        explicit = number(container.get("duration"))
        findings.append(finding("duration_unavailable", "warning" if explicit is not None else "skipped", "Duration is unavailable",
                                "A positive video/container duration was not available. This alone does not prove truncation.",
                                "Use packet/decode scanning to assess the timeline.", declared_duration=explicit))
    for kind, group in (("video", videos), ("audio", audios), ("subtitle", subtitles)):
        defaults = [s.get("index") for s in group if _flag(s, "default")]
        if len(defaults) > 1:
            findings.append(finding("multiple_default_" + kind, "info", "Multiple default %s tracks" % kind,
                                    "Player track selection can vary when several tracks carry the default flag.", streams=defaults))
        for stream in group:
            index, codec = stream.get("index"), _text(stream.get("codec_name"))
            if not _present(codec):
                findings.append(finding(kind + "_codec_unknown", "warning", "A %s codec could not be identified" % kind,
                                        "The probe did not identify a codec. More analysis may distinguish limited probing from malformed data.", stream=index))
    video_duration = None
    if videos:
        main = videos[0]
        video_duration, video_duration_source = _stream_duration(main)
        metrics["video_duration_source"] = video_duration_source
        if video_duration is None or video_duration <= 0:
            video_duration = duration
        metrics.update(video_codec=main.get("codec_name"), pixel_format=main.get("pix_fmt"),
                       width=number(main.get("width")), height=number(main.get("height")))
        for dimension in ("width", "height"):
            val = metrics[dimension]
            if val is None or val <= 0:
                findings.append(finding("video_dimensions_invalid", "warning", "Video dimensions could not be established",
                                        "The %s value is missing, zero, or negative." % dimension, field=dimension, value=val))
        for key in ("r_frame_rate", "avg_frame_rate"):
            metrics[key] = number(main.get(key))
        rf, af = metrics["r_frame_rate"], metrics["avg_frame_rate"]
        if (rf is None or rf <= 0) and (af is None or af <= 0):
            findings.append(finding("frame_rate_unavailable", "skipped", "Frame rate is unavailable",
                                    "Neither a positive declared nor average frame rate was available."))
        elif rf is not None and af is not None and rf > 0 and af > 0 and abs(rf - af) > 0.001:
            findings.append(finding("frame_rates_differ", "info", "Declared and average frame rates differ",
                                    "This can indicate variable frame rate or approximate/container timing; packet timing is needed to distinguish them.", declared=rf, average=af))
        if _text(main.get("field_order")) in {"tt", "bb", "tb", "bt"}:
            findings.append(finding("interlaced_tag", "info", "Video is tagged as interlaced",
                                    "An interlace tag is present; an idet scan can check actual frames and possible telecine.", field_order=main["field_order"]))
        bit_rate = number(main.get("bit_rate"))
        origin = "stream.bit_rate"
        for tag in ("bps", "bps-eng"):
            if not bit_rate or bit_rate <= 0:
                bit_rate, origin = number(_tags(main).get(tag)), "tags." + tag
        metrics.update(video_bitrate=bit_rate if bit_rate and bit_rate > 0 else None,
                       video_bitrate_source=origin if bit_rate and bit_rate > 0 else None,
                       container_bitrate=number(container.get("bit_rate")))
        _hdr_checks(main, frames, findings, metrics)

    if videos and not audios:
        findings.append(finding("audio_missing", "warning", "No audio stream found",
                                "This video may intentionally be silent; compare the intended content or source."))
    audio_metrics = []
    for stream in audios:
        index = stream.get("index")
        channels, sample_rate = number(stream.get("channels")), number(stream.get("sample_rate"))
        length, length_source = _stream_duration(stream)
        bitrate = number(stream.get("bit_rate")) or number(_tags(stream).get("bps")) or number(_tags(stream).get("bps-eng"))
        entry = dict(index=index, codec=stream.get("codec_name"), channels=channels, sample_rate=sample_rate,
                     duration_seconds=length, duration_source=length_source, bitrate=bitrate, language=_tags(stream).get("language"))
        audio_metrics.append(entry)
        if channels is None:
            findings.append(finding("audio_channels_unknown", "skipped", "Audio channel count is unknown", "No numeric channel count was available.", stream=index))
        elif channels <= 0 or channels > 32:
            findings.append(finding("audio_channels_unusual", "warning", "Audio channel count needs review", "The channel count is outside the pipeline's expected 1–32 range.", stream=index, channels=channels))
        if sample_rate is not None and sample_rate <= 0:
            findings.append(finding("audio_sample_rate_invalid", "warning", "Audio sample rate is invalid", "The declared sample rate is not positive.", stream=index, sample_rate=sample_rate))
        if bitrate is not None and (bitrate < 8000 or bitrate > 10000000):
            findings.append(finding("audio_bitrate_unusual", "info", "Unusual declared audio bitrate", "The declared bitrate is outside 8 kbps–10 Mbps; this is a metadata/format observation, not a decode verdict.", stream=index, bitrate=bitrate))
        if length is not None and length < 1:
            findings.append(finding("audio_duration_short", "warning", "Audio track is shorter than one second", "A very short audio stream may be intentional, incomplete, or have inaccurate duration metadata.", stream=index, duration_seconds=length))
        if length is not None and length > 0 and video_duration and video_duration > 0:
            delta = abs(video_duration - length)
            pct = delta / video_duration * 100
            entry["duration_difference_percent"] = pct
            # Tiny timestamp rounding and codec padding should not flag clips.
            if pct > 2 and delta > 2:
                findings.append(finding("audio_video_duration_mismatch", "warning", "Audio and video durations differ",
                                        "Declared track lengths differ; this may reflect a truncated track, intentional alternate audio, or imprecise container tags. It does not prove lip-sync drift.",
                                        "Use packet timing and listen near the beginning and end.", stream=index, audio_seconds=length,
                                        video_seconds=video_duration, difference_seconds=round(delta, 3), difference_percent=round(pct, 3),
                                        substantial=(length < video_duration * .8 or length > video_duration * 1.2)))
        if _text(_tags(stream).get("language")) in {"", "und", "unknown", "undetermined"}:
            findings.append(finding("audio_language_unspecified", "info", "Audio language is unspecified", "Automatic language selection may need a manual track choice.", stream=index))
    metrics["audio"] = audio_metrics
    sub_metrics = []
    for stream in subtitles:
        index, codec = stream.get("index"), _text(stream.get("codec_name"))
        tags = _tags(stream)
        title_forced = "forced" in _text(tags.get("title"))
        forced = _flag(stream, "forced")
        sub_metrics.append(dict(index=index, codec=codec, language=tags.get("language"), forced=forced, title_suggests_forced=title_forced))
        if codec in _BITMAP_SUBS:
            findings.append(finding("subtitle_bitmap", "info", "Image-based subtitle track", "Support varies by client; a selected bitmap subtitle can require conversion or video burn-in.", stream=index, codec=codec))
        elif _present(codec) and codec not in _TEXT_SUBS:
            findings.append(finding("subtitle_codec_unusual", "info", "Subtitle codec needs client-specific review", "The subtitle codec is outside this checker's common text/image formats; this does not establish corruption.", stream=index, codec=codec))
        if title_forced and not forced:
            findings.append(finding("subtitle_forced_title_only", "info", "Forced subtitle title lacks the forced flag", "The title suggests forced dialogue but the forced disposition is unset. Automatic selection may differ.", stream=index))
        if _text(tags.get("language")) in {"", "und", "unknown", "undetermined"}:
            findings.append(finding("subtitle_language_unspecified", "info", "Subtitle language is unspecified", "Automatic language selection may need a manual track choice.", stream=index))
    metrics["subtitles"] = sub_metrics
    if reference is not None:
        if not isinstance(reference, dict):
            findings.append(finding("reference_invalid", "skipped", "Reference metadata is unavailable", "Expected a second ffprobe JSON object."))
        else:
            ref_duration = duration_of(reference)
            if duration and ref_duration and abs(duration - ref_duration) > max(5, ref_duration * .02):
                findings.append(finding("reference_duration_differs", "warning", "Runtime differs from the reference",
                                        "Runtime differs by more than 2% and five seconds. Alternate cuts, trims, speed changes, or damage can cause this; confirm that the reference is the same edition.",
                                        actual_seconds=duration, reference_seconds=ref_duration))
            ref_streams = [s for s in (reference.get("streams") or []) if isinstance(s, dict)]
            for kind, actual in (("audio", audios), ("subtitle", subtitles)):
                expected = sum(s.get("codec_type") == kind for s in ref_streams)
                if len(actual) < expected:
                    findings.append(finding("reference_" + kind + "_count_lower", "warning", "Fewer %s tracks than the reference" % kind,
                                            "Some tracks may have been deliberately omitted. Counts alone cannot establish which track is missing or whether preservation was required.",
                                            actual=len(actual), reference=expected))
    return findings, metrics
