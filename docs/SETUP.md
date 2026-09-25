# Setup: your first scan

This guide starts from a computer with no checker installed. You do not need to know how to code. A **terminal** is a window where you type commands and press Enter. Copy commands without the surrounding code-block markers. Replace example filenames and paths with your own; keep quotation marks around paths with spaces.

| Required item | What it is |
|---|---|
| Python | Runs the checker. Version 3.9 or newer; use a currently supported release for a new installation. |
| FFmpeg and ffprobe | Read and decode media. Both programs are usually in the same FFmpeg download. Use a recent complete build; older builds may lack options or HDR metadata support. |
| Plex File Check | The script and these instructions. Nothing is installed into Plex. |

Internet access is needed to obtain these tools. The checker itself does not upload files or contact Plex. Run scans as your normal user; administrator access is not needed. Tool installation may request administrator permission.

## 1. Download the checker

1. Open the project's [latest release](https://github.com/SamMcFly/plex-file-check/releases/latest).
2. Under **Assets**, download **Plex-File-Check-v0.1.0.zip**. Use the attached release ZIP for this guide; GitHub's automatic **Source code** ZIP does not contain the built `.pyz`.
3. Extract the ZIP. On Windows, right-click it and select **Extract All**; on macOS, double-click it; on Linux, use your file manager's extract option.
4. Open the extracted **Plex-File-Check** folder. You should see `plex-file-check.pyz`, `README.md`, and other files. Do not run it inside the ZIP viewer.

Keep the folder in Downloads or move it somewhere convenient. Your movie can stay where it is. Follow the section for your operating system, then go to step 5.

## 2. Windows

### Install Python

Get Python from [python.org](https://www.python.org/downloads/). The current Windows instructions use the [Python Install Manager](https://docs.python.org/3/using/windows.html). If you already have working Python 3.9+, keep using it.

Open PowerShell from the Start menu and run:

```powershell
py -3 --version
```

You should see `Python 3.x.x`. If the command is not found, finish installation and open a new PowerShell window. If `python --version` works and shows a suitable version, use `python` wherever this guide uses `py -3`.

### Get FFmpeg and ffprobe

1. Visit [FFmpeg's download page](https://ffmpeg.org/download.html). In **Windows EXE Files**, choose one of the linked build providers.
2. Download a compiled build appropriate for your computer, preferably a ZIP. A **static** build is simplest because it does not require separate library files. Do not download FFmpeg's source-code archive.
3. Extract it and find `ffmpeg.exe` and `ffprobe.exe`, usually inside `bin`.
4. Inside your **Plex-File-Check** folder, create **tools** and copy those two programs into it. For a shared build, keep the required DLL files beside them too.

The resulting layout is:

```text
Plex-File-Check/
  plex-file-check.pyz
  README.md
  tools/
    ffmpeg.exe
    ffprobe.exe
```

If FFmpeg already works from any terminal, copying it is optional. **PATH** is the system's list of folders to search for commands; the `tools` method avoids editing PATH.

### Open the checker folder in PowerShell

In File Explorer, open **Plex-File-Check**, click the address bar, type `powershell`, and press Enter. A PowerShell window opens in that folder. Verify:

```powershell
py -3 plex-file-check.pyz --version
```

If you used the `tools` folder, verify both programs:

```powershell
.\tools\ffmpeg.exe -version
.\tools\ffprobe.exe -version
```

If you installed them on PATH, use `ffmpeg -version` and `ffprobe -version` instead. Run your first scan:

```powershell
py -3 plex-file-check.pyz "D:\Movies\Example.mkv" --redact-name --report first-check
```

Replace the media path with your own. File Explorer's **Copy as path** option can help; pasted paths may already include quotation marks, so do not add a second pair.

## 3. macOS

If you already use [Homebrew](https://brew.sh/), install the tools in Terminal:

```sh
brew install python ffmpeg
```

If you do not have Homebrew, follow its installation instructions first, including any final shell-configuration steps, then open a new Terminal. Alternatively, install Python from [python.org](https://www.python.org/downloads/macos/) and obtain compiled FFmpeg/ffprobe builds suitable for your Mac. Match Apple Silicon or Intel as appropriate. See [FFmpeg downloads](https://ffmpeg.org/download.html) for linked providers; a build for the wrong architecture may not run.

Open **Terminal** from Applications → Utilities. Type `cd ` (including the space), drag the extracted **Plex-File-Check** folder from Finder into Terminal, and press Enter. Then run:

```sh
python3 --version
ffmpeg -version
ffprobe -version
python3 plex-file-check.pyz --version
```

Each should print a version. Run a scan, replacing the media path:

```sh
python3 plex-file-check.pyz "/Users/yourname/Movies/Example.mkv" --redact-name --report first-check
```

For manually downloaded binaries, put executable `ffmpeg` and `ffprobe` files in `tools` beside the checker, or provide full paths as shown in [Usage](USAGE.md#tool-locations). Follow the provider's instructions for executable permissions and macOS verification; do not disable system security for an unknown download.

## 4. Linux

Open your distribution's Terminal application. Install Python 3 and FFmpeg with its package manager. On Ubuntu or Debian:

```sh
sudo apt update
sudo apt install python3 ffmpeg
```

On other distributions, use their equivalent packages. You do not need pip or a Python virtual environment. Package age and codec support vary; consult [FFmpeg's download links](https://ffmpeg.org/download.html) if yours lacks a needed decoder or option.

Change to the extracted checker folder, adjusting the location if needed:

```sh
cd "$HOME/Downloads/Plex-File-Check"
python3 --version
ffmpeg -version
ffprobe -version
python3 plex-file-check.pyz --version
```

Run a scan with your file's path:

```sh
python3 plex-file-check.pyz "/home/yourname/Videos/Example.mkv" --redact-name --report first-check
```

## 5. Read and share the result

When the prompt returns, open **first-check.txt** in the checker folder with any text editor. **first-check.json** contains detail useful to someone helping you. A normal scan takes seconds to minutes, depending on the file and storage. Press **Ctrl+C** to cancel; the movie is not changed.

Start with **ERROR** and **WARNING** explanations, then **COVERAGE**. **NOT CHECKED / INCOMPLETE** is expected for work you did not request. A warning alone is not a reason to re-encode a working movie. Read [findings and limitations](FINDINGS_AND_LIMITS.md) before acting.

For a second run, use `--report second-check` or another unused name. To inspect the full file, add `--deep`; this can take hours and use substantial CPU and temporary disk space. You do not need optional Dolby Vision or HDR10+ tools for your first scan.

Review reports before posting them. `--redact-name` hides input and matching subtitle names; it cannot promise removal of every possible sensitive detail. Do not post Plex tokens, private logs, or movie files with a public issue.

For **command not found**, **can't open file**, or **FFprobe was not found**, see [Troubleshooting](TROUBLESHOOTING.md). For every option, see [Usage](USAGE.md). To uninstall the checker, delete its extracted folder and unwanted reports; Python and FFmpeg are separate installations.
