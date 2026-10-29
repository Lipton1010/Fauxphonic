import json
from pathlib import Path

import numpy as np
import soundfile as sf

from local_audio_studio.audio import Runner
from local_audio_studio.cli import analyze


def test_analysis_speech_energy_and_bounded_waveforms(tmp_path):
    source = Path(__file__).parent / "fixtures/silero-leveling-tts.wav"
    report = analyze(source, Runner(), scratch_root=tmp_path / "scratch")
    assert report["frames"] == sf.info(source).frames
    assert report["speech_activity"]["status"] == "measured"
    assert 0 < report["levels"]["speech_active_samples"] < report["frames"]
    noise = report["levels"]["nonspeech_rms_dbfs"]
    assert noise is None or report["levels"]["speech_active_rms_dbfs"] > noise
    assert len(report["waveform"]) <= 1200
    assert [len(level) for level in report["waveform_levels"]] == [len(report["waveform"]),
        (len(report["waveform"])+3)//4, (len(report["waveform"])+15)//16]
    json.dumps(report, allow_nan=False)


def test_analysis_stereo_endpoints_and_clipping_candidates(tmp_path):
    source = tmp_path / "stereo.wav"
    samples = np.zeros((48001, 2), dtype=np.float32)
    samples[0, 0] = 1.0
    samples[-1, 1] = -1.0
    sf.write(source, samples, 48000, subtype="FLOAT")
    report = analyze(source, Runner(), scratch_root=tmp_path / "scratch")
    assert report["frames"] == len(samples)
    assert report["levels"]["near_full_scale_frames"] == 2
    assert report["levels"]["first_near_full_scale_samples"] == [0, 48000]
    assert report["levels"]["sample_peak"] == 1.0
    assert report["waveform"][0] == report["waveform"][-1] == 1.0
    assert report["levels"]["speech_active_rms_dbfs"] is None
    assert report["speech_activity"]["status"].startswith("not run")
    assert np.isclose(report["levels"]["program_rms_dbfs"], 10*np.log10(2/samples.size))
    json.dumps(report, allow_nan=False)
