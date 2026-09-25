import copy
import unittest
from pathlib import Path
from plexcheck.hdr10plus import analyze_hdr10plus, scan_hdr10plus


def scene():
    return {"LuminanceParameters": {"MaxScl": [100, 200, 300], "AverageRGB": 20,
            "LuminanceDistributions": {"DistributionValues": [10, 20, 30, 40]}},
            "TargetedSystemDisplayMaximumLuminance": 1000,
            "BezierCurveData": {"KneePointX": 100, "KneePointY": 50}}


class Hdr10PlusTests(unittest.TestCase):
    def test_good_scenes_are_heuristic_not_certification(self):
        data = {"SceneInfo": [scene() for _ in range(10)]}
        original = copy.deepcopy(data)
        fs, m = analyze_hdr10plus(data)
        self.assertTrue(m["pipeline_viable"])
        self.assertFalse(m["conformance_verified"])
        self.assertEqual(data, original)
        self.assertFalse(any(f["severity"] == "error" for f in fs))

    def test_zero_dark_scenes_are_advisory(self):
        dark = scene()
        dark["LuminanceParameters"]["AverageRGB"] = 0
        dark["LuminanceParameters"]["MaxScl"] = [0,0,0]
        fs, m = analyze_hdr10plus({"SceneInfo": [dark for _ in range(12)]})
        self.assertFalse(m["pipeline_viable"])
        self.assertTrue(any(f["severity"] == "warning" for f in fs))
        self.assertFalse(any(f["severity"] == "error" for f in fs))

    def test_missing_or_nonnumeric_not_healthy(self):
        malformed = scene()
        malformed["LuminanceParameters"]["AverageRGB"] = "nan"
        _, m = analyze_hdr10plus({"SceneInfo": [malformed]})
        self.assertFalse(m["measured"])
        self.assertIsNone(m["pipeline_viable"])
        _, m = analyze_hdr10plus({"SceneInfo": [{}, None]})
        self.assertFalse(m["measured"])

    def test_negative_is_not_valid_measurement(self):
        negative = scene()
        negative["LuminanceParameters"]["MaxScl"] = [-1,10,20]
        _, m = analyze_hdr10plus({"SceneInfo": [negative]})
        self.assertFalse(m["measured"])

    def test_absent_payload_and_missing_tool(self):
        fs, m = analyze_hdr10plus({})
        self.assertEqual(fs[0]["severity"], "skipped")
        fs, m = scan_hdr10plus(Path("absent.mkv"), {"streams":[{"index":0,"codec_type":"video","codec_name":"hevc"}]}, "ffmpeg", None)
        self.assertEqual(fs[0]["code"], "hdr10plus.tools_missing")

    def test_not_hevc_is_not_corruption(self):
        fs, _ = scan_hdr10plus(Path("anything.mp4"), {"streams":[{"index":0,"codec_type":"video","codec_name":"h264"}]}, "ffmpeg", "tool")
        self.assertEqual(fs[0]["severity"], "skipped")
