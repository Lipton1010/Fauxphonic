"""Conservative, bounded detection of highly correlated speech bleed."""
from contextlib import ExitStack
import itertools
import math

import numpy as np
import soundfile as sf

from .contracts import RATE

BLOCK = RATE // 10
VAD_CONTEXT_PAD = round(0.12 * RATE)
MAX_TRACKS = 32
MAX_PAIR_BLOCKS = 250_000
MAX_EVENTS = 20_000
DECIMATE = 24
MAX_LAG = round(0.005 * RATE / DECIMATE)
LEVEL_GAP_DB = 12.0
MIN_CORRELATION = 0.90


def _intersections(left_regions, left_offset, right_regions, right_offset):
    """Yield global intersections after removing VAD's context padding."""
    left = [(left_offset + a + VAD_CONTEXT_PAD, left_offset + b - VAD_CONTEXT_PAD)
            for a, b in left_regions if b - a > 2 * VAD_CONTEXT_PAD]
    right = [(right_offset + a + VAD_CONTEXT_PAD, right_offset + b - VAD_CONTEXT_PAD)
             for a, b in right_regions if b - a > 2 * VAD_CONTEXT_PAD]
    i = j = 0
    while i < len(left) and j < len(right):
        start, end = max(left[i][0], right[j][0]), min(left[i][1], right[j][1])
        if end > start:
            yield start, end
        if left[i][1] <= right[j][1]:
            i += 1
        else:
            j += 1


def _correlation(left, right):
    # 2 kHz box-averaged samples retain useful speech timing while keeping
    # bounded lag checks cheap for long multi-track sessions.
    left = left.reshape(-1, DECIMATE).mean(axis=1).astype(np.float64)
    right = right.reshape(-1, DECIMATE).mean(axis=1).astype(np.float64)
    left -= left.mean()
    right -= right.mean()
    norm = math.sqrt(float(np.dot(left, left) * np.dot(right, right)))
    if norm <= 1e-24:
        return 0.0, 0
    best_corr, best_lag = 0.0, 0
    for lag in range(-MAX_LAG, MAX_LAG + 1):
        if lag < 0:
            a, b = left[-lag:], right[:lag]
        elif lag > 0:
            a, b = left[:-lag], right[lag:]
        else:
            a, b = left, right
        value = abs(float(np.dot(a, b)) / norm)
        if value > best_corr:
            best_corr, best_lag = value, lag
    return min(best_corr, 1.0), best_lag * DECIMATE


def _merge_events(events, hold_samples):
    merged = []
    for event in sorted(events, key=lambda item: (item["start_sample"], item["end_sample"])):
        if merged and event["start_sample"] <= merged[-1]["end_sample"] + hold_samples:
            current = merged[-1]
            current["end_sample"] = max(current["end_sample"], event["end_sample"])
            current["dominant_track_indices"] |= event["dominant_track_indices"]
            current["max_relative_level_db"] = max(current["max_relative_level_db"],
                                                    event["max_relative_level_db"])
            current["min_correlation"] = min(current["min_correlation"], event["min_correlation"])
            current["max_abs_lag_samples"] = max(current["max_abs_lag_samples"],
                                                  event["max_abs_lag_samples"])
        else:
            merged.append({**event, "dominant_track_indices": set(event["dominant_track_indices"])})
    return merged


def analyze_bleed(tracks, raw_paths, activity_regions, *, max_attenuation_db=6.0,
                  attack_ms=100, hold_ms=300, release_ms=500, runner=None):
    """Return source-local attenuation curves only for clearly correlated bleed.

    ``activity_regions`` contains original-signal VAD regions in source-local
    48 kHz samples. Correlation only selects candidate timing; audio is never
    shifted. No eligible or uncertain overlap receives attenuation.
    """
    if len(tracks) != len(raw_paths):
        raise ValueError("Bleed analysis needs one decoded original per track.")
    if len(tracks) > MAX_TRACKS:
        raise ValueError(f"Bleed analysis supports at most {MAX_TRACKS} tracks.")
    if (not math.isfinite(max_attenuation_db) or not 0 <= max_attenuation_db <= 6
            or type(attack_ms) is not int or not 50 <= attack_ms <= 500
            or type(hold_ms) is not int or not 100 <= hold_ms <= 1000
            or type(release_ms) is not int or not 100 <= release_ms <= 2000):
        raise ValueError("Bleed attenuation must be 0..6 dB with attack 50..500 ms, hold 100..1000 ms, and release 100..2000 ms.")

    eligible = []
    report_tracks = []
    lengths = []
    event_count = 0
    with ExitStack() as stack:
        readers = [stack.enter_context(sf.SoundFile(path)) for path in raw_paths]
        for index, (track, reader) in enumerate(zip(tracks, readers)):
            lengths.append(len(reader))
            if track.role != "speech":
                reason = "track role is not Speech"
            elif reader.channels != 1:
                reason = "requires mono source"
            elif not activity_regions.get(index):
                reason = "no confident original VAD regions"
            else:
                reason = None
                eligible.append(index)
            report_tracks.append({"track_index": index, "role": track.role,
                                  "source_frames": len(reader), "status": "eligible" if reason is None else "bypassed",
                                  "reason": "track is protected from bleed attenuation"
                                      if reason is None and getattr(track, "bleed_protected", False) else reason,
                                  "candidate_blocks": 0, "events": []})

        events = {index: [] for index in eligible}
        comparisons = 0
        for left_index, right_index in itertools.combinations(eligible, 2):
            left_track, right_track = tracks[left_index], tracks[right_index]
            if getattr(left_track, "bleed_protected", False) and getattr(right_track, "bleed_protected", False):
                continue
            left_regions = activity_regions[left_index]
            right_regions = activity_regions[right_index]
            pair_runs = {left_index: None, right_index: None}

            def close_run(quiet_index):
                nonlocal event_count
                run = pair_runs[quiet_index]
                if run is None:
                    return
                events[quiet_index].append({
                    "start_sample": run["start"] - tracks[quiet_index].offset_samples,
                    "end_sample": run["end"] - tracks[quiet_index].offset_samples,
                    "dominant_track_indices": {run["dominant"]},
                    "max_relative_level_db": run["relative"],
                    "min_correlation": run["correlation"],
                    "max_abs_lag_samples": abs(run["lag"]),
                })
                pair_runs[quiet_index] = None
                event_count += 1
                if event_count > MAX_EVENTS:
                    raise ValueError(f"Bleed analysis exceeded {MAX_EVENTS} sparse candidate events; no partial curves were returned.")

            for overlap_start, overlap_end in _intersections(
                    left_regions, left_track.offset_samples, right_regions, right_track.offset_samples):
                first = (overlap_start + BLOCK - 1) // BLOCK * BLOCK
                for global_start in range(first, overlap_end - BLOCK + 1, BLOCK):
                    comparisons += 1
                    if comparisons > MAX_PAIR_BLOCKS:
                        raise ValueError(f"Bleed analysis exceeded {MAX_PAIR_BLOCKS} overlapping 100 ms block comparisons; no partial curves were returned.")
                    if runner:
                        runner.check()
                        if comparisons % 1000 == 0:
                            runner.sample_memory()
                    left_start = global_start - left_track.offset_samples
                    right_start = global_start - right_track.offset_samples
                    readers[left_index].seek(left_start)
                    readers[right_index].seek(right_start)
                    left = readers[left_index].read(BLOCK, dtype="float32", always_2d=True)[:, 0]
                    right = readers[right_index].read(BLOCK, dtype="float32", always_2d=True)[:, 0]
                    if len(left) != BLOCK or len(right) != BLOCK:
                        continue
                    if not np.isfinite(left).all() or not np.isfinite(right).all():
                        raise ValueError("Bleed analysis encountered nonfinite original audio.")
                    left_rms = math.sqrt(float(np.dot(left, left)) / BLOCK)
                    right_rms = math.sqrt(float(np.dot(right, right)) / BLOCK)
                    if min(left_rms, right_rms) <= 1e-7:
                        for index in pair_runs:
                            close_run(index)
                        continue
                    relative_db = 20 * math.log10(max(left_rms, right_rms) / min(left_rms, right_rms))
                    quiet_index = right_index if left_rms > right_rms else left_index
                    dominant_index = left_index if quiet_index == right_index else right_index
                    if getattr(tracks[quiet_index], "bleed_protected", False):
                        for index in pair_runs:
                            close_run(index)
                        continue
                    if relative_db < LEVEL_GAP_DB:
                        for index in pair_runs:
                            close_run(index)
                        continue
                    correlation, lag = _correlation(left, right)
                    if correlation < MIN_CORRELATION:
                        for index in pair_runs:
                            close_run(index)
                        continue
                    report_tracks[quiet_index]["candidate_blocks"] += 1
                    run = pair_runs[quiet_index]
                    if run is None:
                        pair_runs[quiet_index] = {"start": global_start,
                                                  "end": global_start + BLOCK,
                                                  "dominant": dominant_index,
                                                  "relative": relative_db,
                                                  "correlation": correlation,
                                                  "lag": lag}
                    else:
                        run["end"] = global_start + BLOCK
                        run["relative"] = max(run["relative"], relative_db)
                        run["correlation"] = min(run["correlation"], correlation)
                        run["lag"] = lag if abs(lag) > abs(run["lag"]) else run["lag"]
                    close_run(left_index if quiet_index == right_index else right_index)
                for index in pair_runs:
                    close_run(index)
            for index in pair_runs:
                close_run(index)

        curves = {}
        hold = round(hold_ms * RATE / 1000)
        attack = round(attack_ms * RATE / 1000)
        release = round(release_ms * RATE / 1000)
        for index in eligible:
            # Never bridge a block that failed the correlation/activity gate.
            # Hold is a confirmation interval before engaging; both ramps stay
            # inside a continuously confirmed region, preserving unknown speech.
            merged = _merge_events(events[index], 0)
            points = []
            applied = []
            for event in merged:
                start, end = max(0, event["start_sample"]), min(lengths[index], event["end_sample"])
                attack_start = start + hold
                plateau_start = attack_start + attack
                plateau_end = end - release
                if plateau_start > plateau_end:
                    continue
                points.extend(((attack_start, 0.0), (plateau_start, -max_attenuation_db),
                               (plateau_end, -max_attenuation_db), (end, 0.0)))
                event["dominant_track_indices"] = sorted(event["dominant_track_indices"])
                event["max_abs_lag_ms"] = 1000 * event.pop("max_abs_lag_samples") / RATE
                event["gain_start_sample"] = attack_start
                event["gain_end_sample"] = end
                applied.append(event)
            compact = []
            for point in sorted(points):
                if compact and point[0] == compact[-1][0]:
                    compact[-1] = (point[0], min(point[1], compact[-1][1]))
                else:
                    compact.append(point)
            if len(compact) > MAX_EVENTS * 4:
                raise ValueError(f"Bleed curve exceeded {MAX_EVENTS} sparse events; no partial curves were returned.")
            if compact and max_attenuation_db > 0:
                curves[index] = (np.asarray([point[0] for point in compact], dtype=np.int64),
                                 np.asarray([point[1] for point in compact], dtype=np.float32))
            report_tracks[index]["events"] = applied
            report_tracks[index]["protected"] = bool(getattr(tracks[index], "bleed_protected", False))
            report_tracks[index]["curve_coordinates"] = "source-local 48 kHz samples"
            report_tracks[index]["attenuation_db"] = max_attenuation_db if applied else 0.0
            report_tracks[index]["source_frames"] = lengths[index]

    report = {"enabled": True, "method": "original Silero VAD + 100 ms mono RMS and normalized correlation",
              "track_thresholds": {"minimum_level_difference_db": LEVEL_GAP_DB,
                                   "minimum_absolute_correlation": MIN_CORRELATION,
                                   "maximum_alignment_lag_ms": 5},
              "settings": {"max_attenuation_db": max_attenuation_db, "attack_ms": attack_ms,
                           "hold_ms": hold_ms, "release_ms": release_ms},
              "candidate_block_comparisons": comparisons,
              "tracks": report_tracks,
              "reason": None if curves else "No qualifying correlated speech-bleed candidates.",
              "confidence": "Heuristic. Similar waveforms and level separation are required; uncertain overlaps are unchanged.",
              "envelope_policy": "Hold confirms a continuous candidate before attack; release ends inside the candidate. No ramps or holds bridge uncertain blocks.",
              "listening_evaluation": "not performed"}
    return curves, report
