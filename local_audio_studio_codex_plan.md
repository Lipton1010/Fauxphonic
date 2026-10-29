# Local Audio Studio — Codex implementation specification

**Research cutoff:** September 25, 2026  
**Product:** Windows-first, native desktop audio cleanup and mastering application  
**Hardware constraint:** NVIDIA graphics card with **16 GB total VRAM**, not 24 GB  
**Operating model:** Local processing; no paid inference service or uploaded recordings  
**Working name:** Local Audio Studio; use original branding, not Auphonic's branding

## 1. Mission and execution instructions

Build a real, installable desktop application that produces polished spoken-word audio from interviews, podcasts, narration, remote calls, and long multitrack tabletop recordings. The reference workflow is Auphonic: import recordings, choose sensible processing, preview, render, and receive consistent, clean, correctly mastered files.

This is an implementation assignment, not a request for another proposal, a mock interface, or a collection of notebooks. Deliver a working vertical slice early, then expand it through the stages below. Do not claim identical output to Auphonic's proprietary algorithms. Treat comparable quality as an empirical goal requiring representative listening tests and measured export compliance.

Work in the repository currently selected in Codex. First identify its absolute path, inspect its contents and Git status, and read applicable AGENTS.md instructions. Never assume that an unrelated existing project is the intended destination. Preserve user changes, recordings, models, and other projects. If the selected repository already contains an application, document how this specification relates to it before integrating; do not replace it wholesale.

Within an appropriate new project, proceed with reversible implementation and tests without repeatedly asking for routine decisions. Request authorization for genuinely consequential external changes: global software installs, driver changes, enabling WSL, paid services, accepting restrictive licenses on the user's behalf, or modifying unrelated projects. No training or fine-tuning is required.

Create a concise project AGENTS.md containing the invariants, commands, and definition of done. Keep this longer specification as a separately referenced design document. Maintain one authoritative PROJECT_STATE.md with working features, evidence, open defects, and the next action. OpenAI documents AGENTS.md as project guidance loaded by Codex; do not change the user's global Codex configuration. [S17]

Use available repository-navigation or graph tools only when they help find real code relationships. Exclude audio, weights, caches, virtual environments, and build outputs from indexes. Do not install a graph service or invent unavailable tools. Independent subagents may handle bounded, nonoverlapping work; the orchestrator owns contracts and integration. Do not change configured model selections or create uncontrolled parallel workers.

## 2. Product boundaries and success definition

The useful comparison is the complete processing chain, not a single denoising model. Auphonic documents adaptive leveling, noise/reverb handling, mastering, and multitrack automation; its July 2026 Studio Voice feature additionally reconstructs damaged speech. [S1–S4]

### Required core release

- Native desktop window, file/folder drag-and-drop, project persistence, presets, preview, batch queue, progress, cancellation, and resumable work.
- Mono speech and explicitly mapped multitrack recordings; stereo sources are supported without silently destroying their spatial information.
- Local noise reduction, conservative filtering, speech-aware leveling, configurable loudness/true-peak mastering, and verified exports.
- Preserve source timing and original files. Process long recordings with bounded memory.
- Explain which model and processing stages actually ran. Export a machine-readable processing report.
- Launch by double-clicking the packaged application without opening a browser or terminal.

### Full spoken-word release, after the core works

Add editable leveling envelopes, de-essing and plosive control, a validated dereverberation backend, track-role-aware automixing, conservative bleed attenuation, music ducking, aligned stems, region-specific processing, metadata, and richer comparison tools.

### Optional extensions, not prerequisites for basic use

Regenerative restoration, transcription and aligned subtitles, reviewed silence/filler editing, chapter markers, video-audio replacement, and watch-folder automation. Live microphone processing and source separation from arbitrary finished music mixes are separate projects unless the core is already stable.

No cloud account, subscription, web server, or LLM is required for the core audio processing. A speech recognizer transcribes speech; it is not the denoiser or mastering engine.

## 3. Evidence-based model decisions

These are **initial candidates**, not benchmark results from the user's computer. Recheck upstream releases at implementation time, pin compatible revisions, and promote models only after the installation, licensing, fidelity, and resource gates below. A newer date alone does not justify replacing a better working model.

### A. Primary efficiency candidate: FastEnhancer, native 48 kHz

Use the official published 48 kHz ONNX models as the first modern enhancement integration. Start by comparing Medium and Small; include Large as a quality candidate rather than presuming that its additional computation is worthwhile. The upstream configuration table lists approximately 512 thousand parameters for Medium48k and 1.132 million for Large48k. These are small specialist networks, not billion-parameter language models. [S5–S7]

Benchmark ONNX Runtime CPU and CUDA execution separately. The CPU may be the better deployment choice for a small streaming network; use measured end-to-end latency, not a mandatory GPU checkbox. The repository is MIT-licensed; separately record the rights covering the downloaded checkpoint and any redistributed components.

Do not call an ordinary noise-suppression checkpoint a dereverberation model. Upstream also lists a dereverberation prerelease: evaluate it as a distinct, opt-in backend with its own manifest, tests, and promotion gate. [S6]

### B. Established fallback: DeepFilterNet3

Implement an explicitly selected DeepFilterNet3 48 kHz backend as an older, lightweight comparison and fallback. Do not label it the newest model. Inspect the exact available binary/Python integration and compensate its documented processing delay. The upstream README includes commands whose default model is an earlier generation; verify that the app really loads version 3. [S9]

### C. Quality challenger: ClearerVoice-Studio

Evaluate the published `alibabasglab/MossFormer2_SE_48K` enhancement checkpoint using its actual ClearerVoice-Studio interface. Treat it as an optional quality challenger, not a claimed universal improvement. Record checkpoint licensing separately from toolkit licensing, test Windows support, and measure chunked peak memory. Do not download every model in the toolkit. [S10]

Do not conflate its enhancement, separation, and speech-super-resolution components. Only advertise a capability supported by the specific installed checkpoint.

### D. Experimental restoration: NVIDIA RE-USE

RE-USE's March 2026 release covers several degradation types, including clipping, codec damage, restricted bandwidth, noise, and reverb. Its card provides chunked inference but describes Linux-oriented integration and a research/development-only, NVIDIA noncommercial license. [S11]

It is **not** an unconditional default dependency or an unrestricted commercial component. Keep it outside the standard distribution unless its actual terms and intended use permit inclusion. Expose its restrictions before installation, never accept them automatically, and do not make a legal conclusion from a repository badge alone.

First test its actual supplied inference scripts and Mamba dependencies in an isolated worker. Do not use a generic model-hub auto-generated language-model example as an audio inference implementation. If native Windows operation cannot be validated, leave it unavailable in the native core. An explicitly authorized WSL research bridge may be explored separately, but never silently install WSL or make it a hidden requirement.

Real-time RE-USE has a June 2026 paper and configurable quality/latency modes. It is relevant to a later live-preview experiment; ordinary file postproduction does not require it. The same license/platform/resource checks apply. [S12]

### E. Further research, not release blockers

UniPASE's April 2026 work is a candidate for regenerative speech restoration. Its repository documents multiple inference components and long-form processing. Confirm that the required weights are obtainable and that every component's license permits the intended use before implementing it. Do not mark an adapter functional because code exists while checkpoints are missing. [S13]

The July 2026 `faster-enhancer.c` paper describes an efficient int8 CPU runtime for FastEnhancer-Medium48k. Evaluate this only after the reference implementation works. Published measurements on other processors are not Windows benchmarks. Promote a native port only after matching the reference output and demonstrating actual benefit on the target machine. [S8]

### F. Optional transcription

Use `Qwen/Qwen3-ASR-0.6B` as an initial compact local transcription candidate. The official card provides a Transformers backend and a separate forced aligner. Run transcription and alignment sequentially, with conservative batches, rather than loading every model together. The card lists Apache-2.0. [S14]

Do not require vLLM, a Gradio server, FlashAttention compilation, or a cloud API for the Windows core. Verify the simplest supported local backend first. ASR remains optional and off the critical path for audio rendering.

### Candidate promotion checklist

For each enabled model, produce a recorded result for: source and revision; code/weight/dependency licenses; actual download location and hashes; clean environment installation; native inference; supported rate/channel layout; length and delay behavior; peak VRAM and host RAM; end-to-end speed; listening evaluation; long-form behavior; and offline operation. Failed candidates remain clearly disabled with the reason shown. Never substitute a different model behind an unchanged label.

## 4. Architecture: desktop first, shared engine

Use a native Python/PySide6 application with a separately testable processing engine. Choose a currently supported Python minor version compatible with the selected dependencies after a small installation proof. Do not pick the newest interpreter solely for its release date or install unpinned latest packages on every launch.

Recommended components:

| Responsibility | Implementation direction |
|---|---|
| Desktop UI | PySide6 Widgets, native dialogs, scalable waveforms, keyboard shortcuts |
| Audio probing/decoding/encoding | Pinned FFmpeg and ffprobe executables |
| Array processing | NumPy/SciPy and a tested resampler, installed only where needed |
| Lightweight inference | ONNX Runtime worker, CPU first and CUDA when beneficial |
| Larger inference | Optional isolated PyTorch workers |
| Durable jobs | SQLite plus versioned project/manifest JSON |
| Media playback | A native Qt audio path with tested device selection and seeking |
| CLI | Thin command layer calling the same engine as the GUI |
| Packaging | Reproducible one-folder Windows bundle; installer after portable validation |

Qt provides documented desktop deployment paths; prove the selected packaging path with the actual worker dependencies. [S16]

Do not put signal processing or model imports inside GUI event handlers. Use worker processes through QProcess or an equivalent supervised process interface. Use versioned JSON Lines over pipes for commands/events, with diagnostics on a separate stream; do not intermix arbitrary library logging with protocol messages. Pass paths and bounded metadata, not base64-encoded hours of audio.

Keep the architecture modest: one desktop application, a coordinator, workers, SQLite, and a local cache. No Kubernetes, distributed queue, vector database, web authentication, or localhost HTTP service is necessary.

### Suggested repository structure

```text
AGENTS.md
PROJECT_STATE.md
README.md
pyproject.toml
locks/
  desktop.lock
  onnx-worker.lock
  torch-worker.lock
models/
  registry.json
  models.lock.json
src/local_audio_studio/
  gui/
  engine/
    contracts.py
    planner.py
    timeline.py
    analysis.py
    audio_io.py
    mastering.py
    verification.py
  dsp/
  backends/
  workers/
  jobs/
  cli/
  reporting/
tests/
  unit/
  integration/
  desktop/
  fixtures/
benchmarks/
  corpus_manifest.json
  results/
docs/
  architecture.md
  model_decisions.md
  feature_parity.md
  packaging.md
  sources.md
scripts/
  bootstrap.ps1
  build_windows.ps1
```

The `models/` repository directory holds manifests, not downloaded weights. Use an external user-selected model cache. Check exact dependency availability before choosing lock tooling.

## 5. Data contracts and reproducibility

Define typed contracts for `Project`, `SourceAsset`, `Track`, `Region`, `Preset`, `AnalysisResult`, `ModelCapability`, `RenderPlan`, `Job`, `Artifact`, and `VerificationReport`.

The audio contract is float32 arrays shaped `(frames, channels)` plus explicit sample rate, channel layout, source timestamp, and valid frame count. Reject ambiguous shapes. Keep model-internal tensor layouts inside adapters.

Represent timeline positions using integer sample indices or rational time values, not accumulated rounded milliseconds. Store original-to-project and project-to-export mappings. Preserve filenames as labels while using internal IDs for safe artifact naming.

Each model manifest must describe: repository/revision, artifact filenames, verified SHA-256, download size, licenses, input/output rates, allowed channels, tested dtypes/devices, streaming state, context requirements, padding, algorithmic delay, length reconciliation, and supported user controls. Include a measured resource profile keyed by hardware, runtime version, model revision, dtype, and chunk configuration.

Use a small backend interface with lifecycle, capability inspection, processing, cancellation, and cleanup. Support two explicit execution styles: stateful streaming and windowed offline inference. Never pretend that resetting a recurrent network every ten seconds is equivalent to its official streaming algorithm.

Record all effective parameters, model checksums, software versions, channel mappings, gains, edits, and output measurements. A render ID must change when a sound-affecting input changes. Cache keys must include input hashes, region/context, parameters, model revision, and engine version.

## 6. Import, timeline, and long recordings

Probe the selected audio stream with ffprobe. Capture duration, frame count when available, rate, channel layout, codec, stream start time, metadata, and available broadcast timestamps. Let the user select an audio stream from a video/container; do not silently use the wrong language or commentary track.

Keep originals immutable. Decode to disk-backed float PCM for processing; use 48 kHz as the initial speech-processing project rate where appropriate. Preserve a reference to the untouched source and record resampling. Do not silently funnel the final master through a 16 kHz analysis signal. Upsampling a narrowband source is not proof that missing detail has been restored.

For long files, use streaming pipes or RF64/W64/chunked scratch storage rather than assuming ordinary WAV can hold every intermediate. Calculate required scratch/output space before starting. Keep a cache quota and explain eviction; never delete source recordings to make room.

Multitrack sources with a common recorded start retain it. Allow explicit offsets and verified metadata-based alignment. Independent microphone files are not automatically a synchronized microphone array. Never align tracks by deleting initial silence. Optional correlation-based alignment must show confidence and require review; do not quietly time-stretch to hide drift.

**Critical rule:** never trim silence independently from each speaker's file. In a tabletop recording this would move dialogue and reactions out of chronological alignment. A future edit operation must apply one shared edit decision list to every affected track.

Support channel mapping that distinguishes stereo program material, dual-mono microphones, separate speakers, music beds, and ambience. Do not run a mono speech model independently on left/right music and call the result stereo-safe. When no validated stereo enhancement method exists, preserve/bypass or request an explicit downmix decision.

## 7. Analysis and processing policy

Before applying significant gain changes, run a first-pass analysis. Calculate fullband loudness/peak statistics, speech-active energy, approximate noise level, clipping candidates, and a multiresolution waveform. Record confidence and limitations; do not fabricate a universal “audio quality score.”

Use a separate resampled analysis stream for a lightweight local voice-activity detector such as Silero VAD, with a pinned model/runtime and verified license. Map analysis timestamps back onto the master timeline exactly. [S18]

Voice activity is not the same thing as speaker identity or artistic relevance. Laughter, breaths, whispers, music, and sound effects must not disappear merely because a detector labels them nonspeech. Infer speaker identity from separately labeled tracks first; single-track speaker diarization is an optional later capability.

Provide these processing intents:

| Intent | Default behavior |
|---|---|
| Natural speech | Conservative noise cleanup and leveling; preserve voice character, pauses, and expressive variation |
| Strong speech cleanup | Greater suppression after preview; warn about removal of background content |
| Preserve music/ambience | Bypass speech-only denoisers unless regions are explicitly selected; linked conservative DSP |
| Restore damaged speech | Optional reconstructive model on selected regions; visible warning and original available |
| Multitrack session | Per-track speech cleanup, preserved timing, conservative mix automation |
| Master only | No neural alteration; loudness/export processing only |

The default must be conservative when content is uncertain. Auphonic itself distinguishes processing that preserves music from speech isolation; do not present one generic denoiser as equivalent to both. [S2]

## 8. Enhancement, dereverberation, and fidelity

First implement clean decode/process/encode bypass. Then add minimal DC/rumble filtering only where justified. Make high-pass cutoff and hum suppression optional, conservative, and bypassable. Do not force one low-frequency cutoff on every voice, instrument, or recording.

### FastEnhancer implementation details

Start from upstream inference, not a guessed ONNX input tensor. Inspect input/output names and cache tensors. Its 48 kHz configuration uses a different FFT/hop setup from the older examples: the published table lists FFT 1024, Medium hop 320, and Large hop 200. Verify these against the selected artifact. The upstream wav2wav documentation describes a delay related to `n_fft - hop_size`; account for initialization and final flushing exactly once. [S5–S7]

Preserve neural state, input buffers, and overlap-add state across disk-read blocks. Streaming frame length is not the same as the size of the file I/O buffer. Test the spec2spec and wav2wav versions against the official reference before selecting a deployment path. If replacing a PyTorch STFT with another implementation, match window definition, normalization, centering, padding, complex convention, and reconstruction numerically.

Do not automatically choose a convolution-STFT export merely because it appears later in a release list. Measure the supported execution providers and end-to-end performance. A CUDA graph that quietly runs most operations on the CPU is not evidence of GPU acceleration.

### Windowed models

For offline models that require windows, begin with a supported short window and explicit context; test approximately 5–10 seconds only where allowed by the model. Use normalized overlap-add with a valid complementary window, boundary context, deterministic padding, and exact output accounting. Do not blindly crop every model to ten seconds or average unrelated chunks.

Delay-align any dry/wet blend. For reconstructive models, prefer region replacement with tested boundary fades instead of mixing misaligned waveforms and creating comb filtering. Processing intensity controls must map to a real supported model parameter or a documented, tested postprocessing operation.

Do not stack multiple strong denoisers by default. Create separate candidate routes and choose based on results. Preserve an intelligible consonant over removing the final trace of background hiss.

### Dereverberation and damage repair

Require a separately validated dereverb-capable checkpoint before enabling a “Reduce room echo” control. Quantify and listen for erased speech tails, watery artifacts, or altered speaker tone. Ordinary denoising must not masquerade as dereverberation.

Clipping, packet loss, and codec artifacts are not fixed by lowering peak level. Flag probable damage and offer a validated restoration route only where available. Regenerated high-frequency content is an estimate, not recovered ground truth. Never use TTS to silently replace the user's words or voice.

No unvalidated resynthesis runs automatically over an entire long session. Preview a selected region, preserve the original, and record every reconstructed range.

## 9. Intelligent leveling: the critical non-model feature

Implement a speech-aware, two-pass gain-envelope system. Global normalization alone will not fix an interview with one quiet person and one loud person.

Use original activity information and post-cleanup speech energy to estimate robust utterance-level loudness. Group speech into phrase/utterance regions rather than treating every tiny VAD fragment as a separate speaker turn. Estimate a stable track/speaker baseline where reliable labels exist.

Construct a slowly changing desired gain envelope relative to a shared working reference. Use confidence gating, a maximum boost, maximum attenuation, slew limits, and explicit silence handling. Initial tunable engineering defaults may include a +9 dB boost cap, -12 dB attenuation cap, and roughly 0.5–2 second transitions; validate them rather than treating them as a professional standard.

Do not boost silence, background bleed, room noise, or isolated breaths to the speech target. Freeze or gently decay gain outside confident speech. Preserve intentional whispers, laughter, emphasis, and distance changes unless the user selects stronger leveling.

Leveler controls should include strength, preserve dynamics, maximum boost, and editable regional gain. Show the actual effective gain curve, with undo and manual overrides. At minimum, implement linked momentary/phrase leveling; only advertise speaker-aware single-file leveling when speaker segmentation really exists.

Follow with a mild, optional dynamics stage when needed, not an aggressive compressor applied indiscriminately. Keep the working mix away from overload before final mastering. A final loudness meter is not proof that local speaker balance is good.

Test two speakers separated by a known gain difference, long gaps, already-normalized narration, whispered passages, laughter peaks, and background music. Listen for pumping, breaths jumping forward, clipped initial consonants, and loss of emphasis.

## 10. EQ, sibilance, plosives, and track-aware mixing

Add these after the main cleanup/leveling path passes tests.

**Tone:** begin with user-controlled broad EQ and a conservative assist mode. Any automatic correction should use speech-active statistics, bounded broad-band changes, and a no-op region. A reasonable starting ceiling is about ±3 dB per broad adjustment. Do not equate a fixed “voice preset” with Auphonic's proprietary AutoEQ or impose gender-based spectral targets.

**Sibilance and plosives:** implement bounded dynamic attenuation with adjustable sensitivity and preview. Avoid a fixed low-pass filter sold as de-essing. Protect legitimate fricatives and low vocal fundamentals. A detector must not claim perfect event recognition.

**Multitrack automix:** maintain independently synchronized tracks. Normalize speaker-track baselines, estimate activity using the original signal, and mix with controlled headroom. Derive bleed candidates from relative level, activity, and temporal correlation; treat true overlapping speech as valid. Unknown cases should receive little or no attenuation, not hard muting. Provide minimum gain, attack, hold, release, and per-track override controls.

**Music and ambience:** the user explicitly assigns these roles. Exempt them from voice-only cleanup. Duck music against confident speech using a smooth sidechain envelope; initial audition ranges of 6–12 dB attenuation, 50–150 ms attack, and 0.5–1.5 second release are engineering starting points, not guaranteed best settings. Preserve room tone and deliberately chosen sound effects.

Export both the final mix and optional aligned processed stems. Clearly label whether stems are pre-master, have an allocated share of master gain, or are individually mastered; do not imply that separately normalized stems will sum to the mastered mix.

## 11. Mastering, encoding, and verification

Master the **whole final program**, after enhancement, leveling, mixing, and approved editing. Never normalize each neural window independently.

Use FFmpeg's documented two-pass `loudnorm` workflow initially. Pass measured integrated loudness, true peak, loudness range, threshold, and offset into the render pass. Its linear path has feasibility conditions and can fall back to dynamic normalization; record the actual mode. Explicitly set output sample rate because dynamic true-peak processing can use a higher internal rate. [S15]

Provide editable starting presets such as stereo spoken word at -16 LUFS, mono spoken word at -19 LUFS, and a -1 dBTP ceiling. These are app starting choices, not universal certification requirements. Store channel interpretation and dual-mono behavior explicitly so changing channel layout does not silently change the meaning of the target.

Expose loudness and true-peak targets separately. Make loudness-range reduction optional. When integrated loudness, dynamics, and peak constraints conflict, explain the tradeoff; never claim all constraints passed without measurements. Detect silence/nonfinite loudness and avoid enormous gain boosts or invalid filter arguments.

A sample-peak limiter is not automatically a true-peak limiter. Use validated oversampled processing where necessary, and verify through a tested true-peak measurement path. Do not write an untested “true peak” routine merely to avoid a dependency.

Render a lossless/float intermediate master. Export selected WAV/FLAC and supported MP3/AAC/Opus profiles through a pinned FFmpeg build. Validate codec availability and distribution obligations. Use integer PCM dither only at the appropriate final quantization step, not repeatedly between processing stages. Avoid lossy intermediates.

After encoding, decode the **actual final file** and remeasure duration, loudness, peaks, channel count, and rate. Lossy codec output may need extra headroom; make a bounded retry from the lossless master, never repeated lossy-to-lossy conversions. Report a failed constraint rather than silently accepting it.

For suitable test material, use a starting integrated-loudness tolerance of ±0.5 LU and a true-peak verification tolerance of 0.1 dB. These are project acceptance thresholds, not a claimed external standard. Very short, silent, heavily constrained, or structurally unusual clips require an explicit exception status, not a fabricated pass.

Audiobook delivery profiles must separately implement the destination's actual current metrics. Do not rename a LUFS preset “ACX compliant”; verify the current RMS, peak, noise, formatting, and chapter rules before offering that label.

## 12. Resource management: enforce the 16 GB constraint

Treat 16 GB as total device memory, not memory reserved exclusively for this app. Detect the actual GPU vendor, model, driver, CUDA compatibility, and free memory. A 16 GB AMD card is not a CUDA device; report supported execution paths honestly. CPU mode should remain available for the basic path.

Use these initial **engineering budgets**, not claims about measured model consumption:

- Aim for no more than approximately 12 GiB peak GPU memory for the complete active worker configuration on a 16 GB card, leaving desktop/runtime headroom. Lower the budget when actual available memory requires it.
- Keep exactly one GPU-heavy job/model resident at a time. Run lightweight analysis and DSP on the CPU. If ASR or restoration is selected, finish and unload the current GPU stage before starting another.
- Start with batch size one. Calibrate supported window sizes on a representative sample before a long run. Show measured speed, GPU peak, host RAM peak, and the chosen configuration.
- Bound host-memory queues and waveform caches. A multi-hour file must not become one enormous NumPy array. Use disk-backed intermediates and bounded mixing blocks.

Measure both runtime allocator statistics and device-level usage where available. Windows/driver monitoring may not expose reliable per-process accounting; state that limitation and use a conservative device-level delta and safety reserve. A PyTorch allocator limit does not constrain every ONNX/CUDA allocation or another application.

GPU OOM recovery must terminate or cleanly reset the failed worker, preserve the last valid checkpoint, reduce only parameters the model supports, and retry a bounded number of times. If state cannot be restored reliably, restart that track/stage deterministically. CPU fallback must be labeled and only used where supported. Never silently skip enhancement and report success.

Use an application-level GPU lease across jobs and instances. Do not kill other applications to free memory. Do not change graphics drivers or system CUDA installations without authorization.

Only enable FP16, BF16, quantization, or a native optimized runtime after numerical and listening comparisons. “Int8 fits” is not equivalent to “int8 sounds the same.” Keep the reference implementation available for diagnosis.

If development hardware has more than 16 GB, a conservative memory target is useful evidence but not a substitute for a real 16 GB compatibility test. Report the hardware actually tested.

## 13. Desktop workflow and user experience

Implement a focused audio utility, not a full digital audio workstation.

The main window should contain a project/queue area, waveform and track view, settings inspector, native transport, preview controls, and output/progress area. Provide a compact default workflow with advanced controls collapsed. Support Windows display scaling, accessible contrast, keyboard navigation, and filenames containing spaces and Unicode.

The primary flow is: import -> assign track roles -> analyze -> choose preset -> preview -> render -> inspect verification/report.

Preview a user-selected 10–30 second region with sufficient model context. For an adaptive leveler, use cached program-level analysis rather than recomputing unrelated local gain behavior. Distinguish a rough preview from a final-equivalent preview when necessary.

Provide time-aligned original/processed switching, optional loudness-matched comparison, and per-stage bypass. Loudness matching is for comparison only and must not alter the intended export. Show any preview gain compensation. A louder sample should not win a quality comparison merely because it is louder.

Every visible control must modify real parameters or be disabled with an explanation. No “AI enhance” button that only copies the file, fake progress, invented quality scores, or success dialogs before verification. Keep worker errors readable, with a technical detail panel.

The model manager displays capability, actual installed version, disk size, license restrictions, resource test result, and installation status. Allow user-selected storage and offline import of a verified model bundle. Download progress must be cancellable and resumable without treating an incomplete artifact as valid.

Give users explicit output naming, destination, collision handling, and overwrite policy. Default to a new file. Never overwrite the original recording.

## 14. Jobs, resumability, privacy, and security

Persist jobs through a clear state machine: queued, analyzing, processing, mixing, mastering, verifying, complete, failed, cancelled, and interrupted. Preserve per-stage events and user-visible recovery information.

Use temporary output names and atomic promotion only after a stage/file is complete and validated. Cache entries require checksums and a matching render key. On restart, revalidate artifacts instead of trusting a database flag alone.

For recurrent models, checkpoint every relevant state tensor and audio position with a versioned format, or explicitly choose track/stage restart. Saving only an input filename and chunk number is insufficient for exact continuation. Do not persist unsafe arbitrary executable objects for convenience.

The default runtime makes no external requests. Separate user-initiated dependency/model acquisition from normal processing. No telemetry, uploads, analytics, cloud fallback, or automatic crash reports containing transcripts or media paths. Respect offline flags in model libraries and test with networking blocked.

Use approved source hosts, pinned revisions, checksum verification, TLS, safe extraction, and least-privilege writes. Do not automatically execute code from an arbitrary dropped checkpoint or enable `trust_remote_code` without review. If an upstream loader requires an unsafe serialization format, isolate and document the risk rather than pretending checksums make it intrinsically safe.

Invoke FFmpeg and workers with argument arrays, not interpolated shell command strings. Treat filenames and metadata as untrusted data. Restrict destructive cache cleanup to app-owned directories, validate path boundaries and symlinks, and avoid logging authentication tokens.

## 15. Optional editing and transcripts

Keep these off by default until their invariants are tested.

**Transcription:** run local ASR on either the reference or cleaned audio selected by the user, keeping the choice in metadata. Use modest windows/batches and precise timestamp mapping. The recognizer's output is not ground truth for a fidelity test. Prefer existing per-track speaker names to a new diarization model when separate tracks already identify speakers.

**Forced alignment:** load only after ASR releases GPU memory. Export TXT, structured JSON, SRT, and VTT where implemented. Preserve source and edited timeline mappings.

**Silence/filler editing:** generate proposals and an editable shared EDL. Provide audition, undo, exclusions, and safe crossfades. Do not delete based only on VAD or uncertain word timestamps. Apply approved edits consistently to all aligned stems and remap captions/chapters. Never remove meaningful pauses by default from dramatic narration or tabletop sessions.

**Chapters/metadata:** support manual markers, saved tags, and embedded metadata supported by each format. Model-generated summaries or chapters are a separate optional feature, not necessary to improve audio.

**Video:** an optional file-based path may replace an audio stream while copying video where compatible. Verify timestamps and container behavior. Do not re-encode video unnecessarily or claim synchronization without a test.

## 16. Quality evaluation: prove usefulness, not just execution

Create a licensed, documented evaluation corpus with two parts: synthetic fixtures for deterministic tests and human recordings for listening. User recordings remain private. Do not download an unrestricted collection of copyrighted audio or redistribute it as test fixtures.

Cover clean narration, fan/hum noise, transient noise, quiet/loud speakers, room reverb, phone bandwidth, codec degradation, clipping, whispers, laughter, breaths, sibilance, plosives, overlapping speech, music, ambience, long silence, and already-good mastered content. Include speaker/accent variety where consent and licensing permit.

Create clean/noisy pairs where possible. Evaluate signal distortion and intelligibility with metrics appropriate to the data; use transcription error only against a trusted reference transcript. Predicted perceptual scores are optional secondary indicators, not a replacement for listening, and their model licenses also require review.

### Comparison protocol

Compare untreated audio, DSP-only mastering, FastEnhancer candidates, DeepFilterNet3, and each promoted quality/restoration route. Include user-provided Auphonic reference exports when available, together with their settings and service date. Never require an Auphonic subscription or automatically upload the user's audio.

Time-align and loudness-match comparisons, randomize labels where practical, and evaluate naturalness, intelligibility, artifacts, voice identity, expression, and music preservation. Keep export-loudness compliance separate from perceptual preference. Do not score jingles, leading padding, or mismatched edits as differences in enhancement quality.

Human listening is a release gate for “recommended quality.” An automated agent must not claim it listened unless it actually has an audio-capable evaluation tool. It may complete and label engineering tests while leaving listening approval pending. Never invent MOS scores or a percentage of “Auphonic parity.”

### Required engineering tests

1. Bypass preserves expected audio samples/timing within the declared format conversion policy.
2. Model outputs have finite values, valid shapes, expected length, and tested delay compensation; first/last words survive.
3. Streaming block sizes do not introduce state resets or audible seams; seek/restart/preview paths agree with their declared semantics.
4. Stereo-linked processing preserves channel relationship in the bypass/master path; unsupported enhancement layouts are blocked or explicitly mapped.
5. Multitrack offsets survive processing; shared edits retain alignment. Never use automatic stretch to pass a test.
6. Final exported files meet the selected measurable constraints or receive a visible exception/failure status.
7. Source hashes remain unchanged, failed outputs are not promoted, and Unicode/space-containing paths work.
8. A long-form test, initially several hours and multiple tracks, shows bounded host/GPU memory rather than growth proportional to duration. Synthetic long files test engineering, not subjective sound quality.
9. Cancellation, worker crashes, disk-full conditions, broken models, missing GPU, and restart are handled without corruption or false success.
10. The packaged application launches and processes offline on a clean Windows machine without a development environment.

Record real-time factor as total processing wall time divided by source duration, with both per-track and whole-job definitions for multitrack tests. Include model load time, preprocessing, postprocessing, and I/O separately. Do not quote a paper's CPU benchmark as the app's measured speed.

## 17. Packaging and installation

Build a one-folder portable Windows bundle first, with a double-click executable and no console window for ordinary use. Include required Qt plugins, native audio support, codecs/runtime components as permitted, and a working worker launcher. Official Qt deployment documentation should guide tool selection, not replace a clean-machine test. [S16]

Do not assume a copied development virtual environment is relocatable. Package a self-contained worker runtime or reproducibly create a private managed environment during an explicit installation step. Test each path without the developer's Python or environment variables.

Keep large model files outside the executable. Use versioned manifests and optional packs so an unavailable experimental model does not break the whole application. Avoid a giant one-file executable that unpacks all models on every launch.

Pin the FFmpeg build, Python packages, model revisions, and packaging toolchain. Generate third-party notices, license inventory, and checksums. Distribution obligations differ among code, binaries, weights, and codecs; verify the specific components actually included.

The installer may offer application shortcuts and uninstall support, but must not silently alter global PATH, replace graphics drivers, or remove user projects/models. Clearly separate deleting the application from deleting user-owned media/cache.

Deliver `bootstrap.ps1`, `build_windows.ps1`, a dependency diagnostic command, and end-user installation instructions. Code signing is optional and must not involve a paid purchase without authorization. Do not promise that an unsigned binary will avoid OS reputation warnings.

## 18. Implementation sequence and exit gates

### Phase 0 — Repository and feasibility audit

Identify the correct workspace and constraints. Produce architecture/model decision notes, a feature-parity matrix, and a minimal dependency proof. Verify the chosen 48 kHz FastEnhancer artifact, its exact configuration and rights, and actual native inference on a short licensed clip. Create hardware diagnostics and document unknowns. Time-box unsupported research-model integration; do not let it prevent core work.

**Exit evidence:** exact workspace, dependency lock approach, source/revision inventory, native sample output, resource report, and prioritized remaining gaps. If no model can yet run, clearly state that limitation and continue building the independent DSP/desktop path without labeling it AI-enhanced.

### Phase 1 — First real desktop vertical slice

Create the engine/CLI and a minimal native window together. Import a file, probe it, choose master-only or an available validated enhancement path, run in a worker, and export a verified WAV. Display genuine progress and errors. Preserve the source.

**Exit evidence:** double-clickable development launcher, real file-to-file transformation, verified report, and tests. An empty desktop shell does not satisfy this phase.

### Phase 2 — Reliable streaming cleanup

Complete FastEnhancer state/delay handling, bounded file I/O, conservative parameters, CPU/CUDA benchmarking, and an explicit DeepFilterNet3 fallback. Add short A/B preview, cache keys, and failure handling.

**Exit evidence:** reference comparisons, no-seam tests, measured resource/speed profiles, and a long recording processed without unbounded memory.

### Phase 3 — Leveling and trustworthy mastering

Implement the speech-aware leveler, editable gain data, two-pass program mastering, export profiles, post-encode verification, and reports. Add tests that distinguish leveling from simple global gain.

**Exit evidence:** quiet/loud speaker fixture improves within the chosen parameters, silence is not amplified, and encoded outputs have measured compliance or explained exceptions.

### Phase 4 — Complete native workflow and multitrack

Finish project persistence, track roles/offsets, waveform/transport, queue recovery, model manager, presets, aligned stems, conservative automix, and music ducking. Add bounded EQ/de-essing/plosive controls where tested. Integrate a dereverb-capable backend only after its capability gate passes.

**Exit evidence:** an end-to-end multitrack project can be imported, previewed, cancelled/reopened, rendered, and verified without changing chronology or deleting originals. Unavailable capabilities stay visibly disabled.

### Phase 5 — Quality comparison and packaging

Run the engineering corpus, prepare blind listening comparisons, fix the highest-impact defects, and build the portable distribution. Test offline on a clean Windows installation and, where available, an actual 16 GB card.

**Exit evidence:** runnable bundle, exact build instructions, feature/capability matrix, test logs, benchmark records with hardware, and an honest list of pending listening/hardware checks. Do not call the app Auphonic-equivalent without supported comparison results.

### Phase 6 — Optional capability packs

Add a promoted quality challenger, authorized restoration research integration, transcription/alignment, reviewed edits, metadata/chapters, or video support in small independent increments. Evaluate the int8 native efficiency candidate only against the proven reference. Each pack must preserve the same timing, licensing, offline, and memory invariants.

**Exit evidence:** pack-specific tests and measurements, not just a new UI checkbox. The core application remains useful when all optional packs are absent.

## 19. CLI contract, artifacts, and completion report

Implement equivalent engine functionality behind commands such as the following. These are **commands to create**, not a claim that an existing package already supplies them:

```powershell
local-audio doctor --json
local-audio analyze "recording.wav" --report "analysis.json"
local-audio process "recording.wav" --preset natural-speech --output "recording_master.wav"
local-audio render-project "session.project.json" --output-dir "exports"
local-audio verify "recording_master.wav" --profile "profiles/podcast-stereo.json"
local-audio benchmark --corpus "benchmarks/corpus_manifest.json" --output-dir "benchmarks/results"
```

Return meaningful exit codes. Output structured events/reports with readable human summaries. Avoid different hidden processing defaults between CLI and GUI.

The handoff must include source, dependency locks, model manifests without fabricated hashes, portable executable or a clearly identified packaging failure, scripts, tests, audio fixtures permitted for redistribution, representative output/report artifacts, and a concise README.

At every major milestone update PROJECT_STATE.md with actual commands run, results, changed files, output locations, known defects, and the next bounded task. Do not create multiple contradictory status documents.

The final report must distinguish: implemented and tested; implemented but not tested on target hardware; unavailable due to dependencies/weights/licenses; awaiting human listening; and deferred optional scope. Include exact launch instructions and measured resource use. A copied input, placeholder adapter, or static GUI is not completion.

## 20. Primary-source reference register

These sources informed the research snapshot. Recheck current documentation when pinning implementation details; retain the date and exact revisions actually used. Publication claims describe upstream work, not measurements performed for this plan.

- **S1 — Auphonic features and algorithm overview:** https://auphonic.com/ and https://auphonic.com/help/algorithms/index.html
- **S2 — Auphonic singletrack algorithms:** https://auphonic.com/help/algorithms/singletrack.html
- **S3 — Auphonic multitrack algorithms:** https://auphonic.com/help/algorithms/multitrack.html
- **S4 — Auphonic Studio Voice, July 21, 2026:** https://auphonic.com/blog/2026/07/21/studio-voice-beta/
- **S5 — FastEnhancer official repository and current configuration tables:** https://github.com/aask1357/fastenhancer and https://raw.githubusercontent.com/aask1357/fastenhancer/main/README.md
- **S6 — FastEnhancer release assets:** https://github.com/aask1357/fastenhancer/releases and https://github.com/aask1357/fastenhancer/releases/tag/onnx-48khz-v1
- **S7 — FastEnhancer paper and ONNX inference/delay documentation:** https://arxiv.org/abs/2509.21867 and https://aask1357.github.io/fastenhancer/onnx/
- **S8 — faster-enhancer.c, July 2026 paper:** https://arxiv.org/abs/2607.25350
- **S9 — DeepFilterNet official repository:** https://github.com/Rikorose/DeepFilterNet
- **S10 — ClearerVoice-Studio and 48 kHz enhancement model:** https://github.com/modelscope/ClearerVoice-Studio and https://huggingface.co/alibabasglab/MossFormer2_SE_48K
- **S11 — NVIDIA RE-USE model, terms, runtime and paper:** https://huggingface.co/nvidia/RE-USE and https://arxiv.org/abs/2603.02641
- **S12 — Real-time RE-USE:** https://huggingface.co/nvidia/Real-time_RE-USE and https://arxiv.org/abs/2606.25621
- **S13 — UniPASE official repository and April 2026 paper:** https://github.com/Xiaobin-Rong/unipase and https://arxiv.org/abs/2604.14606
- **S14 — Qwen3-ASR-0.6B official model card:** https://huggingface.co/Qwen/Qwen3-ASR-0.6B
- **S15 — FFmpeg official filter documentation, including loudnorm:** https://ffmpeg.org/ffmpeg-filters.html and https://raw.githubusercontent.com/FFmpeg/FFmpeg/master/doc/filters.texi
- **S16 — Qt for Python deployment:** https://doc.qt.io/qtforpython-6/deployment/index.html
- **S17 — Official Codex AGENTS.md guidance:** https://developers.openai.com/codex/guides/agents-md
- **S18 — Silero VAD official repository:** https://github.com/snakers4/silero-vad

**Evidence boundary:** This document is a researched engineering specification. No model inference, Windows packaging, 16 GB GPU benchmark, or listening comparison was executed while preparing it. Those are explicit implementation gates, not completed results.
