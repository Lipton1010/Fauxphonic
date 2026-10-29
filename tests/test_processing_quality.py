"""Measured pre-master effects; these checks do not claim listening approval."""
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from local_audio_studio.contracts import GainPoint, Preset, Project, Track, RATE
from local_audio_studio.engine import render


@pytest.mark.parametrize("color", ["white", "pink"])
@pytest.mark.parametrize("noise_db", [-35, -45])
def test_profiled_cleanup_reduces_noise_and_preserves_speech(tmp_path, color, noise_db):
    speech, _ = sf.read(Path(__file__).parent / "fixtures" / "silero-leveling-tts.wav")
    clean = np.pad(speech, (RATE*3, RATE*3))
    rng = np.random.default_rng(27491)
    noise = rng.normal(size=len(clean))
    if color == "pink":
        spectrum = np.fft.rfft(noise)
        spectrum /= np.sqrt(np.maximum(np.fft.rfftfreq(len(noise), 1/RATE), 20))
        noise = np.fft.irfft(spectrum, len(noise))
    noise *= 10**(noise_db/20) / np.sqrt(np.mean(noise**2))
    source = tmp_path / "noisy.wav"
    sf.write(source, clean + noise, RATE, subtype="FLOAT")
    reductions = []
    for amount in (6, 9):
        report = render(Project([Track(str(source), role="speech")],
            Preset(mode="dsp-cleanup", noise_reduction_db=amount), export_stems=True),
            tmp_path / f"cleanup-{amount}.wav")
        actual, _ = sf.read(report["stems"][0]["path"])
        assert len(actual) == len(clean)
        interval = slice(RATE, 2*RATE)
        reduction = 10*np.log10(np.mean(noise[interval]**2)/np.mean(actual[interval]**2))
        reductions.append(reduction)
        voice = slice(3*RATE, 3*RATE + len(speech))
        retained_gain = np.dot(actual[voice], speech)/np.dot(speech, speech)
        assert 0.85 < retained_gain < 1.05
        assert "noise_profile" in report["processing"][0]
    assert reductions[0] >= 2, reductions
    assert reductions[1] >= 4, reductions
    assert reductions[1] > reductions[0] + 0.5, reductions


def test_clean_speech_without_noise_profile_stays_unchanged_before_master(tmp_path):
    source = Path(__file__).parent / "fixtures" / "silero-leveling-tts.wav"
    report = render(Project([Track(str(source), role="speech")], Preset(mode="dsp-cleanup"),
                            export_stems=True), tmp_path / "clean.wav")
    original, _ = sf.read(source)
    actual, _ = sf.read(report["stems"][0]["path"])
    assert len(actual) == len(original)
    assert np.max(np.abs(actual-original)) < 2e-6


def test_imported_leveler_curve_survives_save_and_is_not_applied_twice(tmp_path):
    source = Path(__file__).parent / "fixtures" / "silero-leveling-tts.wav"
    track = Track(str(source), role="speech", gain_envelope=[GainPoint(0, -3)], leveler_curve_imported=True)
    project = Project.from_dict(Project([track], Preset(leveler_enabled=True), export_stems=True).to_dict())
    report = render(project, tmp_path / "imported.wav")
    original, _ = sf.read(source)
    actual, _ = sf.read(report["stems"][0]["path"])
    assert np.max(np.abs(actual-original*10**(-3/20))) < 2e-6
    assert report["leveling"]["imported_curve_tracks_skipped"] == [0]
    assert not report["leveling"]["tracks"]
