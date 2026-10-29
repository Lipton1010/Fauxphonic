# Fauxphonic 0.2.2 audit repairs

This implements the confirmed findings in `CROSS_AUDIT_REVIEW_2026-09-27.md`. The supplied audits were evidence to evaluate, not instructions to execute. Original recordings, the original specification, graft data and the reviewers' folders were preserved.

## Implemented behavior

| Area | Repair and verification |
| --- | --- |
| Mastering/export | Up to six measured corrective passes from the lossless mix, with independent decoded-output verification and linked stereo limiting. All 15 combinations of five formats and -19/-16/-14 LUFS passed on the speech fixture in the exact packaged worker. |
| Damaged input | RIFF/RF64/W64 declared extents are checked before probing/decoding. Truncated-container regressions reject the input and withhold output. |
| Silence/failures | Unavailable sample-peak values serialize as JSON null. Silence and short clips retain explicit measurement exceptions. Failed diagnostics have unique `.failed-*.report.json` names and do not reserve success filenames. A retry regression retains the diagnostic while successfully rendering to the original requested output name. |
| Result ownership | Edits invalidate A/B/export/analysis results; old worker snapshots cannot populate a changed project. Open-project is guarded during processing. |
| Playback | Loading waits for Media Foundation readiness. Newer transport actions supersede pending preparation; valid compressed preparation is reused for resume. Device choices refresh after device changes. |
| Controls | Unfocused wheel events do not change presets/numbers/roles. Invalid stream indices produce a visible explanation. Queue collision checks share the engine's complete output/report/stem naming contract. |
| Leveling | Imported curves retain persisted provenance and are excluded from a second automatic pass. New defaults are strength 0.8, dynamics preservation 0.25; transitions are 120 ms. The default quiet/loud speech regression reduces an initial gap above 11 dB to below 6 dB without lifting the test pauses. Existing saved settings retain their explicit values. |
| Cleanup | A steady broadband sample outside padded speech regions supplies a noise profile and measured floor. The scratch-only profile prefix and filter latency are removed together, preserving the program timeline. Cleanup bypasses explicitly if no suitable sample is found. Clean-speech preservation, source-rate alignment, endpoints and noise reduction have regression coverage. |
| De-essing | Detector threshold scales with source peak. Tests at original level, -12 dB and approximately -24 dB retain sibilant attenuation and protect the quiet high-band negative control. |
| Short previews | Positive selections up to 30 seconds stop at the actual decoded endpoint. Cached previews retain the actual shorter duration. |
| Reliability/evidence | Render scratch carries an ownership marker and process creation time. Dead owned scratch is recovered; unmarked neighboring files are preserved. Tests cover killed workers, cancellation, disk-full preflight and an injected mid-encoding write failure. Cancellation has a bounded forced-stop fallback. Routine screenshot tests write to their unique test directories. |

## Measured processing effect

Exact packaged-worker results on the known-text TTS fixture plus stationary noise at -35 dBFS, measured on pre-master stems:

| Noise | Natural setting (6) | Strong setting (9) |
| --- | ---: | ---: |
| White | 5.44 dB attenuation | 7.77 dB attenuation |
| Pink | 3.30 dB attenuation | 4.09 dB attenuation |

Speech projection gain was approximately 0.996 for these fixtures. Source tests also cover -45 dBFS noise and unchanged clean speech. The settings are reduction controls, not promises of the same measured attenuation on every recording. The pinned FFmpeg build now includes the [`asendcmd` filter](https://ffmpeg.org/ffmpeg-filters.html#afftdn) needed to sample its noise profile; all processing remains local, with networking disabled in that build.

## Evidence

- `evidence/audit-final-0.2.2-tests.xml`: 133 passed, zero failed/errors/skips.
- `evidence/audit-audio-0.2.2.json`: packaged-worker hashes, source hashes, 15 delivery cases, noise measurements, silent exceptions and damaged-input rejection.
- `evidence/packaged-smoke.json`: relocated native GUI, packaged render and full-render A/B preview without developer PATH dependencies.
- `evidence/installer-smoke.json`: 367 installed payload hashes, version, icons, shortcut identity, GUI/worker update locks and preservation through uninstall.
- `evidence/sandbox-installer.json`: clean Windows with no Python and networking disabled; 0.2.1-to-0.2.2 upgrade, verified export, Opus preparation and preservation through uninstall.
- `evidence/packaged-ui-0.2.2.json`: passed direct native original playback, pause/resume, GUI MP3 export at -16.0 LUFS, compressed playback/resume and source preservation.

The original tools and portable payload were copied to unique `.cache/*before-audit*` directories. Earlier release evidence is preserved at `evidence/checkpoints/before-audit-0.2.2-d743264e8b4d441e8eaa961bede34ece`. Earlier versioned installers remain intact.

The single-file installer is 159 MB, exceeding the connected Drive tool's 100 MiB per-file upload limit. The Drive delivery uses Inno Setup's standard disk-spanning format: one setup executable plus two data files. Downloading the entire Drive folder produces one ZIP; extract it and run setup with both data files beside it. The application payload is identical to the verified single-file installer.

## Limits

No human listening approval is claimed. These measurements do not establish “best possible” audio or Auphonic equivalence, and recipient's original media was unavailable. VAD/noise selection are heuristics; nonstationary noise, reverb, heavily clipped input and expressive/whispered speech need representative listening. Stereo remains stereo and bypasses mono-only cleanup/de-essing. No physical device unplug test or new 16 GB memory-compatibility claim is made. Unmarked scratch left by older builds is preserved rather than guessed safe to delete.
