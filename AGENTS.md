# Fauxphonic

Read local_audio_studio_codex_plan.md as the product specification and PROJECT_STATE.md for actual progress.

- Windows native PySide6 Widgets; no web server. Process audio outside the GUI process.
- Originals are immutable. New outputs only, atomic promotion after verification. Never trim speaker tracks independently.
- Integer 48 kHz project samples; explicit mono/stereo and offsets. Reject ambiguous layouts.
- Bound RAM with disk-backed float PCM and fixed blocks. CPU baseline; no unmeasured 16 GB compatibility claims.
- No automatic network access during processing, global installs, driver changes, or hidden fallback models.
- Pin tools/models and record licenses, hashes, parameters, timing, and actual measurements. Never invent listening evidence.
- Preserve graft/, user files, and original specification. Never use Bricolage Grotesque.
- Use `.venv/Scripts/python.exe -m pytest` for checks; `scripts/build_windows.ps1` for a portable bundle.
- Done means real file-to-file audio, verified report, native worker flow, relevant tests, and updated PROJECT_STATE.md.
