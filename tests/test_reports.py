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


if __name__ == "__main__":
    unittest.main()
