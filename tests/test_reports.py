import unittest

from plexcheck.cli import finalize, text_report
from plexcheck.common import finding


def report(*severities):
    return finalize({"file": "example.mkv", "mode": "standard", "metrics": {},
                     "coverage": [], "tools": {}, "findings": [
                         finding(str(index), severity, "Example", "Detail")
                         for index, severity in enumerate(severities)]})


class ReportTests(unittest.TestCase):
    def test_incomplete_is_distinct_from_warnings(self):
        data = report("skipped", "info")
        self.assertEqual(data["exit_code"], 1)
        self.assertEqual(data["counts"], {"error": 0, "warning": 0, "skipped": 1, "info": 1})
        self.assertIn("some checks are incomplete", data["assessment"])
        text = text_report(data)
        self.assertIn("0 errors | 0 warnings | 1 not checked/incomplete | 1 informational", text)
        self.assertIn("INFO / CONTEXT (not failures)", text)

    def test_warning_does_not_claim_file_damage(self):
        data = report("warning")
        self.assertEqual(data["exit_code"], 1)
        self.assertIn("file damage is not established", data["assessment"])

    def test_error_and_info_exit_codes_unchanged(self):
        self.assertEqual(report("info")["exit_code"], 0)
        self.assertEqual(report("error", "warning", "skipped")["exit_code"], 2)

    def test_scan_and_file_identity_without_paths(self):
        data = report()
        data["generated_utc"] = "2026-01-02T03:04:05+00:00"
        data["metrics"].update(file_size_bytes=12825565611, input_identity={
            "size_bytes": 12825565611, "modified_utc": "2026-01-01T12:00:00+00:00"})
        text = text_report(data)
        self.assertIn("12,825,565,611 bytes", text)
        self.assertIn("Scan started (UTC): 2026-01-02", text)
        self.assertIn("File modified (UTC): 2026-01-01", text)
        self.assertIn("not a content hash or source history", text)

    def test_zero_size_and_older_report(self):
        data = report()
        self.assertNotIn("File modified", text_report(data))
        data["metrics"]["file_size_bytes"] = 0
        self.assertIn("File size: 0 bytes (0.00 MiB)", text_report(data))


def focused(*items, mode="standard", profile="focused"):
    return finalize(dict(file="example.mkv", mode=mode, profile=profile,
                         metrics={}, coverage=[], tools={}, findings=list(items)))


class FocusedReportTests(unittest.TestCase):
    def test_preferences_and_clean_thread_retry_do_not_fail_focused_result(self):
        data = focused(
            finding("DECODE_THREAD_DEPENDENT", "warning", "Thread interaction", "Evidence retained", frames=48),
            finding("matroska.cues_absent", "warning", "No index", "Seeking hint"),
            finding("hdr_light_levels_not_observed", "info", "No light levels", "Optional"),
            finding("audio_language_unspecified", "info", "No language", "Selection preference"))
        self.assertEqual(data["exit_code"], 0)
        self.assertEqual(data["findings"], [])
        self.assertEqual(len(data["diagnostics"]), 4)
        self.assertEqual(data["diagnostics"][0]["evidence"]["frames"], 48)
        self.assertNotIn("Thread interaction", text_report(data))
        self.assertIn("Thread interaction", text_report(data, details=True))
        self.assertNotIn("not checked/incomplete", text_report(data))

    def test_actual_errors_and_requested_incomplete_checks_remain(self):
        data = focused(
            finding("DECODE_ERRORS", "error", "Decoder error", "Invalid NAL"),
            finding("DECODE_INCOMPLETE", "skipped", "Timed out", "Try a longer timeout"),
            finding("hdr_mastering_conflict", "warning", "HDR mismatch", "Records disagree"))
        self.assertEqual(data["exit_code"], 2)
        self.assertEqual(data["counts"]["skipped"], 1)
        self.assertIn("FILE ERRORS", text_report(data))
        self.assertIn("REVIEW", text_report(data))
        self.assertIn("INCOMPLETE", text_report(data))
        self.assertEqual(focused(finding("DECODE_INCOMPLETE", "skipped", "Timeout", "Incomplete"))["exit_code"], 1)

    def test_device_notes_are_conditional_grouped_and_do_not_fail(self):
        data = focused(
            finding("dolby_vision_profile", "info", "Dolby Vision", "Profile 5", profile=5),
            finding("subtitle_bitmap", "info", "Image subtitle", "Conditional", stream=2),
            finding("subtitle_bitmap", "info", "Image subtitle", "Conditional", stream=3))
        self.assertEqual(data["exit_code"], 0)
        text = text_report(data)
        self.assertIn("DEVICE SUPPORT", text)
        self.assertIn("no HDR10-compatible base layer", text)
        self.assertIn("2 observations", text)
        self.assertEqual(text.count("Image subtitle"), 1)
        self.assertNotIn("Evidence:", text)
        self.assertNotIn("COVERAGE", text)

    def test_ordinary_dolby_vision_fallback_is_not_a_device_warning(self):
        for profile in (7, 8):
            data = focused(finding("dolby_vision_profile", "info", "Dolby Vision", "Context", profile=profile))
            self.assertFalse(data["findings"])
            self.assertEqual(len(data["diagnostics"]), 1)

    def test_distinct_video_compatibility_reasons_remain_visible(self):
        data = focused(
            finding("video_limited_hardware_support", "info", "Video format", "H.264 10-bit", stream=0),
            finding("video_limited_hardware_support", "info", "Video format", "HEVC 4:2:2", stream=2))
        text = text_report(data)
        self.assertIn("H.264 10-bit", text)
        self.assertIn("HEVC 4:2:2", text)
        self.assertIn("video stream 2", text)

    def test_only_substantial_declared_duration_difference_is_surfaced(self):
        minor = finding("audio_video_duration_mismatch", "warning", "Duration", "Small", substantial=False)
        major = finding("audio_video_duration_mismatch", "warning", "Duration", "Large", substantial=True)
        data = focused(minor, major)
        self.assertEqual(data["counts"]["warning"], 1)
        self.assertEqual(len(data["diagnostics"]), 1)

    def test_substantial_measured_audio_loss_is_visible_without_duration_tags(self):
        data = focused(
            finding("FULL_AUDIO_DURATION", "warning", "Duration", "Large", video_seconds=7200, audio_seconds=90),
            finding("FULL_AUDIO_DURATION", "warning", "Duration", "Small", video_seconds=7200, audio_seconds=6900))
        self.assertEqual(data["counts"]["warning"], 1)
        self.assertEqual(data["exit_code"], 1)
        self.assertEqual(data["findings"][0]["evidence"]["audio_seconds"], 90)
        self.assertEqual(len(data["diagnostics"]), 1)

    def test_advanced_retains_heuristic_warning(self):
        data = focused(finding("BITRATE_BURST", "warning", "Burst", "Heuristic"), profile="advanced")
        self.assertEqual(data["exit_code"], 1)
        self.assertIn("Evidence:", text_report(focused(
            finding("BITRATE_BURST", "warning", "Burst", "Heuristic", peak=90), profile="advanced")))

    def test_unknown_error_is_never_hidden_and_finalize_is_idempotent(self):
        data = focused(finding("FUTURE_ERROR", "error", "Problem", "Evidence"),
                       finding("hdr_mastering_not_observed", "info", "Optional", "Context"))
        counts = dict(data["counts"])
        finalize(data)
        self.assertEqual(data["exit_code"], 2)
        self.assertEqual(data["counts"], counts)
        self.assertEqual(len(data["diagnostics"]), 1)

    def test_quick_limit_is_plain_scope_not_automatic_incomplete(self):
        data = focused(mode="quick")
        self.assertEqual(data["exit_code"], 0)
        self.assertIn("metadata checks", data["assessment"])
        self.assertIn("content was not decoded", text_report(data))

    def test_quick_scope_acknowledges_explicit_checks(self):
        data = focused(mode="quick")
        data["coverage"] = [{"check": "audio loudness", "status": "completed"}]
        finalize(data)
        self.assertIn("explicitly requested checks", text_report(data))
        self.assertNotIn("content was not decoded", text_report(data))


if __name__ == "__main__":
    unittest.main()
