import json
import tempfile
import unittest
import shutil
import uuid
from pathlib import Path
from unittest.mock import patch

from plexcheck.containers import inspect_container, inspect_sidecars, validate_srt


def vint(value):
    for length in range(1, 9):
        if value < (1 << (7 * length)) - 1:
            return ((1 << (7 * length)) | value).to_bytes(length, "big")
    raise ValueError(value)


def element(identifier, payload=b"", unknown=False):
    width = (identifier.bit_length() + 7) // 8
    return identifier.to_bytes(width, "big") + (b"\xff" if unknown else vint(len(payload))) + payload


def matroska(children, unknown_segment=False):
    return element(0x1A45DFA3) + element(0x18538067, children, unknown_segment)


def box(kind, payload=b"", extended=False):
    if extended:
        return b"\x00\x00\x00\x01" + kind + (len(payload) + 16).to_bytes(8, "big") + payload
    return (len(payload) + 8).to_bytes(4, "big") + kind + payload


def codes(findings):
    return {entry["code"] for entry in findings}


class ContainerTests(unittest.TestCase):
    def setUp(self):
        # Normal inherited directory permissions also work under the Windows
        # sandbox; Python 3.12's special mode=0700 temp ACL denies its app token.
        self.root = Path(tempfile.gettempdir()) / ("container-tests-" + uuid.uuid4().hex)
        self.root.mkdir()
        self.media = self.root / "Example Film.mkv"

    def tearDown(self):
        shutil.rmtree(self.root)

    def inspect(self, data, format_name="matroska,webm"):
        self.media.write_bytes(data)
        result = inspect_container(self.media, format_name)
        self.assertEqual(self.media.read_bytes(), data)
        return result

    def test_seekhead_cues_pointer_is_not_actual_cues(self):
        fake = element(0x114D9B74, element(0x53AB, bytes.fromhex("1c53bb6b")))
        cluster = element(0x1F43B675, b"opaque\x1c\x53\xbb\x6b\x80")
        findings, metrics = self.inspect(matroska(fake + cluster))
        stats = metrics["container_structure"]
        self.assertTrue(stats["complete"])
        self.assertEqual(stats["cues_count"], 0)
        self.assertEqual(stats["seekhead_count"], 1)
        self.assertIn("matroska.cues_absent", codes(findings))

    def test_actual_front_cues(self):
        findings, metrics = self.inspect(matroska(element(0x1C53BB6B) + element(0x1F43B675)))
        self.assertTrue(metrics["container_structure"]["cues_before_clusters"])
        self.assertFalse(any(entry["severity"] == "error" for entry in findings))

    def test_actual_end_cues_are_advisory(self):
        findings, metrics = self.inspect(matroska(element(0x1F43B675) + element(0x1C53BB6B)))
        self.assertFalse(metrics["container_structure"]["cues_before_clusters"])
        cue_finding = next(entry for entry in findings if entry["code"] == "matroska.cues_at_end")
        self.assertEqual(cue_finding["severity"], "info")

    def test_unknown_segment_and_cluster_skip_block_payload_signatures(self):
        block = element(0xA3, b"\x1c\x53\xbb\x6b\x80other packet bytes")
        children = element(0x1F43B675, block, unknown=True) + element(0x1C53BB6B)
        findings, metrics = self.inspect(matroska(children, unknown_segment=True))
        stats = metrics["container_structure"]
        self.assertTrue(stats["complete"])
        self.assertEqual(stats["cues_count"], 1)
        self.assertEqual(stats["cluster_count"], 1)
        self.assertNotIn("container.structure_invalid", codes(findings))

    def test_unknown_nested_element_is_incomplete_not_corruption(self):
        cluster = element(0x1F43B675, element(0xA0, unknown=True), unknown=True)
        findings, metrics = self.inspect(matroska(cluster, unknown_segment=True))
        self.assertIn("container.structure_incomplete", codes(findings))
        self.assertNotIn("matroska.cues_absent", codes(findings))
        self.assertFalse(metrics["container_structure"]["complete"])

    def test_truncated_segment_and_partial_header(self):
        full = matroska(element(0x1F43B675, b"payload"))
        findings, _ = self.inspect(full[:-2])
        self.assertIn("container.structure_invalid", codes(findings))
        findings, _ = self.inspect(matroska(b"\x1f\x43", unknown_segment=True))
        self.assertIn("container.structure_invalid", codes(findings))

    def test_declared_child_cannot_overrun_parent(self):
        children = bytes.fromhex("1f43b675") + vint(200)
        findings, _ = self.inspect(matroska(children))
        self.assertIn("container.structure_invalid", codes(findings))

    def test_scan_limit_is_reported_without_false_missing_cues(self):
        data = matroska(element(0xEC) * 20)
        with patch("plexcheck.containers.MAX_ELEMENTS", 5):
            findings, metrics = self.inspect(data)
        self.assertIn("container.structure_incomplete", codes(findings))
        self.assertNotIn("matroska.cues_absent", codes(findings))
        self.assertFalse(metrics["container_structure"]["complete"])

    def test_mp4_faststart_and_payload_signatures(self):
        data = box(b"ftyp", b"isom") + box(b"mdat", box(b"moov")) + box(b"moov", box(b"free"))
        findings, metrics = self.inspect(data, "mov,mp4,m4a,3gp,3g2,mj2")
        self.assertEqual(metrics["container_structure"]["moov_count"], 1)
        self.assertIn("mp4.faststart_absent", codes(findings))
        data = box(b"moov", box(b"free"), extended=True) + box(b"mdat", b"data")
        findings, metrics = self.inspect(data, "mp4")
        self.assertTrue(metrics["container_structure"]["moov_before_mdat"])
        self.assertNotIn("mp4.faststart_absent", codes(findings))

    def test_mp4_fragmented_caveat_from_moov_mvex(self):
        data = box(b"mdat", b"data") + box(b"moov", box(b"mvex"))
        findings, metrics = self.inspect(data, "mp4")
        self.assertTrue(metrics["container_structure"]["fragmented"])
        self.assertIn("mp4.fragmented", codes(findings))
        self.assertNotIn("mp4.faststart_absent", codes(findings))

    def test_mp4_size_zero_and_truncation(self):
        data = box(b"moov") + b"\x00\x00\x00\x00mdatpayload"
        findings, metrics = self.inspect(data, "mp4")
        self.assertTrue(metrics["container_structure"]["complete"])
        self.assertNotIn("container.structure_invalid", codes(findings))
        for data in [b"\x00\x00\x00", box(b"moov")[:-1], b"\x00\x00\x00\x04mdat", box(b"moov", b"bad")]:
            findings, _ = self.inspect(data, "mp4")
            self.assertIn("container.structure_invalid", codes(findings))

    def test_unsupported_and_missing_inputs_are_skipped(self):
        findings, _ = inspect_container(self.media, "mpegts")
        self.assertIn("container.structure_unsupported", codes(findings))
        findings, _ = inspect_container(self.media, "matroska")
        self.assertIn("container.read_unavailable", codes(findings))

    def sidecar(self, name, text, encoding="utf-8"):
        target = self.root / name
        target.write_text(text, encoding=encoding)
        return target

    def test_srt_discovery_is_restricted_and_small_valid_cue_accepted(self):
        cue = "1\n00:00:01,000 --> 00:00:02,000\nHi.\n"
        exact = self.sidecar("Example Film.srt", cue)
        language = self.sidecar("Example Film.en-US.srt", cue)
        self.sidecar("Example Film trailer.srt", "bad")
        self.sidecar("Example Film.commentary.srt", "bad")
        self.sidecar("Other Film.srt", "bad")
        before = {p: p.read_bytes() for p in (exact, language)}
        findings, metrics = inspect_sidecars(self.media)
        self.assertEqual(metrics["sidecar_srt"]["files"], 2)
        self.assertFalse(findings)
        for path, data in before.items():
            self.assertEqual(path.read_bytes(), data)

    def test_srt_malformed_cues_no_private_text_in_evidence(self):
        private = "PRIVATE TEST DIALOGUE 90817"
        self.sidecar("Example Film.eng.srt", "1\n00:61:01,000 --> 00:62:02,000\n" + private + "\n\n2\n00:00:03,000 --> 00:00:02,000\n\n3\n00:00:01,000 --> 00:00:02,000\n" + private)
        findings, metrics = inspect_sidecars(self.media)
        self.assertTrue({"srt.invalid_blocks", "srt.nonpositive_duration", "srt.out_of_order", "srt.empty_cues"} <= codes(findings))
        self.assertNotIn(private, json.dumps([findings, metrics]))

    def test_srt_overlaps_advisory_and_credit_heuristic(self):
        self.sidecar("Example Film.srt", "1\n00:00:01,000 --> 00:00:04,000\nSubtitles by TEST\n\n2\n00:00:02,000 --> 00:00:03,000\nHello\n")
        findings, metrics = inspect_sidecars(self.media)
        self.assertIn("srt.overlap", codes(findings))
        self.assertIn("srt.credit_candidates", codes(findings))
        self.assertFalse(any(entry["severity"] == "error" for entry in findings))
        self.assertEqual(metrics["sidecar_srt"]["reports"][0]["overlaps"], 1)

    def test_empty_control_and_encoding(self):
        self.sidecar("Example Film.srt", "\ufeff \n")
        findings, _ = inspect_sidecars(self.media)
        self.assertIn("srt.empty", codes(findings))
        self.sidecar("Example Film.srt", "1\n00:00:00,000 --> 00:00:01,000\nHi\x00\n")
        findings, _ = inspect_sidecars(self.media)
        self.assertIn("srt.invalid_characters", codes(findings))
        self.sidecar("Example Film.srt", "1\n00:00:00,000 --> 00:00:01,000\nCafé\n", encoding="utf-16")
        findings, _ = inspect_sidecars(self.media)
        self.assertNotIn("srt.encoding_unknown", codes(findings))
        self.sidecar("Example Film.srt", "1\n00:00:00,000 --> 00:00:01,000\nCafé\n", encoding="cp1252")
        findings, _ = inspect_sidecars(self.media)
        self.assertIn("srt.encoding_unknown", codes(findings))
        self.assertFalse(any(entry["severity"] == "error" for entry in findings))

    def test_size_limit_is_bounded(self):
        self.sidecar("Example Film.srt", "X" * 200)
        with patch("plexcheck.containers.MAX_SRT_BYTES", 100):
            findings, metrics = inspect_sidecars(self.media)
        self.assertIn("srt.size_limit", codes(findings))
        self.assertEqual(metrics["sidecar_srt"]["reports"][0]["bytes_read"], 101)

    def test_optional_runtime_tolerance(self):
        target = self.sidecar("Example Film.srt", "1\n00:00:01,000 --> 00:00:15,000\nHello\n")
        findings, _ = validate_srt(target, duration=10)
        self.assertNotIn("srt.past_runtime", codes(findings))
        findings, _ = validate_srt(target, duration=9.9)
        self.assertIn("srt.past_runtime", codes(findings))

    def test_missing_cue_separator_is_malformed(self):
        target = self.sidecar("Example Film.srt", "1\n00:00:01,000 --> 00:00:02,000\nFirst\n2\n00:00:03,000 --> 00:00:04,000\nSecond\n")
        findings, _ = validate_srt(target)
        self.assertIn("srt.invalid_blocks", codes(findings))


if __name__ == "__main__":
    unittest.main()
