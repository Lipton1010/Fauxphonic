import json
import os
from pathlib import Path
import shutil

import numpy as np
import soundfile as sf

from local_audio_studio.audio import Runner
from local_audio_studio.contracts import Preset, Project, Track, RATE
from local_audio_studio import preview as preview_module
from local_audio_studio.preview import create_preview


def test_short_preview_clamps_to_endpoint_and_reuses_actual_length(tmp_path, monkeypatch):
    source = tmp_path / "short.wav"
    original = np.sin(np.arange(RATE*2 + 17)*2*np.pi*330/RATE)*0.1
    sf.write(source, original, RATE, subtype="PCM_24")
    project = Project([Track(str(source))], Preset(mode="bypass"))
    data = tmp_path / "app"
    result = create_preview(project, RATE, RATE*15, data/"preview-cache", data/"preview-work",
        data/"previews"/("preview-active-" + "a"*32), os.getpid())
    assert result["duration_samples"] == RATE + 17
    samples, _ = sf.read(result["processed"])
    assert len(samples) == RATE + 17
    assert np.allclose(samples, original[RATE:], atol=5e-7, rtol=0)
    monkeypatch.setattr(preview_module, "render", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("cache missed")))
    cached = create_preview(project, RATE, RATE*15, data/"preview-cache", data/"preview-work",
        data/"previews"/("preview-active-" + "b"*32), os.getpid())
    assert cached["duration_samples"] == RATE + 17


def test_preview_is_aligned_cached_crop_of_full_render(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_MEDIA_BACKEND", "windows")
    source = tmp_path / "speech.wav"
    samples = np.arange(25 * RATE, dtype=np.float32)
    sf.write(source, 0.08 * np.sin(2 * np.pi * 337 * samples / RATE), RATE)
    project = Project([Track(str(source), role="speech")], Preset())
    data = tmp_path / "app-data"
    cache = data / "preview-cache"
    work = data / "preview-work"
    active = data / "previews" / ("preview-active-" + "a" * 32)
    full_render = tmp_path / "full-render.wav"
    render = preview_module.render
    def capture_full_render(*args, **kwargs):
        report = render(*args, **kwargs)
        shutil.copyfile(args[1], full_render)
        return report
    monkeypatch.setattr(preview_module, "render", capture_full_render)
    result = create_preview(project, 5 * RATE + 37, 10 * RATE, cache, work, active, os.getpid(), Runner())

    with sf.SoundFile(result["original"]) as original, sf.SoundFile(result["processed"]) as processed:
        assert original.samplerate == processed.samplerate == RATE
        assert len(original) == len(processed) == 10 * RATE
        assert original.channels == processed.channels == 1
    manifest = json.loads((cache / result["cache_key"] / "manifest.json").read_text())
    assert manifest["start_sample"] == 5 * RATE + 37
    assert manifest["processed_is_crop_of_full_render"] is True
    assert "mastering_mode" in result
    full_samples, _ = sf.read(full_render, dtype="float32", always_2d=True)
    preview_samples, _ = sf.read(result["processed"], dtype="float32", always_2d=True)
    assert np.array_equal(preview_samples, full_samples[5 * RATE + 37:15 * RATE + 37])

    second_active = data / "previews" / ("preview-active-" + "b" * 32)
    def should_use_cache(*args, **kwargs):
        raise AssertionError("A valid preview cache entry should avoid rendering again")
    monkeypatch.setattr("local_audio_studio.preview.render", should_use_cache)
    cached = create_preview(project, 5 * RATE + 37, 10 * RATE, cache, work, second_active, os.getpid(), Runner())
    assert cached["cache_key"] == result["cache_key"]
    assert Path(cached["processed"]).read_bytes() == Path(result["processed"]).read_bytes()


def test_preview_preserves_odd_mono_source_endpoint(tmp_path):
    source = tmp_path / "odd-mono.wav"
    samples = np.zeros(10 * RATE + 1, dtype=np.float32)
    samples[0] = 0.1
    samples[-1] = 0.2
    sf.write(source, samples, RATE, subtype="PCM_24")
    project = Project([Track(str(source))], Preset(mode="bypass"))
    data = tmp_path / "app-data"
    result = create_preview(project, 0, len(samples), data / "preview-cache", data / "preview-work",
                            data / "previews" / ("preview-active-" + "f" * 32), os.getpid(), Runner())

    with sf.SoundFile(result["original"]) as original, sf.SoundFile(result["processed"]) as processed:
        assert len(original) == len(processed) == len(samples)
        assert original.channels == processed.channels == 1
        assert original.read(len(samples), dtype="float32")[-1] != 0
        assert processed.read(len(samples), dtype="float32")[-1] != 0


def test_preview_44100_odd_frame_estimate_is_conservative(tmp_path):
    source = tmp_path / "preview-441001.wav"
    source_frames = 441001
    samples = np.zeros(source_frames, dtype=np.float32)
    samples[0] = 0.1
    samples[-1] = 0.2
    sf.write(source, samples, 44100, subtype="PCM_24")
    project = Project([Track(str(source))], Preset(mode="bypass"))
    data = tmp_path / "app-data"
    result = create_preview(project, 2, 480000, data / "preview-cache", data / "preview-work",
                            data / "previews" / ("preview-active-" + "1" * 32), os.getpid(), Runner())

    with sf.SoundFile(result["original"]) as original, sf.SoundFile(result["processed"]) as processed:
        assert len(original) == len(processed) == 480000
        assert original.channels == processed.channels == 1


def test_corrupt_owned_cache_is_quarantined_and_rebuilt_unknown_entry_is_preserved(tmp_path, monkeypatch):
    source = tmp_path / "speech.wav"
    samples = np.arange(22 * RATE, dtype=np.float32)
    sf.write(source, 0.06 * np.sin(2 * np.pi * 239 * samples / RATE), RATE)
    project = Project([Track(str(source), role="speech")], Preset())
    data = tmp_path / "app-data"
    cache_root = data / "preview-cache"
    work_root = data / "preview-work"
    cache_root.mkdir(parents=True)
    first_active = data / "previews" / ("preview-active-" + "c" * 32)
    result = create_preview(project, 2 * RATE, 10 * RATE, cache_root, work_root,
                            first_active, os.getpid(), Runner())
    cache = cache_root / result["cache_key"]
    manifest_path = cache / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    (cache / "original.wav").write_bytes(b"malformed WAV payload")
    manifest["sha256"]["original.wav"] = preview_module.digest(cache / "original.wav")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert not preview_module._valid_cache(cache, result["cache_key"], 2 * RATE, 10 * RATE)

    real_render = preview_module.render
    def check_quarantine(*args, **kwargs):
        assert list(cache_root.glob(f".{result['cache_key']}.corrupt-*"))
        return real_render(*args, **kwargs)
    monkeypatch.setattr(preview_module, "render", check_quarantine)
    second_active = data / "previews" / ("preview-active-" + "d" * 32)
    rebuilt = create_preview(project, 2 * RATE, 10 * RATE, cache_root, work_root,
                             second_active, os.getpid(), Runner())
    assert rebuilt["cache_key"] == result["cache_key"]
    assert preview_module._valid_cache(cache, result["cache_key"], 2 * RATE, 10 * RATE)
    assert not list(cache_root.glob(f".{result['cache_key']}.corrupt-*"))

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["owner"] = "user-owned"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    preserved = manifest_path.read_bytes()
    def refuse_rerender(*args, **kwargs):
        raise AssertionError("An unrecognized cache entry must be preserved without rerendering")
    monkeypatch.setattr(preview_module, "render", refuse_rerender)
    third_active = data / "previews" / ("preview-active-" + "e" * 32)
    try:
        create_preview(project, 2 * RATE, 10 * RATE, cache_root, work_root,
                       third_active, os.getpid(), Runner())
    except FileExistsError as exc:
        assert "preserved" in str(exc)
    else:
        raise AssertionError("Unrecognized cache collision should be refused")
    assert manifest_path.read_bytes() == preserved
