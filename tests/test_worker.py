import json
import os
from pathlib import Path
import subprocess
import sys
import time

import numpy as np
import psutil
import soundfile as sf

from local_audio_studio.contracts import Project, Track
from local_audio_studio import lifecycle


def test_crashed_worker_kills_children_and_does_not_promote(tmp_path):
    source = tmp_path / "long.wav"
    with sf.SoundFile(source, "w", samplerate=48000, channels=1, subtype="PCM_16") as audio:
        chunk = np.sin(np.arange(48000)*2*np.pi*233/48000)*0.05
        for _ in range(600):
            audio.write(chunk)
    output = tmp_path / "never-promoted.wav"
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"action": "render", "project": Project([Track(str(source))]).to_dict(),
                                   "output": str(output)}))
    root = Path(__file__).resolve().parents[1]
    process = subprocess.Popen([sys.executable, str(root / "run_worker.py"), "worker", str(request)],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    children = []
    try:
        deadline = time.monotonic()+15
        while time.monotonic() < deadline and process.poll() is None:
            children = psutil.Process(process.pid).children(recursive=True)
            if any(p.name().lower() == "ffmpeg.exe" for p in children):
                break
            time.sleep(0.02)
        assert children, process.communicate(timeout=1) if process.poll() is not None else "No child observed"
        process.kill()
        process.wait(timeout=5)
        _, alive = psutil.wait_procs(children, timeout=5)
        assert not alive
        assert not output.exists()
        assert not output.with_suffix(".wav.report.json").exists()
        assert not output.with_name(output.name + ".render-lock").exists()
        stale = list(tmp_path.glob(".fauxphonic-*"))
        assert stale
        unrelated = tmp_path / ".fauxphonic-user-recordings"
        unrelated.mkdir()
        (unrelated / "keep.txt").write_text("user data")
        lifecycle.sweep_render_scratch(tmp_path)
        assert all(folder.exists() for folder in stale)  # Interrupted recovery is preserved.
        assert (unrelated / "keep.txt").read_text() == "user data"
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=5)


def test_startup_sweeps_only_dead_owned_scratch_and_requests(tmp_path, monkeypatch):
    scratch = tmp_path / "scratch"
    active = scratch / "fauxphonic-analysis-active"
    stale = scratch / "fauxphonic-analysis-stale"
    active.mkdir(parents=True)
    stale.mkdir()
    (active / "owner.pid").write_text("101")
    (stale / "owner.pid").write_text("202")
    monkeypatch.setattr(lifecycle.psutil, "pid_exists", lambda pid: pid == 101)
    lifecycle.sweep_analysis_scratch(scratch)
    assert active.exists()
    assert not stale.exists()

    key = "a" * 32
    request = tmp_path / f"{key}.request.json"
    marker = tmp_path / f"{key}.worker.pid"
    cancel = tmp_path / f"{key}.cancel"
    request.write_text(json.dumps({"cancel_file": str(cancel)}))
    marker.write_text("202")
    cancel.touch()
    lifecycle.sweep_requests(tmp_path)
    assert not request.exists()
    assert not marker.exists()
    assert not cancel.exists()


def test_cli_natural_speech_uses_named_leveler_preset_and_explicit_targets(tmp_path, monkeypatch):
    from local_audio_studio import cli

    source = Path(__file__).parent / "fixtures" / "silero-leveling-tts.wav"
    output = tmp_path / "natural-speech.wav"
    events = []
    monkeypatch.setattr(cli, "emit", events.append)
    result = cli.main(["process", str(source), "--output", str(output), "--preset", "natural-speech",
                       "--target-lufs", "-20", "--true-peak", "-2"])

    assert result == 0, events
    assert output.is_file()
    report_path = output.with_suffix(".wav.report.json")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["project"]["preset"]["leveler_enabled"] is True
    assert report["project"]["preset"]["target_lufs"] == -20
    assert report["project"]["preset"]["true_peak"] == -2
    assert report["project"]["tracks"][0]["role"] == "speech"
    assert report["leveling"]["enabled"] is True
    assert report["leveling"]["model_sha256"]
    assert events[-1]["stage"] == "complete"
