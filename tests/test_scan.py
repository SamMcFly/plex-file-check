import unittest
from plexcheck.scan import check_decode_result
from plexcheck.cli import finalize, positive, text_report


class ScanTests(unittest.TestCase):
    def test_zero_exit_does_not_hide_decoder_errors(self):
        fs, _ = check_decode_result(dict(stdout="frame=120\n", stderr="Invalid NAL unit size", stderr_bytes=21, returncode=0, timed_out=False))
        self.assertEqual(fs[0]["severity"], "error")

    def test_timeout_never_passes(self):
        fs, m = check_decode_result(dict(stdout="frame=1\n", stderr="", returncode=-1, timed_out=True))
        self.assertEqual(fs[0]["severity"], "skipped")
        self.assertNotIn("clean", m)

    def test_missing_decoder_not_damaged_file(self):
        fs, _ = check_decode_result(dict(stdout="", stderr="Unknown decoder xyz", returncode=1, timed_out=False))
        self.assertEqual(fs[0]["severity"], "skipped")

    def test_empty_decode_not_pass(self):
        fs, _ = check_decode_result(dict(stdout="frame=0\n", stderr="", returncode=0, timed_out=False))
        self.assertEqual(fs[0]["code"], "DECODE_EMPTY")

    def test_truncated_progress_not_full_frame_count(self):
        fs, m = check_decode_result(dict(stdout="frame=25\n", stderr="", returncode=0, timed_out=False, output_truncated=True))
        self.assertEqual(fs[0]["severity"], "skipped")
        self.assertNotIn("clean", m)

    def test_incomplete_report_not_clean_exit(self):
        report = finalize({"findings": [dict(code="X", severity="skipped", title="x", detail="x", advice="", evidence={})]})
        self.assertEqual(report["exit_code"], 1)

    def test_reject_nonfinite_cli_numbers(self):
        for value in ("nan", "inf", "-5", "0"):
            with self.assertRaises(Exception):
                positive(value)


if __name__ == "__main__":
    unittest.main()
