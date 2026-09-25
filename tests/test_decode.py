import json
import unittest
from unittest.mock import patch

from plexcheck.decode import check_decode_result, run_decode_sample


ARGS = ["ffmpeg", "-v", "error", "-threads", "2", "-ss", "10", "-i", "private movie.mkv",
        "-map", "0:v:0", "-map", "0:a?", "-t", "2", "-progress", "pipe:1", "-f", "null", "-"]
PPS = "[hevc @ 000001abc] PPS changed between slices.\n    Last message repeated 6 times\n"


def result(stderr="", frames=48, **kwargs):
    output = dict(stdout="frame={}\nprogress=end\n".format(frames), stderr=stderr,
                  returncode=0, timed_out=False)
    output.update(kwargs)
    return output


class DecodeTests(unittest.TestCase):
    def run_pair(self, first, second, args=None):
        with patch("plexcheck.decode.run_text", side_effect=[first, second]) as tool:
            findings, metrics = run_decode_sample(args or ARGS, 15, offset=10)
        return findings, metrics, tool

    def test_isolated_pps_retry_same_frames_is_warning_not_clean(self):
        findings, metrics, tool = self.run_pair(result(PPS), result())
        self.assertEqual([f["code"] for f in findings], ["DECODE_THREAD_DEPENDENT"])
        self.assertEqual(findings[0]["severity"], "warning")
        self.assertNotIn("clean", metrics)
        self.assertEqual(metrics["thread_retry"]["outcome"], "thread_dependent")
        self.assertEqual(metrics["attempts"][0]["findings"][0]["code"], "DECODE_FAILED")
        self.assertEqual(metrics["attempts"][0]["metrics"]["decoded_video_frames"], 48)
        self.assertEqual(metrics["attempts"][1]["metrics"]["decoded_video_frames"], 48)
        expected = list(ARGS)
        expected[expected.index("-threads") + 1] = "1"
        self.assertEqual(tool.call_args_list[1].args, (expected, 15))
        self.assertEqual(ARGS[ARGS.index("-threads") + 1], "2")

    def test_missing_stderr_bytes_does_not_hide_diagnostic(self):
        findings, metrics = check_decode_result(result("decoder diagnostic"))
        self.assertEqual(findings[0]["code"], "DECODE_FAILED")
        self.assertTrue(metrics["unrecognized_diagnostics"])
        self.assertGreater(metrics["stderr_bytes"], 0)
        self.assertNotIn("clean", metrics)

    def test_mixed_structural_error_remains_error_after_clean_retry(self):
        findings, metrics, _ = self.run_pair(result(PPS + "Invalid NAL unit size 42\n"), result())
        self.assertEqual(findings[0]["code"], "DECODE_ERRORS")
        self.assertEqual(findings[0]["severity"], "error")
        self.assertNotIn("DECODE_THREAD_DEPENDENT", [f["code"] for f in findings])
        self.assertEqual(metrics["thread_retry"]["outcome"], "mixed_initial_diagnostics")

    def test_mixed_unknown_message_remains_warning_and_private(self):
        secret = r"C:\private\movie.mkv?token=TOPSECRET"
        findings, metrics, _ = self.run_pair(result(PPS + secret + "\n"), result())
        report = json.dumps([findings, metrics])
        self.assertNotIn("TOPSECRET", report)
        self.assertNotIn("private", report)
        self.assertTrue(metrics["unrecognized_diagnostics"])
        self.assertEqual(findings[0]["code"], "DECODE_FAILED")
        self.assertEqual(findings[-1]["code"], "DECODE_RETRY_UNRESOLVED")

    def test_same_line_extra_text_is_not_isolated_pps(self):
        findings, metrics, tool = self.run_pair(result(PPS.strip() + " secret-extra"), result())
        self.assertNotIn("DECODE_THREAD_DEPENDENT", [f["code"] for f in findings])
        self.assertTrue(metrics["unrecognized_diagnostics"])
        self.assertEqual(tool.call_count, 2)

    def test_structural_error_without_pps_does_not_retry(self):
        findings, _, tool = self.run_pair(result("Invalid data found"), result())
        self.assertEqual(findings[0]["code"], "DECODE_ERRORS")
        self.assertEqual(tool.call_count, 1)

    def test_unknown_message_without_pps_does_not_retry(self):
        findings, _, tool = self.run_pair(result("unrecognized error"), result())
        self.assertEqual(findings[0]["code"], "DECODE_FAILED")
        self.assertEqual(tool.call_count, 1)

    def test_clean_initial_sample_does_not_retry(self):
        findings, metrics, tool = self.run_pair(result(), result())
        self.assertEqual(findings, [])
        self.assertTrue(metrics["clean"])
        self.assertEqual(tool.call_count, 1)

    def test_timeout_retry_retains_initial_and_reports_incomplete(self):
        findings, metrics, _ = self.run_pair(result(PPS), result(frames=9, timed_out=True, returncode=-9))
        self.assertEqual(findings[0]["code"], "DECODE_FAILED")
        self.assertIn("DECODE_INCOMPLETE", [f["code"] for f in findings])
        self.assertEqual(metrics["thread_retry"]["outcome"], "timed_out")
        self.assertNotIn("clean", metrics)

    def test_retry_new_structural_error_is_retained(self):
        findings, metrics, _ = self.run_pair(result(PPS), result("Invalid NAL unit size 99"))
        self.assertIn("DECODE_ERRORS", [f["code"] for f in findings])
        self.assertEqual(metrics["thread_retry"]["outcome"], "diagnostics_remain")

    def test_frame_count_mismatch_is_unresolved(self):
        findings, metrics, _ = self.run_pair(result(PPS), result(frames=47))
        self.assertEqual(metrics["thread_retry"]["outcome"], "frame_count_mismatch")
        self.assertEqual(findings[0]["code"], "DECODE_FAILED")
        self.assertNotIn("clean", metrics)

    def test_zero_initial_frames_never_get_thread_dependent_result(self):
        findings, metrics, _ = self.run_pair(result(PPS, frames=0), result(frames=0))
        self.assertNotIn("DECODE_THREAD_DEPENDENT", [f["code"] for f in findings])
        self.assertFalse(metrics["thread_retry"]["same_frame_count"])

    def test_initial_nonzero_exit_never_gets_thread_dependent_result(self):
        findings, metrics, _ = self.run_pair(result(PPS, returncode=1), result())
        self.assertNotIn("DECODE_THREAD_DEPENDENT", [f["code"] for f in findings])
        self.assertEqual(metrics["thread_retry"]["outcome"], "initial_run_failed")

    def test_full_or_long_decode_never_retries(self):
        full_args = list(ARGS)
        duration_index = full_args.index("-t")
        del full_args[duration_index:duration_index + 2]
        long_args = list(ARGS)
        long_args[long_args.index("-t") + 1] = "7200"
        for args in (full_args, long_args):
            findings, _, tool = self.run_pair(result(PPS), result(), args)
            self.assertEqual(findings[0]["code"], "DECODE_FAILED")
            self.assertEqual(tool.call_count, 1)

    def test_no_retry_if_threads_not_explicitly_two_before_input(self):
        single_args = list(ARGS)
        single_args[single_args.index("-threads") + 1] = "1"
        findings, _, tool = self.run_pair(result(PPS), result(), single_args)
        self.assertEqual(tool.call_count, 1)
        self.assertEqual(findings[0]["code"], "DECODE_FAILED")

    def test_progress_truncation_and_initial_timeout_do_not_retry(self):
        for first in (result(PPS, output_truncated=True), result(PPS, timed_out=True)):
            findings, _, tool = self.run_pair(first, result())
            self.assertEqual(findings[0]["code"], "DECODE_INCOMPLETE")
            self.assertEqual(tool.call_count, 1)

    def test_diagnostics_truncation_prevents_thread_dependent_classification(self):
        findings, metrics, _ = self.run_pair(result(PPS, stderr_bytes=100000), result())
        self.assertNotIn("DECODE_THREAD_DEPENDENT", [f["code"] for f in findings])
        self.assertTrue(metrics["diagnostics_truncated"])

    def test_retry_launch_error_does_not_publish_exception_path(self):
        with patch("plexcheck.decode.run_text", side_effect=[result(PPS), OSError("private token=TOPSECRET")]):
            findings, metrics = run_decode_sample(ARGS, 15)
        self.assertEqual(findings[0]["code"], "DECODE_FAILED")
        self.assertEqual(findings[-1]["code"], "DECODE_RETRY_UNAVAILABLE")
        self.assertNotIn("TOPSECRET", json.dumps([findings, metrics]))

    def test_structural_diagnostic_evidence_uses_constant_labels(self):
        findings, metrics = check_decode_result(result("Failed to decode picture in C:/private/movie.mkv token=TOPSECRET"))
        self.assertEqual(findings[0]["severity"], "error")
        self.assertNotIn("TOPSECRET", json.dumps([findings, metrics]))
        self.assertEqual(findings[0]["evidence"]["error_types"], ["Failed to decode picture or frame"])

    def test_structural_error_is_not_hidden_by_unsupported_decoder(self):
        findings, metrics, _ = self.run_pair(
            result(PPS + "Invalid NAL unit size 42\nUnknown decoder private\n"), result())
        self.assertEqual(findings[0]["code"], "DECODE_ERRORS")
        self.assertIn("DECODE_INCOMPLETE", [f["code"] for f in findings])
        self.assertNotIn("clean", metrics)

    def test_retry_nonzero_exit_retains_original_warning(self):
        findings, metrics, _ = self.run_pair(result(PPS), result(returncode=1))
        self.assertEqual(findings[0]["code"], "DECODE_FAILED")
        self.assertEqual(metrics["attempts"][1]["metrics"]["exit_code"], 1)
        self.assertFalse(metrics["thread_retry"]["single_thread_clean"])


if __name__ == "__main__":
    unittest.main()
