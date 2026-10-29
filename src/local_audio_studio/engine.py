"""Bounded, source-preserving audio pipeline shared by CLI and native GUI."""
from contextlib import ExitStack
from dataclasses import asdict, replace
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import time
import uuid
from fractions import Fraction

import numpy as np
import soundfile as sf

from . import __version__
from .audio import FLOAT_SCRATCH_ARGS, Runner, binary, decode_source, digest, final_meter, loudness, probe, write_json
from .contracts import Project, RATE
from .exports import FORMATS, LOSSY_FORMATS, encoding_args, output_paths, verification_audio
from .automix import analyze_bleed
from .leveling import analyze_speech_activity, build_leveling, validate_model
from .lifecycle import sweep_render_scratch
from .storage import complete as complete_storage, render_scratch
from .windows_job import reserve_output

BLOCK = 65536


def mix_tracks(paths, tracks, destination, runner, leveler_curves=None, duck_curves=None,
               track_indices=None, total_frames=None, output_channels=None, bleed_curves=None):
    """Mono maps to equal L/R when any input is stereo; no automatic gain normalization."""
    with ExitStack() as stack:
        readers = [stack.enter_context(sf.SoundFile(p)) for p in paths]
        channels = output_channels or max(f.channels for f in readers)
        frames = total_frames or max(len(f) + t.offset_samples for f, t in zip(readers, tracks))
        manual_curves = [(np.fromiter((p.sample for p in t.gain_envelope), dtype=np.int64),
                          np.fromiter((p.gain_db for p in t.gain_envelope), dtype=np.float32))
                         if t.gain_envelope else None for t in tracks]
        output = stack.enter_context(sf.SoundFile(destination, "w", samplerate=RATE,
                                                   channels=channels, format="RF64", subtype="FLOAT"))
        for start in range(0, frames, BLOCK):
            runner.check()
            count = min(BLOCK, frames - start)
            block = np.zeros((count, channels), np.float32)
            for local_index, (source, track) in enumerate(zip(readers, tracks)):
                index = track_indices[local_index] if track_indices is not None else local_index
                left = max(start, track.offset_samples)
                right = min(start + count, track.offset_samples + len(source))
                if right > left:
                    source.seek(left - track.offset_samples)
                    audio = source.read(right-left, dtype="float32", always_2d=True)
                    if not np.isfinite(audio).all():
                        raise ValueError("Nonfinite decoded audio; export stopped.")
                    gain = track.gain_db
                    local = np.arange(left - track.offset_samples, right - track.offset_samples)
                    for curves in (leveler_curves, duck_curves, bleed_curves):
                        if curves and index in curves:
                            positions, values = curves[index]
                            gain = gain + np.interp(local, positions, values)
                    if manual_curves[local_index]:
                        positions, values = manual_curves[local_index]
                        gain = gain + np.interp(local, positions, values)
                    if isinstance(gain, np.ndarray):
                        block[left-start:right-start] += audio * np.power(10, np.asarray(gain)[:, None] / 20)
                    else:
                        block[left-start:right-start] += audio * (10 ** (gain / 20))
            if not np.isfinite(block).all():
                raise ValueError("Mix overflow.")
            output.write(block)
            runner.sample_memory()
            runner.emit({"stage": "mixing", "progress": (start+count)/frames})
    return frames, channels


def _duck_curves(tracks, activity_regions, lengths, preset):
    """Create linked, source-local music attenuation curves from speech activity."""
    if preset.mode == "bypass" or not preset.music_ducking_enabled or preset.music_duck_db == 0:
        return {}, {"tracks": [], "speech_regions_project_samples": [], "gain_curve_definition": None}
    release = round(preset.duck_release_ms * RATE / 1000)
    intervals = sorted((track.offset_samples + start, track.offset_samples + end)
                       for index, regions in activity_regions.items()
                       for track in [tracks[index]] for start, end in regions)
    merged = []
    for start, end in intervals:
        # Keep the next speech phrase from entering an earlier phrase's release ramp.
        if merged and start <= merged[-1][1] + release:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    curves, music_tracks = {}, []
    attack = round(preset.duck_attack_ms * RATE / 1000)
    for index, track in enumerate(tracks):
        if track.role != "music":
            continue
        length = lengths[index]
        points = [(0, 0.0), (length, 0.0)]
        for global_start, global_end in merged:
            start = max(0, global_start - track.offset_samples)
            end = min(length, global_end - track.offset_samples)
            if end <= 0 or start >= length or start >= end:
                continue
            points.extend([(max(0, start - attack), 0.0), (start, -preset.music_duck_db),
                           (end, -preset.music_duck_db), (min(length, end + release), 0.0)])
        points.sort(key=lambda pair: pair[0])
        compact = []
        for point in points:
            if compact and point[0] == compact[-1][0]:
                compact[-1] = point
            else:
                compact.append(point)
        if any(value < 0 for _, value in compact):
            curves[index] = (np.asarray([p[0] for p in compact], dtype=np.int64),
                             np.asarray([p[1] for p in compact], dtype=np.float32))
            music_tracks.append({"track_index": index, "offset_samples": track.offset_samples,
                                 "source_frames": length,
                                 "gain_curve_source": "shared project speech regions + configured attack/release"})
    return curves, {"tracks": music_tracks,
                    "speech_regions_project_samples": [[int(start), int(end)] for start, end in merged],
                    "gain_curve_definition": "0 dB to configured attenuation over attack; hold while speech is active; linear release to 0 dB"}


def effective_timeline_tracks(tracks, sources):
    """Apply container stream start-time deltas without mutating saved project tracks."""
    result = [replace(track) for track in tracks]
    starts_by_file = {}
    for track, source in zip(tracks, sources):
        start = source["probe"]["stream"].get("start_time")
        starts_by_file.setdefault(str(Path(track.path).resolve()), []).append(
            (source["probe"]["stream"]["index"], start))
    for track, source in zip(result, sources):
        starts = starts_by_file[str(Path(track.path).resolve())]
        start = source["probe"]["stream"].get("start_time")
        distinct_streams = {index for index, _ in starts}
        if len(distinct_streams) > 1 and any(value is None for _, value in starts):
            raise ValueError("Selected streams from the same file need explicit start times to preserve sync.")
        if len(distinct_streams) > 1 and start is not None:
            starts_exact = [Fraction(str(value)) for _, value in starts]
            track.offset_samples += round((Fraction(str(start)) - min(starts_exact)) * RATE)
    return result


def inspect_samples(path, runner):
    frames = 0
    peak = 0.0
    with sf.SoundFile(path) as source:
        for block in source.blocks(blocksize=BLOCK, dtype="float32", always_2d=True):
            runner.check()
            if not np.isfinite(block).all():
                raise ValueError("Export contains nonfinite samples.")
            frames += len(block)
            peak = max(peak, float(np.max(np.abs(block))))
        return {"frames": frames, "channels": source.channels, "sample_rate": source.samplerate,
                "sample_peak": peak}


def verify(path, preset, runner=None, expected_frames=None, expected_channels=None):
    runner = runner or Runner()
    with verification_audio(path, runner, Path(path).parent if expected_frames is not None else None) as (decoded, timing):
        result = _verify_pcm(decoded, preset, runner, expected_frames, expected_channels)
    result["encoding_timing"] = timing
    return result


def _verify_pcm(path, preset, runner, expected_frames, expected_channels):
    runner = runner or Runner()
    samples = inspect_samples(path, runner)
    measurements = final_meter(path, runner, "verifying", samples["frames"] / RATE)
    integrated = -math.inf if measurements["input_i"] is None else float(measurements["input_i"])
    true_peak = -math.inf if measurements["input_tp"] is None else float(measurements["input_tp"])
    errors = []
    if samples["sample_rate"] != RATE:
        errors.append("Export sample rate changed.")
    if expected_frames is not None and samples["frames"] != expected_frames:
        errors.append(f"Expected {expected_frames} frames, got {samples['frames']}.")
    if expected_channels is not None and samples["channels"] != expected_channels:
        errors.append("Export channel count changed.")
    exception = None
    sample_peak_db = 20 * math.log10(samples["sample_peak"]) if samples["sample_peak"] > 0 else -math.inf
    if math.isfinite(sample_peak_db) and math.isfinite(true_peak) and sample_peak_db > true_peak + 0.11:
        errors.append(f"True-peak meter inconsistency: measured dBTP {true_peak:.2f} is below sample peak {sample_peak_db:.2f} dBFS.")
    if preset.mode != "bypass":
        if not math.isfinite(integrated):
            exception = "No measurable integrated loudness (silence or below the gating threshold)."
        elif samples["frames"] < 3 * RATE:
            exception = "Clip shorter than 3 seconds; integrated loudness is not a release pass."
        elif abs(integrated - preset.target_lufs) > 0.5:
            errors.append(f"Loudness {integrated:.2f} LUFS is outside target ±0.5 LU.")
        if math.isfinite(true_peak) and true_peak > preset.true_peak + 0.1:
            errors.append(f"True peak {true_peak:.2f} dBTP exceeds ceiling +0.1 dB.")
    samples["sample_peak_dbfs"] = sample_peak_db if math.isfinite(sample_peak_db) else None
    return {"status": "failed" if errors else "exception" if exception else "passed",
            "errors": errors, "exception": exception, "samples": samples,
            "measurements": measurements, "loudness_constraints_applied": preset.mode != "bypass"}


def _render_linked_master(source, destination, gain_db, true_peak_db, runner, duration):
    """Apply measured global gain, then a shared-channel 4x oversampled limiter."""
    limit = 10 ** (true_peak_db / 20)
    filters = (f"volume={gain_db:.8f}dB,aresample=192000,"
               f"alimiter=limit={limit:.10f}:attack=5:release=10:level=disabled:latency=true,"
               f"aresample={RATE}")
    return runner.run(["-i", source, "-af", filters, "-ar", RATE,
                       *FLOAT_SCRATCH_ARGS, destination], "mastering", duration)


def _noise_prefix(path, regions, destination, runner):
    """Find a steady, broadband half-second outside padded speech regions.

    The copied profile precedes the scratch stream only; it is removed together
    with filter latency. Originals and the project timeline remain untouched.
    """
    size, margin = RATE // 2, RATE // 10
    best = None
    with sf.SoundFile(path) as source:
        left = 0
        for start, end in [*regions, (len(source), len(source))]:
            right = min(len(source), start - margin)
            if right - left >= size:
                for position in sorted({left, (left + right - size)//2, right - size}):
                    runner.check()
                    source.seek(position)
                    sample = source.read(size, dtype="float64")
                    powers = np.mean(sample.reshape(10, -1)**2, axis=1)
                    power = float(np.mean(powers))
                    if power < 1e-12 or powers.max() > max(powers.min(), 1e-20)*2:
                        continue
                    spectrum = np.abs(np.fft.rfft(sample * np.hanning(size)))**2
                    flatness = np.exp(np.mean(np.log(spectrum[1:] + 1e-30))) / (np.mean(spectrum[1:]) + 1e-30)
                    if flatness < 0.02:  # Avoid profiling tones or strongly harmonic voice as noise.
                        continue
                    if best is None or power < best[0]:
                        best = (power, position, sample)
            left = max(left, end + margin)
    if best is None:
        return None
    power, position, sample = best
    sf.write(destination, np.tile(sample, 2), RATE, subtype="FLOAT", format="RF64")
    return {"start_sample": position, "duration_samples": size,
            "rms_dbfs": 10*math.log10(power), "prefix_samples": size*2,
            "method": "stationary broadband sample outside padded Silero speech regions"}


def render(project: Project, output, runner=None, storage_job=None):
    project.validate()
    runner = runner or Runner()
    started = time.perf_counter()
    output = Path(output).resolve()
    if output.suffix.lower() not in FORMATS:
        raise ValueError("Choose WAV, FLAC, MP3, AAC/M4A or Opus output.")
    source_paths = [Path(t.path).resolve() for t in project.tracks]
    if output in source_paths:
        raise ValueError("Output must not replace a source recording.")
    _, report_path, *stem_paths = output_paths(output, project)
    stem_indices = list(range(len(project.tracks))) if project.export_stems else []
    stem_suffix = ".wav" if output.suffix.lower() in LOSSY_FORMATS else output.suffix.lower()
    stem_outputs = dict(zip(stem_indices, stem_paths))
    all_outputs = [output, *stem_outputs.values()]
    if any(path in source_paths for path in all_outputs) or report_path in source_paths:
        raise ValueError("Output and stem paths must not replace source recordings.")
    if any(path.exists() for path in [*all_outputs, report_path]):
        raise FileExistsError("Output or report already exists. Choose a new output name.")
    output.parent.mkdir(parents=True, exist_ok=True)
    sweep_render_scratch(output.parent)
    locks = [path.with_name(path.name + ".render-lock") for path in all_outputs]
    if any(lock in source_paths for lock in locks):
        raise ValueError("Output sidecars must not replace source recordings.")
    reservations = []
    try:
        for lock in locks:
            reservations.append(reserve_output(lock))
        runner.emit({"stage": "analyzing", "detail": "Hashing and probing originals", "progress": 0})
        sources = []
        for track, path in zip(project.tracks, source_paths):
            runner.check()
            sources.append({"path": str(path), "sha256": digest(path), "probe": probe(path, track.stream)})
        effective_tracks = effective_timeline_tracks(project.tracks, sources)
        duration = max(s["probe"]["duration"] + t.offset_samples / RATE
                       for s, t in zip(sources, effective_tracks))
        if duration <= 0 or not math.isfinite(duration):
            raise ValueError("Could not determine a finite positive source duration.")
        # Decode + optional DSP + mix + float master + final PCM, with a safety margin.
        channels = max(s["probe"]["stream"]["channels"] for s in sources)
        stem_scratch = (len(project.tracks) * duration * RATE * channels * 4
                        + duration * RATE * channels * 8) if project.export_stems else 0
        scratch_bytes = int(sum(s["probe"]["duration"] * RATE * 8 * 2 for s in sources)
                            + duration * RATE * 8 * 4 + stem_scratch + 100 * 1024**2)
        if shutil.disk_usage(output.parent).free < scratch_bytes:
            raise OSError(f"Insufficient scratch space. Need approximately {scratch_bytes / 1024**3:.2f} GiB.")
        model_hash = None
        if (project.preset.mode != "bypass" and (project.preset.leveler_enabled or project.preset.music_ducking_enabled
                or project.preset.bleed_reduction_enabled or project.preset.mode == "dsp-cleanup")
                and any(track.role == "speech" for track in project.tracks)):
            model_hash = validate_model()
        key_data = {"project": project.to_dict(), "sources": [s["sha256"] for s in sources],
                    "engine": __version__, "ffmpeg": digest(binary("ffmpeg")),
                    "vad_model_sha256": model_hash}
        render_key = hashlib.sha256(json.dumps(key_data, sort_keys=True).encode()).hexdigest()
        stages = []
        with render_scratch(output.parent) as temp:
            work = Path(temp)
            decoded = []
            raw_paths = []
            raw_lengths = []
            for index, (track, source) in enumerate(zip(project.tracks, sources)):
                runner.check()
                raw = work / f"source-{index}.wav"
                source["decode_timing"] = decode_source(track.path, source["probe"], raw, runner)
                with sf.SoundFile(raw) as raw_file:
                    raw_lengths.append(len(raw_file))
                raw_paths.append(raw)
            activity_regions = {}
            activity_info = None
            if model_hash:
                activity_regions, activity_info = analyze_speech_activity(effective_tracks, raw_paths, work, runner)
            for index, (track, source, raw) in enumerate(zip(project.tracks, sources, raw_paths)):
                target = work / f"track-{index}.wav"
                prefix = None
                filters = []
                stage = {"track": index, "role": track.role, "cleanup": "disabled",
                         "highpass": "disabled", "deesser": {"status": "disabled"},
                         "broad_eq": {"status": "disabled"}}
                if project.preset.mode == "bypass":
                    if project.preset.highpass_hz:
                        stage["highpass"] = "bypassed: project mode is bypass"
                    if project.preset.deesser_enabled:
                        stage["deesser"] = {"status": "bypassed", "reason": "project mode is bypass"}
                    if project.preset.broad_eq_enabled:
                        stage["broad_eq"] = {"status": "bypassed", "reason": "project mode is bypass"}
                if project.preset.mode != "bypass" and project.preset.highpass_hz:
                    filters.append(f"highpass=f={project.preset.highpass_hz}")
                    stage["highpass"] = {"status": "applied", "filter": filters[-1]}
                if project.preset.mode == "dsp-cleanup":
                    if project.preset.noise_reduction_db == 0:
                        stage["cleanup"] = "bypassed: noise reduction set to 0 dB"
                    elif track.role != "speech" or source["probe"]["stream"]["channels"] != 1:
                        stage["cleanup"] = "bypassed: requires explicitly assigned mono speech"
                    else:
                        prefix = work / f"noise-profile-{index}.wav"
                        profile = _noise_prefix(raw, activity_regions.get(index, []), prefix, runner)
                        if profile:
                            noise_floor = max(-80, min(-20, profile["rms_dbfs"]))
                            profile["filter_noise_floor_db"] = noise_floor
                            filters.extend(["apad=pad_len=1200",
                                "asendcmd=c='0 afftdn sn start; 0.5 afftdn sn stop'",
                                f"afftdn=nr={project.preset.noise_reduction_db}:nf={noise_floor:.4f}:tn=0",
                                f"atrim=start_sample={profile['prefix_samples'] + 1200}", "asetpts=PTS-STARTPTS"])
                            stage["cleanup"] = "FFmpeg afftdn with measured noise profile (spectral DSP, not neural)"
                            stage["noise_profile"] = profile
                        else:
                            prefix = None
                            stage["cleanup"] = "bypassed: no steady broadband noise-only sample could be identified safely"
                if project.preset.mode != "bypass" and project.preset.deesser_enabled:
                    if project.preset.deesser_range == 0:
                        stage["deesser"] = {"status": "bypassed", "reason": "range set to 0"}
                    elif track.role != "speech" or source["probe"]["stream"]["channels"] != 1:
                        stage["deesser"] = {"status": "bypassed",
                                             "reason": "requires explicitly assigned mono Speech"}
                    else:
                        threshold = max(0.000001, inspect_samples(raw, runner)["sample_peak"] * 0.3)
                        deesser = (
                            f"adynamicequalizer=threshold={threshold:.9f}:dfrequency=5200:dqfactor=0.8:"
                            "tfrequency=6500:tqfactor=1.2:attack=5:release=80:ratio=1:makeup=0:"
                            f"range={project.preset.deesser_range:g}:mode=cutabove:"
                            "dftype=highpass:tftype=bell:auto=disabled:precision=double"
                        )
                        filters.append(deesser)
                        stage["deesser"] = {"status": "applied", "filter": deesser,
                                             "detector_threshold": threshold,
                                             "range_setting": project.preset.deesser_range}
                if project.preset.mode != "bypass" and project.preset.broad_eq_enabled:
                    if project.preset.broad_eq_gain_db == 0:
                        stage["broad_eq"] = {"status": "bypassed", "reason": "gain set to 0 dB"}
                    else:
                        broad_eq = (f"equalizer=f={project.preset.broad_eq_frequency_hz}:t=q:w=1:"
                                    f"g={project.preset.broad_eq_gain_db:+g}:precision=f64")
                        filters.append(broad_eq)
                        stage["broad_eq"] = {"status": "applied", "filter": broad_eq,
                                              "frequency_hz": project.preset.broad_eq_frequency_hz,
                                              "gain_db": project.preset.broad_eq_gain_db,
                                              "q": 1}
                if filters:
                    args = ["-i", raw, "-map", "0:a:0", "-af", ",".join(filters),
                            "-ar", str(RATE), *FLOAT_SCRATCH_ARGS, target]
                    if prefix:
                        args = ["-i", prefix, "-i", raw, "-filter_complex",
                            "[0:a:0][1:a:0]concat=n=2:v=0:a=1," + ",".join(filters),
                            "-ar", RATE, *FLOAT_SCRATCH_ARGS, target]
                    runner.run(args, "processing", source["probe"]["duration"])
                    decoded.append(target)
                else:
                    decoded.append(raw)
                stages.append(stage)
            leveler_curves, leveling = ({}, {"enabled": False, "reason":
                "Bypass mode disables automatic processing." if project.preset.mode == "bypass" else "Leveler disabled."})
            if project.preset.mode != "bypass" and project.preset.leveler_enabled:
                if any(track.role == "speech" for track in effective_tracks):
                    leveler_curves, leveling = build_leveling(
                        effective_tracks, raw_paths, decoded, project.preset, work, runner,
                        activity_regions, activity_info)
                else:
                    leveling = {"enabled": False, "reason": "No tracks are assigned the Speech role."}
            bleed_curves, bleed = {}, {"enabled": False, "tracks": [], "reason":
                "Bypass mode disables automatic processing." if project.preset.mode == "bypass" else "Bleed attenuation disabled."}
            if project.preset.mode != "bypass" and project.preset.bleed_reduction_enabled:
                bleed_started = time.perf_counter()
                runner.emit({"stage": "analyzing", "detail": "Checking correlated speech-bleed candidates"})
                bleed_curves, bleed = analyze_bleed(effective_tracks, raw_paths, activity_regions,
                    max_attenuation_db=project.preset.bleed_max_attenuation_db,
                    attack_ms=project.preset.bleed_attack_ms, hold_ms=project.preset.bleed_hold_ms,
                    release_ms=project.preset.bleed_release_ms, runner=runner)
                bleed["enabled"] = bool(bleed_curves)
                bleed["analysis_wall_seconds"] = time.perf_counter() - bleed_started
            mix = work / "mix.wav"
            lengths = [sf.info(path).frames for path in decoded]
            duck_curves, ducking = _duck_curves(effective_tracks, activity_regions, lengths, project.preset)
            frames, channels = mix_tracks(decoded, effective_tracks, mix, runner, leveler_curves,
                                          duck_curves, bleed_curves=bleed_curves)
            expected_frames = max(n + track.offset_samples for n, track in zip(raw_lengths, effective_tracks))
            if frames != expected_frames:
                raise RuntimeError(f"Processed timeline length changed: expected {expected_frames}, got {frames}.")
            duration = frames / RATE
            encoding = encoding_args(output.suffix, channels)
            metadata = [value for name, text in project.metadata.items() for value in ("-metadata", f"{name}={text}")]
            stem_candidates = {}
            for index, stem_path in stem_outputs.items():
                track = effective_tracks[index]
                role_mix = work / f"track-{index + 1:02d}-stem.wav"
                role_frames, role_channels = mix_tracks(
                    [decoded[index]], [track], role_mix, runner,
                    {index: leveler_curves[index]} if index in leveler_curves else {},
                    {index: duck_curves[index]} if index in duck_curves else {},
                    [index], frames, channels, bleed_curves=bleed_curves)
                if (role_frames, role_channels) != (frames, channels):
                    raise RuntimeError(f"Track {index + 1} stem does not match the project timeline layout.")
                if inspect_samples(role_mix, runner)["sample_peak"] > 1:
                    raise ValueError(f"The pre-master track {index + 1} stem exceeds the PCM range; lower track gains before exporting stems.")
                stem_candidate = work / f"candidate-track-{index + 1:02d}{stem_path.suffix.lower()}"
                runner.run(["-i", role_mix, "-af", "aresample=dither_method=triangular", "-ar", RATE,
                            *encoding_args(stem_suffix, channels), stem_candidate], "encoding", duration)
                stem_check = inspect_samples(stem_candidate, runner)
                if (stem_check["frames"], stem_check["channels"], stem_check["sample_rate"]) != (frames, channels, RATE):
                    raise RuntimeError(f"Track {index + 1} stem failed aligned timeline verification.")
                stem_candidates[index] = stem_candidate
            measured = loudness(mix, runner, project.preset.target_lufs,
                                project.preset.true_peak, duration=duration)
            master = mix
            actual_mode = "bypass"
            measured_global_gain_db = None
            limiter_ceiling_db = None
            finite = all(math.isfinite(float(measured[k])) for k in
                         ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset"))
            if (project.preset.mode != "bypass" and finite
                    and float(measured["input_i"]) >= project.preset.target_lufs - 20):
                master = work / "master.wav"
                measured_global_gain_db = project.preset.target_lufs - float(measured["input_i"])
                limiter_ceiling_db = project.preset.true_peak - (0.5 if output.suffix.lower() in LOSSY_FORMATS else 0.25)
                _render_linked_master(mix, master, measured_global_gain_db,
                                      limiter_ceiling_db, runner, duration)
                actual_mode = "measured-global-gain+linked-true-peak-limiter"
            elif project.preset.mode != "bypass":
                actual_mode = "bypass-gain-cap" if finite else "bypass-unmeasurable-loudness"
            if project.preset.mode == "bypass" and inspect_samples(master, runner)["sample_peak"] > 1:
                raise ValueError("Bypass mix exceeds the integer PCM range. Lower track gains or enable mastering.")
            candidate = work / ("candidate" + output.suffix.lower())
            correction_attempts = 0
            correction_method = None
            corrective_true_peak = None
            corrective_mode = None
            correction_history = []
            runner.run(["-i", master, "-af", "aresample=dither_method=triangular", "-ar", RATE,
                        *encoding, *metadata, candidate], "encoding", duration)
            verification = verify(candidate, project.preset, runner, frames, channels)
            if actual_mode.startswith("bypass-") and verification["errors"] and all(
                    "Loudness" in error for error in verification["errors"]):
                verification["status"] = "exception"
                verification["exception"] = "Input is silent or would require more than 20 dB gain; mastering was bypassed."
                verification["errors"] = []
            previous_gain = previous_loudness = None
            for attempt in range(1, 7):
                if not (verification["status"] == "failed" and project.preset.mode != "bypass"
                    and measured_global_gain_db is not None and verification["errors"] and all(
                        error.startswith(("Loudness", "True peak", "True-peak meter"))
                        for error in verification["errors"])):
                    break
                # Retry from the original float mix, never from quantized output.
                retry_meter = verification["measurements"]
                retry_loudness = retry_meter["input_i"]
                if retry_loudness is not None and math.isfinite(retry_loudness):
                    gain_adjustment = project.preset.target_lufs - retry_loudness
                    # Limiting makes output LUFS respond less than 1:1 to input gain.
                    # Use the measured response only while the ceiling is unchanged.
                    if previous_gain is not None and abs(measured_global_gain_db - previous_gain) > 0.05:
                        slope = (retry_loudness - previous_loudness) / (measured_global_gain_db - previous_gain)
                        gain_adjustment /= max(0.2, min(1.0, slope))
                    gain_adjustment = max(-3.0, min(3.0, gain_adjustment))
                    corrected_gain_db = measured_global_gain_db + gain_adjustment
                    if abs(corrected_gain_db) <= 20:
                        encoded_peak = verification["measurements"]["input_tp"]
                        peak_error = max(0, encoded_peak - project.preset.true_peak) if encoded_peak is not None else 0
                        corrective_true_peak = limiter_ceiling_db - (peak_error + 0.15 if peak_error > 0 else 0)
                        previous_gain, previous_loudness = (measured_global_gain_db, retry_loudness) if peak_error == 0 else (None, None)
                        correction_history.append({"attempt": attempt, "measured_lufs": retry_loudness,
                            "measured_true_peak_dbtp": encoded_peak, "gain_db": corrected_gain_db,
                            "limiter_ceiling_dbtp": corrective_true_peak})
                        corrective_mode = "measured-global-gain+linked-true-peak-limiter"
                        candidate.unlink()
                        if master != mix:
                            master.unlink()
                        corrective_master = work / "corrective-master.wav"
                        _render_linked_master(mix, corrective_master, corrected_gain_db,
                                              corrective_true_peak, runner, duration)
                        candidate = work / ("candidate-corrected" + output.suffix.lower())
                        runner.run(["-i", corrective_master, "-af", "aresample=dither_method=triangular",
                                    "-ar", RATE, *encoding, *metadata, candidate], "encoding", duration)
                        master = corrective_master
                        correction_attempts = attempt
                        correction_method = ("bounded linked-limiter retries from the original lossless mix; "
                                             f"global gain adjustment {gain_adjustment:+.2f} dB")
                        measured_global_gain_db = corrected_gain_db
                        limiter_ceiling_db = corrective_true_peak
                        actual_mode = corrective_mode
                        verification = verify(candidate, project.preset, runner, frames, channels)
                    else:
                        break
                else:
                    break
            for source in sources:
                runner.check()
                if digest(source["path"]) != source["sha256"]:
                    raise RuntimeError("Source changed during processing; output was not promoted.")
            elapsed = time.perf_counter() - started
            used_models = ([{"name": "Silero VAD v6.2.1", "sha256": leveling["model_sha256"],
                            "license": leveling["model_license"], "runtime": leveling["runtime"],
                            "provider": leveling["provider"],
                            "use": "speech activity detection only"}]
                           if leveling.get("model_sha256") else
                           [{"name": "Silero VAD v6.2.1", "sha256": activity_info["model_sha256"],
                             "license": activity_info["model_license"], "runtime": activity_info["runtime"],
                             "provider": activity_info["provider"], "use": "speech activity detection only"}]
                           if activity_info else [])
            stem_report = [{"track_index": index, "role": project.tracks[index].role,
                            "source": str(source_paths[index]), "path": str(stem_outputs[index]),
                            "sha256": digest(path), "mastering": "pre-master; full timeline"}
                           for index, path in stem_candidates.items()]
            leveling_event = {"enabled": bool(leveling.get("enabled")),
                              "reason": leveling.get("reason"), "method": leveling.get("method"),
                              "phrase_count": sum(len(item.get("regions", []))
                                                  for item in leveling.get("tracks", [])),
                              "has_curves": bool(leveling.get("tracks")),
                              "model_sha256": leveling.get("model_sha256")}
            duck_reason = None if duck_curves else (
                "Bypass mode disables automatic processing." if project.preset.mode == "bypass" else
                "Music ducking is disabled." if not project.preset.music_ducking_enabled
                else "No confident speech-to-music overlap.")
            ducking_event = {"enabled": bool(duck_curves),
                             "reason": duck_reason,
                             "max_attenuation_db": project.preset.music_duck_db,
                             "track_count": len(ducking["tracks"])}
            bleed_event = {"enabled": bool(bleed_curves), "reason": bleed.get("reason"),
                           "track_count": len(bleed_curves), "candidate_events":
                           sum(len(track.get("events", [])) for track in bleed["tracks"])}
            report = {"schema": 1, "engine_version": __version__, "render_id": render_key,
                      "project": project.to_dict(), "sources": sources, "processing": stages,
                      "leveling": leveling, "speech_activity": activity_info, "bleed_reduction": bleed,
                      "music_ducking": {"enabled": bool(duck_curves),
                          "max_attenuation_db": project.preset.music_duck_db,
                          "attack_ms": project.preset.duck_attack_ms,
                          "release_ms": project.preset.duck_release_ms,
                          "tracks": ducking["tracks"],
                          "speech_regions_project_samples": ducking["speech_regions_project_samples"],
                          "gain_curve_definition": ducking["gain_curve_definition"],
                          "reason": duck_reason},
                      "stems": stem_report,
                      "neural_models": used_models, "mastering_mode": actual_mode, "analysis": measured,
                      "verification": verification, "correction_attempts": correction_attempts,
                      "correction_method": correction_method,
                      "corrective_true_peak_target": corrective_true_peak,
                      "corrective_mastering_mode": corrective_mode,
                      "measured_global_gain_db": measured_global_gain_db,
                      "correction_history": correction_history,
                      "limiter_ceiling_db": limiter_ceiling_db,
                      "output": str(output), "sha256": digest(candidate),
                      "delivery": {"format": output.suffix.lower(), "encoder_arguments": encoding,
                                   "metadata": project.metadata, "stems_format": stem_suffix},
                      "ffmpeg_sha256": key_data["ffmpeg"], "wall_seconds": elapsed,
                      "duration_seconds": duration, "real_time_factor": elapsed / duration,
                      "whole_job_rtf_definition": "wall seconds / final timeline duration",
                      "peak_process_tree_rss_bytes": runner.peak_tree_rss,
                      "memory_sample_interval_seconds": 0.08, "gpu": "CPU-only; no CUDA runtime loaded",
                      "listening_evaluation": "not performed", "estimated_scratch_bytes": scratch_bytes,
                      "timeline": [{"source": t.path, "offset_samples": t.offset_samples,
                                    "project_rate": RATE, "source_start_time": s["probe"]["stream"].get("start_time")}
                                   for t, s in zip(effective_tracks, sources)]}
            if verification["status"] == "failed":
                pending_report = work / "failure-report.json"
                write_json(pending_report, report)
                failure_path = output.with_suffix(output.suffix + f".failed-{uuid.uuid4().hex}.report.json")
                os.rename(pending_report, failure_path)
                detail = "Export not promoted: " + " ".join(verification["errors"]) + f" Diagnostic report: {failure_path}"
                runner.emit({"stage": "failed", "detail": detail, "report": str(failure_path), "verification": verification})
                raise ValueError(detail)
            runner.check()
            # Promote the output set with rollback if any member collides at promotion time.
            pending_report = work / "report.json"
            write_json(pending_report, report)
            promoted = []
            try:
                for index, path in stem_candidates.items():
                    os.rename(path, stem_outputs[index])
                    promoted.append(stem_outputs[index])
                os.rename(candidate, output)
                promoted.append(output)
                os.rename(pending_report, report_path)
            except BaseException:
                for path in promoted:
                    path.unlink(missing_ok=True)
                raise
            try:
                complete_storage(work, [*all_outputs, report_path], queue_job=storage_job)
            except (OSError, ValueError) as exc:
                # Verified finals are already promoted. Preserve scratch rather than
                # misreporting a successful audio export as failed on receipt errors.
                runner.emit({"stage": "finalizing", "detail": f"Scratch preserved; cleanup receipt unavailable: {exc}"})
        runner.emit({"stage": "complete", "progress": 1, "output": str(output),
                     "report": str(report_path), "status": verification["status"],
                     "verification": verification,
                     "mastering_mode": actual_mode, "processing": stages,
                     "stems": stem_report, "music_ducking": ducking_event, "bleed_reduction": bleed_event,
                     "leveling": leveling_event})
        return report
    finally:
        for reservation in reservations:
            reservation.close()
