import io
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from plexcheck.dovi import _scratch_directory, scan_dovi


def probe(profile=8):
    stream = {"index": 2, "codec_type": "video", "codec_name": "hevc"}
    if profile is not None:
        stream["side_data_list"] = [{"side_data_type": "DOVI configuration record", "dv_profile": profile}]
    return {"streams": [{"index": 0, "codec_type": "video", "codec_name": "png",
                         "disposition": {"attached_pic": 1}}, stream]}


class FakeProcess:
    def __init__(self, rc=0, timeout=False):
        self.returncode = None
        self.result = rc
        self.timeout = timeout
        self.stdout = io.BytesIO(b"bitstream")
        self.killed = False

    def wait(self, timeout=None):
        if self.timeout and not self.killed:
            raise subprocess.TimeoutExpired("fake", timeout)
        self.returncode = -9 if self.killed else self.result
        return self.returncode

    def poll(self):
        return self.returncode

    def kill(self):
        self.killed = True


class DoviScanTests(unittest.TestCase):
    def setUp(self):
        self.temp = _scratch_directory()
        directory = self.temp.__enter__()
        self.addCleanup(self.temp.__exit__, None, None, None)
        self.source = directory / "private movie & name.mkv"
        self.source.write_bytes(b"unchanged source")
        self.calls, self.processes = [], []

    def factory(self, summary="Frames: 123\nProfile: 8\n", ff_rc=0, extract_rc=0,
                info_rc=0, payload=True, timeout_at=None, structural_log=b"", fail_extract=False):
        def popen(args, **kwargs):
            stage = "ffmpeg" if args[0] == "ffmpeg-local" else args[1]
            self.calls.append((args, kwargs))
            if stage == "extract-rpu" and fail_extract:
                raise FileNotFoundError("private executable path")
            proc = FakeProcess({"ffmpeg": ff_rc, "extract-rpu": extract_rc, "info": info_rc}[stage],
                               timeout_at == stage)
            self.processes.append(proc)
            if stage == "ffmpeg":
                kwargs["stderr"].write(structural_log)
            elif stage == "extract-rpu" and payload:
                Path(args[args.index("-o") + 1]).write_bytes(b"RPU")
            elif stage == "info":
                kwargs["stdout"].write(summary.encode())
            return proc
        return popen

    def scan(self, **kwargs):
        return scan_dovi(self.source, kwargs.pop("probe", probe()), "ffmpeg-local", "dovi-local", **kwargs)

    def test_pipe_argument_list_primary_stream_and_source_unchanged(self):
        with patch("plexcheck.dovi.subprocess.Popen", side_effect=self.factory()):
            findings, metrics = self.scan(expected_frames=123)
        self.assertTrue(metrics["measured"])
        self.assertTrue(metrics["rpu_frame_count_matches"])
        self.assertFalse(metrics["frame_alignment_verified"])
        self.assertEqual(self.source.read_bytes(), b"unchanged source")
        ff_args, ff_kwargs = self.calls[0]
        self.assertEqual(ff_args[ff_args.index("-map") + 1], "0:2")
        self.assertEqual(ff_args[-1], "-")
        self.assertEqual(ff_args[ff_args.index("-protocol_whitelist") + 1], "file,pipe")
        self.assertFalse(ff_kwargs["shell"])
        self.assertEqual(self.calls[1][0][2:4], ["-i", "-"])
        self.assertIs(self.calls[1][1]["stdin"], self.processes[0].stdout)
        self.assertTrue(self.processes[0].stdout.closed)
        self.assertNotIn(str(self.source), json.dumps([findings, metrics]))
        payload = Path(self.calls[1][0][-1])
        self.assertFalse(payload.exists(), "scratch payload must be cleaned up")

    def test_frame_and_profile_mismatch_warn_without_profile8_requirement(self):
        with patch("plexcheck.dovi.subprocess.Popen", side_effect=self.factory(summary="Frames: 100\nProfile: 5\n")):
            findings, metrics = self.scan(expected_frames=123)
        codes = {f["code"] for f in findings}
        self.assertIn("dovi.profile_mismatch", codes)
        self.assertIn("dovi.frame_count_mismatch", codes)
        with patch("plexcheck.dovi.subprocess.Popen", side_effect=self.factory(summary="Frames: 100\nProfile: 5\n")):
            findings, _ = self.scan(probe=probe(5), expected_frames=100)
        self.assertNotIn("dovi.profile_mismatch", {f["code"] for f in findings})

    def test_no_known_frame_count_is_explicit(self):
        with patch("plexcheck.dovi.subprocess.Popen", side_effect=self.factory()):
            findings, metrics = self.scan()
        self.assertIn("dovi.frame_comparison_unavailable", {f["code"] for f in findings})
        self.assertNotIn("rpu_frame_count_matches", metrics)

    def test_extraction_timeout_kills_both_and_cleans_scratch(self):
        with patch("plexcheck.dovi.subprocess.Popen", side_effect=self.factory(timeout_at="extract-rpu")):
            findings, metrics = self.scan(timeout=.5)
        self.assertTrue(metrics["timed_out"])
        self.assertFalse(metrics["measured"])
        self.assertEqual(len(self.processes), 2)
        self.assertTrue(all(p.killed for p in self.processes))
        self.assertFalse(Path(self.calls[1][0][-1]).exists())

    def test_summary_timeout_has_no_success_claim(self):
        with patch("plexcheck.dovi.subprocess.Popen", side_effect=self.factory(timeout_at="info")):
            _, metrics = self.scan(timeout=.5)
        self.assertTrue(metrics["timed_out"])
        self.assertFalse(metrics["measured"])
        self.assertTrue(self.processes[-1].killed)

    def test_second_spawn_failure_stops_first_and_redacts_error_paths(self):
        with patch("plexcheck.dovi.subprocess.Popen", side_effect=self.factory(fail_extract=True)):
            findings, metrics = self.scan()
        self.assertTrue(self.processes[0].killed)
        self.assertTrue(self.processes[0].stdout.closed)
        self.assertNotIn("private executable path", json.dumps([findings, metrics]))
        self.assertFalse(metrics["measured"])

    def test_no_payload_without_signaling_is_inconclusive(self):
        with patch("plexcheck.dovi.subprocess.Popen", side_effect=self.factory(payload=False)):
            findings, metrics = self.scan(probe=probe(None))
        missing = [f for f in findings if f["code"] == "dovi.payload_missing"][0]
        self.assertEqual(missing["severity"], "skipped")
        self.assertFalse(metrics["measured"])

    def test_signaled_missing_payload_warns(self):
        with patch("plexcheck.dovi.subprocess.Popen", side_effect=self.factory(payload=False)):
            findings, _ = self.scan()
        missing = [f for f in findings if f["code"] == "dovi.payload_missing"][0]
        self.assertEqual(missing["severity"], "warning")

    def test_structural_evidence_in_middle_of_large_log_survives(self):
        log = b"x\n" * 40000 + b"Invalid NAL unit size in private file\n" + b"y\n" * 40000
        with patch("plexcheck.dovi.subprocess.Popen", side_effect=self.factory(structural_log=log)):
            findings, metrics = self.scan(expected_frames=123)
        self.assertIn("dovi.structure_diagnostics", {f["code"] for f in findings})
        self.assertEqual(metrics["diagnostics"]["ffmpeg"]["matches"]["bitstream_structure"], 1)
        self.assertNotIn("private file", json.dumps([findings, metrics]))

    def test_partial_payload_cannot_pass_count_comparison(self):
        with patch("plexcheck.dovi.subprocess.Popen", side_effect=self.factory(ff_rc=1)):
            findings, metrics = self.scan(expected_frames=123)
        self.assertTrue(metrics["summary_parsed"])
        self.assertFalse(metrics["measured"])
        self.assertNotIn("rpu_frame_count_matches", metrics)

    def test_optional_tools_and_nonhevc_skip_without_launch(self):
        with patch("plexcheck.dovi.subprocess.Popen") as launch:
            findings, _ = scan_dovi(self.source, probe(), None, None)
            self.assertEqual(findings[0]["severity"], "skipped")
            alternate = probe()
            alternate["streams"][1]["codec_name"] = "av1"
            findings, _ = scan_dovi(self.source, alternate, "ffmpeg", "dovi")
            self.assertEqual(findings[0]["code"], "dovi.non_hevc")
            launch.assert_not_called()

    def test_shared_deadline_not_fresh_timeout_per_stage(self):
        # Start at 0; checks spend two seconds before extraction wait and
        # another second before producer wait; info must use the same budget.
        with patch("plexcheck.dovi.subprocess.Popen", side_effect=self.factory()), \
                patch("plexcheck.dovi.time.monotonic", side_effect=[0, 0, 1, 2, 3, 11]):
            _, metrics = self.scan(timeout=10)
        self.assertTrue(metrics["timed_out"])
        self.assertFalse(metrics["measured"])


if __name__ == "__main__":
    unittest.main()
