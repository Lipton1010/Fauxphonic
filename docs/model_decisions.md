# Model decisions — 2026-09-25

## FastEnhancer Phase 0 proof

Official source revision: f85223bd546b27f39dc0744e0310dcd246f750a4. Release: onnx-48khz-v1. Actual downloaded SHA-256 digests match the GitHub release digests; see models/registry.json and evidence/model-probe.json. CPU ONNX Runtime 1.30.0, float32, one intra/inter-op thread, sequential execution. Medium takes [1,320] wav_in plus two [1,704] waveform caches and four [1,72,72] recurrent caches. Small takes [1,512], two [1,512] caches and three [1,48,48] caches. No guessed tensor names.

The reviewed upstream wav2wav inference algorithm initializes all caches once, passes each returned cache to the next hop, right-pads the end, and compensates n_fft-hop exactly once (704 Medium / 512 Small). scripts/model_probe.py reproduces this numerical reference and compares disk streaming at 4,093 and 65,536 frames per read. Both models produced exactly 576,137 finite samples and zero maximum difference versus the reference loop. A synthetic, project-generated 12.002854-second clip avoids third-party recording rights. It contains tones, noise, and endpoint impulses, not actual speech. This does not establish that first/last words survive.

Medium measured 0.202–0.204 RTF including session loading, Small 0.070–0.071. Process RSS was sampled at disk-block boundaries; approximately 82–86 MiB, including arrays used by the short reference test. These are local Windows CPU measurements, not CUDA tests or perceptual scores. Peak transients between samples may be missed. See the machine's doctor evidence for hardware.

The repository license is MIT (copyright AHN Sung Hwan 2025); its full text is preserved. The release has no explicit separate checkpoint rights statement, and model metadata does not resolve it. We do not infer commercial checkpoint permission from the code badge. Weight redistribution and desktop promotion remain blocked pending rights clarification and representative speech evaluation. Research artifacts stay in ignored .cache/upstream; nothing is downloaded at normal app startup.

The Medium spec2spec artifact was downloaded and hash-checked but not executed. No wav2wav/spec2spec parity, CUDA behavior, human listening, long-form neural stability, or stereo speech enhancement is claimed. Those are separate Phase 2 gates.

## Silero VAD verification — 2026-09-26

The official `v6.2.1` tag resolves to commit `7e30209a3e901f9842f81b225f3e93d8199902b1`. Its repository includes the ONNX artifact and MIT license. Pinned upstream: https://github.com/snakers4/silero-vad/tree/7e30209a3e901f9842f81b225f3e93d8199902b1 . Downloaded model SHA-256: `1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3`.

Local ONNX Runtime 1.30.0 CPU execution confirmed the actual inputs `input`, `state`, and scalar int64 `sr`, and outputs `output` and `stateN`. The reviewed wrapper uses 512 samples at 16 kHz, 64 prior context samples, and persistent float32 recurrent state shaped `(2, batch, 128)`. A zero-input call returned finite probability 0.0016697943 and correctly shaped state. `evidence/silero-artifact-verification.json` records the model, reference-code and license hashes.

The application integrates this VAD for speech-aware phrase leveling, optional music ducking and conservative bleed analysis. Analysis uses a separate 16 kHz stream; processing and timeline positions remain at 48 kHz. Known-text, project-generated Microsoft SAPI speech is documented in `tests/fixtures`. The controlled leveler regression reduced a 12.031 dB utterance difference to 0.045 dB without increasing measured inter-phrase noise; current tests check the pre-master stem so whole-program mastering is not confused with local leveling. This is engineering evidence, not human listening approval. The packaged processing core rendered two tracks to 10,807 seconds in 493.37 seconds with sampled peak worker-tree RSS of 115,474,432 bytes (`evidence/speech-longform.json`). That evidence records the precise worker hash; PROJECT_STATE.md distinguishes the later playback-only build. Silero detects speech activity: it does not enhance, denoise, identify speakers, or repair speech.

## Other enhancement candidates

DeepFilterNet3 is not integrated; exact version, delay, binary interface, checkpoint rights, and Windows proof remain to be checked before implementation. ClearerVoice/MossFormer2, RE-USE, UniPASE, transcription, and optimized int8 runtimes are absent, with no placeholder adapters. No WSL, PyTorch, Mamba, CUDA, drivers, or restricted-license model terms were installed/accepted. The supplied specification's research claims about these candidates are not treated as locally verified facts.

Recheck on 2026-09-26: DeepFilterNet's [official README](https://github.com/Rikorose/DeepFilterNet/blob/main/README.md) explicitly licenses **code** as MIT/Apache-2.0 and documents a 48 kHz WAV CLI with an explicit delay-compensation option. The upstream [checkpoint-license question #697](https://github.com/Rikorose/DeepFilterNet/issues/697) remains open; no affirmative checkpoint permission was established by this check. Consequently it is not silently substituted for the verified DSP route or bundled as a cleared model. Windows support described upstream is not a local Windows inference test.

The working desktop offers real FFmpeg spectral DSP and master-only processing. It never labels that route neural, dereverberation, reconstructive repair, or Auphonic-equivalent.
