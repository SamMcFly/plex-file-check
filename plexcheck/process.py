"""Bounded external commands; argument lists only, never a shell."""
from contextlib import contextmanager
from pathlib import Path
import json
import os
import shutil
import subprocess
import sys
import tempfile


def locate(name, explicit=None):
    if explicit:
        path = Path(explicit).expanduser()
        return str(path.resolve()) if path.is_file() else shutil.which(explicit)
    # Portable binaries may be next to the CLI, or on PATH.
    entry = Path(sys.argv[0]).resolve()
    base = entry.parent if entry.suffix.lower() == ".pyz" else Path(__file__).resolve().parent.parent
    for directory in (base / "tools", base):
        candidate = directory / (name + (".exe" if os.name == "nt" else ""))
        if candidate.is_file():
            return str(candidate)
    return shutil.which(name)


@contextmanager
def run_file(args, timeout=120):
    with tempfile.TemporaryFile(mode="w+b") as stdout, tempfile.TemporaryFile(mode="w+b") as stderr:
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        process = subprocess.Popen([str(a) for a in args], stdin=subprocess.DEVNULL,
                                   stdout=stdout, stderr=stderr, shell=False, creationflags=flags)
        timed_out = False
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            process.kill()
            process.wait()
        except BaseException:
            process.kill()
            process.wait()
            raise
        stdout.seek(0)
        stderr.seek(0)
        # Tool diagnostics may be huge on a corrupt input. Keep start and end.
        head = stderr.read(49152)
        stderr.seek(0, 2)
        length = stderr.tell()
        if length > 65536:
            stderr.seek(-16384, 2)
            head += b"\n[diagnostics truncated]\n" + stderr.read()
        else:
            stderr.seek(len(head))
            head += stderr.read()
        yield stdout, dict(returncode=process.returncode, timed_out=timed_out,
                           stderr=head.decode("utf-8", errors="replace"), stderr_bytes=length)


def run_text(args, timeout=120, limit=8_000_000):
    with run_file(args, timeout) as (handle, result):
        data = handle.read(limit + 1)
        result["output_truncated"] = len(data) > limit
        result["stdout"] = data[:limit].decode("utf-8", errors="replace")
        return result


def probe_json(tool, path, extra=None, timeout=120):
    result = run_text([tool, "-v", "error", "-protocol_whitelist", "file,pipe"] +
                      list(extra or ["-show_format", "-show_streams"]) +
                      ["-of", "json", str(path)], timeout)
    if result["timed_out"] or result["returncode"] or result["output_truncated"]:
        return None, result
    try:
        return json.loads(result["stdout"]), result
    except (ValueError, TypeError):
        return None, result
