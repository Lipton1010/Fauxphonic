# Windows packaging

Run `scripts/bootstrap.ps1`, then `scripts/build_windows.ps1` in PowerShell.
Python 3.12 must already be available; `-Python` can select its path. The
private `.venv` and project-local FFmpeg compiler do not change global PATH or
install software system-wide. Processing stays offline.

The x64 FFmpeg and ffprobe 8.1.1 executables are built from pinned upstream
sources with the exact flags in `scripts/ffmpeg_configure_args.txt`. The build
uses pinned w64devkit 2.10.0, an explicit `SOURCE_DATE_EPOCH`, and statically
links LAME 4.0 for MP3 encoding and Opus 1.5.2 for Opus encoding. Two clean
builds on this host produced byte-identical binaries; hashes, linked source
hashes, and measured codec behavior are in `evidence/ffmpeg-reproducible-build.json`
and `evidence/ffmpeg-delivery-codec-probe.json`. `scripts/build_ffmpeg.ps1`
repeats the build and checks the resulting hashes.
The previous broad Gyan GPL build is retained only as historical material in
`.cache`, outside the application payload.

The portable output is `dist/Fauxphonic/`. `Fauxphonic.exe` has no console;
`Fauxphonic-worker.exe` runs processing and VAD in a separate process. The
worker payload includes the pinned Silero VAD ONNX model. Qt preview selects
Windows Media Foundation; its alternate FFmpeg backend, Qt PDF, QML/Quick, and
Virtual Keyboard modules are excluded. Build checks fail if those modules or
developer `README.md` / `PROJECT_STATE.md` files enter the bundle.

Before checksums are generated, the build places the versioned FFmpeg, LAME,
Opus, Qt, PySide, and libsndfile corresponding-source archives, licenses, and
build recipes in `legal/sources/` and `licenses/`. `dependency-inventory.json` is
generated from the finished payload, listing every bundled Python distribution
and native executable, DLL, extension, and model file with its hash and
attribution. `SHA256SUMS.txt` covers the complete bundle.

`scripts/packaged_smoke.py` relocates the bundle, removes developer Python/Qt
paths, renders a synthetic Unicode-named file through the bundled worker, and
launches/closes the native GUI. It does not replace the clean-Windows installer
test or prove operation on every machine.

## Desktop installer

After a portable build, run
`powershell -ExecutionPolicy Bypass -File scripts/build_installer.ps1`. It
verifies every payload hash and writes a versioned installer, adjacent SHA-256
file, and installer build evidence. The current version is 0.2.1. The tested
Inno Setup compiler is 6.7.1; the build evidence records its exact hash and
engine version.

The installer uses a stable AppId and per-user installation under
`LocalAppData\Programs\Fauxphonic`. `AppMutex=Local\Fauxphonic-ApplicationOrWorker`
causes Setup and Uninstall to refuse while the GUI or worker holds the mutex;
they do not close the process. The smoke test exercises both cases. Uninstall
removes installer-owned files only and preserves user recordings, projects,
exports, and application data.
Because the withdrawn 0.1.0 preview did not use this mutex and shipped a
different payload, Setup refuses an in-place update from that exact version.
Close it, uninstall 0.1.0, and then install 0.2.1; its application data remains.

Audio preview requires Windows Media Foundation. Windows N editions may not
include it until the Microsoft Media Feature Pack is installed. The installer
does not add Windows features or codec packs. The required Windows backend
loaded and sought WAV and FLAC in automated muted checks. Compressed formats
and explicitly selected streams are decoded locally by the worker to temporary
PCM WAV before native playback, so Opus does not require a system codec pack.
Windows N remains untested; automated playback checks are not listening tests.

The installer is unsigned. Clean Windows Sandbox acceptance uses no installed
Python, disabled networking and disabled virtual GPU; it checks installation,
real speech processing, exact export length, native GUI launch, running-app
uninstall refusal and preservation of personal files. The exact installer hash
and result are in `evidence/sandbox-installer.json`. Host acceptance additionally
checks running-worker protection and installed payload hashes. This is not
evidence for every Windows edition or actual 16 GB GPU hardware. Representative
human listening and code signing remain pending.
