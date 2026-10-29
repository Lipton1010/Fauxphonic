from pathlib import Path
import json

import numpy as np
import pytest
import soundfile as sf

from local_audio_studio.audio import Runner, digest, probe
from local_audio_studio.contracts import Preset, Project, Track
from local_audio_studio.cli import analyze
from local_audio_studio.engine import render, verify
from local_audio_studio.exports import verification_audio


@pytest.mark.parametrize("suffix", [".wav", ".flac", ".mp3", ".m4a", ".opus"])
@pytest.mark.parametrize("target", [-19, -16, -14])
def test_speech_delivery_reaches_target_without_changing_original(tmp_path, suffix, target):
    source = Path(__file__).parent / "fixtures" / "silero-leveling-tts.wav"
    before = digest(source)
    output = tmp_path / ("speech" + suffix)
    report = render(Project([Track(str(source))], Preset(target_lufs=target)), output)
    measurement = report["verification"]["measurements"]
    assert report["verification"]["status"] == "passed"
    assert abs(measurement["input_i"] - target) <= 0.5
    assert measurement["input_tp"] <= -0.9
    assert report["verification"]["samples"]["frames"] == sf.info(source).frames
    assert digest(source) == before


@pytest.mark.parametrize("suffix", [".wav", ".mp3", ".m4a"])
def test_silent_delivery_has_finite_json_and_explicit_exception(tmp_path, suffix):
    source = tmp_path / "silence.wav"
    sf.write(source, np.zeros(480000), 48000, subtype="PCM_24")
    output = tmp_path / ("silent-master" + suffix)
    report = render(Project([Track(str(source))]), output)
    assert output.exists()
    assert report["verification"]["status"] == "exception"
    peak = report["verification"]["samples"]["sample_peak_dbfs"]
    assert peak is None or peak < -120  # Lossless PCM can contain quantization dither.
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize("container", ["WAV", "RF64", "W64"])
def test_truncated_wave_containers_rejected_before_render(tmp_path, container):
    source = tmp_path / ("original." + container.lower())
    sf.write(source, np.ones(48000)*0.1, 48000, format=container, subtype="PCM_24")
    assert probe(source)["stream"]["channels"] == 1
    data = source.read_bytes()
    source.write_bytes(data[:len(data)//2])
    output = tmp_path / "result.wav"
    with pytest.raises(ValueError, match="[Tt]runcated|[Ii]ncomplete"):
        render(Project([Track(str(source))]), output)
    assert not output.exists()
    assert not output.with_suffix(".wav.report.json").exists()


@pytest.mark.parametrize("suffix", [".m4a", ".mp3", ".opus"])
def test_actual_lossy_exports_exact_presentation_and_metadata(tmp_path, suffix):
    source = tmp_path / "original.wav"
    frames = 480001
    time = np.arange(frames)/48000
    mono = (0.2*np.sin(2*np.pi*319*time) + 0.08*np.sin(2*np.pi*1907*time)).astype(np.float32)
    sf.write(source, np.column_stack((mono, mono*0.37)), 48000, subtype="PCM_24")
    before = digest(source)
    output = tmp_path / ("delivery" + suffix)
    project = Project([Track(str(source))], Preset(target_lufs=-16),
                      metadata={"title": "Épisode — test", "artist": "Fauxphonic fixture"},
                      export_stems=suffix == ".mp3")
    report = render(project, output)
    assert report["verification"]["status"] == "passed"
    assert report["verification"]["samples"]["frames"] == frames
    assert digest(source) == before
    tags = probe(output)
    normalized = {key.lower(): value for container in [tags["format"], tags["stream"]]
                  for key, value in container.get("tags", {}).items()}
    assert normalized["title"] == project.metadata["title"]
    assert normalized["artist"] == project.metadata["artist"]
    with verification_audio(output, Runner()) as (decoded, timing):
        audio, rate = sf.read(decoded, always_2d=True)
        assert len(audio) == frames and rate == 48000
        assert np.sqrt(np.mean(audio[:240]**2)) > 0.01
        assert np.sqrt(np.mean(audio[-240:]**2)) > 0.01
        assert np.corrcoef(audio[:, 0], audio[:, 1])[0, 1] > 0.99
        assert 0 <= timing["codec_tail_padding_frames"] < 1024
    # A different expected endpoint must fail; no blind crop to the requested length.
    assert verify(output, project.preset, expected_frames=frames-1)["status"] == "failed"
    if project.export_stems:
        stem = Path(report["stems"][0]["path"])
        assert stem.suffix == ".wav" and sf.info(stem).frames == frames
    if suffix == ".m4a":
        analysis = analyze(output, Runner(), scratch_root=tmp_path / "analysis")
        assert analysis["frames"] == frames
        assert analysis["decode_timing"]["codec_tail_padding_frames"] > 0
        imported = render(Project([Track(str(output))], Preset(mode="bypass")), tmp_path / "reimport.wav")
        assert imported["verification"]["samples"]["frames"] == frames


def test_metadata_validation_and_lossless_roundtrip(tmp_path):
    source = tmp_path / "source.wav"
    sf.write(source, np.zeros(48001, dtype=np.float32), 48000)
    project = Project([Track(str(source))], Preset(mode="bypass"), metadata={"title": "Local test"})
    output = tmp_path / "tagged.wav"
    report = render(project, output)
    assert report["delivery"]["metadata"] == project.metadata
    assert probe(output)["format"]["tags"]["title"] == "Local test"
    project.metadata["unsupported"] = "value"
    with pytest.raises(ValueError, match="Metadata"):
        project.validate()
