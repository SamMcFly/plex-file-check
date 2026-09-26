from contextlib import ExitStack, contextmanager
import io
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import uuid

from plexcheck.common import finding
from plexcheck.scan import scan


def issue(code, severity="warning"):
    return finding(code, severity, "Synthetic finding", "Synthetic evidence.")


class OrchestrationTests(unittest.TestCase):
    def setUp(self):
        self.parent = Path(tempfile.gettempdir()).resolve()
        self.root = self.parent / ("plex-check-orchestration-" + uuid.uuid4().hex)
        self.root.mkdir()
        self.addCleanup(self.cleanup_source)
        self.source = self.root / "private-title.mkv"
        self.source.write_bytes(b"fixture")
        self.probe = {
            "streams": [{"index": 0, "codec_type": "video", "codec_name": "hevc",
                         "avg_frame_rate": "24/1"},
                        {"index": 1, "codec_type": "audio", "codec_name": "aac"}],
            "format": {"duration": "120", "format_name": "matroska"},
        }
        self.ok = dict(returncode=0, timed_out=False, stdout="frame=24\nprogress=end\n",
                       stderr="", stderr_bytes=0)
        self.frames = [{"stream_index": 0, "pts_time": "0.0", "side_data_list": []}]
        self.packet_status = self.ok
        self.full_status = self.ok
        self.tools = {"ffprobe": "probe", "ffmpeg": "decoder"}
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.mocks = {}
        defaults = {
            "plexcheck.scan.run_text": self.ok,
            "plexcheck.metadata.analyze_metadata": ([], {}),
            "plexcheck.containers.inspect_container": ([], {"container_structure": {"complete": True}}),
            "plexcheck.containers.inspect_sidecars": ([], {}),
            "plexcheck.scan.analyze_packets": ([], {}),
            "plexcheck.scan.run_decode_sample": ([], {"clean": True, "decoded_video_frames": 24}),
            "plexcheck.scan.analyze_full_packets": ([], {"streams": {"0": {"packets": 28, "span_seconds": 5}}}),
            "plexcheck.visual.analyze_idet": ([], {"measured": True}),
            "plexcheck.visual.analyze_signalstats": ([], {"measured": True}),
            "plexcheck.cadence.scan_telecine": ([], {}),
            "plexcheck.extras.scan_embedded_subtitles": ([], {"embedded_subtitles": {"complete": True}}),
            "plexcheck.extras.scan_loudness": ([], {"loudness": {"complete": True}}),
            "plexcheck.dovi.scan_dovi": ([], {"measured": True}),
            "plexcheck.hdr10plus.scan_hdr10plus": ([], {"measured": True, "extraction_complete": True}),
        }
        for target, value in defaults.items():
            self.mocks[target.rsplit(".", 1)[-1]] = self.stack.enter_context(patch(target, return_value=value))
        self.mocks["probe_json"] = self.stack.enter_context(patch("plexcheck.scan.probe_json", side_effect=self.probe_response))
        self.mocks["run_file"] = self.stack.enter_context(patch("plexcheck.scan.run_file", side_effect=self.packet_file))

    def cleanup_source(self):
        assert self.root.resolve().parent == self.parent
        shutil.rmtree(self.root)

    def probe_response(self, tool, path, args=None, timeout=None):
        if args and "-show_frames" in args:
            return {"frames": self.frames}, self.ok
        if args and "-show_packets" in args:
            return {"packets": [{"stream_index": 0}, {"stream_index": 0}]}, self.packet_status
        return self.probe, self.ok

    @contextmanager
    def packet_file(self, args, timeout):
        with io.BytesIO(b"stream_index=0|pts_time=0|size=10\n") as handle:
            yield handle, self.full_status

    def run_scan(self, **kwargs):
        return scan(self.source, self.tools, **kwargs)

    def codes(self, report):
        return {item["code"] for item in report["findings"]}

    def coverage(self, report):
        return {item["check"]: item["status"] for item in report["coverage"]}

    def test_default_keeps_frame_metadata_structure_and_decode_without_optional_work(self):
        self.mocks["analyze_metadata"].return_value = ([issue("hdr_mastering_conflict")], {})
        messages = []
        report = self.run_scan(progress=messages.append)
        self.assertEqual(report["schema_version"], 2)
        self.assertEqual(report["profile"], "focused")
        self.assertIn("hdr_mastering_conflict", self.codes(report))
        self.assertEqual(self.mocks["analyze_metadata"].call_args.kwargs["frames"], self.frames)
        self.assertEqual(self.mocks["run_decode_sample"].call_count, 3)
        for call in self.mocks["run_decode_sample"].call_args_list:
            self.assertIn("0:a?", call.args[0])
        self.mocks["inspect_container"].assert_called_once()
        for name in ("inspect_sidecars", "analyze_packets", "analyze_idet", "scan_telecine",
                     "scan_embedded_subtitles", "scan_loudness", "scan_dovi", "scan_hdr10plus", "run_file"):
            self.mocks[name].assert_not_called()
        self.assertFalse(any(item["severity"] == "skipped" for item in report["findings"]))
        self.assertEqual(self.coverage(report)["software video + all audio decode"], "completed")
        self.assertEqual(report["metrics"]["input_identity"]["size_bytes"], 7)
        self.assertTrue(report["metrics"]["input_identity"]["modified_utc"].endswith("+00:00"))
        self.assertNotIn(str(self.root), str(report))
        self.assertEqual(self.source.read_bytes(), b"fixture")
        self.assertTrue(any("container structure" in line for line in messages))

    def test_quick_is_metadata_and_structure_without_opt_out_findings(self):
        report = self.run_scan(mode="quick")
        self.assertEqual(self.mocks["probe_json"].call_count, 1)
        self.mocks["run_decode_sample"].assert_not_called()
        self.mocks["inspect_sidecars"].assert_not_called()
        self.assertEqual(self.coverage(report), {"metadata": "completed", "container structure": "completed"})
        self.assertFalse(any(item["severity"] == "skipped" for item in report["findings"]))

    def test_advanced_restores_packet_visual_and_subtitle_diagnostics(self):
        messages = []
        report = self.run_scan(advanced=True, timeout=3, full_timeout=456, progress=messages.append)
        self.assertEqual(report["profile"], "advanced")
        for name in ("inspect_sidecars", "analyze_packets", "analyze_idet", "scan_embedded_subtitles"):
            self.assertTrue(self.mocks[name].called, name)
        subtitles = self.mocks["scan_embedded_subtitles"]
        self.assertEqual(subtitles.call_args.kwargs["timeout"], 456)
        self.assertEqual(subtitles.call_args.kwargs["progress"], messages.append)
        self.assertEqual(self.coverage(report)["embedded text subtitles"], "completed")
        self.mocks["scan_loudness"].assert_not_called()
        self.mocks["scan_dovi"].assert_not_called()

    def test_advanced_no_visual_does_not_report_an_incomplete_check(self):
        report = self.run_scan(advanced=True, visual=False)
        self.mocks["analyze_idet"].assert_not_called()
        self.assertNotIn("VISUAL_SKIPPED", self.codes(report))
        self.assertNotIn("interlace/cadence/signal samples", self.coverage(report))

    def test_generated_container_findings_are_retained_for_report_classification(self):
        layout = {"matroska.cues_at_end", "matroska.cues_absent", "mp4.faststart_absent", "mp4.fragmented"}
        essential = {"container.structure_invalid", "matroska.clusters_absent", "mp4.moov_absent"}
        self.mocks["inspect_container"].return_value = (
            [issue(code) for code in sorted(layout | essential)], {"container_structure": {"complete": True}})
        focused = self.run_scan(mode="quick")
        advanced = self.run_scan(mode="quick", advanced=True)
        self.assertTrue(layout | essential <= self.codes(focused))
        self.assertTrue(layout | essential <= self.codes(advanced))

    def test_requested_bandwidth_runs_packets_without_visual_or_subtitle_scans(self):
        self.mocks["analyze_packets"].return_value = (
            [issue("BANDWIDTH_HEADROOM"), issue("AV_SAMPLE_OFFSET"), issue("BITRATE_BURST"),
             issue("INTERLEAVE_UNMEASURED", "skipped")], {})
        report = self.run_scan(bandwidth_mbps=12)
        self.assertEqual(self.mocks["analyze_packets"].call_args.kwargs["bandwidth_mbps"], 12)
        self.assertIn("BANDWIDTH_HEADROOM", self.codes(report))
        # Already-computed observations remain available to the report's
        # assessment layer, which separates diagnostics from major findings.
        self.assertTrue({"AV_SAMPLE_OFFSET", "BITRATE_BURST", "INTERLEAVE_UNMEASURED"} <= self.codes(report))
        self.assertEqual(self.coverage(report)["packet samples"], "completed")
        self.mocks["analyze_idet"].assert_not_called()
        self.mocks["scan_embedded_subtitles"].assert_not_called()

    def test_explicit_bandwidth_overrides_quick_packet_scope(self):
        report = self.run_scan(mode="quick", bandwidth_mbps=12)
        self.mocks["analyze_packets"].assert_called_once()
        self.mocks["run_decode_sample"].assert_not_called()
        self.assertIn("packet samples", self.coverage(report))

    def test_requested_bandwidth_keeps_real_sampling_failures_and_diagnostics(self):
        self.mocks["analyze_packets"].return_value = (
            [issue("PACKETS_INCOMPLETE", "skipped"), issue("BITRATE_UNMEASURED", "skipped")], {})
        self.packet_status = dict(self.ok, stderr="Invalid NAL unit size")
        report = self.run_scan(bandwidth_mbps=12)
        self.assertTrue({"PACKETS_INCOMPLETE", "BITRATE_UNMEASURED", "PACKET_PROBE_DIAGNOSTICS"} <= self.codes(report))
        self.assertEqual(self.coverage(report)["packet samples"], "incomplete")

    def test_default_keeps_real_decode_and_frame_metadata_failures(self):
        self.frames = []
        self.mocks["run_decode_sample"].return_value = ([issue("DECODE_ERRORS", "error")], {})
        report = self.run_scan()
        self.assertTrue({"DECODE_ERRORS", "FRAME_METADATA_UNMEASURED"} <= self.codes(report))
        self.assertEqual(self.coverage(report)["software video + all audio decode"], "incomplete")
        self.assertEqual(self.coverage(report)["frame metadata (first 48 packets)"], "incomplete")

    def test_verified_thread_retry_completes_coverage_with_diagnostics_not_clean(self):
        from plexcheck.decode import run_decode_sample
        pps = "[hevc @ 000001abc] PPS changed between slices\n"
        initial = dict(self.ok, stderr=pps, stderr_bytes=len(pps))
        # Exercise the real controlled-retry logic for the middle sample, while
        # substituting process results so this test needs no installed decoder.
        with patch("plexcheck.scan.run_decode_sample", side_effect=run_decode_sample), \
                patch("plexcheck.decode.run_text", side_effect=[self.ok, initial, self.ok, self.ok]) as decoder:
            report = self.run_scan()
        self.assertEqual(decoder.call_count, 4)
        self.assertEqual(self.coverage(report)["software video + all audio decode"], "completed with diagnostics")
        middle = report["metrics"]["decode_samples"][1]
        self.assertEqual(middle["thread_retry"]["outcome"], "thread_dependent")
        self.assertNotIn("clean", middle)
        self.assertIn("DECODE_THREAD_DEPENDENT", self.codes(report))

    def test_controlled_retry_does_not_hide_another_unresolved_sample(self):
        controlled = {"thread_retry": {"outcome": "thread_dependent"}}
        for outcome in ("timed_out", "frame_count_mismatch", "diagnostics_remain", "tool_unavailable"):
            with self.subTest(outcome=outcome):
                self.mocks["run_decode_sample"].side_effect = [
                    ([], {"clean": True}),
                    ([issue("DECODE_THREAD_DEPENDENT")], controlled),
                    ([issue("DECODE_INCOMPLETE", "skipped")], {"thread_retry": {"outcome": outcome}}),
                ]
                report = self.run_scan()
                self.assertEqual(self.coverage(report)["software video + all audio decode"], "incomplete")
                self.assertIn("DECODE_INCOMPLETE", self.codes(report))

    def test_missing_ffmpeg_is_still_incomplete_in_default_and_deep(self):
        self.tools.pop("ffmpeg")
        report = self.run_scan(mode="deep")
        self.assertIn("FFMPEG_MISSING", self.codes(report))
        self.assertEqual(self.coverage(report)["software video + all audio decode"], "incomplete")
        self.assertEqual(self.coverage(report)["whole-file software decode"], "incomplete")

    def test_advanced_missing_ffmpeg_preserves_requested_visual_and_subtitle_failures(self):
        self.tools.pop("ffmpeg")
        self.mocks["scan_embedded_subtitles"].return_value = (
            [issue("subtitle.ffmpeg_missing", "skipped")], {"embedded_subtitles": {"complete": False}})
        report = self.run_scan(advanced=True)
        self.assertIn("subtitle.ffmpeg_missing", self.codes(report))
        self.assertIsNone(self.mocks["scan_embedded_subtitles"].call_args.args[2])
        self.assertEqual(self.coverage(report)["embedded text subtitles"], "incomplete")
        self.assertEqual(self.coverage(report)["interlace/cadence/signal samples"], "incomplete")

    def test_deep_keeps_generated_timeline_findings_but_gates_extra_comparisons(self):
        essential = {"FULL_PACKET_GAP", "FULL_PTS_MISSING", "FULL_DTS_BACKWARDS", "DURATION_PACKET_MISMATCH"}
        advanced = {"FULL_BITRATE_BURST", "FULL_KEYFRAMES_SPARSE", "FULL_AUDIO_DURATION"}
        self.mocks["analyze_full_packets"].return_value = (
            [issue(code) for code in sorted(essential | advanced)],
            {"streams": {"0": {"packets": 28, "span_seconds": 5}}})
        report = self.run_scan(mode="deep")
        self.mocks["run_file"].assert_called_once()
        self.mocks["analyze_packets"].assert_not_called()
        self.assertTrue(essential | advanced <= self.codes(report))
        self.assertNotIn("PACKET_FRAME_DIFFERENCE", self.codes(report))
        self.assertNotIn("MEASURED_FPS_MISMATCH", self.codes(report))
        self.assertEqual(self.coverage(report)["whole-file software decode"], "completed")
        detailed = self.run_scan(mode="deep", advanced=True, visual=False)
        self.assertTrue(essential | advanced <= self.codes(detailed))
        self.assertIn("PACKET_FRAME_DIFFERENCE", self.codes(detailed))
        self.assertIn("MEASURED_FPS_MISMATCH", self.codes(detailed))

    def test_deep_failed_timeline_remains_incomplete(self):
        self.full_status = dict(self.ok, returncode=1)
        report = self.run_scan(mode="deep")
        self.assertIn("FULL_PACKETS_FAILED", self.codes(report))
        self.assertEqual(self.coverage(report)["whole-file packet timeline"], "incomplete")
        self.mocks["analyze_full_packets"].assert_not_called()

    def test_explicit_optional_checks_work_without_advanced(self):
        report = self.run_scan(mode="quick", loudness=True, dovi=True, hdr10plus=True,
                               reference=self.source, expected_runtime=200)
        for name in ("scan_loudness", "scan_dovi", "scan_hdr10plus"):
            self.mocks[name].assert_called_once()
        self.assertEqual(self.mocks["analyze_metadata"].call_args.kwargs["reference"], self.probe)
        self.assertIn("RUNTIME_SHORT", self.codes(report))
        self.assertIn("expected_runtime", report["metrics"])
        self.assertEqual(self.coverage(report)["audio loudness"], "completed")
        self.assertEqual(self.coverage(report)["Dolby Vision RPU validation"], "completed")
        self.assertEqual(self.coverage(report)["HDR10+ scene metadata"], "completed")


if __name__ == "__main__":
    unittest.main()
