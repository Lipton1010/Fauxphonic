# Fauxphonic 0.1.0 — Independent Release Audit

**Date:** 2026-09-25
**Scope:** the workspace `D:\Fauxphonic App` (branch `main`, HEAD `b4d4b9a`, plus uncommitted and untracked installer work) and the Windows installer intended for recipient.
**Method:** Adversarial. I designed counterexamples against the app's own claims, using only synthetic audio and isolated, project-owned test installs under `.cache\audit-20260925\`. I did not modify production code, `PROJECT_STATE.md`, evidence files, or `dist\`.

---

## 1. Verdict: NOT READY

What is sound:

- The installer contains exactly the audited source.
- It installs per user without admin rights.
- The installed app runs with no developer Python or PATH entries and opened no network sockets.
- With the app closed, uninstall preserves recordings, projects, exports, and app data.
- "Master only" produced verified exports on long speech-like material and on TTS speech.

Why it is not ready: `README-recipient.txt`, which the installer shows before installing, tells recipient to use **Conservative DSP cleanup**. That feature has two defects:

- **It shifts timing (F1).** Every cleaned voice track starts 25 ms late and loses its last 25 ms. The report still says `passed`.
- **It is silently bypassed (F2).** Unless a track is mono *and* set to Speech, no cleanup happens, and the GUI still shows `passed`.

The bundle's own license notice also records its GPL source obligation for FFmpeg as unresolved (F7). All three fixes are small, and for F1/F2 the control can simply be disabled. With them done, this build would be **READY WITH CAVEATS** (F3–F6).

## 2. Artifact audited

| | |
|---|---|
| Installer | `dist\installers\Fauxphonic-0.1.0-Setup.exe` |
| SHA-256 | `8421256eb871045ed288a3f0be593d57e609e1b09540e12089fcea19076edde8` |
| Size | 102,614,773 bytes |
| Compiler | Inno Setup 6.7.1 (its hash is in `evidence/installer-build.json`) |
| Signature | None (Authenticode `NotSigned`); recipient should expect a SmartScreen prompt |

**Do the source, portable build, installer, and evidence represent the same implementation? Yes.**

- Both executables contain bytecode identical to the current working tree. This covers every `local_audio_studio` module each executable contains, plus both entry scripts; by design the GUI executable omits `engine` and `cli`. I checked this by recursive code-object comparison, and a negative control confirmed the check detects a single changed constant.
- `dist\Fauxphonic` matches its `SHA256SUMS.txt` (350/350 files).
- The installed payload is byte-identical to `dist\Fauxphonic` (351/351 files, including `SHA256SUMS.txt`). The only additions are `README-recipient.txt` and the uninstaller.
- The bundled `ffmpeg.exe` and `ffprobe.exe` match `tools/manifest.json`. The bundled numpy, PySide6, soundfile, and psutil versions match `pyproject.toml`.
- The installer hash matches the `.sha256` file, `evidence/installer-build.json`, and `evidence/installer-smoke.json`.
- **Caveat:** the installer sources are untracked in Git (`installer/`, `scripts/build_installer.ps1`, `scripts/installer_smoke.py`, `evidence/installer-*.json`).
- **Caveat:** the `PROJECT_STATE.md` and `README.md` copies inside the installer are the 18:35 versions, which predate the installer and do not mention it.

---

## 3. Findings (most severe first)

### F1 — HIGH — DSP cleanup shifts cleaned tracks 25 ms late and drops their last 25 ms, yet reports `passed`

- **Where:**
  - `src/local_audio_studio/engine.py:147` inserts `afftdn` with no latency compensation.
  - `engine.py:156` and `engine.py:182` take the expected frame count from the already-processed mix, so a shift or truncation cannot be detected.
  - The feature is recommended in `installer/README-recipient.txt:18-19`.
- **Trigger:** "Conservative DSP cleanup" with any mono track set to Speech. This is the only configuration in which cleanup runs.
- **User impact:**
  - Each cleaned track is delayed by 1,200 samples (25.0 ms) while its frame count stays the same. The first 25 ms become silence and the last 25 ms of the source are discarded.
  - Mixed sessions desync. If a session also contains uncleaned tracks (a stereo track, any Program/Music/Ambience role, or a stereo voice), the cleaned voices land 25 ms late relative to them.
  - The report still records offset 0 and `passed`.
  - This contradicts AGENTS.md ("Never trim speaker tracks independently") and PROJECT_STATE's "exact offsets/endpoint impulses". That claim is only tested in bypass mode (`tests/test_engine.py:56-71`).
- **Evidence:**
  - `exp01_timing`, single track: lag = 1200 samples and the first 10 ms RMS = 0.0.
  - `exp01_timing`, speech mixed with a program track: speech lag 1200, program lag 0.
  - `exp13_timing_confirm`: same shift in the **installed** build's GUI render (1200) and on a 44.1 kHz source (1199 at 48 kHz). TTS speech with an 80 Hz high-pass also shifted (1199).
  - Master-only and bypass renders: lag 0.
- **Reproduce with the shipped worker and FFmpeg only:**

  ```powershell
  $W = "D:\Fauxphonic App\.cache\audit-20260925\work\repro"; $F = "D:\Fauxphonic App\tools\ffmpeg.exe"; $X = "D:\Fauxphonic App\dist\Fauxphonic\Fauxphonic-worker.exe"
  & $F -v error -y -f lavfi -i "sine=f=1000:sample_rate=48000:d=10,volume=0.25,afade=t=in:st=0:d=0.001,adelay=1000|1000" -af "apad=whole_dur=12,aformat=channel_layouts=mono" "$W\burst.wav"
  & $X process "$W\burst.wav" --preset bypass --output "$W\ref.wav"
  & $X process "$W\burst.wav" --preset dsp-cleanup --role speech --output "$W\clean.wav"
  foreach ($f in 'burst','ref','clean') { & $F -hide_banner -nostats -i "$W\$f.wav" -af silencedetect=noise=-40dB:d=0.2 -f null - 2>&1 | Select-String silence_end | Select-Object -First 1 }
  ```

  Observed results:
  - The burst starts at `1.000604` s in the source and in the bypass output, but at `1.025104` s after cleanup.
  - `clean.wav.report.json` reports verification `passed`, 576,000 frames, and timeline offset 0.
- **Smallest fix:**
  1. Compensate the `afftdn` latency in the decode filter chain, e.g. `apad=pad_len=N,afftdn=…,atrim=start_sample=N`. Here N is the latency measured at the source sample rate, about 25 ms.
  2. Compute the expected output length from the sources (resampled length plus offset) rather than from the processed mix.
  3. Add a regression test that mixes a cleaned track with an uncleaned one and asserts zero lag and intact endpoints.
  4. Alternative for this release: disable DSP cleanup and remove the README-recipient paragraph.

### F2 — MEDIUM — "Conservative DSP cleanup" silently does nothing for default-role or stereo tracks, and the GUI reports success

- **Where:**
  - `gui.py:192`: Program is the first role in the list, so it is the default.
  - `gui.py:170`: the noise-reduction control is enabled whenever this mode is selected, whatever the track roles are.
  - `engine.py:144-145`: the bypass is recorded only in the report JSON.
  - `gui.py:377`: the log shows only the verification status.
- **Trigger:** choosing DSP cleanup without changing a track's role to Speech, or using a stereo voice file.
- **User impact:** no noise reduction is applied at all. The GUI log says `Export verification: passed` and the queue shows `complete: passed`. The only hints are a static note (`gui.py:108`) and the report's `"bypassed: requires explicitly assigned mono speech"`.
- **Evidence:** `exp09_gui_crash` → `gui_dsp_cleanup_default_role`. This used the real `gui.Window` offscreen, with an isolated data folder.
- **Smallest fix:** refuse to queue (or warn) when DSP cleanup is selected and no track qualifies. Also append the report's `processing` entries to the GUI log when a job completes.

### F3 — MEDIUM — Stream start times are recorded but ignored, so multi-stream containers desync silently

- **Where:**
  - `engine.py:149`: decoding discards the selected stream's start time.
  - `engine.py:199`: the start time is written into the report and used for nothing else.
- **Trigger:** importing one container twice with different stream indices (the GUI "Stream index" column), when those streams start at different times. An example is a late-joining track in an MKV or MOV file.
- **User impact:** the later stream is placed at 0 instead of its recorded time. Relative sync is lost while the report says `passed`.
- **Evidence:** `exp05_edges` → `container_stream_start_time`.
  - ffprobe reported start times of 0.000000 s and 1.500000 s.
  - The measured lag of stream 1 in the mix was 0 samples; 72,000 was expected.
  - The report's timeline lists `source_start_time: "1.500000"`.
- **Smallest fix:** add (stream start time − earliest start among the selected streams of that file), converted to integer samples, to the track offset. Alternatively, reject selections whose start times differ. Cover it with a two-stream fixture test.

### F4 — MEDIUM — The verification meter disagrees with BS.1770 references, so some compliant exports produce no output

- **Where:**
  - `engine.py:70-71` and `engine.py:86-87`: verification uses `audio.loudness()` (`audio.py:123-130`), which is loudnorm's own analysis pass.
  - `engine.py:164-173`: the dynamic-mode fallback.
- **Trigger:** "Master only" on short (≤ about 30 s) or noise-like material. loudnorm falls back to dynamic mode whenever true peak plus the needed gain would exceed the ceiling. That happened in 59/59 speech-like renders (F8), and it lands about 0.2–0.65 LU under target by its own meter.
- **User impact:** the export fails with "Export not promoted …" and leaves no audio. Retrying reproduces the same result. Independent meters say the refused files meet the ±0.5 LU gate. This fails safe (no false success), but the core export fails.
- **Evidence:**
  - `exp02_master_modes`: 12/32 synthetic 30 s speech-like cases were refused, at −19.52…−19.65 LUFS by the app's meter.
  - `exp03_meter_check`: all 5 refused candidates measured −19.19…−19.42 LUFS on two independent meters, FFmpeg `ebur128` and a NumPy BS.1770-4 implementation. Those two agree within 0.03 LU.
  - On steady noise, the app's meter read 0.23 LU below both references. On sine tones from 100 Hz to 16 kHz it agreed within 0.05 LU, and on a 997 Hz calibration tone all three read −23.0 ±0.05.
  - `exp06_rejections`, length sweep: 2/3 refused at 30 s; 6/6 passed at 120 s and 600 s.
  - `exp04_tts_master`: 12/12 TTS speech renders passed, with the meters agreeing within 0.04 LU.
  - Other refusals:
    - an 8 s clip (at −20.41 LUFS);
    - a peaky stereo clip (at −16.84 LUFS and −0.86 dBTP). The true-peak overshoot there is real: it is created by the 192 → 48 kHz conversion after loudnorm.
- **Smallest fix:** verify final files with FFmpeg `ebur128=peak=true`, which is independent of loudnorm. Add a noise-like fixture to the tests.

### F5 — MEDIUM — Near-silent inputs receive unbounded gain and are marked `passed`

- **Where:** `engine.py:162-171` and `engine.py:82-83`. Only non-finite loudness (true silence) is special-cased.
- **Trigger:** a dead or muted microphone, the wrong input, or a very quiet recording.
- **User impact:** a room-noise floor at −58.9 LUFS was raised **+39.7 dB** to −19.25 LUFS, producing loud hiss with verification `passed`. The specification (§11) requires avoiding "enormous gain boosts".
- **Evidence:** `exp05_edges` → `near_silent_noise_floor`.
- **Smallest fix:** cap the applied gain. For example, return an `exception` status requiring confirmation when the measured loudness is below a floor or the gain exceeds a limit.

### F6 — MEDIUM — Uninstall and update don't handle a running app

- **Where:**
  - `installer/Fauxphonic.iss:24-25`: `CloseApplications=no`, and there is no `AppMutex`.
  - `audio.py:22`: the error text shown to end users.
- **Trigger:** uninstalling, or running setup again, while Fauxphonic is open or rendering.
- **User impact, uninstall:**
  - The uninstaller exits 0 and removes the Windows Apps entry, but leaves **50 program files (114.6 MB)**, including both executables. They no longer have an uninstaller (the log says `Removed all? No`).
  - It deletes `_internal\tools\ffmpeg.exe` underneath an active render. The render then fails with "Missing pinned tool … Run scripts/bootstrap.ps1", a developer instruction.
- **User impact, setup over a running app:** setup aborts at the in-use file (exit code 5 in silent mode). Interactive users get an Abort/Retry/Ignore prompt; choosing Ignore would mix versions. I did not test that path.
- No user data was lost, and no partial output was promoted.
- **Evidence:** `exp11_running_app`, including `install2\*.log`.
- **Smallest fix:** set `CloseApplications=yes` (Restart Manager) or add an `AppMutex` created by both executables. Replace the `bootstrap.ps1` hint in frozen builds.

### F7 — MEDIUM (compliance gap, not a legal conclusion) — The license notices don't match the shipped payload

- **FFmpeg (GPLv3).** `ffmpeg.exe` and `ffprobe.exe` are Gyan "essentials" builds, which gyan.dev describes as "static and licensed as GPLv3". They ship with the GPLv3 text (`_internal\tools\LICENSE`) but with no Corresponding Source or written offer. `THIRD_PARTY_NOTICES.md:5` itself says the source obligation still requires review and that the build "is for local/private testing"; that gate has not been cleared. GPLv3 §0 defines "convey" as enabling other parties to receive copies, and §6 attaches Corresponding Source conditions to conveying object code. Whether this particular hand-off requires more is a legal question I am not answering.
- **Qt Virtual Keyboard (GPLv3 or commercial).** `Qt6VirtualKeyboard.dll` and `plugins\platforminputcontexts\qtvirtualkeyboardplugin.dll` are bundled. Per the Qt documentation, this module is available only under a commercial license or GPLv3, not LGPL. The app never uses it, and the notices don't name it. PyInstaller's PySide6 hook pulled it in along with Qt Quick, QML, and PDF (`Fauxphonic.spec:10`).
- **Qt's FFmpeg DLLs (LGPL-2.1+).** Qt's FFmpeg 7.1 libraries (`avcodec-61`, `avformat-61`, `avutil-59`, `swresample-5`, `swscale-8`) are not identified in the notices.
- **Inventory.** `dependency-inventory.json` is generated from the whole build environment (`scripts/notices.py:13`). It therefore lists packages that are not shipped (onnxruntime, pytest, pip, PyInstaller, protobuf, …) and omits the components above.
- **Not issues:**
  - The OpenSSL 3.0.16 license is present, as the unnamed Apache-2.0 text inside `licenses\Python-PSF-LICENSE.txt`.
  - Inno Setup's installed `license.txt` permits commercial use. The vendor *requests* a paid license from commercial users with over USD 5,000 in revenue ("not strictly required") and asks nothing of non-commercial users.
- **Smallest fix:**
  1. Include the corresponding source, or a written offer, for this exact FFmpeg build and its external libraries.
  2. Exclude Qt Virtual Keyboard, Quick, QML, and PDF in the PyInstaller spec.
  3. Generate the inventory from the payload instead of the build environment.

### F8 — LOW — "Master only" is usually dynamic loudnorm with unreported clamping, and stereo linkage is not strict

- **Where:** `engine.py:164-173`.
  - The GUI never shows `mastering_mode`.
  - The startup message (`gui.py:161-162`) says "adaptive leveling … not part of this build".
- **Evidence:**
  - All 59 speech-like renders used dynamic mode (44 passed, 15 refused); none used linear mode. At −19 LUFS / −1 dBTP, linear mode is impossible when the peak-to-loudness ratio exceeds 18 dB.
  - `exp07_clipper`: at 192 kHz, loudnorm's final per-channel clamp flat-topped samples. There were 356 consecutive clamped pairs in a 30 s peaky mono case.
  - In the stereo case, the right channel was clamped 304 times and the left 0 times. An input with L = 0.5·R came out with L/R ranging 0.47–0.73.
  - Pause-versus-speech gain differed by up to +1.25 dB (`exp02`).
- **User impact:** "Master only" is not one global gain. It is a short-term automatic gain control plus limiter that is not speech-aware. It can clip briefly without saying so and momentarily change the stereo image. The report does record `dynamic`.
- **Smallest fix:** show the mastering mode (and a count of clamped samples) in the GUI. Where a single global gain is intended, apply linear gain and a tested oversampled true-peak limiter.

### F9 — LOW — The failure report overwrites a file that appears at `<output>.report.json` during the render

- **Where:** `engine.py:201-202`, which calls `audio.py:31-35` (`os.replace`).
- **Evidence:** in `exp08_reliability`, a user file created at the report path mid-render was replaced by the failure report.
- **Smallest fix:** write the failure report with exclusive creation, the same way the success path refuses collisions.

### F10 — LOW — Crash leftovers don't match the documentation

- `README.md:31` says interrupted renders may leave a *hidden* scratch folder. In fact the `.fauxphonic-*` folder in the output directory is not hidden (116 MB for 10 minutes of mono audio).
- Analysis scratch (`cli.py:47`) goes to `%TEMP%\fauxphonic-analysis-*` and leaks on a crash (115 MB per 10 minutes of mono). This is undocumented.
- A GUI crash leaves `<key>.request.json`, which contains project paths, in the app-data folder (`gui.py:318`).
- **Evidence:** `exp08_reliability` and `exp09_gui_crash`.
- **Smallest fix:** either set the hidden attribute or correct the README. Put analysis scratch in an app-owned folder and sweep stale items at startup.

### F11 — LOW — The payload includes developer and private material

- `scripts/build_windows.ps1:12` copies `PROJECT_STATE.md` into the bundle, and `Fauxphonic.iss:33` ships it. It contains the private repository URL `github.com/Lipton1010/Fauxphonic`, the workspace path, CPU/GPU/driver details, and internal evidence paths.
- The developer `README.md` is shipped too, with `.venv` and `bootstrap.ps1` instructions.
- The executables themselves embed no absolute paths or user names.
- **Smallest fix:** ship only README-recipient and the notices.

### F12 — LOW (unverified risk) — Playback depends on Media Foundation

- `plugins\multimedia\ffmpegmediaplugin.dll` hard-imports `mf.dll`, `mfplat.dll`, and `mfreadwrite.dll`. The running GUI loaded them from System32.
- Windows N/KN editions lack these DLLs unless the Media Feature Pack is installed, so playback is expected to fail there. I did not test this.
- README-recipient's system requirements don't mention it.

---

## 4. Verified behavior, failed behavior, and unverified claims

| Status | Behavior or claim | Evidence |
|---|---|---|
| **Verified** | Installer SHA-256 and size as stated; unsigned | §2; `Get-AuthenticodeSignature` |
| **Verified** | Frozen code is identical to the working tree; installed payload identical to `dist` | `compare_bytecode.py`; `exp10_install` → `payload` |
| **Verified** | The existing suite passes: 18 passed in 11.02 s, run in an isolated copy | pytest command in §7 |
| **Verified** | Per-user install, no admin rights | install log: "User privileges: None", "Administrative install mode: No", HKCU root |
| **Verified** | Installed worker and GUI run with PATH = System32 and no PYTHON*/QT* variables. Every loaded module comes from the install or Windows folders, with no VC++ runtime taken from System32 | `exp10_install`; `exp12_clean_machine` |
| **Verified** | Installed GUI resumes a queued job, drives the installed worker, and produces a verified FLAC | `exp10_install` → `installed_gui_queue_e2e` |
| **Verified** | No sockets were opened by the installed worker, GUI, or FFmpeg children. FFmpeg runs with `-protocol_whitelist file,pipe` | `exp10_install` → `network`; `audio.py:39,88` |
| **Verified** | Uninstall with the app closed: exit 0; registration removed; user files inside the install folder and `_internal`, exports, and app data all preserved | `exp10_install` → `uninstall_app_closed` |
| **Verified** | Originals are never modified; a source changed mid-render blocks promotion | `exp08_reliability` |
| **Verified** | Cancelling at any of the 6 stages leaves no output, report, or scratch | `exp08_reliability` → `cancel_each_stage` |
| **Verified** | Two workers rendering the same output: one succeeds and the other fails cleanly (WinError 80) | `exp08_reliability` |
| **Verified** | A file created at the output name mid-render is not overwritten | `exp08_reliability` |
| **Verified** | Killing the worker kills its FFmpeg children and releases the lock; nothing is promoted | `exp08_reliability`; existing `test_worker` |
| **Verified** | GUI crash mid-render: the worker exits within 0.1 s, nothing is promoted, and the job reads `interrupted` after restart without auto-rerunning | `exp09_gui_crash` |
| **Verified** | Bypass keeps exact frames and zero lag; mono maps equally to L/R | `exp01_timing`; `exp05_edges` |
| **Verified** | Master-only keeps zero lag and exact frames; TTS speech passes 12/12 | `exp01_timing`; `exp04_tts_master` |
| **Verified** | MP3/Opus sources: lag 0 and exact frames. AAC: lag 0 with 256 extra trailing frames | `exp05_edges` → `lossy_source_timing` |
| **Verified** | Emoji/CJK paths and 288-character paths are handled (LongPathsEnabled = 1 on this PC) | `exp05_edges` |
| **Verified** | Clear rejections for 5.1 audio, ambiguous multi-stream files, and 0-sample files; silence becomes an exception with no gain; a loud short clip is refused rather than clipped | `exp14`; `exp05_edges` |
| **Verified** | No neural model ships or runs: no onnxruntime or `.onnx` in the bundle, and `neural_models: []` | bundle listing; reports |
| **Verified** | Memory stays bounded: installed worker ~83–90 MiB for 120 s, consistent with the project's 81.6 MiB for 3 hours | report `peak_process_tree_rss_bytes` |
| **Failed** | DSP cleanup preserves timing and endpoints | F1 |
| **Failed** | Visible controls do what they say, and the UI states the result | F2 |
| **Failed** | Multi-stream sources keep their common recorded start | F3 |
| **Failed** | Verification measures loudness like an independent BS.1770 meter; compliant exports are accepted | F4 |
| **Failed** | Silence / near-silence is not given huge gain | F5 |
| **Failed** | Uninstall or update while the app is running | F6 |
| **Failed** | License notices describe the shipped payload | F7 |
| **Failed** | "Master only" is global normalization with linked stereo | F8 |
| **Failed** | Failed renders never overwrite an existing file | F9 |
| **Failed** | Crash leftovers are hidden (README) and limited to the output folder | F10 |
| **Failed** | The payload contains only end-user material | F11 |
| **Failed** | PROJECT_STATE's "exact offsets/endpoint impulses": true for bypass only. `docs/feature_parity.md:20` still says "no installer" | F1; docs |
| **Unverified** | Clean Windows 10 1809 / Windows 11 machine without development tools; Windows N editions; SmartScreen and Mark-of-the-Web flow on recipient's PC | not tested; all runs on this development PC |
| **Unverified** | Long paths when `LongPathsEnabled = 0` | this PC has it enabled |
| **Unverified** | Behavior on real disk-full during a render, and after power loss following promotion (no flush is performed) | not reproducible safely |
| **Unverified** | Upgrading from a different version; the interactive "Ignore" choice on in-use files; shortcut and Start-menu behavior | no second version exists; I used `/NOICONS` (prior smoke evidence shows shortcuts working) |
| **Unverified** | Speech quality, listening, real multi-speaker recordings, Auphonic comparison | no listening or real-speech corpus. I did not listen to any output |
| **Unverified** | GPU / 16 GB compatibility | not applicable: CPU-only, and no such claim is made |
| **Unverified** | Legal status of conveying copies to recipient; whether this use counts as commercial | outside an engineering audit (F7) |

---

## 5. Notes by audit area

**A. Actual functionality**

- **Flow:** the GUI builds a `Project` and writes `<appdata>\<key>.request.json`. A `QProcess` then runs `Fauxphonic-worker.exe worker <request>`, which goes through `cli.execute` to `engine.render` and on to FFmpeg subprocesses. Events travel as JSON lines on stdout, and job state is kept in SQLite.
- **No early success:** `complete` is emitted only after verification and an atomic rename (`engine.py:204-215`). I found no success state that appears before verified completion.
- **Controls with real effect:** loudness target, true-peak ceiling, high-pass, stream index, offset, and gain. Noise reduction works only on mono Speech tracks (F2). Role affects only DSP eligibility.
- **Loudness normalization vs. leveling:** there is **no speech-aware leveling** (no voice-activity detection, no gain envelope). "Master only" is FFmpeg two-pass loudnorm.
  - It applies a single linear gain only when true peak plus that gain fits under the ceiling.
  - Otherwise it uses loudnorm's dynamic mode, which is time-varying but not speech-aware (F8).
- **Delivered vs. specified:**
  - Delivered: the Phase 1 slice plus parts of Phases 4–5 (queue, projects, integer offsets, portable build, installer).
  - Absent, and documented as absent: neural enhancement, speech-aware leveling, preview/A-B comparison, model manager, lossy exports, stems/automix/ducking, dereverb/de-essing, listening, clean-machine testing, and 16 GB GPU testing.

**B. Audio correctness**

- **Where the tests fall short of the claims:**
  - Offsets and endpoints are asserted only in bypass mode (`tests/test_engine.py:56-71`).
  - The DSP test checks only a report string (`:123-132`).
  - The failed-verification test replaces `verify` with a monkeypatch rather than producing a real failure (`:179-188`).
  - The GUI test drives the development worker, not the frozen one.
  - Nothing covers start times, gain limits, or meter independence.

**C. Reliability and data safety**

- **Strong:** cancellation, crash containment, output collisions, source immutability, and bounded memory all held up.
- **Weak:** F6, F9, and F10.

**D. Installer and portability**

- **Configuration:** per-user install into `LocalAppData\Programs`, x64 only, Windows 10 1809 minimum declared, stable AppId, no Registry/Environment/UninstallDelete entries, and no network or downloads.
- **Clean-machine gap:** relocation with a stripped environment on this PC is **not** a clean-machine test. VC++ runtimes are bundled and were the copies actually loaded, and the static import scan found no unresolved imports. Media Foundation is still a clean-machine gap (F12).

**E. Dependencies, models, and claims**

- **Models:** FastEnhancer remains research-only, with weight rights unresolved. No model runs in the shipped app.
- **FFmpeg:** provenance pinned and verified (F7 covers redistribution).
- **Claims:** there are no listening, Auphonic, or GPU claims, which is consistent with the evidence.

---

## 6. Prioritized repair list

**Shipping blockers (rebuild before sending):**

1. **F1 + F2:** compensate `afftdn` latency and add the mixed cleaned/uncleaned timing test, and refuse or warn when no track qualifies. Alternatively, disable DSP cleanup and delete README-recipient lines 18-19.
2. **F7:** add the FFmpeg corresponding-source / written-offer material and drop Qt Virtual Keyboard, Quick, QML, and PDF from the spec. (The project's own notice sets this gate.)
3. **Rebuild and re-verify:** strip `PROJECT_STATE.md` and the developer `README.md` from the payload (F11, trivial). Re-run the installer smoke test, and record the new installer hash.

**Before wider use:** F4 (independent verification meter), F3 (stream start times), F5 (gain cap), F6 (`CloseApplications`), F9, F10.

**Later product work (from the specification):** speech-aware leveling, show `mastering_mode` in the GUI (F8), test on a clean Windows machine and a Windows N edition (F12), signing, and representative listening evaluation.

---

## 7. Commands, environment, and limitations

**Environment:** a Windows 11 Pro 10.0.26200 development PC (i9-13900K, RTX 4090 24 GB, VC++ runtimes installed system-wide, LongPathsEnabled = 1), using the project `.venv` (Python 3.12.10). It is not a clean machine. All audio was synthetic: NumPy generators, FFmpeg lavfi, and the built-in Windows TTS voices. No user media was used.

**Commands:**

```text
git status --porcelain=v1 -uall ; git log ; git diff
sha256sum dist/installers/Fauxphonic-0.1.0-Setup.exe
.venv/Scripts/python.exe .cache/audit-20260925/compare_bytecode.py dist/Fauxphonic/Fauxphonic.exe dist/Fauxphonic/Fauxphonic-worker.exe   -> ALL IDENTICAL
(isolated copy) .venv/Scripts/python.exe -m pytest -q -p no:cacheprovider --basetemp=<audit>/pytest-base --tb=short                   -> 18 passed in 11.02s
cd .cache/audit-20260925 ; ../../.venv/Scripts/python.exe <script>   (results in results/*.json)
   exp01_timing.py  exp02_master_modes.py  exp03_meter_check.py  exp03b_calibration.py  exp04_tts_master.py
   exp05_edges.py  exp06_rejections.py  exp07_clipper.py  exp08_reliability.py
   exp09_gui_crash.py (QT_QPA_PLATFORM=offscreen; drives gui_harness.py)  exp10_install.py / exp10b_install_continue.py
   exp11_running_app.py  exp12_clean_machine.py  exp13_timing_confirm.py  bash exp14_rejections.sh
   (TTS inputs: System.Speech SpeechSynthesizer, 48 kHz mono, written to work/tts/)
Fauxphonic-0.1.0-Setup.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- /NOICONS /MERGETASKS=!desktopicon /DIR=<audit dir> /LOG=<log>
unins000.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG=<log>
```

**Authoritative sources:**
- `C:\Program Files (x86)\Inno Setup 6\license.txt` and `whatsnew.htm`
- https://jrsoftware.org/isorder.php
- https://doc.qt.io/qt-6/qtvirtualkeyboard-index.html
- https://doc.qt.io/qt-6/qtmultimedia-index.html
- https://www.gyan.dev/ffmpeg/builds/
- The GPLv3 text in `tools/LICENSE`

**Limitations:**

- **Registry sandbox:** the agent's command sandbox virtualized HKCU for the installer. Its uninstall key was visible only inside that sandbox, never in your real Apps list. File effects were real and confined to `.cache\audit-20260925`.
- **Network:** sockets were observed by polling (about 100 ms), not by packet capture.
- **Material:** the synthetic signals are not speech. The pass/refusal rates in F4 are specific to the material tested.
- **Scope:** there was no listening, clean machine, or GPU testing.
- **Harness fault (mine):** the first run of `exp10_install.py` deadlocked. I piped the installed worker's stdout without draining it, and the worker blocked writing progress events; the real GUI drains this pipe continuously. I stopped that worker (its leftover scratch folder was recorded as crash evidence), and `exp10b_install_continue.py` resumed from the same test install.

**State after the audit:**

- Repository: `git status` is unchanged and `evidence/desktop.png` is unchanged.
- The app-data folder my GUI test created (absent before) was moved into `.cache\audit-20260925\appdata-created-by-audit`.
- My crash test's `%TEMP%` scratch was removed.
- No install folder, shortcut, or registration remains.
- Audit scripts, results, leftover test installs, and about 3 GB of synthetic audio are in `.cache\audit-20260925\`, which is safe to delete.
