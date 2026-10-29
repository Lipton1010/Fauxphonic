# Fauxphonic: review of five audits and three cross-audits

Reviewed 2026-09-27 against the current working tree. This is an adjudication and repair plan, not a claim that repairs have been implemented. Production code, tests, release artifacts, audit folders and existing evidence were not modified during this review.

## Decision

Update: the confirmed repairs were implemented for 0.2.2. See [AUDIT_FIXES_0.2.2.md](AUDIT_FIXES_0.2.2.md) and `PROJECT_STATE.md` for current implementation and release evidence. The assessment below records the pre-fix state.

Do not treat the current source as ready for a general cleanup/mastering release. The playback fix is supported, but the audits exposed important gaps in mastering, damaged-input validation, processing effectiveness and UI state. The earlier 97-test pass establishes the cases those tests cover; it does not establish useful cleanup or reliable delivery across speech targets.

Earlier investigation of JSON-only output was incomplete. JSON is a separate report after successful exports, but it is also the only promoted artifact after some ordinary speech renders fail verification. The original attempt cannot be reconstructed without its settings and report; the failure mechanism is now reproduced independently.

## Evidence independently reproduced in this review

Probe: `evidence/local/codex-audit-recheck-20260927.py`. Results: `evidence/local/codex-audit-recheck-20260927.json`. Actual audio and full engine reports: `.cache/codex-adjudication-cyfskmye/`. These probes create unique scratch and never edit the application. An initial sandbox attempt encountered the known Windows temporary-folder ACL issue; the completed run used normal filesystem permissions.

| Case | Observed result | Assessment |
|---|---|---|
| Repository speech fixture, -19 LUFS WAV | Passed at -19.1 LUFS / -1.2 dBTP; 706,889 frames | Control works |
| Same speech, -16 LUFS MP3 | -17.0 LUFS / -2.6 dBTP; failed verification; report exists, audio absent | Confirms ordinary speech export failure |
| Same speech, -14 LUFS WAV | -15.1 LUFS / -1.5 dBTP; failed verification; report exists, audio absent | Confirms the issue is not exclusive to lossy codecs |
| Valid PCM24 WAV cut to half its sample data, header preserved | 353,444 of the original 706,889 frames exported with status `passed` | Confirms damaged source acceptance |
| Ten seconds of digital silence to MP3 | `ValueError: Out of range float values are not JSON compliant: -inf`; neither audio nor report | Confirms a practical trigger beyond tiny clips |
| Speech plus seeded -35 dBFS white noise, cleanup 0/6/9, leveler disabled | Pre-master pause-noise attenuation 0 / 0.3186 / 0.3265 dB | Confirms weak cleanup and negligible preset separation on this fixture |

The noise measurement uses the same known noise-only interval in exported pre-master stems, avoiding final mastering gain as a confound. This is one engineering fixture, not a universal attenuation curve or listening judgment. The 6/9 dB values are filter settings; they should not be interpreted as guaranteed attenuation on every recording. Nevertheless, approximately 0.008 dB separation on this fixture is poor support for a materially stronger preset.

The original speech fixture hash was unchanged. Audited source hashes:

- `engine.py`: `cd8eac2cd05d42bc924364b64eb2fe59f1f2e69692d9844e32670c9dd6b44f0e`
- `audio.py`: `3713933fc0f41e1f3a180481234e58e30065567ae126ce7f952fe9a655e27b39`
- `gui.py`: `bd95501c6a93abda7c845c5eb88ae2644d4eb401bf7a8a79a82f488782ecb50f`
- `leveling.py`: `0c16730b9ff6be542b96536bd3b0d6e219155f6af76572b43411e36f71e6f803`

## Consolidated priorities

Evidence labels distinguish this review's fresh reproduction from source inspection and other reviewers' reported reproductions. Severity prioritizes practical consequences; a disagreement between P2 and P3 is less important than preserving the trigger and acceptance test.

| Priority | Finding and evidence | Repair direction and acceptance |
|---|---|---|
| P1 | **Damaged WAV accepted as verified.** Fresh reproduction; `audio.py:41` and `audio.py:88` trust ffprobe/decoded timing, which can both reflect only the remaining bytes. | Validate container structure and declared data extent, with format-aware RIFF/RF64/W64 handling. Reject sample-aligned truncation as well as partial frames. Do not claim recovery of missing content. |
| P1 | **Mastering fails ordinary high-crest speech targets.** Fresh reproduction; `engine.py:399`, `engine.py:424`. Initial fixed headroom and a single retry fail these cases; the lossy retry lowers the limiter ceiling even when the measured peak is already comfortably below the requested ceiling. | Improve bounded convergence while preserving peak safety, stereo linkage and the final encoded-file check. Test speech across targets, codecs and crest factors, including successful corrective retries and genuinely infeasible cases. |
| P1 for cleanup release | **Cleanup has little effect on important test cases; Strong is barely distinct.** Fresh reproduction plus several audit matrices; `engine.py:295`. | Evaluate measured noise profiles, tracking policy and noise colour together. Test several noise levels/types, clean-voice damage, transients and expression. Establish meaningful preset separation or change the claims/presets. Do not prescribe one fixed replacement noise floor from one test. |
| P2 | **Silent lossy export loses both audio and report to nonfinite JSON.** Fresh reproduction; `engine.py:177`, `engine.py:189`, `audio.py:34`. | Represent unavailable/nonfinite measurements explicitly without making up a pass. Verify silent and tiny sources across all formats, with a readable exception or failure report. |
| P2 | **Stale preview and project results.** Source-confirmed at `gui.py:684`, `gui.py:969`, `gui.py:1336`; reproduced by multiple audits. | Associate previews, measurements and exports with their originating project/settings. Invalidate or clearly label old results; prevent late job completion from masquerading as the current project's output. Include in-flight edits and project switching in tests. |
| P2 | **Late playback preparation overrides a newer play/pause choice.** Source-confirmed at `gui.py:1325`; multiple reported reproductions. | Give transport requests ownership/generation identity; discard or retain stale preparation without auto-playing it. Test newer original, A/B, pause and project-change commands. |
| P2 | **Imported leveler curves can be applied twice after a preset change.** Source-confirmed: import disables leveling at `gui.py:819`; `gui.py:704` re-enables it while retaining envelopes. Cross-audits reproduced extra gain. | Preserve the distinction between imported automatic curves and intentional manual envelopes. Prevent accidental automatic-plus-imported duplication without banning legitimate manual-plus-automatic workflows. Compare actual rendered gain. |
| P2 | **Queued stems are not fully reserved.** Source-confirmed at `gui.py:1154`; queued reservations contain main outputs only. Cross-audits reproduced MP3/WAV same-stem collisions. | Use one output-set definition for master, report, stems and reservations. Check every prospective output against every queued output, then retain engine-side atomic protection. |
| P2 | **Wheel input silently changes processing settings.** Opus and DeepSeek report reproductions; Space Bunny's conflicting probe used different event delivery/direction. | Test actual wheel routing and unfocused widgets; allow intended panel scrolling without changing parameters. Include preset changes that reset advanced controls and re-enable leveling. |
| P2 | **Exceptions are insufficiently prominent.** Source-confirmed at `gui.py:1337`; quiet gain-cap and short-clip cases reproduced by reviewers. | Show passed/exception/failed separately, with the reason, actual measurements and requested target. Do not present completion alone as compliance. Gain-cap policy changes require separate quality/safety validation. |
| P2 | **Crash leaves render scratch in the output folder.** `engine.py:262` uses output-local temporary directories; multiple cross-audits report retained large PCM files. Not crash-reproduced in this review. | Track ownership and job scratch locations for safe recovery, bounded disk management and explicit diagnostics. Never recursively sweep arbitrary user output folders based only on a name prefix. |
| P2 quality validation | **Leveler defaults correct only about 26% of estimated phrase differences.** Formula source-confirmed at `leveling.py:207`; measurements align across several reports. | Determine intended default behavior with representative quiet/loud dialogue and expression-preservation tests. The factor is real, but conservative defaults are not automatically a defect. Phrase-merging failure remains fixture-dependent. |
| P2 quality validation | **De-esser depends strongly on input level.** `engine.py:306` uses a fixed pre-leveling threshold; several reviewers report no effect after attenuating the same fixture. | Test equivalent sibilant content at multiple recording levels, including negative controls that should not trigger. Calibrate detection/gain staging and preserve final verification. A single universal source-peak cutoff is not established. |
| P2 workflow | **Compressed-source resume restarts and re-decodes.** Source-confirmed at `gui.py:1085`; reproduced in cross-audits. | Reuse a valid prepared source/stream for resume, with identity/invalidation and safe lifetime handling. Verify position, no redundant worker, and playback through end-of-file. |
| P3 | **Invalid stream-index text causes an uncaught slot exception.** `gui.py:1433`; multiple reported reproductions. | Validate and explain the error in the GUI. Test invalid, missing and nonexistent stream selections. |
| P3 | **CLI `dsp-cleanup` defaults to Program role, so cleanup bypasses.** Source-confirmed in `presets.py:10`. | Align the preset's intent and default eligibility, or remove the ambiguous alias; preserve explicit user role choices. |
| P3 | **Failure reports occupy success-style output names and retries may repeat failing settings.** `engine.py:520`, `gui.py:1210`. | Make failed diagnostic artifacts clearly discoverable and distinguish retrying a saved job from creating a render with edited settings. Preserve all existing user reports; renaming is not an audio fix. |
| P3 | **Short recordings cannot use the 10–30 second preview flow.** Multiple reported reproductions, consistent with the current specification. | Treat as a usability limitation, not an application crash. If supporting shorter clips, define the measurement/preview exception behavior explicitly. |
| P3 | **Stereo eligibility messaging is incomplete/misleading in some paths.** Static note and per-track bypass reports exist; Strong can still warn of artifacts when cleanup will bypass. | Show actual per-track stage eligibility. Preserve stereo; any mono conversion needs explicit user choice and a recorded mapping. Check leveling separately: its eligibility is not identical to mono-only cleanup/de-essing. |
| Process | **Tests overwrite cited feedback screenshots.** Source-confirmed in `tests/test_desktop.py:70` and `tests/test_desktop.py:492`; audits report changed hashes. | Separate routine test artifacts from deliberately captured, versioned release evidence. Preserve originals and use explicit capture/provenance; do not silently restore a disputed snapshot. |

Additional unresolved areas: worker hangs and close timeouts, device hot-plug, completion emitted before temporary cleanup, dense long-form phrase/block limits, mid-render disk exhaustion, synchronized folders, and installer/clean-machine behavior for a future update. These are not confirmed defects merely because they remain untested.

## Claims and proposed fixes that should not be accepted literally

- **“New project clears the project during processing” is false in current source.** It has an active-worker guard. Open project/result attribution is the actual gap. A serialized render snapshot means this is primarily incorrect UI context, not proof that the active render's inputs change.
- **“The leveler drops whispered words” is misleading.** Excluding a region from automatic gain does not remove its audio. The fixed floor can leave very quiet speech under-corrected; removing the floor indiscriminately could amplify noise.
- **“Only JSON” is not merely a file-location misunderstanding.** The mastering failure is reproduced. But withholding a noncompliant render is the correct safeguard; do not simply convert failures into exceptions to force audio delivery. Any noncompliant-output option must be explicit and accurately labeled.
- **“Cleanup is entirely inert” is too broad.** It has a small measured effect here and different effects on other fixtures. The central defect is insufficient validated usefulness and weak amount/preset differentiation, not a universal claim of zero processing. Neither the `nr` value nor a filter's presence establishes delivered noise attenuation.
- **Do not apply reviewers' numerical targets as requirements without validation.** Six dB of attenuation or a three dB phrase spread can be useful fixture criteria, but blanket thresholds could incentivize voice damage or excessive compression.
- **“The de-esser is always dead below a particular source peak” is not a universal detector rule.** Content in the detector band matters. The robust finding is under-triggering across input levels, not one global source-peak threshold.
- **Short-preview refusal is not a GUI crash.** Likewise, a handled FileExistsError is a failed job, not evidence the application terminates.
- **Existing output/report collision refusal protects data.** Do not fix retry ergonomics by deleting or silently overwriting reports. Several suggested regression snippets would weaken this invariant.
- **The exact mastering boundary depends on the signal and format.** Claims of a universal “-16 LUFS cliff” overstate fixture results. Space Bunny's cross-audit wheel example also reverses the target/failure direction relative to its own matrix; keep the general interaction, discard those example numbers.
- **A matching frame count alone does not prove intact input.** The truncated-source case shows that measuring a decoder's reduced length and then reproducing that length is insufficient.

## Reliability of the audit process

All eight supplied files were reviewed: the original Opus, Gemini, Mimo, Space Bunny and DeepSeek reports, plus the Opus, Space Bunny and DeepSeek cross-audits. The cross-audits are useful because they retract false negatives and refine triggers; they are not three additional independent original audits.

Opus's ordinary-speech target finding survived the strongest challenges and this review's fresh reproduction. Space Bunny's expanded truncation reproduction is also confirmed here. DeepSeek supplied useful counterexamples and corrected event/measurement methodology. Mimo's state findings are credible, but matching scripts/numbers across reports should not be counted as independent replications. Gemini found useful failure paths but mixed valid findings with incorrect descriptions and overly strong severity claims. Evaluate evidence per finding rather than choosing one model as the authority.

The Gemini transcript records `Remove-Item -Recurse -Force audit_repro` at line 789. The Opus cross-audit reports a deleted script and its restoration. This review did not execute that command or attempt any restoration. Concurrent audits shared folders and test outputs, so independence and artifact provenance are weaker than the number of reports suggests. Future audits need isolated checkouts/output directories and explicit ownership boundaries.

This review did not rerun the entire suite, existing audit scripts, packaged executables, or destructive/crash probes. It ran fresh bounded engine probes and inspected relevant current source. No listening evaluation occurred. No exact recreation of recipient's original session is claimed.

## Recommended repair and acceptance order

1. **Protect truthful delivery.** Fix damaged-input acceptance, nonfinite reporting and mastering convergence; surface exception/failure reasons and diagnostic artifacts. Preserve refusal of genuinely invalid output.
2. **Make comparisons and controls trustworthy.** Bind results to project/settings, guard transport ownership, restore compressed resume, prevent accidental double leveling and wheel changes, and reserve complete output sets.
3. **Establish useful audio processing.** Test noise suppression, leveler defaults and de-essing over meaningful ranges, with clean-speech protection. Include independent pre-master measurements so normalization cannot hide defects.
4. **Close operational/evidence gaps.** Safe crash-scratch recovery, honest CLI eligibility, invalid-input handling and explicit evidence capture. Add regressions for every confirmed trigger, not just report strings.
5. **Run listening and package acceptance.** Use consented representative recordings and aligned, attenuation-only loudness-matched blind comparisons. Judge intelligibility, naturalness, artifacts, expression and background preservation separately from meter compliance. Build only after the source gates pass; test the exact packaged GUI/worker through playback completion, exports, failure paths and clean offline installation.

The original 0.2.1 artifacts remain unchanged and lack the source playback fix. A source patch, a passing development suite and a validated distributable are three different milestones. No report here establishes “best results” or Auphonic-equivalent quality.
