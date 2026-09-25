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


class MetadataTests(unittest.TestCase):
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
