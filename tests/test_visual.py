"""Representative external-tool output and missing-evidence cases."""
import unittest

from plexcheck.visual import analyze_dovi_summary, analyze_idet, analyze_signalstats


def codes(findings):
    return {item["code"] for item in findings}


class IdetTests(unittest.TestCase):
    def test_uses_final_multiframe_not_cumulative_sum_or_single(self):
        log = """
[Parsed_idet_0 @ 0001] Repeated Fields: Neither: 800 Top: 80 Bottom: 120
[Parsed_idet_0 @ 0001] Multi frame detection: TFF: 1 BFF: 0 Progressive: 99 Undetermined: 0
[Parsed_idet_0 @ 0001] Single frame detection: TFF: 0 BFF: 0 Progressive: 1000 Undetermined: 0
[Parsed_idet_0 @ 0001] Multi frame detection: TFF: 200 BFF: 100 Progressive: 700 Undetermined: 20
"""
        findings, metrics = analyze_idet(log, "progressive")
        self.assertEqual(metrics["classified_frames"], 1000)
        self.assertEqual(metrics["interlaced_percent"], 30)
        self.assertEqual(metrics["repeated_fields"]["percent"], 20)
        self.assertIn("visual.idet.progressive_flag_mismatch", codes(findings))
        self.assertIn("visual.idet.repeated_fields", codes(findings))
        self.assertFalse(any(f["severity"] == "error" for f in findings))

    def test_single_fallback_and_metadata_caution(self):
        findings, metrics = analyze_idet(
            "Single frame detection: TFF: 0 BFF: 0 Progressive: 500 Undetermined: 0", "tt")
        self.assertTrue(metrics["measured"])
        self.assertEqual(metrics["detector"], "single")
        self.assertIn("visual.idet.interlaced_flag_mismatch", codes(findings))

    def test_two_percent_is_not_above_combing_threshold(self):
        findings, metrics = analyze_idet("Multi frame detection: TFF: 2 BFF: 0 Progressive: 98")
        self.assertFalse(metrics["combing_risk"])
        self.assertNotIn("visual.idet.combing", codes(findings))

    def test_missing_zero_and_undetermined_are_not_clean(self):
        for log in ("decoder failed", "Multi frame detection: TFF: 0 BFF: 0 Progressive: 0 Undetermined: 0",
                    "Multi frame detection: TFF: 0 BFF: 0 Progressive: 0 Undetermined: 200"):
            with self.subTest(log=log):
                findings, metrics = analyze_idet(log)
                self.assertFalse(metrics["measured"])
                self.assertEqual(findings[0]["severity"], "skipped")


class SignalStatsTests(unittest.TestCase):
    def test_pq_like_bright_sample_is_not_reported_as_bad_hdr(self):
        log = """[Parsed_metadata_1] lavfi.signalstats.YMIN=64
[Parsed_metadata_1] lavfi.signalstats.YMAX=940
[Parsed_metadata_1] lavfi.signalstats.YAVG=810.125
"""
        findings, metrics = analyze_signalstats(log)
        self.assertTrue(metrics["measured"])
        self.assertEqual(metrics["maximum_y"], 940)
        self.assertFalse(metrics["maxcll_measured"])
        self.assertFalse(any(f["severity"] in {"error", "warning"} for f in findings))

    def test_full_range_values_only_informational(self):
        findings, metrics = analyze_signalstats("lavfi.signalstats.YMIN=0\n"
            "lavfi.signalstats.YMAX=1023\nlavfi.signalstats.YAVG=500", 10)
        self.assertIn("visual.signalstats.nominal_range", codes(findings))
        self.assertTrue(all(f["severity"] == "info" for f in findings))

    def test_wrong_depth_is_measurement_warning(self):
        findings, metrics = analyze_signalstats("lavfi.signalstats.YMIN=64\n"
            "lavfi.signalstats.YMAX=940\nlavfi.signalstats.YAVG=200", 8)
        self.assertIn("visual.signalstats.depth_mismatch", codes(findings))
        self.assertFalse(any(f["severity"] == "error" for f in findings))

    def test_no_metrics_and_invalid_depth_are_inconclusive(self):
        for log, depth in (("no output", 10), ("lavfi.signalstats.YMAX=940", 10),
                           ("", 0), ("", True)):
            with self.subTest(log=log, depth=depth):
                findings, metrics = analyze_signalstats(log, depth)
                self.assertFalse(metrics["measured"])
                self.assertEqual(findings[0]["severity"], "skipped")

    def test_partial_counts_reported_not_paired(self):
        log = "lavfi.signalstats.YMIN=64\nlavfi.signalstats.YMAX=900\n" \
              "lavfi.signalstats.YAVG=100\nlavfi.signalstats.YAVG=105"
        findings, metrics = analyze_signalstats(log)
        self.assertTrue(metrics["measured"])
        self.assertFalse(metrics["complete_measurements"])
        self.assertIn("visual.signalstats.partial", codes(findings))


class DoviTests(unittest.TestCase):
    def test_profile_and_level6_with_separate_level1(self):
        log = """Frames: 83060
Profile: 8
L1 metadata:
    Mastering display: 1/9999 nits
    MaxCLL: 9999 nits, MaxFALL: 9998 nits
L6 metadata:
    Mastering display: 0.005/1000 nits
    MaxCLL: 1120 nits, MaxFALL: 300 nits
L9 MDP: BT.2020
"""
        findings, metrics = analyze_dovi_summary(log)
        self.assertTrue(metrics["measured"])
        self.assertEqual(metrics["mastering_max_nits"], 1000)
        self.assertEqual(metrics["maxcll_nits"], 1120)
        self.assertFalse(metrics["frame_alignment_verified"])
        self.assertFalse(metrics["source_preservation_verified"])

    def test_no_level6_does_not_borrow_another_level(self):
        log = """Frames: 100
Profile: 5
L6 metadata:
    absent
L8 metadata:
    Mastering display: 0/1000 nits
    MaxCLL: 1000 nits, MaxFALL: 100 nits
"""
        findings, metrics = analyze_dovi_summary(log)
        self.assertFalse(metrics["has_l6"])
        self.assertNotIn("maxcll_nits", metrics)
        self.assertIn("visual.dovi.profile5_compatibility", codes(findings))
        self.assertFalse(any(f["severity"] in {"warning", "error"} for f in findings))

    def test_inconsistent_nonzero_light_values_warn(self):
        findings, metrics = analyze_dovi_summary("Frames: 200\nProfile: 8\nL6 metadata:\n"
            "Mastering display: 0.005/1000 nits\nMaxCLL: 100 nits, MaxFALL: 200 nits")
        self.assertIn("visual.dovi.light_inconsistent", codes(findings))

    def test_zero_maxcll_does_not_prove_inconsistent_light(self):
        findings, _ = analyze_dovi_summary("Frames: 200\nProfile: 8\nL6 metadata:\n"
            "MaxCLL: 0 nits, MaxFALL: 200 nits")
        self.assertNotIn("visual.dovi.light_inconsistent", codes(findings))

    def test_missing_and_empty_rpu_are_inconclusive(self):
        for log in ("", "Frames: 0\nProfile: 8", "Frames: 200", "Profile: 8"):
            with self.subTest(log=log):
                findings, metrics = analyze_dovi_summary(log)
                self.assertFalse(metrics["measured"])
                self.assertEqual(findings[0]["severity"], "skipped")


if __name__ == "__main__":
    unittest.main()
