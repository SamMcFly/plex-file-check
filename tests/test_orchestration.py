from contextlib import ExitStack
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import uuid

from plexcheck.scan import scan


class OrchestrationTests(unittest.TestCase):
    def test_standard_subtitles_use_full_timeout_and_progress(self):
        parent = Path(tempfile.gettempdir()).resolve()
        root = parent / ("plex-check-orchestration-" + uuid.uuid4().hex)
        root.mkdir()
        try:
            source = root / "private-title.mkv"
            source.write_bytes(b"fixture")
            probe = {"streams": [{"index": 0, "codec_type": "video", "codec_name": "hevc"}],
                     "format": {"duration": "1", "format_name": "matroska"}}
            ok = dict(returncode=0, timed_out=False, stdout="frame=24\n", stderr="", stderr_bytes=0)
            messages = []
            with ExitStack() as stack:
                stack.enter_context(patch("plexcheck.scan.run_text", return_value=ok))
                stack.enter_context(patch("plexcheck.scan.probe_json", return_value=(probe, ok)))
                stack.enter_context(patch("plexcheck.metadata.analyze_metadata", return_value=([], {})))
                stack.enter_context(patch("plexcheck.containers.inspect_container", return_value=([], {"container_structure": {"complete": True}})))
                stack.enter_context(patch("plexcheck.containers.inspect_sidecars", return_value=([], {})))
                stack.enter_context(patch("plexcheck.scan.analyze_packets", return_value=([], {})))
                # A clean short decode should not need any external process here.
                stack.enter_context(patch("plexcheck.decode.run_text", return_value=ok))
                subtitles = stack.enter_context(patch("plexcheck.extras.scan_embedded_subtitles", return_value=([], {"embedded_subtitles": {"complete": True}})))
                report = scan(source, {"ffprobe": "probe", "ffmpeg": "decoder"},
                              timeout=3, full_timeout=456, visual=False, progress=messages.append)
            self.assertEqual(subtitles.call_args.kwargs["timeout"], 456)
            self.assertEqual(subtitles.call_args.kwargs["progress"], messages.append)
            self.assertEqual(report["metrics"]["input_identity"]["size_bytes"], 7)
            self.assertTrue(report["metrics"]["input_identity"]["modified_utc"].endswith("+00:00"))
            self.assertNotIn(str(root), str(report))
            self.assertEqual(source.read_bytes(), b"fixture")
            self.assertTrue(any("container layout" in line for line in messages))
        finally:
            assert root.resolve().parent == parent
            shutil.rmtree(root)


if __name__ == "__main__":
    unittest.main()
