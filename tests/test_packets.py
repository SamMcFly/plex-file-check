import unittest
from plexcheck.packets import analyze_packets, timeline_gap, sample_starts
from plexcheck.deep import analyze_full_packets

PROBE = {"streams": [{"index": 0, "codec_type": "video", "height": 1080}]}


def packet(pts, dts=None, size=1000, duration=.04, pos=None, flags="__"):
    return dict(stream_index=0, pts_time=str(pts), dts_time=str(dts if dts is not None else pts),
                duration_time=str(duration), size=str(size), pos=str(pos if pos is not None else pts*10000), flags=flags)


class PacketTests(unittest.TestCase):
    def test_reordered_pts_not_a_fault(self):
        p = [packet(.08, 0), packet(0, .04), packet(.04, .08), packet(.12, .12)]
        fs, _ = analyze_packets([p], PROBE)
        self.assertFalse(any(f["code"] in ("DTS_BACKWARDS", "PACKET_GAP") for f in fs))

    def test_real_backwards_dts(self):
        fs, _ = analyze_packets([[packet(0, 0), packet(1, 1), packet(2, .5)]], PROBE)
        self.assertIn("DTS_BACKWARDS", [f["code"] for f in fs])

    def test_no_cross_window_gap(self):
        fs, _ = analyze_packets([[packet(0), packet(1)], [packet(1000), packet(1001)]], PROBE)
        self.assertNotIn("PACKET_GAP", [f["code"] for f in fs])

    def test_long_still_frame_not_missing_media(self):
        gap, _ = timeline_gap([packet(0, duration=10), packet(10)])
        self.assertEqual(gap, 0)

    def test_overlapping_windows_do_not_inflate_bitrate(self):
        p = [packet(i/10, size=10000) for i in range(51)]
        _, single = analyze_packets([p], PROBE)
        _, overlap = analyze_packets([p, p], PROBE)
        self.assertEqual(single["sampled_video_peak_1s_mbps"], overlap["sampled_video_peak_1s_mbps"])
        self.assertAlmostEqual(single["sampled_video_peak_1s_mbps"], .8)

    def test_timestamp_gap_detected(self):
        fs, _ = analyze_full_packets(iter([packet(0), packet(1), packet(10)]), PROBE)
        self.assertIn("FULL_PACKET_GAP", [f["code"] for f in fs])

    def test_tiny_video_samples_deduplicated(self):
        self.assertEqual(sample_starts(2), [0])

    def test_nonfinite_timestamps_are_not_valid(self):
        fs, _ = analyze_full_packets(iter([packet("nan"), packet(1), packet(2)]), PROBE)
        self.assertIn("FULL_PTS_MISSING", [f["code"] for f in fs])

    def test_huge_timestamp_gap_is_bounded(self):
        packets = [packet(0), packet(1), packet(1e12)]
        fs, _ = analyze_full_packets(iter(packets), PROBE)
        self.assertIn("FULL_PACKET_GAP", [f["code"] for f in fs])
        fs, _ = analyze_packets([packets], PROBE)
        self.assertIn("PACKET_GAP", [f["code"] for f in fs])


if __name__ == "__main__":
    unittest.main()
