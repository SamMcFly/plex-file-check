import json
from pathlib import Path
import unittest
from unittest.mock import patch

from plexcheck.extras import scan_loudness, scan_embedded_subtitles


def result(stderr="", **extra):
    r = dict(returncode=0, timed_out=False, stdout="", stderr=stderr, output_truncated=False)
    r.update(extra)
    return r


def audio_probe():
    return {"streams": [dict(index=1, codec_type="audio"), dict(index=4, codec_type="audio")], "format": {"duration": "60"}}


def subtitle_probe(codec="subrip"):
    return {"streams": [dict(index=0, codec_type="video", duration="10"), dict(index=3, codec_type="subtitle", codec_name=codec)], "format": {"duration": "10"}}


class ExtrasTests(unittest.TestCase):
    def test_loudness_all_audio_to_null_with_protocol_restriction(self):
        measurement = dict(input_i="-23.1", input_tp="-2.5", input_lra="5.0", input_thresh="-33.0")
        calls = []
        def run(args, **kwargs):
            calls.append(args)
            return result("unrelated {x}\n" + json.dumps(measurement))
        with patch("plexcheck.extras.run_text", side_effect=run):
            findings, metrics = scan_loudness("source.mkv", audio_probe(), "ffmpeg")
        self.assertEqual(len(calls), 2)
        self.assertEqual([a[a.index("-map") + 1] for a in calls], ["0:1", "0:4"])
        self.assertTrue(metrics["loudness"]["complete"])
        for args in calls:
            self.assertIn("-nostdin", args)
            self.assertEqual(args[args.index("-protocol_whitelist") + 1], "file,pipe")
            self.assertEqual(args[-3:], ["-f", "null", "-"])
        self.assertTrue(all(f["severity"] == "info" for f in findings))

    def test_silence_is_info_not_damage(self):
        with patch("plexcheck.extras.run_text", return_value=result(json.dumps(dict(input_i="-inf", input_tp="-inf", input_lra="0.0", input_thresh="-70.0")))):
            findings, metrics = scan_loudness("source.mkv", audio_probe(), "ffmpeg")
        self.assertTrue(metrics["loudness"]["complete"])
        self.assertTrue(all(f["code"] == "loudness.silence" and f["severity"] == "info" for f in findings))

    def test_nonfinite_measurement_cannot_pass(self):
        with patch("plexcheck.extras.run_text", return_value=result(json.dumps(dict(input_i="-20", input_tp="NaN", input_lra="7", input_thresh="-30")))):
            findings, metrics = scan_loudness("source.mkv", audio_probe(), "ffmpeg")
        self.assertFalse(metrics["loudness"]["complete"])
        self.assertTrue(all(f["severity"] == "skipped" for f in findings))

    def test_loudness_timeout_cannot_pass_with_partial_json(self):
        with patch("plexcheck.extras.run_text", return_value=result('{"input_i":"-23"}', timed_out=True)):
            findings, metrics = scan_loudness("source.mkv", audio_probe(), "ffmpeg")
        self.assertFalse(metrics["loudness"]["complete"])
        self.assertTrue(all(f["code"] == "loudness.incomplete" for f in findings))

    def test_measurement_outside_reference_is_only_info(self):
        with patch("plexcheck.extras.run_text", return_value=result(json.dumps(dict(input_i="-8", input_tp="0.3", input_lra="20", input_thresh="-18")))):
            findings, metrics = scan_loudness("source.mkv", audio_probe(), "ffmpeg")
        self.assertTrue(metrics["loudness"]["complete"])
        self.assertFalse(metrics["loudness"]["tracks"][0]["within_reference_targets"])
        self.assertTrue(all(f["severity"] == "info" for f in findings))

    def test_measurement_with_decode_errors_is_incomplete(self):
        text = "Error while decoding stream\n" + json.dumps(dict(input_i="-23", input_tp="-3", input_lra="3", input_thresh="-33"))
        with patch("plexcheck.extras.run_text", return_value=result(text)):
            findings, metrics = scan_loudness("source.mkv", audio_probe(), "ffmpeg")
        self.assertFalse(metrics["loudness"]["complete"])
        self.assertIn("loudness.decode_diagnostics", [f["code"] for f in findings])

    def test_text_subtitle_extraction_validation_and_cleanup(self):
        destinations = []
        def run(args, **kwargs):
            self.assertIn("-nostdin", args)
            self.assertEqual(args[args.index("-protocol_whitelist") + 1], "file,pipe")
            self.assertEqual(args[args.index("-map") + 1], "0:3")
            dest = Path(args[-1])
            self.assertEqual(dest.name, "subtitle.srt")
            self.assertNotIn("private-title", dest.name)
            destinations.append(dest)
            dest.write_text("1\n00:00:00,000 --> 00:00:01,000\nHi\n", encoding="utf-8")
            return result()
        with patch("plexcheck.extras.run_text", side_effect=run):
            findings, metrics = scan_embedded_subtitles("private-title.mkv", subtitle_probe(), "ffmpeg")
        self.assertTrue(metrics["embedded_subtitles"]["complete"])
        self.assertEqual(metrics["embedded_subtitles"]["tracks"][0]["cue_count"], 1)
        self.assertTrue(all(not p.exists() for p in destinations))
        self.assertNotIn("private-title", json.dumps([findings, metrics]))

    def test_bitmap_subtitle_is_explicitly_uninspected(self):
        with patch("plexcheck.extras.run_text") as run:
            findings, metrics = scan_embedded_subtitles("source.mkv", subtitle_probe("hdmv_pgs_subtitle"), "ffmpeg")
        run.assert_not_called()
        self.assertFalse(metrics["embedded_subtitles"]["complete"])
        self.assertEqual(findings[0]["severity"], "skipped")

    def test_subtitle_failed_process_cannot_pass_existing_file(self):
        def run(args, **kwargs):
            Path(args[-1]).write_text("1\n00:00:00,000 --> 00:00:01,000\nHi\n", encoding="utf-8")
            return result(returncode=1)
        with patch("plexcheck.extras.run_text", side_effect=run):
            findings, metrics = scan_embedded_subtitles("source.mkv", subtitle_probe(), "ffmpeg")
        self.assertFalse(metrics["embedded_subtitles"]["complete"])
        self.assertEqual(findings[0]["code"], "subtitle.extraction_incomplete")

    def test_success_with_errors_has_no_clean_claim(self):
        def run(args, **kwargs):
            Path(args[-1]).write_text("1\n00:00:00,000 --> 00:00:01,000\nHi\n", encoding="utf-8")
            return result("error decoding a cue")
        with patch("plexcheck.extras.run_text", side_effect=run):
            findings, metrics = scan_embedded_subtitles("source.mkv", subtitle_probe(), "ffmpeg")
        self.assertFalse(metrics["embedded_subtitles"]["complete"])
        self.assertIn("subtitle.extraction_diagnostics", [f["code"] for f in findings])

    def test_no_subtitles_not_applicable(self):
        findings, metrics = scan_embedded_subtitles("source.mkv", {"streams": []}, None)
        self.assertEqual(findings, [])
        self.assertFalse(metrics["embedded_subtitles"]["applicable"])


if __name__ == "__main__":
    unittest.main()
