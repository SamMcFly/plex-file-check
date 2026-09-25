"""Pure parsers for optional sampled visual and Dolby Vision inspections.

Each idet call consumes one FFmpeg invocation's log, not a concatenation of
different windows. Its final summary is cumulative: earlier summaries must not
be added. Findings describe samples, never a whole-file visual guarantee.

No function starts a process or writes/changes media. Signalstats works in code
values because this API does not know the stream's transfer function or range.
"""
import math
import re

from .common import finding


_IDET = re.compile(
    r"(?P<mode>Multi|Single)\s+frame\s+detection:\s*"
    r"TFF:\s*(?P<tff>\d+)\s+BFF:\s*(?P<bff>\d+)\s+"
    r"Progressive:\s*(?P<progressive>\d+)"
    r"(?:\s+Undetermined:\s*(?P<undetermined>\d+))?",
    re.IGNORECASE,
)
_REPEATED = re.compile(
    r"Repeated\s+Fields:\s*Neither:\s*(\d+)\s+Top:\s*(\d+)\s+Bottom:\s*(\d+)",
    re.IGNORECASE,
)
_FLOAT = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"


def analyze_idet(text, field_order="unknown"):
    """Return (findings, metrics) from one idet run's stderr.

    The >2% and >20% classification thresholds reproduce pipeline screening
    policy. They do not establish corrupt video, telecine, or a need to encode.
    """
    text = str(text or "")
    findings = []
    metrics = {"measured": False, "scope": "sample", "field_order": field_order}
    matches = list(_IDET.finditer(text))
    multi = [m for m in matches if m.group("mode").lower() == "multi"]
    chosen = multi[-1] if multi else (matches[-1] if matches else None)
    if chosen is None:
        findings.append(finding(
            "visual.idet.unavailable", "skipped", "Interlace sample is inconclusive",
            "No readable idet frame-classification summary was returned.",
            "Retain the FFmpeg log and check that video frames reached the idet filter."
        ))
        return findings, metrics

    counts = {k: int(chosen.group(k) or 0)
              for k in ("tff", "bff", "progressive", "undetermined")}
    classified = counts["tff"] + counts["bff"] + counts["progressive"]
    total = classified + counts["undetermined"]
    metrics.update(counts)
    metrics.update(detector=chosen.group("mode").lower(), classified_frames=classified,
                   detected_frames=total, summary_count=len(matches))
    repeated = list(_REPEATED.finditer(text))
    if repeated:
        neither, top, bottom = map(int, repeated[-1].groups())
        rep_total = neither + top + bottom
        metrics["repeated_fields"] = {"neither": neither, "top": top, "bottom": bottom,
                                      "total": rep_total,
                                      "percent": round((top + bottom) / rep_total * 100, 3)
                                      if rep_total else None}
    if not classified:
        findings.append(finding(
            "visual.idet.unclassified", "skipped", "Interlace sample is inconclusive",
            "The detector classified no frames as progressive or interlaced; "
            "zero or undetermined frames are not a clean result.",
            "Check the sample seek and decoder, then inspect a sample with visible motion.",
            **counts
        ))
        return findings, metrics

    percent = (counts["tff"] + counts["bff"]) / classified * 100
    undetermined_percent = counts["undetermined"] / total * 100 if total else 0
    likely_order = ("tff" if counts["tff"] > counts["bff"] else "bff")
    if counts["tff"] == counts["bff"]:
        likely_order = "mixed_or_unknown"
    metrics.update(measured=True, interlaced_percent=round(percent, 3),
                   undetermined_percent=round(undetermined_percent, 3),
                   likely_field_order=likely_order,
                   combing_risk=percent > 2, interlaced_signal=percent > 20)

    if classified < 30 or undetermined_percent > 50:
        findings.append(finding(
            "visual.idet.low_confidence", "info", "Limited interlace evidence",
            "Fewer than 30 classified frames or more than half undetermined frames "
            "limits this sample's reliability.",
            "Inspect additional motion-heavy samples before changing field handling.",
            classified_frames=classified, undetermined_percent=round(undetermined_percent, 3)
        ))

    order = str(field_order or "unknown").strip().lower()
    progressive_flag = order in {"progressive", "prog", "p"}
    interlaced_flag = order in {"tt", "bb", "tb", "bt", "tff", "bff", "interlaced"}
    if percent > 2:
        findings.append(finding(
            "visual.idet.combing", "warning", "Sample contains an interlace/combing signal",
            "{:.2f}% of classified sample frames were tagged interlaced by idet. "
            "This is a visual/playback concern, not proof of file corruption. "
            "Interlace, telecine and detector false positives can produce this signal.".format(percent),
            "Inspect motion at the sampled time; choose deinterlacing or inverse telecine "
            "only after verifying the actual cadence.",
            threshold_percent=2.0, interlaced_percent=round(percent, 3),
            likely_field_order=likely_order
        ))
        if progressive_flag:
            findings.append(finding(
                "visual.idet.progressive_flag_mismatch", "warning", "Progressive flag needs review",
                "The stream is marked progressive, but this sample contains an "
                "interlace/combing signal. Residual combing or a misleading flag is possible.",
                "View a moving scene at the reported sample time; do not change metadata blindly.",
                field_order=field_order, interlaced_percent=round(percent, 3)
            ))
    else:
        findings.append(finding(
            "visual.idet.sample_result", "info", "Low interlace signal in this sample",
            "{:.2f}% of classified sample frames were tagged interlaced. "
            "This does not exclude artifacts elsewhere in the file.".format(percent),
            interlaced_percent=round(percent, 3), threshold_percent=2.0
        ))
        if interlaced_flag:
            findings.append(finding(
                "visual.idet.interlaced_flag_mismatch", "info", "Field flags and sample differ",
                "The stream carries interlace field flags, while this sample was mostly "
                "classified progressive. Static scenes, mixed material or misleading flags "
                "can explain the difference.",
                "Inspect additional moving scenes before deinterlacing or removing field flags.",
                field_order=field_order, interlaced_percent=round(percent, 3)
            ))

    repeated_info = metrics.get("repeated_fields", {})
    if repeated_info.get("top", 0) + repeated_info.get("bottom", 0) > 0:
        findings.append(finding(
            "visual.idet.repeated_fields", "info", "Repeated fields observed",
            "Repeated fields may indicate a cadence worth inspecting, but do not "
            "establish 3:2 telecine. Still scenes and other source patterns can also repeat fields.",
            "Confirm telecine with matched raw and fieldmatch/decimate samples, checking "
            "both residual combing and emitted frame counts.",
            **repeated_info
        ))
    return findings, metrics


def analyze_signalstats(text, bit_depth=10):
    """Report sampled luma code levels; do not infer HDR light levels or damage.

    bit_depth must describe the pixel format actually fed to signalstats.
    Nominal limited-range excursions are informational because valid full-range
    signals and legal signal headroom may exceed those nominal bounds.
    """
    findings = []
    metrics = {"measured": False, "scope": "sample", "bit_depth": bit_depth,
               "maxcll_measured": False, "maxfall_measured": False}
    if isinstance(bit_depth, bool) or not isinstance(bit_depth, int) or not 8 <= bit_depth <= 16:
        findings.append(finding(
            "visual.signalstats.depth_unknown", "skipped", "Luma analysis is inconclusive",
            "The analyzed pixel format's bit depth must be an integer from 8 to 16.",
            "Pass the bit depth of the pixel format actually used by the signalstats filter."
        ))
        return findings, metrics

    values = {key: [] for key in ("YMIN", "YMAX", "YAVG")}
    pattern = re.compile(r"\blavfi\.signalstats\.(YMIN|YMAX|YAVG)\s*=\s*(" + _FLOAT + r")(?![\w.])")
    for match in pattern.finditer(str(text or "")):
        value = float(match.group(2))
        if math.isfinite(value):
            values[match.group(1)].append(value)
    counts = {key: len(samples) for key, samples in values.items()}
    metrics["value_counts"] = counts
    if not all(counts.values()):
        findings.append(finding(
            "visual.signalstats.unavailable", "skipped", "Luma sample is inconclusive",
            "No complete set of YMIN, YMAX and YAVG measurements was returned.",
            "Check that signalstats,metadata=print ran on video and retain its diagnostic log.",
            value_counts=counts
        ))
        return findings, metrics

    maximum = (1 << bit_depth) - 1
    nominal_low, nominal_high = 16 << (bit_depth - 8), 235 << (bit_depth - 8)
    ymin, ymax = min(values["YMIN"]), max(values["YMAX"])
    avg_min, avg_max = min(values["YAVG"]), max(values["YAVG"])
    incomplete = len(set(counts.values())) != 1
    metrics.update(measured=True, complete_measurements=not incomplete,
                   samples=min(counts.values()), minimum_y=ymin, maximum_y=ymax,
                   min_frame_average_y=avg_min, max_frame_average_y=avg_max,
                   full_range_min=0, full_range_max=maximum,
                   nominal_limited_min=nominal_low, nominal_limited_max=nominal_high)
    if incomplete:
        findings.append(finding(
            "visual.signalstats.partial", "info", "Partial luma measurements",
            "YMIN, YMAX and YAVG have different sample counts; extrema are reported "
            "without assuming frame-by-frame correspondence.",
            value_counts=counts
        ))
    impossible = any(v < 0 or v > maximum for samples in values.values() for v in samples)
    if impossible:
        findings.append(finding(
            "visual.signalstats.depth_mismatch", "warning", "Luma values do not fit the supplied depth",
            "Some measured values fall outside 0..{} for {}-bit samples. "
            "A pixel-format or measurement mismatch should be ruled out first.".format(maximum, bit_depth),
            "Verify the format actually entering signalstats; this alone does not prove damaged HDR.",
            minimum_y=ymin, maximum_y=ymax, bit_depth=bit_depth
        ))
    elif ymin < nominal_low or ymax > nominal_high:
        findings.append(finding(
            "visual.signalstats.nominal_range", "info", "Luma extends beyond nominal limited range",
            "Sample extrema {}..{} extend beyond nominal {}-bit limited-range {}..{}. "
            "Full-range encoding or signal headroom can explain this; brightness and "
            "these excursions alone are not evidence of bad HDR.".format(
                ymin, ymax, bit_depth, nominal_low, nominal_high),
            "Compare the stream's range signaling and decoded appearance before drawing a conclusion.",
            minimum_y=ymin, maximum_y=ymax,
            nominal_limited_min=nominal_low, nominal_limited_max=nominal_high
        ))
    findings.append(finding(
        "visual.signalstats.sample_result", "info", "Sampled luma measurements collected",
        "Measured code levels in sampled frames. These do not establish the correct "
        "HDR transfer function, display appearance, clipping, or whole-program MaxCLL/MaxFALL.",
        samples=min(counts.values()), minimum_y=ymin, maximum_y=ymax,
        min_frame_average_y=avg_min, max_frame_average_y=avg_max
    ))
    return findings, metrics


def analyze_dovi_summary(text):
    """Parse dovi_tool info -s, reporting structure without source preservation claims."""
    text = str(text or "")
    findings = []
    metrics = {"measured": False, "frame_alignment_verified": False,
               "source_preservation_verified": False}
    frames_match = re.search(r"(?im)^\s*Frames:\s*(\d+)\s*$", text)
    profile_match = re.search(r"(?im)^\s*Profile:\s*(\d+)\s*$", text)
    frames = int(frames_match.group(1)) if frames_match else None
    profile = int(profile_match.group(1)) if profile_match else None
    metrics.update(rpu_frames=frames, profile=profile)
    if frames is None or profile is None or frames <= 0 or profile <= 0:
        findings.append(finding(
            "visual.dovi.unavailable", "skipped", "Dolby Vision payload summary is inconclusive",
            "A positive RPU frame count and profile could not both be parsed. "
            "This is not proof that the file contains no Dolby Vision metadata.",
            "Check dovi_tool extraction and its complete summary output.",
            rpu_frames=frames, profile=profile
        ))
        return findings, metrics
    metrics["measured"] = True
    metrics["has_l6"] = False
    # Restrict parsing to the actual Level 6 block. An L1 scene maximum or
    # a different metadata level must never be reported as static mastering.
    l6 = re.search(r"(?ims)^\s*L6\s+metadata:\s*(.*?)(?=^\s*L\d+\b|\Z)", text)
    if l6:
        block = l6.group(1)
        mastering = re.search(r"Mastering\s+display:\s*(" + _FLOAT + r")\s*/\s*(" + _FLOAT + r")\s*nits", block, re.I)
        light = re.search(r"MaxCLL:\s*(" + _FLOAT + r")\s*nits\s*,\s*MaxFALL:\s*(" + _FLOAT + r")\s*nits", block, re.I)
        metrics["has_l6"] = bool(mastering or light)
        if mastering:
            minimum, maximum = map(float, mastering.groups())
            metrics.update(mastering_min_nits=minimum, mastering_max_nits=maximum)
            if not all(math.isfinite(v) for v in (minimum, maximum)) or minimum < 0 or maximum < minimum:
                findings.append(finding(
                    "visual.dovi.mastering_inconsistent", "warning", "Dolby Vision mastering values need review",
                    "Parsed Level 6 mastering luminance bounds are negative, nonfinite or reversed.",
                    "Inspect the extracted payload with dovi_tool; do not replace metadata automatically.",
                    mastering_min_nits=minimum, mastering_max_nits=maximum
                ))
        if light:
            maxcll, maxfall = map(float, light.groups())
            metrics.update(maxcll_nits=maxcll, maxfall_nits=maxfall)
            if not all(math.isfinite(v) for v in (maxcll, maxfall)) or maxcll < 0 or maxfall < 0:
                findings.append(finding(
                    "visual.dovi.light_invalid", "warning", "Dolby Vision light metadata needs review",
                    "Level 6 MaxCLL/MaxFALL contain negative or nonfinite values.",
                    maxcll_nits=maxcll, maxfall_nits=maxfall
                ))
            elif maxcll > 0 and maxfall > maxcll:
                findings.append(finding(
                    "visual.dovi.light_inconsistent", "warning", "Dolby Vision light metadata is inconsistent",
                    "Reported MaxFALL exceeds a nonzero MaxCLL. This is a metadata "
                    "consistency concern, not proof of corrupted video frames.",
                    "Review the source metadata before attempting any correction.",
                    maxcll_nits=maxcll, maxfall_nits=maxfall
                ))
    primaries = re.search(r"(?im)^\s*L9\s+MDP:\s*(.+?)\s*$", text)
    if primaries:
        metrics["mastering_primaries"] = primaries.group(1).strip()

    findings.append(finding(
        "visual.dovi.summary", "info", "Dolby Vision RPU summary parsed",
        "Profile {} with {} RPU entries was reported. Parsing does not establish "
        "per-frame alignment, a correct base layer, or preservation from an original file.".format(profile, frames),
        "Compare the RPU count with an actual video frame count for alignment testing.",
        profile=profile, rpu_frames=frames, has_l6=metrics["has_l6"]
    ))
    if profile == 5:
        findings.append(finding(
            "visual.dovi.profile5_compatibility", "info", "Dolby Vision Profile 5 compatibility",
            "Profile 5 lacks a conventional HDR10 fallback. Playback depends on "
            "Dolby Vision support or a suitable tone-mapping path; this is not file corruption.",
            profile=profile
        ))
    elif profile == 7:
        findings.append(finding(
            "visual.dovi.profile7_compatibility", "info", "Dolby Vision Profile 7 compatibility",
            "Profile 7 enhancement-layer handling varies by player. The RPU summary "
            "does not establish which layers a particular device will use.",
            profile=profile
        ))
    return findings, metrics
