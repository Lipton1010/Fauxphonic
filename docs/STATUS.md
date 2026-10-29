# Fauxphonic source state

This source snapshot includes the local 0.2.2 audit repairs. The application is a native PySide6 Windows interface with a separate CPU audio worker. It preserves originals, uses integer 48 kHz project samples, and verifies encoded exports before atomic publication.

## Changes present in this snapshot

- Declared WAVE container extents are checked before decoding; silent-file reports use finite JSON values and explicit measurement exceptions.
- Export verification uses bounded measured correction from the lossless mix. Failed diagnostics use separate names, preserving success destinations for a retry.
- Project edits invalidate prior analysis and preview/export results. Playback preparation is owned by the current request and supports compressed-file resume.
- Imported leveling curves retain provenance and avoid a second automatic leveling pass. Default leveling and source-relative de-essing have regression coverage.
- Spectral cleanup uses a measured stationary noise profile when available and reports an explicit bypass otherwise. Short/end-of-file previews retain their actual length.
- Queued output, report and stem names share collision checks. Worker cancellation is bounded and scratch recovery preserves unowned folders.
- Installer/build scripts and tool manifests record version 0.2.2 and the FFmpeg build with the required noise-profile filter.

## Validation and limits

The local engineering record reports 133 source tests and packaged/installer checks for 0.2.2. These are historical local results, not a claim that this synchronization reran them. Generated evidence, audio, installers, private delivery records and local models are intentionally excluded from this source commit.

The Git synchronization ran syntax checks and selected model-free CPU regressions; its final report records the exact checks and results. No new installer or release is published by this commit. No human listening approval or unmeasured hardware compatibility is claimed.

See README.md and docs/AUDIT_FIXES_0.2.2.md for source behavior and remaining limitations.

Current synchronization check: 79 selected model-free CPU regressions passed; three Silero model-loading tests were deliberately excluded. This does not establish GUI/playback acceptance or human listening approval.

Private operating logs and generated evidence remain local and are excluded from source control.
