# Contributing

Bug reports, clearer instructions, reproducible false positives and platform testing are welcome. This project needs evidence about where its rules are useful and where they are not. A warning threshold inherited from the author's workflow is not a Plex requirement.

## Reporting results

Use the issue templates. State what was observed separately from the proposed explanation. Include versions, mode, finding codes and a reviewed/redacted report. Describe the actual playback outcome and client when relevant. Prefer small synthetic fixtures over real movies. Do not publish credentials, private logs or copyrighted media.

## Development

Python 3.9+ and its standard library are sufficient for unit tests and release building; no pip dependencies. Use a current supported Python for development. From the repository folder:

```sh
python3 -m unittest discover -s tests
python3 plex_check.py --help
python3 build_release.py
```

On Windows, use `py -3` instead of `python3`. The build places the source ZIP, portable `.pyz` and SHA-256 manifest in `dist`. Generated assets, user reports, local tools, media and caches must not be committed.

With FFmpeg and ffprobe on PATH, run the small synthetic integration check:

```sh
python3 tests/smoke_ffmpeg.py
```

It creates its own short H.264/AAC Matroska and MP4 fixtures, exercises source and portable entry points, checks decode counts, redaction and report overwrite protection, and verifies input hashes. It never needs a user's movie.

### Optional GitHub automation

`ci/check.yml` is a ready-to-enable GitHub Actions template for unit tests, packaging and synthetic FFmpeg checks on Windows, macOS and Linux. **It is not an active workflow and has not run on GitHub.** The publishing credential could create this repository but lacked GitHub's separate `workflow` scope. To enable it, a maintainer with suitable access can move it to `.github/workflows/check.yml` and push that change (an OAuth token used for that push needs workflow permission). Consult the resulting run before claiming any platform passed; a template alone is not test evidence.

For changes, preserve read-only input handling and explicit incomplete states. Use argument lists rather than shell-built commands. Failed extraction, missing metadata and timeouts must never become clean results. Add focused tests for behavior changes, especially false positives, timestamp handling and malformed input. Document a new threshold's origin and limitations.

## Releases

1. Update the version in `plexcheck/__init__.py`, changelog, validation notes and versioned download examples.
2. Run the tests and build above. Inspect the archive inventory and all public text for private paths, media, logs or credentials.
3. Run the packaged `.pyz`. Run the synthetic checks on available platforms, or enable the GitHub workflow and verify its results. Record limits rather than treating untested platforms, optional tools or hardware as covered.
4. Create a version tag and GitHub release. Attach `dist/Plex-File-Check-vVERSION.zip`, `dist/plex-file-check.pyz`, and `dist/SHA256SUMS.txt`; users should not have to build a download themselves.

`build_release.py` uses an explicit source-file allowlist, so local tools, reports, temporary folders and `.git` are excluded. The top-level manifest verifies the downloadable ZIP and `.pyz`; the ZIP also contains a manifest of its individual files. Checksums detect differences from the published assets; they are not a digital signature or certification.

Contributions are distributed under the project's [MIT license](LICENSE). FFmpeg and the optional tools are separate projects with their own licenses and are not bundled.
