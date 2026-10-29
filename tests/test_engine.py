import json
import math
from pathlib import Path
import subprocess
import threading
import time

import numpy as np
import pytest
import soundfile as sf

from local_audio_studio.audio import Cancelled, Runner, binary, digest
from local_audio_studio.contracts import Preset, Project, RATE, Track
from local_audio_studio.engine import mix_tracks, render
from local_audio_studio.jobs import Jobs


def test_bypass_ignores_enabled_automatic_stages(tmp_path, monkeypatch):
    import local_audio_studio.engine as engine
    source = tmp_path / "bypass-flags.wav"
    samples = (0.1*np.sin(2*np.pi*311*np.arange(48001)/RATE)).astype(np.float32)
    sf.write(source, samples, RATE, subtype="FLOAT")
    def unexpected(*args, **kwargs):
        raise AssertionError("Bypass must not load or run the activity model.")
    monkeypatch.setattr(engine, "validate_model", unexpected)
    monkeypatch.setattr(engine, "analyze_speech_activity", unexpected)
    preset = Preset(mode="bypass", leveler_enabled=True, music_ducking_enabled=True,
                    deesser_enabled=True, broad_eq_enabled=True, broad_eq_gain_db=3,
                    highpass_hz=100)
    output = tmp_path / "bypass-result.wav"
    report = render(Project([Track(str(source), role="speech")], preset), output)
    actual, _ = sf.read(output)
    assert len(actual) == len(samples)
    assert np.max(np.abs(actual-samples)) < 4e-7
    assert report["leveling"]["enabled"] is False
    assert report["music_ducking"]["enabled"] is False
    assert "Bypass" in report["leveling"]["reason"]


def fixture_audio(path, seconds=8, stereo=False):
    t = np.arange(int(seconds * RATE)) / RATE
    signal = (0.06 * np.sin(2*np.pi*233*t) + 0.015 * np.sin(2*np.pi*967*t))
    signal *= 0.7 + 0.3 * np.sin(2*np.pi*0.6*t)
    audio = np.column_stack((signal, signal*0.5)) if stereo else signal
    sf.write(path, audio, RATE, subtype="FLOAT")
    return audio


def speechlike_fixture(seconds, seed, level, spikes):
    rng = np.random.default_rng(seed)
    count = int(seconds * RATE)
    carrier = rng.normal(0, 1, count)
    spectrum = np.fft.rfft(carrier)
    frequency = np.fft.rfftfreq(count, 1 / RATE)
    shape = np.where((frequency > 90) & (frequency < 7000),
                     1 / np.sqrt(np.maximum(frequency, 90) / 90), 0)
    carrier = np.fft.irfft(spectrum * shape, count)
    carrier /= np.max(np.abs(carrier)) + 1e-12
    time_axis = np.arange(count) / RATE
    envelope = np.clip(np.sin(2 * np.pi * 4.1 * time_axis + rng.uniform(0, 6)), 0, None) ** 1.5
    envelope *= 0.6 + 0.4 * np.sin(2 * np.pi * 0.23 * time_axis + 1)
    gate = np.ones(count)
    position = 0
    while position < count:
        talk = int(rng.uniform(1.5, 4) * RATE)
        silence = int(rng.uniform(0.4, 1.5) * RATE)
        gate[min(position + talk, count):min(position + talk + silence, count)] = 0
        position += talk + silence
    kernel = np.hanning(int(0.03 * RATE))
    envelope *= np.convolve(gate, kernel / kernel.sum(), mode="same")
    signal = carrier * envelope
    signal *= level / (np.sqrt(np.mean(signal ** 2)) + 1e-12)
    for _ in range(spikes):
        at = int(rng.uniform(0.05, 0.95) * count)
        width = int(0.004 * RATE)
        signal[at:at + width] += np.hanning(width) * rng.choice([-1, 1]) * 0.7
    return signal


def band_db(audio, start, end, low_hz, high_hz):
    segment = np.asarray(audio[int(start * RATE):int(end * RATE)], dtype=np.float64)
    spectrum = np.fft.rfft(segment)
    frequencies = np.fft.rfftfreq(len(segment), 1 / RATE)
    energy = np.sum(np.abs(spectrum[(frequencies >= low_hz) & (frequencies <= high_hz)]) ** 2)
    return 10 * np.log10(max(float(energy), 1e-30))


def test_master_unicode_source_immutable(tmp_path):
    source = tmp_path / "Voix été original.wav"
    original = fixture_audio(source)
    before = digest(source)
    output = tmp_path / "verified master.wav"
    report = render(Project([Track(str(source))]), output)
    assert digest(source) == before
    assert report["verification"]["status"] == "passed"
    assert report["verification"]["samples"]["frames"] == len(original)
    assert abs(float(report["verification"]["measurements"]["input_i"]) + 19) <= 0.5
    assert float(report["verification"]["measurements"]["input_tp"]) <= -0.9
    assert report["mastering_mode"] == "measured-global-gain+linked-true-peak-limiter"
    saved = json.loads(output.with_suffix(".wav.report.json").read_text(encoding="utf-8"))
    assert saved["sha256"] == digest(output)
    assert saved["sources"][0]["path"] == str(source)
    assert not np.allclose(sf.read(output)[0], original)


@pytest.mark.parametrize("extension", ["wav", "flac"])
def test_bypass_stereo_timing_and_relationship(tmp_path, extension):
    source = tmp_path / "stereo.wav"
    samples = fixture_audio(source, seconds=3.137, stereo=True)
    output = tmp_path / f"bypass.{extension}"
    report = render(Project([Track(str(source))], Preset(mode="bypass")), output)
    actual, rate = sf.read(output)
    assert actual.shape == samples.shape and rate == RATE
    assert np.max(np.abs(samples - actual)) < 4e-7
    assert np.max(np.abs(actual[:, 0]*0.5 - actual[:, 1])) < 4e-7
    assert not report["verification"]["loudness_constraints_applied"]


def test_multitrack_offsets_and_end_impulses(tmp_path):
    paths = [tmp_path / f"track{i}.wav" for i in range(2)]
    data = np.zeros(48000, dtype=np.float32)
    data[[0, 2000, -1]] = [0.2, 0.3, 0.4]
    for path in paths:
        sf.write(path, data, RATE, subtype="FLOAT")
    offsets = [0, 12345]
    project = Project([Track(str(p), offset_samples=o) for p, o in zip(paths, offsets)], Preset(mode="bypass"))
    output = tmp_path / "aligned.wav"
    render(project, output)
    actual, _ = sf.read(output)
    expected = np.zeros(60345)
    expected[:48000] += data
    expected[12345:] += data
    assert len(actual) == len(expected)
    assert np.max(np.abs(actual-expected)) < 4e-7


def test_silence_is_exception_without_gain(tmp_path):
    path = tmp_path / "silent.wav"
    sf.write(path, np.zeros(RATE*4), RATE)
    output = tmp_path / "silent-output.wav"
    result = render(Project([Track(str(path))]), output)
    assert result["verification"]["status"] == "exception"
    assert result["mastering_mode"] == "bypass-unmeasurable-loudness"
    assert np.max(np.abs(sf.read(output)[0])) < 4e-7


def test_collisions_and_original_protection(tmp_path):
    path = tmp_path / "original.wav"
    fixture_audio(path, seconds=1)
    project = Project([Track(str(path))])
    before = digest(path)
    with pytest.raises(ValueError, match="source"):
        render(project, path)
    output = tmp_path / "existing.wav"
    output.write_bytes(b"user file")
    with pytest.raises(FileExistsError):
        render(project, output)
    assert output.read_bytes() == b"user file" and digest(path) == before


def test_cancel_and_no_partial_promotion(tmp_path):
    source = tmp_path / "source.wav"
    fixture_audio(source, seconds=30)
    cancel = tmp_path / "cancel"
    def on_event(event):
        if event["stage"] == "processing":
            cancel.touch()
    output = tmp_path / "cancelled.wav"
    with pytest.raises(Cancelled):
        render(Project([Track(str(source))]), output, Runner(on_event, cancel))
    assert not output.exists()
    assert not output.with_suffix(".wav.report.json").exists()
    assert list(tmp_path.glob(".fauxphonic-*"))  # Cancelled-job recovery remains protected.


def test_disk_full_preflight(tmp_path, monkeypatch):
    source = tmp_path / "source.wav"
    fixture_audio(source, seconds=1)
    import shutil
    monkeypatch.setattr(shutil, "disk_usage", lambda p: type("Space", (), {"free": 1})())
    with pytest.raises(OSError, match="scratch"):
        render(Project([Track(str(source))]), tmp_path / "full.wav")
    assert not list(tmp_path.glob("*.render-lock"))


def test_disk_full_during_encoding_withholds_partial_output_and_preserves_recovery(tmp_path):
    source = tmp_path / "original.wav"
    fixture_audio(source, seconds=3)
    before = digest(source)
    class FullDiskRunner(Runner):
        def run(self, args, stage, duration=0):
            if stage == "encoding":
                Path(args[-1]).write_bytes(b"partial encoding")
                raise OSError(28, "No space left on device")
            return super().run(args, stage, duration)
    output = tmp_path / "export.wav"
    with pytest.raises(OSError, match="No space left"):
        render(Project([Track(str(source))]), output, FullDiskRunner())
    assert digest(source) == before
    assert not output.exists() and not output.with_suffix(".wav.report.json").exists()
    assert list(tmp_path.glob(".fauxphonic-*"))
    assert not list(tmp_path.glob("*.render-lock"))


def test_cleanup_is_real_and_stereo_bypassed(tmp_path):
    source = tmp_path / "noise.wav"
    rng = np.random.default_rng(42)
    sf.write(source, rng.normal(0, 0.001, RATE*8), RATE, subtype="FLOAT")
    report = render(Project([Track(str(source), role="speech")], Preset(mode="dsp-cleanup")), tmp_path / "clean.wav")
    assert "afftdn" in report["processing"][0]["cleanup"]
    stereo = tmp_path / "music.wav"
    fixture_audio(stereo, stereo=True)
    report = render(Project([Track(str(stereo), role="music")], Preset(mode="dsp-cleanup", target_lufs=-16)), tmp_path / "music-out.wav")
    assert "bypassed" in report["processing"][0]["cleanup"]


def test_nonfinite_audio_rejected(tmp_path):
    path = tmp_path / "nan.w64"
    sf.write(path, [0, float("nan"), 1], RATE, format="W64", subtype="FLOAT")
    with pytest.raises(ValueError, match="Nonfinite"):
        mix_tracks([path], [Track(str(path))], tmp_path / "mix.wav", Runner())


def test_mix_scratch_is_rf64_and_keeps_odd_mono_length(tmp_path):
    source = tmp_path / "odd-source.w64"
    sf.write(source, [0.1, 0.0, 0.2], RATE, format="W64", subtype="FLOAT")
    output = tmp_path / "mix.wav"
    mix_tracks([source], [Track(str(source))], output, Runner())
    info = sf.info(output)
    assert info.format == "RF64"
    assert info.frames == 3


def test_queue_recovery(tmp_path):
    jobs = Jobs(tmp_path / "jobs.sqlite")
    job = jobs.add({"output": "example.wav"})
    jobs.update(job, {"stage": "mastering"})
    jobs.recover()
    assert jobs.rows()[0][2] == "interrupted"


def test_bad_project_contract(tmp_path):
    path = tmp_path / "source.wav"
    fixture_audio(path, seconds=1)
    with pytest.raises(ValueError):
        Project([Track(str(path), offset_samples=0.5)]).validate()
    with pytest.raises(ValueError):
        Project([Track(str(path))], Preset(target_lufs=float("nan"))).validate()
    with pytest.raises(ValueError, match="De-essing"):
        Project([Track(str(path))], Preset(deesser_range=1.81)).validate()
    with pytest.raises(ValueError, match="Broad EQ"):
        Project([Track(str(path))], Preset(broad_eq_enabled=True, broad_eq_gain_db=3.01)).validate()


def test_broad_eq_applies_bounded_gain_and_reports_parameters(tmp_path):
    time_axis = np.arange(RATE * 4) / RATE
    source_audio = (0.04 * np.sin(2 * np.pi * 120 * time_axis)
                    + 0.04 * np.sin(2 * np.pi * 1000 * time_axis)
                    + 0.04 * np.sin(2 * np.pi * 6500 * time_axis))
    source = tmp_path / "eq-multitone.wav"
    sf.write(source, source_audio, RATE, subtype="FLOAT")
    output = tmp_path / "eq-minus-three.wav"
    preset = Preset(broad_eq_enabled=True, broad_eq_frequency_hz=1000, broad_eq_gain_db=-3)
    report = render(Project([Track(str(source), role="program")], preset), output)
    actual, _ = sf.read(output, dtype="float32")
    before = band_db(source_audio, 1, 3, 950, 1050) - band_db(source_audio, 1, 3, 80, 200)
    after = band_db(actual, 1, 3, 950, 1050) - band_db(actual, 1, 3, 80, 200)
    stage = report["processing"][0]["broad_eq"]
    assert stage["status"] == "applied"
    assert stage["frequency_hz"] == 1000 and stage["gain_db"] == -3 and stage["q"] == 1
    assert after - before == pytest.approx(-3, abs=0.1)


@pytest.mark.parametrize("recording_gain", [1, 0.25, 0.063])
def test_deesser_changes_high_band_on_mono_speech_and_bypasses_ineligible_tracks(tmp_path, recording_gain):
    rng = np.random.default_rng(290926)
    count = RATE * 8
    time_axis = np.arange(count) / RATE
    voiced = (0.055 * np.sin(2 * np.pi * 125 * time_axis)
              + 0.024 * np.sin(2 * np.pi * 250 * time_axis)
              + 0.012 * np.sin(2 * np.pi * 750 * time_axis)
              + 0.009 * np.sin(2 * np.pi * 1250 * time_axis))
    noise = rng.normal(0, 1, count)
    frequencies = np.fft.rfftfreq(count, 1 / RATE)
    spectrum = np.fft.rfft(noise)
    spectrum[(frequencies < 5200) | (frequencies > 9500)] = 0
    frication = np.fft.irfft(spectrum, count)
    frication /= np.sqrt(np.mean(frication ** 2))
    gate = np.zeros(count)
    for left, right in ((RATE, int(1.55 * RATE)), (5 * RATE, int(5.6 * RATE))):
        edge = RATE // 20
        gate[left:left + edge] = np.linspace(0, 1, edge)
        gate[left + edge:right - edge] = 1
        gate[right - edge:right] = np.linspace(1, 0, edge)
    source_audio = recording_gain * (voiced + 0.001 * frication + 0.06 * frication * gate)
    source = tmp_path / "synthetic-mono-speech.wav"
    sf.write(source, source_audio, RATE, subtype="FLOAT")
    off_path, on_path = tmp_path / "deess-off.wav", tmp_path / "deess-on.wav"
    render(Project([Track(str(source), role="speech")], Preset()), off_path)
    report = render(Project([Track(str(source), role="speech")],
                             Preset(deesser_enabled=True, deesser_range=1.8)), on_path)
    before, _ = sf.read(off_path, dtype="float32")
    after, _ = sf.read(on_path, dtype="float32")
    assert len(before) == len(after) == count
    assert np.isfinite(after).all()
    active_before = band_db(before, 1.08, 1.48, 5500, 9000) - band_db(before, 1.08, 1.48, 80, 400)
    active_after = band_db(after, 1.08, 1.48, 5500, 9000) - band_db(after, 1.08, 1.48, 80, 400)
    quiet_before = band_db(before, 2, 4, 5500, 9000) - band_db(before, 2, 4, 80, 400)
    quiet_after = band_db(after, 2, 4, 5500, 9000) - band_db(after, 2, 4, 80, 400)
    assert active_after - active_before < -0.5
    assert quiet_after - quiet_before == pytest.approx(0, abs=0.15)
    assert report["processing"][0]["deesser"]["status"] == "applied"

    stereo = tmp_path / "stereo-speech.wav"
    sf.write(stereo, np.column_stack((source_audio, source_audio * 0.5)), RATE, subtype="FLOAT")
    bypass_report = render(Project([Track(str(stereo), role="speech")],
                                      Preset(deesser_enabled=True)), tmp_path / "deess-stereo.wav")
    outcome = bypass_report["processing"][0]["deesser"]
    assert outcome["status"] == "bypassed"
    assert "mono Speech" in outcome["reason"]


def test_bypass_overload_is_not_silently_clipped(tmp_path):
    source = tmp_path / "overload.wav"
    sf.write(source, np.full(RATE, 0.8), RATE, subtype="FLOAT")
    project = Project([Track(str(source)), Track(str(source))], Preset(mode="bypass"))
    output = tmp_path / "clipped.wav"
    with pytest.raises(ValueError, match="PCM range"):
        render(project, output)
    assert not output.exists()


def test_resampling_44100_preserves_rational_duration(tmp_path):
    source = tmp_path / "44100.wav"
    frames = 44100*4+137
    t = np.arange(frames)/44100
    sf.write(source, 0.08*np.sin(2*np.pi*347*t), 44100)
    output = tmp_path / "48000.wav"
    result = render(Project([Track(str(source))], Preset(mode="bypass")), output)
    assert abs(result["verification"]["samples"]["frames"] - frames*48000/44100) <= 1


@pytest.mark.parametrize("sample_rate", [44100, RATE])
@pytest.mark.parametrize("channels", [1, 2])
@pytest.mark.parametrize("source_frames", [100, 101])
def test_rf64_scratch_preserves_declared_frame_counts(tmp_path, sample_rate, channels, source_frames):
    source = tmp_path / f"odd-{sample_rate}-{channels}-{source_frames}.wav"
    output = tmp_path / f"out-{sample_rate}-{channels}-{source_frames}.wav"
    samples = np.zeros((source_frames, channels), dtype=np.float32)
    samples[0] = 0.1
    samples[-1] = 0.2
    sf.write(source, samples[:, 0] if channels == 1 else samples, sample_rate, subtype="PCM_24")
    assert sf.info(source).frames == source_frames

    report = render(Project([Track(str(source))], Preset(mode="bypass")), output)
    actual, rate = sf.read(output, dtype="float32", always_2d=True)
    expected_frames = (source_frames if sample_rate == RATE else
                       (source_frames * RATE + sample_rate - 1) // sample_rate)
    assert rate == RATE
    assert actual.shape == (expected_frames, channels)
    assert report["verification"]["samples"]["frames"] == expected_frames
    assert np.max(np.abs(actual[-3:])) > 0.05


def test_failed_verification_does_not_promote(tmp_path, monkeypatch):
    source = tmp_path / "source.wav"
    fixture_audio(source)
    import local_audio_studio.engine as engine
    real_verify = engine.verify
    monkeypatch.setattr(engine, "verify", lambda *a: {"status": "failed", "errors": ["Injected measurement failure"]})
    output = tmp_path / "failed.wav"
    with pytest.raises(ValueError, match="not promoted"):
        render(Project([Track(str(source))]), output)
    assert not output.exists()
    assert not output.with_suffix(".wav.report.json").exists()
    diagnostics = list(tmp_path.glob("failed.wav.failed-*.report.json"))
    assert len(diagnostics) == 1
    assert json.loads(diagnostics[0].read_text())["verification"]["status"] == "failed"
    monkeypatch.setattr(engine, "verify", real_verify)
    assert render(Project([Track(str(source))]), output)["verification"]["status"] == "passed"
    assert diagnostics[0].exists()


def test_zero_noise_reduction_is_explicit_bypass(tmp_path):
    source = tmp_path / "speech.wav"
    fixture_audio(source)
    result = render(Project([Track(str(source), role="speech")], Preset(mode="dsp-cleanup", noise_reduction_db=0)), tmp_path / "zero.wav")
    assert "0 dB" in result["processing"][0]["cleanup"]


def test_cleanup_keeps_mixed_track_alignment_and_endpoints(tmp_path):
    speech = tmp_path / "speech.wav"
    program = tmp_path / "program.wav"
    n = RATE * 4
    t = np.arange(n) / RATE
    voice = (0.04 * np.sin(2 * np.pi * 300 * t)).astype(np.float32)
    voice[:RATE // 10] = 0
    voice[-RATE // 10:] = 0
    voice[-RATE // 10 - 1] = 0.25
    bed = np.zeros(n, dtype=np.float32)
    bed[[500, 1700, -501]] = [0.02, -0.03, 0.04]
    sf.write(speech, voice, RATE, subtype="FLOAT")
    sf.write(program, bed, RATE, subtype="FLOAT")
    result = render(Project([Track(str(speech), role="speech"), Track(str(program))],
                             Preset(mode="dsp-cleanup")), tmp_path / "mixed.wav")
    actual, _ = sf.read(tmp_path / "mixed.wav")
    # The uncleaned track stays on the project timeline while cleanup runs on speech.
    positions = np.array([500, 1700, n - 501])
    ratios = actual[positions] / bed[positions]
    assert np.allclose(ratios, ratios[0], rtol=0.03)
    assert np.sqrt(np.mean(actual[RATE // 10:RATE // 10 + RATE // 100] ** 2)) > 1e-4
    assert len(actual) == n
    assert result["verification"]["status"] in {"passed", "exception"}


@pytest.mark.parametrize("source_rate", [RATE, 44100])
def test_cleanup_burst_timing_and_full_tail_at_source_rates(tmp_path, source_rate):
    source = tmp_path / f"speech-{source_rate}.wav"
    output = tmp_path / f"clean-{source_rate}.wav"
    rng = np.random.default_rng(2026)
    n = source_rate * 8
    audio = np.zeros(n, dtype=np.float32)
    audio[source_rate: 7 * source_rate] = rng.normal(0, 0.04, 6 * source_rate)
    audio[-source_rate:] = rng.normal(0, 0.04, source_rate)
    sf.write(source, audio, source_rate, subtype="FLOAT")
    result = render(Project([Track(str(source), role="speech")], Preset(mode="dsp-cleanup")), output)
    actual, _ = sf.read(output)
    frame = RATE // 100
    active = [np.sqrt(np.mean(actual[i:i + frame] ** 2)) for i in range(0, RATE, frame)]
    active_at = next(i * frame for i, rms in enumerate(active) if rms > max(active) * 0.01)
    assert active_at <= RATE + frame
    assert len(actual) == result["verification"]["samples"]["frames"]
    assert len(actual) == round(n * RATE / source_rate)
    tail = actual[-RATE // 100:]
    assert np.sqrt(np.mean(tail * tail)) > max(np.sqrt(np.mean(actual * actual)) * 0.01, 1e-5)


def test_selected_container_stream_start_times_are_applied(tmp_path):
    container = tmp_path / "late-stream.mkv"
    output = tmp_path / "streams.wav"
    command = [binary("ffmpeg"), "-hide_banner", "-nostdin", "-y",
               "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=3",
               "-itsoffset", "1.5", "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000:duration=1.5",
               "-map", "0:a:0", "-map", "1:a:0", "-c:a", "pcm_s16le", str(container)]
    subprocess.run(command, check=True, capture_output=True)
    result = render(Project([Track(str(container), stream=0), Track(str(container), stream=1)],
                            Preset(mode="bypass")), output)
    timeline = result["timeline"]
    assert timeline[0]["offset_samples"] == 0
    assert timeline[1]["offset_samples"] == RATE * 3 // 2
    assert result["verification"]["samples"]["frames"] == RATE * 3


def test_final_report_meter_and_gain_cap(tmp_path):
    source = tmp_path / "quiet.wav"
    rng = np.random.default_rng(7)
    sf.write(source, rng.normal(0, 1e-4, RATE * 4), RATE, subtype="FLOAT")
    result = render(Project([Track(str(source))]), tmp_path / "quiet-out.wav")
    actual, _ = sf.read(tmp_path / "quiet-out.wav")
    assert result["mastering_mode"].startswith("bypass-")
    assert result["verification"]["status"] == "exception"
    assert np.max(np.abs(actual)) < 0.01
    assert result["verification"]["measurements"]["meter"] == "FFmpeg ebur128 BS.1770"


def test_final_true_peak_meter_flushes_end_of_file(tmp_path):
    source = tmp_path / "end-peak.wav"
    audio = np.zeros(RATE * 4, dtype=np.float32)
    audio[-64:] = np.sin(np.arange(64) * np.pi / 2) * 0.83
    sf.write(source, audio, RATE, subtype="FLOAT")
    from local_audio_studio.engine import verify
    result = verify(source, Preset(target_lufs=-10, true_peak=-1))
    sample_db = result["samples"]["sample_peak_dbfs"]
    assert result["measurements"]["input_tp"] >= sample_db - 0.11


def test_independent_meter_and_bounded_retry_accept_compliant_speechlike_export(tmp_path):
    source = tmp_path / "peaky-speechlike.wav"
    sf.write(source, speechlike_fixture(30, seed=0, level=0.02, spikes=6), RATE, subtype="FLOAT")
    report = render(Project([Track(str(source))], Preset(target_lufs=-19, true_peak=-1)),
                    tmp_path / "peaky-master.wav")
    assert report["verification"]["status"] == "passed"
    assert abs(report["verification"]["measurements"]["input_i"] + 19) <= 0.5
    assert report["verification"]["measurements"]["input_tp"] <= -0.9
    assert report["correction_attempts"] <= 6


def test_stereo_resample_true_peak_overshoot_gets_bounded_corrective_master(tmp_path):
    mono = speechlike_fixture(20, seed=6, level=0.05, spikes=4)
    source = tmp_path / "stereo-half-left.wav"
    sf.write(source, np.column_stack((mono * 0.5, mono)), RATE, subtype="FLOAT")
    report = render(Project([Track(str(source))], Preset(target_lufs=-16, true_peak=-1)),
                    tmp_path / "stereo-safe.wav")
    assert report["correction_attempts"] <= 6
    assert report["corrective_mastering_mode"] in {None, report["mastering_mode"]}
    assert report["verification"]["status"] == "passed"
    assert abs(report["verification"]["measurements"]["input_i"] + 16) <= 0.5
    assert report["verification"]["measurements"]["input_tp"] <= -0.9
    decoded, _ = sf.read(tmp_path / "stereo-safe.wav")
    active = np.abs(decoded[:, 1]) > 1e-2
    ratio_error = np.abs(decoded[active, 0] / decoded[active, 1] - 0.5)
    assert np.quantile(ratio_error, 0.99) < 1e-5


def test_linked_master_keeps_stereo_endpoint_impulses_and_exact_duration(tmp_path):
    mono = speechlike_fixture(8, seed=71, level=0.025, spikes=3)
    mono[0] = 0.8
    mono[-1] = -0.75
    source = tmp_path / "stereo-endpoints.wav"
    sf.write(source, np.column_stack((mono * 0.25, mono)), RATE, subtype="FLOAT")
    output = tmp_path / "stereo-endpoints-master.wav"
    report = render(Project([Track(str(source))], Preset(target_lufs=-19, true_peak=-1)), output)
    actual, sample_rate = sf.read(output)
    assert sample_rate == RATE
    assert len(actual) == len(mono)
    assert abs(actual[0, 1]) > 0.1 and abs(actual[-1, 1]) > 0.1
    assert actual[0, 0] / actual[0, 1] == pytest.approx(0.25, abs=2e-6)
    assert actual[-1, 0] / actual[-1, 1] == pytest.approx(0.25, abs=2e-6)
    assert report["verification"]["status"] == "passed"


def test_corrective_master_restarts_from_lossless_mix(tmp_path, monkeypatch):
    import local_audio_studio.engine as engine

    source = tmp_path / "source.wav"
    sf.write(source, speechlike_fixture(8, seed=19, level=0.025, spikes=2), RATE, subtype="FLOAT")
    real_verify = engine.verify
    real_master = engine._render_linked_master
    verify_calls = 0
    master_sources = []

    def force_first_meter_failure(*args, **kwargs):
        nonlocal verify_calls
        verify_calls += 1
        measured = real_verify(*args, **kwargs)
        if verify_calls == 1:
            measured["status"] = "failed"
            measured["errors"] = ["True peak exceeds the test limit"]
        return measured

    def observe_master_input(input_path, *args, **kwargs):
        master_sources.append(Path(input_path).name)
        return real_master(input_path, *args, **kwargs)

    monkeypatch.setattr(engine, "verify", force_first_meter_failure)
    monkeypatch.setattr(engine, "_render_linked_master", observe_master_input)
    report = render(Project([Track(str(source))], Preset(target_lufs=-19, true_peak=-1)),
                    tmp_path / "corrected.wav")
    assert report["correction_attempts"] == 1
    assert "from the original lossless mix" in report["correction_method"]
    assert master_sources == ["mix.wav", "mix.wav"]
    assert report["verification"]["status"] == "passed"


def test_true_peak_report_does_not_substitute_sample_peak(tmp_path, monkeypatch):
    import local_audio_studio.engine as engine

    source = tmp_path / "tone.wav"
    sf.write(source, np.full(RATE * 4, 0.8), RATE, subtype="FLOAT")
    monkeypatch.setattr(engine, "final_meter", lambda *args: {"input_i": -2.0, "input_tp": -6.0,
                                                                "meter": "injected"})
    report = engine.verify(source, Preset(mode="bypass"))
    assert report["measurements"]["input_tp"] == -6.0
    assert report["samples"]["sample_peak_dbfs"] > -2.0
    assert report["status"] == "failed"
    assert any("meter inconsistency" in error for error in report["errors"])


@pytest.mark.parametrize("defaults", [True, False])
def test_silero_phrase_leveler_reduces_track_level_gap_without_lifting_silence(tmp_path, defaults):
    fixture = Path(__file__).parent / "fixtures" / "silero-leveling-tts.wav"
    speech, sample_rate = sf.read(fixture, dtype="float32")
    quiet_gap = np.random.default_rng(11).normal(0, 0.001, RATE * 2).astype(np.float32)
    lead = np.zeros(RATE // 2, dtype=np.float32)
    tail = np.zeros(RATE, dtype=np.float32)
    quiet_start = len(lead) + len(speech) + len(quiet_gap)
    source_audio = np.concatenate((lead, speech * 0.15, quiet_gap, speech * 0.0375, tail))
    source = tmp_path / "two-level-tts.wav"
    sf.write(source, source_audio, sample_rate, subtype="FLOAT")
    base_project = Project([Track(str(source), role="speech")], Preset(mode="bypass"))
    output_off = tmp_path / "leveler-off.wav"
    render(base_project, output_off)
    preset = Preset(mode="master-only", leveler_enabled=True, leveler_strength=1,
                    preserve_dynamics=0, max_boost_db=9, max_attenuation_db=12)
    if defaults:
        preset = Preset(leveler_enabled=True)
    output_on = tmp_path / "leveled.wav"
    result = render(Project([Track(str(source), role="speech")], preset, export_stems=True), output_on)
    assert result["leveling"]["enabled"]
    assert result["leveling"]["analysis_wall_seconds"] >= result["leveling"]["vad_analysis_wall_seconds"]
    assert result["leveling"]["leveler_analysis_wall_seconds"] > 0
    assert result["leveling"]["model_sha256"] == "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3"
    regions = result["leveling"]["tracks"][0]["regions"]
    first = max((region for region in regions if region["end_sample"] < quiet_start - RATE),
                key=lambda region: region["end_sample"] - region["start_sample"])
    second = max((region for region in regions if region["start_sample"] >= quiet_start),
                 key=lambda region: region["end_sample"] - region["start_sample"])
    left = first["start_sample"] + RATE // 2
    right = first["end_sample"] - RATE // 2
    quiet_left = second["start_sample"] + RATE // 2
    quiet_right = second["end_sample"] - RATE // 2
    assert right > left and quiet_right > quiet_left
    base, _ = sf.read(output_off)
    # Pre-master stem isolates the leveler's gain from whole-program mastering.
    actual, _ = sf.read(result["stems"][0]["path"])
    rms = lambda audio, start, end: float(np.sqrt(np.mean(audio[start:end] ** 2)))
    before = abs(20 * np.log10(rms(base, left, right) / rms(base, quiet_left, quiet_right)))
    after = abs(20 * np.log10(rms(actual, left, right) / rms(actual, quiet_left, quiet_right)))
    assert before >= 11 and after < (6 if defaults else 4)
    noise_before = rms(base, len(lead) + len(speech) + RATE // 5,
                       len(lead) + len(speech) + len(quiet_gap) - RATE // 5)
    noise_after = rms(actual, len(lead) + len(speech) + RATE // 5,
                      len(lead) + len(speech) + len(quiet_gap) - RATE // 5)
    assert abs(20 * np.log10(noise_after / noise_before)) < 0.1
    assert np.max(np.abs(actual[:RATE // 3])) <= 2e-7
    assert np.max(np.abs(actual[-RATE // 2:])) <= 2e-7


def test_silero_phrase_metadata_and_memory_remain_bounded_at_one_minute(tmp_path):
    fixture = Path(__file__).parent / "fixtures" / "silero-leveling-tts.wav"
    speech, sample_rate = sf.read(fixture, dtype="float32")
    unit = np.concatenate((np.zeros(RATE // 2), speech, np.zeros(RATE)))
    short_source = tmp_path / "short.wav"
    long_source = tmp_path / "long.wav"
    sf.write(short_source, unit, sample_rate, subtype="FLOAT")
    repeats = math.ceil((RATE * 60) / len(unit))
    sf.write(long_source, np.tile(unit, repeats), sample_rate, subtype="FLOAT")
    preset = Preset(mode="master-only", leveler_enabled=True, leveler_strength=0.5)
    short = render(Project([Track(str(short_source), role="speech")], preset), tmp_path / "short-out.wav")
    long = render(Project([Track(str(long_source), role="speech")], preset), tmp_path / "long-out.wav")
    assert long["verification"]["samples"]["frames"] >= RATE * 60
    assert long["leveling"]["tracks"][0]["regions"]
    assert long["peak_process_tree_rss_bytes"] < short["peak_process_tree_rss_bytes"] + 64 * 1024**2


def test_failure_report_does_not_replace_concurrent_file(tmp_path, monkeypatch):
    source = tmp_path / "source.wav"
    fixture_audio(source)
    import local_audio_studio.engine as engine
    output = tmp_path / "race.wav"
    report_path = output.with_suffix(".wav.report.json")
    def fail_and_create_report(*args):
        report_path.write_text("user data", encoding="utf-8")
        return {"status": "failed", "errors": ["injected"]}
    monkeypatch.setattr(engine, "verify", fail_and_create_report)
    with pytest.raises(ValueError, match="not promoted"):
        render(Project([Track(str(source))]), output)
    assert report_path.read_text(encoding="utf-8") == "user data"
    assert not output.exists()
