"""Bounded Silero activity analysis and linked phrase gain envelopes."""
import math
from pathlib import Path
import time

import numpy as np
import soundfile as sf

from .audio import FLOAT_SCRATCH_ARGS, ROOT, Runner, digest
from .contracts import RATE
from .model_store import MODEL_SHA256, model_path

VAD_RATE = 16000
VAD_WINDOW = 512
VAD_TO_PROJECT = RATE // VAD_RATE
SPEECH_THRESHOLD = 0.55
MERGE_GAP = int(0.4 * VAD_RATE)
MIN_PHRASE = int(0.25 * VAD_RATE)
PAD = int(0.12 * VAD_RATE)
TRANSITION = int(0.12 * RATE)
BLOCK = 65536
# ponytail: Cap phrase metadata at 20k; stream report JSON before raising this ceiling.
MAX_PROJECT_PHRASES = 20000


def validate_model():
    path = model_path()
    actual = digest(path)
    return actual


def _activity_regions(path, work, runner, max_regions):
    if max_regions <= 0:
        raise ValueError(f"Speech leveler found more than {MAX_PROJECT_PHRASES} phrase regions. Render a shorter project or disable leveling.")
    validate_model()
    vad_audio = work / f"vad-{Path(path).stem}.wav"
    with sf.SoundFile(path) as source:
        frames = len(source)
    timings = {"vad_preprocess_seconds": 0.0, "vad_model_load_seconds": 0.0,
               "vad_inference_seconds": 0.0, "vad_region_seconds": 0.0}
    stage_started = time.perf_counter()
    runner.run(["-i", path, "-map", "0:a:0", "-ac", "1", "-ar", VAD_RATE,
                *FLOAT_SCRATCH_ARGS, vad_audio], "analyzing", frames / RATE)
    timings["vad_preprocess_seconds"] = time.perf_counter() - stage_started
    with sf.SoundFile(vad_audio) as source:
        vad_frames = len(source)
    count = math.ceil(vad_frames / VAD_WINDOW)
    activity = np.memmap(work / f"vad-{Path(path).stem}.bin", mode="w+", dtype="uint8", shape=(count,))
    try:
        stage_started = time.perf_counter()
        import onnxruntime as ort
        options = ort.SessionOptions()
        options.inter_op_num_threads = options.intra_op_num_threads = 1
        session = ort.InferenceSession(str(model_path()), sess_options=options, providers=["CPUExecutionProvider"])
        timings["vad_model_load_seconds"] = time.perf_counter() - stage_started
        state = np.zeros((2, 1, 128), dtype=np.float32)
        context = np.zeros((1, 64), dtype=np.float32)
        stage_started = time.perf_counter()
        with sf.SoundFile(vad_audio) as source:
            for index in range(count):
                runner.check()
                chunk = source.read(VAD_WINDOW, dtype="float32", always_2d=True).mean(axis=1)
                if len(chunk) < VAD_WINDOW:
                    chunk = np.pad(chunk, (0, VAD_WINDOW - len(chunk)))
                model_input = np.concatenate((context[0], chunk))[None, :].astype(np.float32, copy=False)
                probability, state = session.run(None, {"input": model_input, "state": state,
                                                         "sr": np.array(VAD_RATE, dtype=np.int64)})
                activity[index] = float(probability[0, 0]) >= SPEECH_THRESHOLD
                context[0] = model_input[0, -64:]
                if index % 64 == 0:
                    runner.sample_memory()
        timings["vad_inference_seconds"] = time.perf_counter() - stage_started
    except BaseException:
        activity.flush()
        activity._mmap.close()
        raise
    activity.flush()
    intervals = None
    region_started = time.perf_counter()
    try:
        intervals = np.memmap(work / f"regions-{Path(path).stem}.bin", mode="w+",
                              dtype=np.int64, shape=(max_regions, 2))
        written = 0
        run_start = None
        merged_start = merged_end = None

        def save_region(left, right):
            nonlocal written
            if right - left < MIN_PHRASE:
                return
            if written == max_regions:
                raise ValueError(f"Speech leveler found more than {MAX_PROJECT_PHRASES} phrase regions. Render a shorter project or disable leveling.")
            intervals[written] = (max(0, left - PAD) * VAD_TO_PROJECT,
                                  min(frames, (right + PAD) * VAD_TO_PROJECT))
            written += 1

        for index in range(count + 1):
            on = bool(activity[index]) if index < count else False
            if on and run_start is None:
                run_start = index * VAD_WINDOW
            elif not on and run_start is not None:
                run_end = min(vad_frames, index * VAD_WINDOW)
                if merged_start is None:
                    merged_start, merged_end = run_start, run_end
                elif run_start - merged_end <= MERGE_GAP:
                    merged_end = run_end
                else:
                    save_region(merged_start, merged_end)
                    merged_start, merged_end = run_start, run_end
                run_start = None
        if merged_start is not None:
            save_region(merged_start, merged_end)
        activity.flush()
        result = [(int(intervals[i, 0]), int(intervals[i, 1])) for i in range(written)]
        timings["vad_region_seconds"] = time.perf_counter() - region_started
        return result, timings
    finally:
        if intervals is not None:
            intervals.flush()
            intervals._mmap.close()
        activity._mmap.close()


def _rms_db(path, start, end, runner):
    total = 0.0
    count = 0
    with sf.SoundFile(path) as source:
        source.seek(start)
        remaining = end - start
        while remaining > 0:
            runner.check()
            block = source.read(min(BLOCK, remaining), dtype="float32", always_2d=True)
            if not len(block):
                break
            total += float(np.sum(block.astype(np.float64) ** 2))
            count += block.size
            remaining -= len(block)
    return 10 * math.log10(total / count) if total > 0 and count else -math.inf


def analyze_speech_activity(tracks, raw_paths, work, runner=None):
    """Analyze original speech-role tracks once; offsets are applied by the mixer."""
    runner = runner or Runner()
    regions = {}
    remaining = MAX_PROJECT_PHRASES
    analyzed = 0
    timings = {key: 0.0 for key in ("vad_preprocess_seconds", "vad_model_load_seconds",
                                    "vad_inference_seconds", "vad_region_seconds")}
    started = time.perf_counter()
    for index, track in enumerate(tracks):
        if track.role != "speech":
            continue
        current, current_timings = _activity_regions(raw_paths[index], work, runner, remaining)
        for key in timings:
            timings[key] += current_timings[key]
        remaining -= len(current)
        regions[index] = current
        with sf.SoundFile(raw_paths[index]) as source:
            analyzed += len(source)
    elapsed = time.perf_counter() - started
    return regions, {"model_sha256": MODEL_SHA256, "model_license": "MIT",
                     "runtime": __import__("onnxruntime").__version__,
                     "provider": "CPUExecutionProvider", "analyzed_samples": analyzed,
                     "analysis_wall_seconds": elapsed,
                     "analysis_rtf": elapsed / (analyzed / RATE) if analyzed else 0,
                     "timings": timings}


def build_leveling(tracks, raw_paths, processed_paths, preset, work, runner=None,
                   activity_regions=None, activity_info=None):
    """Return sparse, source-local dB curves and an auditable phrase summary."""
    import onnxruntime as ort

    runner = runner or Runner()
    started = time.perf_counter()
    candidates = []
    activity_regions = activity_regions or {}
    vad_timings = dict((activity_info or {}).get("timings", {}))
    if not vad_timings:
        vad_timings = {key: 0.0 for key in ("vad_preprocess_seconds", "vad_model_load_seconds",
                                             "vad_inference_seconds", "vad_region_seconds")}
    analyzed_samples = 0
    for index, track in enumerate(tracks):
        if track.role != "speech" or track.leveler_curve_imported:
            continue
        regions = activity_regions.get(index)
        if regions is None:
            regions, current_timings = _activity_regions(raw_paths[index], work, runner, MAX_PROJECT_PHRASES)
            for key in vad_timings:
                vad_timings[key] += current_timings[key]
        with sf.SoundFile(raw_paths[index]) as source:
            analyzed_samples += len(source)
        levels = []
        for start, end in regions:
            level = _rms_db(processed_paths[index], start, end, runner)
            if level > -55:
                levels.append({"start_sample": start, "end_sample": end,
                               "level_dbfs": level + track.gain_db,
                               "post_cleanup_level_dbfs": level, "track_gain_db": track.gain_db})
        if levels:
            candidates.append((index, levels))
    all_levels = [region["level_dbfs"] for _, regions in candidates for region in regions]
    curves = {}
    summaries = []
    if all_levels:
        reference = float(np.median(all_levels))
        factor = preset.leveler_strength * (1 - preset.preserve_dynamics)
        for index, regions in candidates:
            with sf.SoundFile(processed_paths[index]) as source:
                length = len(source)
            points = [(0, 0.0)]
            for region in regions:
                desired = float(np.clip((reference - region["level_dbfs"]) * factor,
                                        -preset.max_attenuation_db, preset.max_boost_db))
                start, end = region["start_sample"], region["end_sample"]
                ramp = min(TRANSITION, max(1, (end - start) // 2))
                points.extend([(start, 0.0), (start + ramp, desired),
                               (end - ramp, desired), (end, 0.0)])
                region["gain_db"] = desired
            points.append((length, 0.0))
            points.sort(key=lambda point: point[0])
            compact = []
            for point in points:
                if compact and point[0] == compact[-1][0]:
                    compact[-1] = point
                else:
                    compact.append(point)
            curves[index] = (np.asarray([p[0] for p in compact], dtype=np.int64),
                             np.asarray([p[1] for p in compact], dtype=np.float32))
            summaries.append({"track": index, "regions": regions,
                              "gain_curve": [{"sample": int(x), "gain_db": float(y)}
                                             for x, y in compact]})
    leveling_elapsed = time.perf_counter() - started
    elapsed = leveling_elapsed + float((activity_info or {}).get("analysis_wall_seconds", 0))
    return curves, {"enabled": bool(curves), "method": "Silero VAD v6.2.1 + post-cleanup phrase RMS",
                    "model_sha256": MODEL_SHA256, "model_license": "MIT",
                    "runtime": ort.__version__, "provider": "CPUExecutionProvider",
                    "vad_sample_rate": VAD_RATE, "vad_window_samples": VAD_WINDOW,
                    "speech_probability_threshold": SPEECH_THRESHOLD,
                    "analyzed_track_seconds": analyzed_samples / RATE,
                    "analysis_wall_seconds": elapsed,
                    "analysis_rtf": elapsed / (analyzed_samples / RATE) if analyzed_samples else 0,
                    "vad_analysis_wall_seconds": float((activity_info or {}).get("analysis_wall_seconds", 0)),
                    "leveler_analysis_wall_seconds": leveling_elapsed,
                    "analysis_timings": vad_timings,
                    "reference_phrase_dbfs": float(np.median(all_levels)) if all_levels else None,
                    "leveler_strength": preset.leveler_strength,
                    "preserve_dynamics": preset.preserve_dynamics,
                    "transition_samples": TRANSITION,
                    "imported_curve_tracks_skipped": [index for index, track in enumerate(tracks)
                                                       if track.leveler_curve_imported],
                    "max_boost_db": preset.max_boost_db,
                    "max_attenuation_db": preset.max_attenuation_db,
                    "reason": None if curves else "No above-floor, confident speech phrases were found.",
                    "tracks": summaries}
