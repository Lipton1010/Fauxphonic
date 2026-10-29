# Capability matrix

This describes 0.2.1; audio processing is unchanged from 0.2.0. `PROJECT_STATE.md` identifies the exact tested artifacts and remaining quality gates. The withdrawn 0.1.0 installer does not represent this table.

| Specification area | Implemented behavior / boundary |
|---|---|
| Native Windows workflow | PySide6 Widgets, worker process, native import/dialogs, file/folder drop, projects, queue and transport |
| Cleanup | Real spectral DSP with delay compensation on explicitly mono Speech; optional high-pass; visible role/layout bypass |
| Speech leveling | Pinned local Silero VAD plus bounded phrase gain; original activity and post-cleanup energy; no speaker identification claim |
| Gain editing | Source-local integer-sample envelopes, manual edit/undo and import of matching measured curves |
| Analysis | Bounded multiresolution peak overview, LUFS/peak, mono VAD speech/nonspeech energy and near-full-scale candidates; approximate noise statistics, no quality score |
| Mastering | Whole-program measurement, global gain and linked 4× limiter; actual final encoded measurements; bounded lossless-source retry |
| Exports | WAV24, FLAC, MP3, AAC/M4A and Opus; decoded presentation frames/layout/rate, finite audio, loudness and true peak checked; explicit metadata |
| Timing | Integer 48 kHz samples, explicit stream/role/offset/gain; container start deltas for selected streams of one file; no silence removal or automatic stretch |
| Long recordings | Fixed blocks and RF64 scratch; packaged two-track 10,807-second output passed with 115,474,432-byte sampled peak worker-tree RSS |
| A/B preview | 10–30 second crop of complete program render, aligned source mix, optional comparison-only matching; first uncached preview renders the full program |
| Cache/recovery | Verified bounded preview cache; recognized corruption quarantined; interrupted jobs restart from originals |
| Music ducking | Explicit Music role, shared original speech activity, attack/release curves; overlap regression |
| Aligned stems | Per-track, pre-master, full shared timeline; not separately normalized; no claim they sum to the mastered mix |
| Speech automix / bleed | Optional conservative relative-level/correlation attenuation, protected tracks, bounded ramps and detection hold; unknown/uncorrelated overlap stays unchanged; not source separation |
| Tone / sibilance / plosives | Optional bounded broad EQ and dynamic high-band attenuation; plosive proof failed voice-protection gate and remains unavailable |
| Model capability UI | Actual installed model/tool status, license/hash/resource evidence, user-selected storage and verified offline model import; unavailable models have explanations |
| Neural enhancement / dereverb | FastEnhancer research inference exists, checkpoint rights/listening unresolved; no cleared desktop enhancement/dereverb backend |
| Packaging | Controlled FFmpeg/libsndfile with repeat-build hashes, actual payload licenses and corresponding source; native portable app and per-user installer; exact acceptance recorded in PROJECT_STATE.md |
| Playback | Native device selection/seek; compressed media and explicit streams are decoded by the worker into temporary PCM WAV, preserving presentation length |
| Region processing | Editable gain envelopes and selected preview region; arbitrary region-specific enhancement chains are not implemented |
| Hardware / listening | CPU evidence on i9-13900K; development GPU has 24 GB; 16 GB compatibility and human listening are unverified |
| Optional packs | Restoration, ASR, reviewed silence editing, captions and video replacement are absent |

No Auphonic equivalence, automatic alignment, neural cleanup, subjective quality approval or complete spoken-word-suite claim is made.
