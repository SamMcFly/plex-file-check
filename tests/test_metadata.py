import copy
import unittest

from plexcheck.metadata import analyze_metadata


def probe(**video_fields):
    video = dict(index=0, codec_type="video", codec_name="hevc", width=1920,
                 height=1080, pix_fmt="yuv420p10le", duration="100",
                 r_frame_rate="24000/1001", avg_frame_rate="24000/1001")
    video.update(video_fields)
    return {"streams": [video, dict(index=1, codec_type="audio", codec_name="aac",
                                    channels=2, sample_rate="48000", duration="100", tags={"language": "eng"})],
            "format": {"duration": "100", "size": "10000000"}}


def codes(result):
    return {f["code"] for f in result[0]}


def mastering(**changes):
    result = dict(side_data_type="Mastering display metadata", red_x="34000/50000", red_y="16000/50000",
                  green_x="13250/50000", green_y="34500/50000", blue_x="7500/50000", blue_y="3000/50000",
                  white_point_x="15635/50000", white_point_y="16450/50000",
                  max_luminance="10000000/10000", min_luminance="50/10000")
    result.update(changes)
    return result


def hdr_probe(records):
    return probe(color_transfer="smpte2084", color_primaries="bt2020", color_space="bt2020nc",
                 side_data_list=records)


class MetadataTests(unittest.TestCase):
    def test_video_compatibility_groups_depth_and_chroma_as_one_info_note(self):
        result = analyze_metadata(probe(codec_name="h264", pix_fmt="yuv444p10le"))
        notes = [item for item in result[0] if item["code"] == "video_limited_hardware_support"]
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]["severity"], "info")
        self.assertEqual(notes[0]["evidence"]["features"], ["10-bit video", "4:4:4 chroma"])
        self.assertEqual(notes[0]["evidence"]["stream"], 0)
        self.assertIn("some systems support it", notes[0]["detail"])
        self.assertIn("does not establish a damaged file", notes[0]["detail"])

    def test_higher_bit_depth_compatibility_note(self):
        for codec, pixel_format, declared, depth in (("h264", "yuv420p10le", None, 10),
                                                    ("hevc", "yuv420p12le", None, 12),
                                                    ("hevc", "p012le", None, 12),
                                                    ("h264", None, "10", 10)):
            with self.subTest(codec=codec, pixel_format=pixel_format, declared=declared):
                result = analyze_metadata(probe(codec_name=codec, pix_fmt=pixel_format, bits_per_raw_sample=declared))
                note = next(item for item in result[0] if item["code"] == "video_limited_hardware_support")
                self.assertEqual(note["evidence"]["bit_depth"], depth)
                self.assertEqual(note["evidence"]["features"], ["%d-bit video" % depth])

    def test_explicit_422_and_444_formats_get_device_note(self):
        for codec in ("h264", "hevc"):
            for pixel_format, chroma in (("yuv422p", "4:2:2"), ("yuvj422p", "4:2:2"),
                                        ("yuv422p10le", "4:2:2"), ("p210le", "4:2:2"),
                                        ("nv16", "4:2:2"), ("yuyv422", "4:2:2"),
                                        ("yuv444p", "4:4:4"), ("yuv444p10be", "4:4:4"),
                                        ("p410le", "4:4:4"), ("nv24", "4:4:4"), ("gbrp", "4:4:4")):
                with self.subTest(codec=codec, pixel_format=pixel_format):
                    result = analyze_metadata(probe(codec_name=codec, pix_fmt=pixel_format))
                    notes = [item for item in result[0] if item["code"] == "video_limited_hardware_support"]
                    self.assertEqual(len(notes), 1)
                    self.assertEqual(notes[0]["evidence"]["chroma"], chroma)

    def test_common_video_formats_and_unestablished_formats_stay_quiet(self):
        for codec, pixel_format, profile in (("h264", "yuv420p", "High"), ("h264", "nv12", None),
                                             ("hevc", "yuv420p10le", "Main 10"), ("hevc", "p010le", None),
                                             ("hevc", "yuv420p", None), ("hevc", None, None),
                                             ("h264", "unknown444", None), ("h264", None, "High 4:4:4 Predictive"),
                                             ("av1", "yuv444p12le", None), ("unknown", "yuv444p10le", None)):
            with self.subTest(codec=codec, pixel_format=pixel_format, profile=profile):
                result = analyze_metadata(probe(codec_name=codec, pix_fmt=pixel_format, profile=profile))
                self.assertNotIn("video_limited_hardware_support", codes(result))

    def test_video_compatibility_checks_each_video_but_excludes_cover_art(self):
        p = probe()
        p["streams"].extend([dict(index=2, codec_type="video", codec_name="h264", pix_fmt="yuv420p10le"),
                              dict(index=3, codec_type="video", codec_name="hevc", pix_fmt="yuv422p10le"),
                              dict(index=4, codec_type="video", codec_name="h264", pix_fmt="yuv444p10le",
                                   disposition={"attached_pic": 1})])
        original = copy.deepcopy(p)
        result = analyze_metadata(p)
        notes = [item for item in result[0] if item["code"] == "video_limited_hardware_support"]
        self.assertEqual([item["evidence"]["stream"] for item in notes], [2, 3])
        self.assertEqual(p, original)

    def test_cover_art_is_not_video(self):
        p = {"streams": [dict(codec_type="video", codec_name="mjpeg", disposition={"attached_pic": 1})]}
        findings, metrics = analyze_metadata(p)
        self.assertIn("video_missing", codes((findings, metrics)))
        self.assertEqual(metrics["video_streams"], 0)
        self.assertEqual(metrics["attached_pictures"], 1)

    def test_explicit_sdr_transfer_does_not_infer_hdr(self):
        result = analyze_metadata(probe(color_primaries="bt2020", color_transfer="bt709"))
        self.assertFalse(result[1]["hdr"])
        self.assertFalse(result[1]["hdr_candidate"])
        self.assertNotIn("hdr_transfer_missing", codes(result))

    def test_missing_transfer_only_suggests_hdr(self):
        result = analyze_metadata(probe(color_primaries="bt2020"))
        self.assertFalse(result[1]["hdr"])
        self.assertTrue(result[1]["hdr_candidate"])
        self.assertIn("hdr_transfer_missing", codes(result))

    def test_hdr_rationals_and_valid_zero_black(self):
        master = dict(side_data_type="Mastering display metadata", red_x="354/500", red_y="146/500",
                      green_x="17/100", green_y="797/1000", blue_x="131/1000", blue_y="46/1000",
                      white_point_x="3127/10000", white_point_y="329/1000", max_luminance="2000/2", min_luminance="0/10000")
        frames = [dict(stream_index=0, side_data_list=[master, dict(side_data_type="Content light level metadata", max_content=0, max_average=0)])]
        result = analyze_metadata(probe(color_transfer="smpte2084", color_primaries="bt2020", color_space="bt2020nc"), frames)
        self.assertEqual(result[1]["mastering_max_nits"], 1000)
        self.assertEqual(result[1]["mastering_min_nits"], 0)
        self.assertNotIn("hdr_mastering_invalid", codes(result))
        self.assertNotIn("hdr_light_levels_invalid", codes(result))
        self.assertIn("hdr_light_levels_unspecified", codes(result))

    def test_inconsistent_hdr_values_are_reported(self):
        frames = [dict(stream_index=0, side_data_list=[dict(side_data_type="Mastering display metadata",
                                                           max_luminance="1000/1", min_luminance="2000/1"),
                                                     dict(side_data_type="Content light level metadata", max_content=500, max_average=1000)])]
        result = analyze_metadata(probe(color_transfer="smpte2084"), frames)
        self.assertIn("hdr_mastering_invalid", codes(result))
        self.assertIn("hdr_light_levels_invalid", codes(result))
        self.assertIn("hdr_mastering_incomplete", codes(result))

    def test_partial_stream_does_not_mask_complete_frame_mastering(self):
        partial = dict(side_data_type="Mastering display metadata", max_luminance="1000/1", min_luminance="1/200")
        result = analyze_metadata(hdr_probe([partial]), [dict(stream_index=0, pts_time="2.5", side_data_list=[mastering()])])
        self.assertNotIn("hdr_mastering_incomplete", codes(result))
        self.assertNotIn("hdr_mastering_invalid", codes(result))
        self.assertNotIn("hdr_mastering_conflict", codes(result))
        metrics = result[1]
        self.assertEqual(metrics["mastering_record_count"], 2)
        self.assertEqual(metrics["mastering_selected_record"], 2)
        self.assertEqual(metrics["mastering_records"][1]["source"]["kind"], "frame")
        self.assertEqual(metrics["mastering_records"][1]["source"]["pts_seconds"], 2.5)
        self.assertEqual(metrics["mastering_max_nits"], 1000)

    def test_optional_partial_mastering_is_information_not_invalid(self):
        partial = dict(side_data_type="Mastering display metadata", max_luminance="1000/1", min_luminance="0/10000")
        result = analyze_metadata(hdr_probe([partial]))
        item = next(f for f in result[0] if f["code"] == "hdr_mastering_incomplete")
        self.assertEqual(item["severity"], "info")
        self.assertNotIn("hdr_mastering_invalid", codes(result))
        self.assertEqual(result[1]["mastering_min_nits"], 0)
        self.assertFalse(result[1]["mastering_records"][0]["complete"])

    def test_extreme_minimum_is_invalid_without_readable_maximum(self):
        for maximum in ({}, {"max_luminance": "1/0"}):
            with self.subTest(maximum=maximum):
                partial = dict(side_data_type="Mastering display metadata", min_luminance="10000000/1", **maximum)
                result = analyze_metadata(hdr_probe([partial]))
                item = next(f for f in result[0] if f["code"] == "hdr_mastering_invalid")
                self.assertIn("min_luminance", item["evidence"]["fields"])

    def test_invalid_stream_mastering_survives_valid_frame(self):
        partial = dict(side_data_type="Mastering display metadata", max_luminance="10000000/1", min_luminance="1/1")
        result = analyze_metadata(hdr_probe([partial]), [dict(stream_index=0, side_data_list=[mastering()])])
        item = next(f for f in result[0] if f["code"] == "hdr_mastering_invalid")
        self.assertEqual(item["evidence"]["source"]["kind"], "stream")
        self.assertEqual(item["evidence"]["values"]["max_luminance"], 10000000)
        self.assertIn("max_luminance", item["evidence"]["fields"])
        self.assertEqual(result[1]["mastering_max_nits"], 1000)
        self.assertNotIn("hdr_mastering_incomplete", codes(result))
        self.assertIn("hdr_mastering_conflict", codes(result))

    def test_conflicting_complete_mastering_records_show_sources(self):
        result = analyze_metadata(hdr_probe([mastering()]),
                                  [dict(stream_index=0, side_data_list=[mastering(max_luminance="2000/1")])])
        item = next(f for f in result[0] if f["code"] == "hdr_mastering_conflict")
        difference = item["evidence"]["differences"]["max_luminance"]
        self.assertEqual(difference["minimum"], 1000)
        self.assertEqual(difference["maximum"], 2000)
        self.assertEqual(difference["minimum_source"]["kind"], "stream")
        self.assertEqual(difference["maximum_source"]["kind"], "frame")
        self.assertNotIn("hdr_mastering_invalid", codes(result))
        self.assertEqual(result[1]["mastering_max_nits"], 2000)

    def test_equivalent_rationals_and_normal_quantization_do_not_conflict(self):
        equivalent = mastering(max_luminance="2000/2", min_luminance="1/200", blue_x="15/100")
        quantized = mastering(max_luminance="1000.0005", min_luminance="0.00505", blue_x="0.15001")
        result = analyze_metadata(hdr_probe([mastering()]),
                                  [dict(stream_index=0, side_data_list=[record]) for record in (equivalent, quantized)])
        self.assertNotIn("hdr_mastering_conflict", codes(result))
        self.assertNotIn("hdr_mastering_invalid", codes(result))
        self.assertEqual(result[1]["mastering_conflicts"], {})

    def test_present_unreadable_mastering_fields_are_not_optional_omissions(self):
        for malformed in (None, "N/A", "nan", "1/0", "not-a-number", True):
            with self.subTest(value=malformed):
                result = analyze_metadata(hdr_probe([mastering(red_x=malformed)]),
                                          [dict(stream_index=0, side_data_list=[mastering()])])
                item = next(f for f in result[0] if f["code"] == "hdr_mastering_invalid")
                self.assertIn("red_x", item["evidence"]["unreadable_fields"])
                self.assertIsNone(item["evidence"]["values"]["red_x"])
                self.assertNotIn("red_x", result[1]["mastering_records"][0]["missing_fields"])
                self.assertEqual(result[1]["mastering_selected_record"], 2)

    def test_later_invalid_frame_is_not_hidden_by_first_valid_frame(self):
        frames = [dict(stream_index=0, side_data_list=[record]) for record in
                  (mastering(), mastering(red_x="-1/2"), mastering())]
        result = analyze_metadata(hdr_probe([]), frames)
        invalid = [f for f in result[0] if f["code"] == "hdr_mastering_invalid"]
        self.assertEqual(len(invalid), 1)
        self.assertEqual(invalid[0]["evidence"]["source"]["frame_index"], 1)
        self.assertIn("red_x", invalid[0]["evidence"]["fields"])

    def test_repeated_equivalent_frames_keep_count_and_provenance(self):
        frames = [dict(stream_index=0, pts_time="0", side_data_list=[mastering()]),
                  dict(stream_index=0, pts_time="0.04", side_data_list=[mastering(max_luminance="2000/2")])]
        original = copy.deepcopy(frames)
        result = analyze_metadata(hdr_probe([]), frames)
        self.assertEqual(result[1]["mastering_record_count"], 1)
        self.assertEqual(result[1]["mastering_observation_count"], 2)
        record = result[1]["mastering_records"][0]
        self.assertEqual(record["occurrences"], 2)
        self.assertEqual(record["source"]["frame_index"], 0)
        self.assertEqual(record["last_source"]["frame_index"], 1)
        self.assertEqual(frames, original)

    def test_tolerance_never_hides_invalid_near_boundary_value(self):
        frames = [dict(stream_index=0, side_data_list=[mastering(max_luminance=value)])
                  for value in ("10000", "10000.00001")]
        result = analyze_metadata(hdr_probe([]), frames)
        self.assertEqual(result[1]["mastering_record_count"], 2)
        self.assertIn("hdr_mastering_invalid", codes(result))
        self.assertNotIn("hdr_mastering_conflict", codes(result))
        self.assertEqual(result[1]["mastering_max_nits"], 10000)

    def test_conflicts_compare_full_range_not_only_adjacent_records(self):
        frames = [dict(stream_index=0, side_data_list=[mastering(min_luminance=value)])
                  for value in ("0.005", "0.00509", "0.00518")]
        result = analyze_metadata(hdr_probe([]), frames)
        item = next(f for f in result[0] if f["code"] == "hdr_mastering_conflict")
        self.assertEqual(item["evidence"]["fields"], ["min_luminance"])

    def test_valid_complete_stream_preferred_to_invalid_complete_frame(self):
        result = analyze_metadata(hdr_probe([mastering()]),
                                  [dict(stream_index=0, side_data_list=[mastering(max_luminance="10000000/1")])])
        self.assertEqual(result[1]["mastering_selected_record"], 1)
        self.assertEqual(result[1]["mastering_max_nits"], 1000)
        self.assertIn("hdr_mastering_invalid", codes(result))

    def test_partial_records_are_not_combined_into_complete_mastering(self):
        all_fields = mastering()
        luminance = {key: value for key, value in all_fields.items() if key in
                     {"side_data_type", "max_luminance", "min_luminance"}}
        chromaticity = {key: value for key, value in all_fields.items() if key not in
                       {"max_luminance", "min_luminance"}}
        result = analyze_metadata(hdr_probe([luminance]), [dict(stream_index=0, side_data_list=[chromaticity])])
        self.assertIn("hdr_mastering_incomplete", codes(result))
        self.assertTrue(all(not record["complete"] for record in result[1]["mastering_records"]))
        self.assertIsNone(result[1]["mastering_max_nits"])

    def test_dv8_hlg_is_not_claimed_hdr10(self):
        result = analyze_metadata(probe(color_transfer="arib-std-b67", side_data_list=[dict(side_data_type="DOVI configuration record", dv_profile=8, dv_bl_signal_compatibility_id=4)]))
        note = next(f for f in result[0] if f["code"] == "dolby_vision_profile")
        self.assertIn("HLG", note["detail"])
        self.assertNotIn("HDR10", note["detail"])

    def test_frame_side_data_belongs_to_selected_video(self):
        result = analyze_metadata(probe(), [dict(stream_index=2, side_data_list=[dict(side_data_type="DOVI configuration record", dv_profile=5)])])
        self.assertFalse(result[1]["dolby_vision"])

    def test_missing_duration_is_unknown_not_truncation(self):
        p = probe()
        del p["streams"][0]["duration"]
        del p["format"]["duration"]
        result = analyze_metadata(p)
        note = next(f for f in result[0] if f["code"] == "duration_unavailable")
        self.assertEqual(note["severity"], "skipped")
        self.assertNotIn("audio_video_duration_mismatch", codes(result))

    def test_audio_mkv_duration_tag_and_missing_unknown(self):
        p = probe()
        p["streams"][1].pop("duration")
        p["streams"][1]["tags"]["DURATION"] = "00:00:50.000000000"
        result = analyze_metadata(p)
        self.assertIn("audio_video_duration_mismatch", codes(result))
        self.assertEqual(result[1]["audio"][0]["duration_seconds"], 50)
        p["streams"][1]["tags"].pop("DURATION")
        self.assertNotIn("audio_video_duration_mismatch", codes(analyze_metadata(p)))

    def test_audio_zero_channels_is_not_silently_ignored(self):
        p = probe()
        p["streams"][1]["channels"] = 0
        self.assertIn("audio_channels_unusual", codes(analyze_metadata(p)))

    def test_subtitle_language_and_forced_are_advisory(self):
        p = probe()
        p["streams"].append(dict(index=2, codec_type="subtitle", codec_name="hdmv_pgs_subtitle", tags={"title": "English Forced"}))
        result = analyze_metadata(p)
        for code in ("subtitle_bitmap", "subtitle_language_unspecified", "subtitle_forced_title_only"):
            self.assertEqual(next(f for f in result[0] if f["code"] == code)["severity"], "info")

    def test_reference_counts_conservative_and_input_unchanged(self):
        p, reference = probe(), probe(duration="200")
        reference["streams"].append(dict(index=2, codec_type="audio", codec_name="aac"))
        original = copy.deepcopy(p)
        result = analyze_metadata(p, reference=reference)
        self.assertIn("reference_duration_differs", codes(result))
        self.assertIn("reference_audio_count_lower", codes(result))
        self.assertEqual(p, original)

    def test_no_container_bitrate_masquerading_as_video(self):
        p = probe()
        p["format"]["bit_rate"] = "10000000"
        result = analyze_metadata(p)
        self.assertIsNone(result[1]["video_bitrate"])
        self.assertEqual(result[1]["container_bitrate"], 10000000)

    def test_invalid_probe(self):
        self.assertEqual(analyze_metadata(None)[0][0]["code"], "probe_invalid")


if __name__ == "__main__":
    unittest.main()
