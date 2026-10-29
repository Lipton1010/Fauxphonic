import numpy as np
import pytest
import soundfile as sf

from local_audio_studio.automix import analyze_bleed
from local_audio_studio.audio import Runner
from local_audio_studio.contracts import RATE, Preset, Project, Track
from local_audio_studio.engine import mix_tracks, render


def duplicate_pair(tmp_path, seconds=8, offset=0):
    frames = RATE * seconds
    rng = np.random.default_rng(4001)
    time_axis = np.arange(frames) / RATE
    dominant = 0.05 * np.sin(2 * np.pi * 240 * time_axis)
    dominant += rng.normal(0, 0.008, frames)
    lag = 96
    source_indices = np.minimum(np.arange(frames) + offset + lag, frames - 1)
    quiet = dominant[source_indices] * 0.1
    quiet += rng.normal(0, 0.00003, frames)
    quiet[-1] = 0.23
    loud_path, quiet_path = tmp_path / "dominant.wav", tmp_path / "bleed.wav"
    sf.write(loud_path, dominant, RATE, subtype="FLOAT")
    sf.write(quiet_path, quiet, RATE, subtype="FLOAT")
    tracks = [Track(str(loud_path), role="speech"),
              Track(str(quiet_path), offset_samples=offset, role="speech")]
    regions = {0: [(RATE // 2, frames - RATE // 2)],
               1: [(max(0, RATE // 2 - offset), max(0, frames - RATE // 2 - offset))]}
    return dominant.astype(np.float32), quiet.astype(np.float32), tracks, regions


def test_correlated_quiet_speech_bleed_gets_smooth_capped_source_local_curve(tmp_path):
    dominant, quiet, tracks, regions = duplicate_pair(tmp_path, offset=12000)
    curves, report = analyze_bleed(tracks, [track.path for track in tracks], regions, runner=Runner())

    assert set(curves) == {1}
    positions, gains = curves[1]
    assert np.all(np.diff(positions) > 0)
    assert np.isfinite(gains).all()
    assert np.min(gains) == pytest.approx(-6)
    assert gains.min() >= -6
    assert report["tracks"][1]["events"]
    event = report["tracks"][1]["events"][0]
    assert event["dominant_track_indices"] == [0]
    assert event["min_correlation"] >= 0.9
    assert event["max_abs_lag_ms"] <= 5
    assert report["tracks"][1]["events"][0]["gain_start_sample"] >= 0
    assert np.interp([2*RATE, 4*RATE, 6*RATE], positions, gains).tolist() == [-6, -6, -6]

    # Curves are source-local, timing stays on the original project timeline,
    # and the final source frame remains present after mixing.
    output = tmp_path / "aligned-mix.wav"
    frames, _ = mix_tracks([track.path for track in tracks], tracks, output, Runner(), curves)
    mixed, _ = sf.read(output, dtype="float32")
    assert frames == RATE * 8 + 12000
    assert len(mixed) == frames
    assert mixed[-1] == pytest.approx(quiet[-1], abs=1e-6)
    assert mixed[0] == pytest.approx(dominant[0], abs=1e-6)


def test_unknown_short_overlap_never_gets_bridged_and_protected_reference_still_works(tmp_path):
    _, quiet, tracks, regions = duplicate_pair(tmp_path, offset=RATE)
    tracks[0].bleed_protected = True
    rng = np.random.default_rng(7721)
    quiet[2*RATE:27*RATE//10] = rng.normal(0, 0.004, 7*RATE//10)
    sf.write(tracks[1].path, quiet, RATE, subtype="FLOAT")
    curves, _ = analyze_bleed(tracks, [t.path for t in tracks], regions)
    assert set(curves) == {1}
    positions, gains = curves[1]
    assert np.interp([21*RATE//10, 23*RATE//10, 25*RATE//10, 27*RATE//10], positions, gains).tolist() == [0, 0, 0, 0]
    assert np.interp(4*RATE, positions, gains) == -6


def test_real_speech_bleed_export_stems_preserve_offset_and_protection(tmp_path):
    from pathlib import Path
    speech, rate = sf.read(Path(__file__).parent / "fixtures/silero-leveling-tts.wav", dtype="float32")
    source = tmp_path / "speech.wav"
    quiet = tmp_path / "quiet-copy.wav"
    sf.write(source, speech, rate, subtype="FLOAT")
    sf.write(quiet, speech[RATE//2:]*0.1, rate, subtype="FLOAT")
    project = Project([Track(str(source), role="speech", bleed_protected=True),
                       Track(str(quiet), role="speech", offset_samples=RATE//2)],
                      Preset(bleed_reduction_enabled=True), export_stems=True)
    report = render(project, tmp_path / "bleed-master.wav")
    assert report["verification"]["status"] == "passed"
    assert report["verification"]["samples"]["frames"] == len(speech)
    assert report["bleed_reduction"]["enabled"]
    assert not report["bleed_reduction"]["tracks"][0]["events"]
    main_stem, _ = sf.read(report["stems"][0]["path"])
    quiet_stem, _ = sf.read(report["stems"][1]["path"])
    assert np.max(np.abs(main_stem-speech)) < 4e-7
    assert np.max(np.abs(quiet_stem[:RATE//2])) < 4e-7
    assert np.sum(quiet_stem**2) < np.sum((speech[RATE//2:]*0.1)**2)*0.9


def test_independent_overlap_and_quiet_vocal_like_track_remain_unchanged(tmp_path):
    frames = RATE * 8
    rng = np.random.default_rng(9002)
    time_axis = np.arange(frames) / RATE
    loud = 0.06 * np.sin(2 * np.pi * 187 * time_axis) + rng.normal(0, 0.008, frames)
    independent = 0.004 * np.sin(2 * np.pi * 311 * time_axis + 0.7)
    independent += rng.normal(0, 0.0005, frames)
    whisper_laughter_like = 0.002 * np.sin(2 * np.pi * 437 * time_axis)
    whisper_laughter_like += rng.normal(0, 0.0002, frames)
    paths = [tmp_path / "loud.wav", tmp_path / "independent.wav", tmp_path / "quiet-voice.wav"]
    for path, audio in zip(paths, (loud, independent, whisper_laughter_like)):
        sf.write(path, audio, RATE, subtype="FLOAT")
    tracks = [Track(str(path), role="speech") for path in paths]
    regions = {index: [(0, frames)] for index in range(3)}

    curves, report = analyze_bleed(tracks, paths, regions, runner=Runner())
    assert curves == {}
    assert report["reason"] == "No qualifying correlated speech-bleed candidates."
    assert all(track["attenuation_db"] == 0 for track in report["tracks"] if "attenuation_db" in track)


def test_protected_track_and_ineligible_roles_are_explicit_noops(tmp_path):
    _, _, tracks, regions = duplicate_pair(tmp_path)
    tracks[1].bleed_protected = True
    tracks.append(Track(tracks[0].path, role="music"))
    paths = [track.path for track in tracks]
    regions[2] = [(0, RATE * 8)]
    curves, report = analyze_bleed(tracks, paths, regions)
    assert curves == {}
    assert report["tracks"][1]["reason"] == "track is protected from bleed attenuation"
    assert report["tracks"][2]["reason"] == "track role is not Speech"


def test_bleed_caps_are_validated_and_track_count_is_bounded(tmp_path):
    _, _, tracks, _ = duplicate_pair(tmp_path)
    with pytest.raises(ValueError, match="0..6 dB"):
        analyze_bleed(tracks, [track.path for track in tracks], {}, max_attenuation_db=6.1)
    with pytest.raises(ValueError, match="at most 32 tracks"):
        analyze_bleed(tracks * 17, [tracks[0].path] * 34, {})


def test_pair_block_work_cap_fails_without_returning_partial_curves(tmp_path, monkeypatch):
    import local_audio_studio.automix as automix

    _, _, tracks, regions = duplicate_pair(tmp_path)
    monkeypatch.setattr(automix, "MAX_PAIR_BLOCKS", 1)
    with pytest.raises(ValueError, match="no partial curves were returned"):
        analyze_bleed(tracks, [track.path for track in tracks], regions)
