"""Build shareable assets from an explicit allowlist; no external packages."""
import argparse
import ast
import hashlib
from pathlib import Path
import shutil
import zipfile

from plexcheck import __version__


def build(output):
    source = Path(__file__).resolve().parent
    output = output.resolve()
    release = output / "Plex-File-Check"
    release.mkdir(parents=True, exist_ok=True)
    files = sorted(set(
        list(source.glob("*.md"))
        + [source / name for name in ("LICENSE", "plex_check.py", "build_release.py")]
        + list((source / "plexcheck").glob("*.py"))
        + list((source / "tests").glob("*.py"))
        + list((source / "docs").glob("*.md"))
        + list((source / "ci").glob("*.yml"))
    ))
    packaged = []
    for path in files:
        if path.suffix == ".py":
            ast.parse(path.read_text(encoding="utf-8-sig"), feature_version=(3, 9))
        target = release / path.relative_to(source)
        if target.resolve() == path.resolve():
            raise ValueError("Output must not replace the source tree")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        packaged.append(target)
    portable = release / "plex-file-check.pyz"
    with zipfile.ZipFile(portable, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("__main__.py", "from plexcheck.cli import main\nraise SystemExit(main())\n")
        for path in sorted((source / "plexcheck").glob("*.py")):
            archive.write(path, path.relative_to(source).as_posix())
    packaged.append(portable)
    manifest = release / "SHA256SUMS.txt"
    manifest.write_text("".join(
        hashlib.sha256(path.read_bytes()).hexdigest() + "  " + path.relative_to(release).as_posix() + "\n"
        for path in sorted(packaged)), encoding="utf-8")
    packaged.append(manifest)
    bundle = output / ("Plex-File-Check-v" + __version__ + ".zip")
    # Never walk the destination: stale files, caches or local tools cannot leak.
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(packaged):
            archive.write(path, (Path(release.name) / path.relative_to(release)).as_posix())
    standalone = output / portable.name
    shutil.copyfile(portable, standalone)
    (output / "SHA256SUMS.txt").write_text("".join(
        hashlib.sha256(path.read_bytes()).hexdigest() + "  " + path.name + "\n"
        for path in (bundle, standalone)), encoding="utf-8")
    print("Built {} files in {}".format(len(packaged), output))
    return bundle, standalone


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent / "dist")
    build(parser.parse_args().output_dir)
