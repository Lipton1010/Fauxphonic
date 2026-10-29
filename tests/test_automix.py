from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from local_audio_studio.contracts import GainPoint, Preset, Project, RATE, Track
from local_audio_studio.engine import render


def test_manual_envelope_round_trips_and_is_sample_local(tmp_path):
    source = tmp_path / "tone.wav"
    frames = RATE * 4
    audio = 0.08 * np.sin(2 * np.pi * 220 * np.arange(frames) / RATE)
    sf.write(source, audio, RATE, subtype="FLOAT")
    track = Track(str(source), role="speech", gain_envelope=[
        GainPoint(0, 0), GainPoint(RATE, -6), GainPoint(2 * RATE, -6), GainPoint(3 * RATE, 0)])
    project = Project([track], Preset(mode="bypass"))
    restored = Project.from_dict(project.to_dict())
    assert restored.tracks[0].gain_envelope == track.gain_envelope
    out = tmp_path / "automated.wav"
    render(restored, out)
    actual, _ = sf.read(out)
    measured = np.sqrt(np.mean(actual[int(1.1*RATE):int(1.9*RATE)] ** 2))
    original = np.sqrt(np.mean(audio[int(1.1*RATE):int(1.9*RATE)] ** 2))
    assert 20 * np.log10(measured / original) == pytest.approx(-6, abs=0.03)


def test_speech_ducks_music_and_exports_aligned_premaster_stems(tmp_path):
    fixture = Path(__file__).parent / "fixtures" / "silero-leveling-tts.wav"
    speech, rate = sf.read(fixture, dtype="float32")
    speech_path = tmp_path / "speech.wav"
    offset = RATE
    sf.write(speech_path, speech, rate, subtype="FLOAT")
    music_path = tmp_path / "music.wav"
    music_frames = RATE * 18
    music = 0.03 * np.sin(2 * np.pi * 220 * np.arange(music_frames) / RATE)
    sf.write(music_path, music, RATE, subtype="FLOAT")

    project = Project([
        Track(str(speech_path), offset_samples=offset, role="speech"),
        Track(str(music_path), role="music"),
    ], Preset(mode="master-only", music_ducking_enabled=True, music_duck_db=6), export_stems=True)
    output = tmp_path / "program.wav"
    report = render(project, output)

    assert report["music_ducking"]["enabled"]
    assert report["music_ducking"]["tracks"]
    activity = report["speech_activity"]
    assert activity["analysis_wall_seconds"] >= sum(activity["timings"].values())
    assert activity["timings"]["vad_model_load_seconds"] > 0
    assert all(stem["mastering"].startswith("pre-master") for stem in report["stems"])
    music_stem, music_rate = sf.read(tmp_path / "program.track-02-music-stem.wav")
    speech_stem, speech_rate = sf.read(tmp_path / "program.track-01-speech-stem.wav")
    mixed, mixed_rate = sf.read(output)
    assert music_rate == speech_rate == mixed_rate == RATE
    assert len(music_stem) == len(speech_stem) == len(mixed) == music_frames
    assert report["verification"]["status"] == "passed"
    assert np.max(np.abs(speech_stem[:offset])) < 4e-7
    assert np.allclose(speech_stem[offset:offset+len(speech)], speech, atol=4e-7)
    assert np.max(np.abs(speech_stem[offset+len(speech):])) < 4e-7

    assert report["music_ducking"]["gain_curve_definition"]
    plateau, phrase_end = report["music_ducking"]["speech_regions_project_samples"][0]
    window = RATE // 4
    assert plateau + window < phrase_end
    ducked_rms = np.sqrt(np.mean(music_stem[plateau:plateau + window] ** 2))
    source_rms = np.sqrt(np.mean(music[plateau:plateau + window] ** 2))
    assert 20 * np.log10(ducked_rms / source_rms) == pytest.approx(-6, abs=0.08)
    assert 20 * np.log10(np.sqrt(np.mean(music_stem[:RATE // 2] ** 2)) /
                         np.sqrt(np.mean(music[:RATE // 2] ** 2))) == pytest.approx(0, abs=0.02)


def test_gain_envelope_rejects_ambiguous_point_order(tmp_path):
    source = tmp_path / "tone.wav"
    sf.write(source, np.zeros(RATE), RATE)
    track = Track(str(source), gain_envelope=[GainPoint(10, -3), GainPoint(10, 2)])
    with pytest.raises(ValueError, match="strictly increasing"):
        track.validate()


def test_existing_track_stem_is_protected_without_partial_output(tmp_path):
    source = tmp_path / "tone.wav"
    sf.write(source, np.zeros(RATE), RATE)
    output = tmp_path / "program.wav"
    stem = tmp_path / "program.track-01-program-stem.wav"
    stem.write_bytes(b"keep this file")
    with pytest.raises(FileExistsError):
        render(Project([Track(str(source))], Preset(mode="bypass"), export_stems=True), output)
    assert stem.read_bytes() == b"keep this file"
    assert not output.exists()
    assert not output.with_suffix(".wav.report.json").exists()


def test_music_duck_release_never_rises_during_following_speech(tmp_path):
    from local_audio_studio.engine import _duck_curves

    speech = tmp_path / "speech.wav"
    music = tmp_path / "music.wav"
    sf.write(speech, np.zeros(RATE * 5), RATE)
    sf.write(music, np.zeros(RATE * 5), RATE)
    tracks = [Track(str(speech), role="speech"), Track(str(music), role="music")]
    preset = Preset(music_ducking_enabled=True, music_duck_db=6, duck_release_ms=800)
    curves, _ = _duck_curves(tracks, {0: [(RATE, 2 * RATE), (RATE * 5 // 2, RATE * 7 // 2)]},
                             [RATE * 5, RATE * 5], preset)
    positions, gains = curves[1]
    for sample in (int(2.1 * RATE), int(2.8 * RATE), int(3.4 * RATE)):
        assert np.interp(sample, positions, gains) == pytest.approx(-6)
