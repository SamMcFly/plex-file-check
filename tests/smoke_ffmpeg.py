"""Small real-FFmpeg integration checks; all media is generated here."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parent.parent


def run(args):
    return subprocess.run([str(a) for a in args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=180)


def require_ok(args):
    result = run(args)
    assert result.returncode == 0, (args, result.stdout, result.stderr)
    return result


def inspect(entry, media, *flags):
    before = hashlib.sha256(media.read_bytes()).hexdigest()
    result = run([sys.executable, entry, media, "--json", "--quiet", *flags])
    assert result.returncode in (0, 1, 2), result.stderr
    report = json.loads(result.stdout)
    assert report["exit_code"] == result.returncode
    assert hashlib.sha256(media.read_bytes()).hexdigest() == before
    assert str(media.parent) not in result.stdout
    return report


def main():
    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg and shutil.which("ffprobe"), "FFmpeg and ffprobe must be on PATH"
    scratch_parent = Path(tempfile.gettempdir()).resolve()
    scratch = scratch_parent / ("plex-check-smoke-" + uuid.uuid4().hex)
    scratch.mkdir(mode=0o777 if os.name == "nt" else 0o700)
    try:
        mkv = scratch / "sample [test] ü.mkv"
        mp4 = scratch / "fast start ü.mp4"
        require_ok([ffmpeg, "-v", "error", "-nostdin", "-f", "lavfi", "-i",
                    "testsrc2=size=160x90:rate=24", "-f", "lavfi", "-i",
                    "sine=frequency=440:sample_rate=48000", "-t", "3", "-c:v",
                    "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", mkv])
        require_ok([ffmpeg, "-v", "error", "-nostdin", "-i", mkv, "-c", "copy",
                    "-movflags", "+faststart", mp4])
        require_ok([sys.executable, ROOT / "build_release.py", "--output-dir", scratch / "build"])
        source = ROOT / "plex_check.py"
        portable = scratch / "build" / "plex-file-check.pyz"
        report = inspect(source, mkv, "--deep")
        assert report["metrics"]["full_decode"].get("clean"), report["findings"]
        assert report["metrics"]["full_decode"]["decoded_video_frames"] == 72
        assert not report["counts"].get("error"), report["findings"]
        assert report["profile"] == "focused" and report["exit_code"] == 0, report["findings"]
        assert "visual_samples" not in report["metrics"]
        assert "embedded_subtitles" not in report["metrics"]
        report = inspect(portable, mp4, "--redact-name")
        assert report["file"] == "media.mp4"
        assert report["metrics"]["container"]["container_structure"]["moov_before_mdat"]
        assert not report["counts"].get("error"), report["findings"]
        assert report["exit_code"] == 0, report["findings"]
        assert "packets" not in report["metrics"]
        compact = run([sys.executable, portable, mp4, "--quiet"])
        assert compact.returncode == 0, compact.stderr
        assert "No major file problems" in compact.stdout
        assert "COVERAGE" not in compact.stdout and "INFO / CONTEXT" not in compact.stdout
        expanded = run([sys.executable, portable, mp4, "--quiet", "--details"])
        assert expanded.returncode == 0 and "COVERAGE" in expanded.stdout
        audit = inspect(portable, mp4, "--advanced", "--no-visual")
        assert audit["profile"] == "advanced" and "packets" in audit["metrics"]
        assert "embedded_subtitles" in audit["metrics"]
        prefix = scratch / "report"
        result = run([sys.executable, portable, mkv, "--quick", "--report", prefix])
        assert result.returncode in (0, 1), result.stderr
        saved = Path(str(prefix) + ".json")
        before = saved.read_bytes()
        result = run([sys.executable, portable, mkv, "--quick", "--report", prefix])
        assert result.returncode == 3 and saved.read_bytes() == before
        empty = scratch / "empty.mkv"
        empty.write_bytes(b"")
        assert inspect(portable, empty, "--quick")["exit_code"] == 2
        result = run([sys.executable, portable, mkv, "--ffprobe", "no-such-probe-458219"])
        assert result.returncode == 3
        print("PASS: source/portable, MKV decode (72 frames), MP4 faststart, Unicode paths,")
        print("      redaction, input hashes, report overwrite, empty input, missing tool.")
    finally:
        # This is only the fresh UUID directory created above, never a user input.
        assert scratch.resolve().parent == scratch_parent and scratch.name.startswith("plex-check-smoke-")
        shutil.rmtree(scratch)


if __name__ == "__main__":
    main()
