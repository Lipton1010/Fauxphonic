"""Build short preview pairs from full-program renders and an aligned source mix."""
from importlib.metadata import PackageNotFoundError, version
from dataclasses import replace
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
import uuid
from fractions import Fraction

import soundfile as sf

from . import __version__
from .audio import Runner, binary, decode_source, digest, final_meter, probe
from .contracts import Project, RATE
from .engine import effective_timeline_tracks, inspect_samples, mix_tracks, render
from .windows_job import reserve_output

CACHE_LIMIT = 512 * 1024 * 1024
OWNER = "fauxphonic-preview-v1"


def _estimated_project_frames(source):
    """Conservative preflight estimate; exact decoded frames are checked later."""
    stream = source["probe"]["stream"]
    duration = None
    if stream.get("duration_ts") is not None and stream.get("time_base"):
        duration = Fraction(int(stream["duration_ts"])) * Fraction(stream["time_base"])
    if duration is None:
        duration = Fraction(str(source["probe"]["duration"]))
    # Container duration can exceed a selected stream's duration. Preflight may
    # overestimate, but must never reject a range that decoded audio can cover.
    duration = max(duration, Fraction(str(source["probe"]["duration"])))
    project_frames = duration * RATE
    return (project_frames.numerator + project_frames.denominator - 1) // project_frames.denominator


def _cache_key(project, sources, start_sample, duration_samples):
    material = {"owner": OWNER, "engine": __version__, "project": project.to_dict(),
                "sources": [{"path": s["path"], "sha256": s["sha256"]} for s in sources],
                "tools": {name: digest(binary(name)) for name in ("ffmpeg", "ffprobe")},
                "start_sample": start_sample, "duration_samples": duration_samples}
    if (project.preset.leveler_enabled or project.preset.music_ducking_enabled
            or project.preset.bleed_reduction_enabled or project.preset.mode == "dsp-cleanup"):
        from .leveling import model_path
        try:
            runtime = version("onnxruntime")
        except PackageNotFoundError:
            runtime = "unavailable"
        vad_model = model_path()
        material["speech_activity"] = {"model_path": str(vad_model), "model_sha256": digest(vad_model),
                                        "runtime_version": runtime}
    return hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode("utf-8")).hexdigest(), material


def _valid_cache(folder, key, start_sample, duration_samples):
    try:
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        if (manifest.get("owner") != OWNER or manifest.get("key") != key
                or manifest.get("start_sample") != start_sample
                or not 0 < manifest.get("duration_samples", 0) <= duration_samples
                or manifest.get("processed_is_crop_of_full_render") is not True
                or not isinstance(manifest.get("leveling"), dict)
                or {item.name for item in folder.iterdir()} != {"manifest.json", "original.wav", "processed.wav"}):
            return False
        expected_channels = manifest["channels"]
        for name in ("original.wav", "processed.wav"):
            path = folder / name
            if digest(path) != manifest["sha256"][name]:
                return False
            info = sf.info(path)
            if info.samplerate != RATE or info.frames != manifest["duration_samples"] or info.channels != expected_channels:
                return False
        return True
    except (OSError, ValueError, KeyError, TypeError, RuntimeError):
        return False


def _recognized_cache(folder, key, root):
    try:
        if (folder.is_symlink() or getattr(folder, "is_junction", lambda: False)()
                or folder.name != key or folder.resolve().parent != root.resolve()
                or {item.name for item in folder.iterdir()} != {"manifest.json", "original.wav", "processed.wav"}):
            return False
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        hashes = manifest.get("sha256", {})
        return (manifest.get("owner") == OWNER and manifest.get("key") == key
                and manifest.get("processed_is_crop_of_full_render") is True
                and isinstance(manifest.get("leveling"), dict)
                and isinstance(manifest.get("channels"), int)
                and set(hashes) == {"original.wav", "processed.wav"}
                and all(re.fullmatch(r"[0-9a-f]{64}", value) for value in hashes.values()))
    except (OSError, ValueError, KeyError, TypeError):
        return False


def _quarantine_cache(folder, key, root):
    if not _recognized_cache(folder, key, root):
        return None
    quarantine = root / f".{key}.corrupt-{uuid.uuid4().hex}"
    os.rename(folder, quarantine)
    (quarantine / "owner.pid").write_text(str(os.getpid()), encoding="ascii")
    return quarantine


def _owned_cache_dirs(root):
    for folder in root.iterdir():
        if (not folder.is_dir() or folder.is_symlink() or getattr(folder, "is_junction", lambda: False)()
                or len(folder.name) != 64):
            continue
        try:
            if folder.resolve().parent != root.resolve():
                continue
            manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
            if (manifest.get("owner") == OWNER and manifest.get("key") == folder.name
                    and isinstance(manifest.get("created"), (int, float))
                    and _valid_cache(folder, folder.name, manifest.get("start_sample"), manifest.get("duration_samples"))):
                yield folder, manifest
        except (OSError, ValueError, TypeError):
            continue


def _prune_cache(root, keep_key):
    entries = []
    total = 0
    for folder, manifest in _owned_cache_dirs(root):
        size = sum(path.stat().st_size for path in folder.iterdir() if path.is_file() and not path.is_symlink())
        total += size
        entries.append((manifest.get("created", 0), size, folder))
    for _, size, folder in sorted(entries):
        if total <= CACHE_LIMIT:
            break
        if folder.name == keep_key:
            continue
        shutil.rmtree(folder)
        total -= size


def _copy_active(cache, active_dir, key, duration_samples, owner_pid):
    manifest = json.loads((cache / "manifest.json").read_text(encoding="utf-8"))
    duration_samples = manifest["duration_samples"]
    active = Path(active_dir)
    active.mkdir(parents=True, exist_ok=False)
    marker = active / "owner.pid"
    marker.write_text(str(os.getpid()), encoding="ascii")
    for name in ("original.wav", "processed.wav"):
        shutil.copyfile(cache / name, active / name)
    marker.write_text(str(owner_pid), encoding="ascii")
    return {"original": str(active / "original.wav"), "processed": str(active / "processed.wav"),
            "cache_key": key, "duration_samples": duration_samples,
            "active_dir": str(active),
            "original_compensation_db": manifest["original_compensation_db"],
            "original_lufs": manifest["original_lufs"],
            "processed_lufs": manifest["processed_lufs"],
            "loudness_match_db": manifest.get("loudness_match_db"),
            "estimated_scratch_bytes": manifest["estimated_scratch_bytes"],
            "mastering_mode": manifest["processed_mastering_mode"],
            "leveling": manifest["leveling"],
            "bleed_reduction": manifest.get("bleed_reduction", {"enabled": False, "reason": "No cached bleed summary."}),
            "processing": manifest.get("processing", []),
            "processed_is_crop_of_full_render": True}


def create_preview(project: Project, start_sample, duration_samples, cache_root, work_root,
                   active_dir, owner_pid, runner=None):
    project.validate()
    if type(start_sample) is not int or start_sample < 0:
        raise ValueError("Preview start must be a nonnegative project sample.")
    if type(duration_samples) is not int or not 0 < duration_samples <= 30 * RATE:
        raise ValueError("Preview length must be positive and at most 30 seconds.")
    if type(owner_pid) is not int or owner_pid <= 0:
        raise ValueError("Preview owner must be the active desktop process.")
    runner = runner or Runner()
    cache_root, work_root, active_dir = Path(cache_root), Path(work_root), Path(active_dir)
    data_dir = cache_root.parent.resolve()
    if (cache_root.name != "preview-cache" or work_root.name != "preview-work"
            or cache_root.parent.resolve() != work_root.parent.resolve()
            or active_dir.parent.name != "previews" or active_dir.parent.parent.resolve() != data_dir
            or not re.fullmatch(r"preview-active-[0-9a-f]{32}", active_dir.name)):
        raise ValueError("Preview files must stay in the app-owned preview folders.")
    for folder in (cache_root, work_root, active_dir.parent):
        if folder.is_symlink() or getattr(folder, "is_junction", lambda: False)():
            raise ValueError("Preview folders cannot be redirected through links.")
    cache_root.mkdir(parents=True, exist_ok=True)
    work_root.mkdir(parents=True, exist_ok=True)
    active_dir.parent.mkdir(parents=True, exist_ok=True)
    from .lifecycle import sweep_preview_files
    sweep_preview_files(data_dir)
    sources = []
    for track in project.tracks:
        runner.check()
        info = probe(track.path, track.stream)
        sources.append({"path": str(Path(track.path).resolve()), "sha256": digest(track.path), "probe": info})
    effective = effective_timeline_tracks(project.tracks, sources)
    total_frames = max(_estimated_project_frames(source) + track.offset_samples
                       for source, track in zip(sources, effective))
    if start_sample >= total_frames:
        raise ValueError("Preview start is beyond the project timeline.")
    key, material = _cache_key(project, sources, start_sample, duration_samples)
    cache = cache_root / key
    quarantine = None
    try:
        lock = reserve_output(cache_root / ".preview-cache.lock")
    except OSError as exc:
        raise RuntimeError("Another preview is updating the local preview cache. Try again shortly.") from exc
    try:
        if _valid_cache(cache, key, start_sample, duration_samples):
            return _copy_active(cache, active_dir, key, duration_samples, owner_pid)
        if cache.exists() or cache.is_symlink():
            quarantine = _quarantine_cache(cache, key, cache_root)
            if quarantine is None:
                raise FileExistsError(f"An unrecognized preview cache entry exists and was preserved: {cache}")
    finally:
        lock.close()

    channels = max(source["probe"]["stream"]["channels"] for source in sources)
    estimated_scratch = sum(int(source["probe"]["duration"] * RATE * 16) for source in sources)
    estimated_original = sum(int(source["probe"]["duration"] * RATE * source["probe"]["stream"]["channels"] * 4)
                             for source in sources) + total_frames * channels * 4
    estimated_output = total_frames * channels * 3
    required_free = estimated_scratch + estimated_original + estimated_output + 256 * 1024 * 1024
    free_bytes = shutil.disk_usage(work_root).free
    if free_bytes < required_free:
        raise OSError(f"Preview needs approximately {required_free / 1024**3:.2f} GiB of free space; "
                      f"{free_bytes / 1024**3:.2f} GiB is available. The first preview renders the full program.")

    with tempfile.TemporaryDirectory(prefix="fauxphonic-preview-", dir=work_root) as folder_name:
        work = Path(folder_name)
        (work / "owner.pid").write_text(str(os.getpid()), encoding="ascii")
        decoded = []
        for index, source in enumerate(sources):
            target = work / f"source-{index}.wav"
            decode_source(source["path"], source["probe"], target, runner, "preview source mix")
            decoded.append(target)
        original_mix = work / "original-mix.wav"
        frames, _ = mix_tracks(decoded, effective, original_mix, runner)
        if start_sample >= frames:
            raise ValueError("Preview start is beyond the decoded project timeline.")
        duration_samples = min(duration_samples, frames - start_sample)
        samples = inspect_samples(original_mix, runner)
        compensation = 20 * math.log10(0.999 / samples["sample_peak"]) if samples["sample_peak"] > 0.999 else 0.0
        original = work / "original.wav"
        runner.run(["-i", original_mix, "-af", f"atrim=start_sample={start_sample}:end_sample={start_sample + duration_samples},asetpts=PTS-STARTPTS,volume={compensation:.8f}dB",
                    "-ar", RATE, "-c:a", "pcm_s24le", "-rf64", "auto", original],
                   "preview original segment", duration_samples / RATE)

        processed_full = work / "processed-full.wav"
        render_runner = Runner(lambda event: runner.emit(
            {**event, "stage": "preview_full_render_complete"} if event.get("stage") == "complete" else event),
            runner.cancel_file)
        # Stem files are export deliverables and are irrelevant to the private A/B pair.
        report = render(replace(project, export_stems=False), processed_full, render_runner)
        processed = work / "processed.wav"
        runner.run(["-i", processed_full, "-af", f"atrim=start_sample={start_sample}:end_sample={start_sample + duration_samples},asetpts=PTS-STARTPTS",
                    "-ar", RATE, "-c:a", "pcm_s24le", "-rf64", "auto", processed],
                   "preview processed segment", duration_samples / RATE)
        original_measurement = final_meter(original, runner, "preview measurement", duration_samples / RATE)
        processed_measurement = final_meter(processed, runner, "preview measurement", duration_samples / RATE)
        for source in sources:
            runner.check()
            if digest(source["path"]) != source["sha256"]:
                raise RuntimeError("Source changed during preview; neither A/B file was cached.")
        leveling = report.get("leveling", {})
        leveling_summary = {"enabled": leveling.get("enabled", False), "reason": leveling.get("reason"),
            "method": leveling.get("method"), "model_sha256": leveling.get("model_sha256"),
            "analysis_rtf": leveling.get("analysis_rtf"),
            "phrase_count": sum(len(track.get("regions", [])) for track in leveling.get("tracks", []))}
        bleed = report.get("bleed_reduction", {})
        bleed_summary = {"enabled": bleed.get("enabled", False), "reason": bleed.get("reason"),
                         "track_count": sum(bool(track.get("events")) for track in bleed.get("tracks", [])),
                         "candidate_events": sum(len(track.get("events", [])) for track in bleed.get("tracks", []))}
        metadata = {"owner": OWNER, "key": key, "created": time.time(), "inputs": material,
                    "sha256": {"original.wav": digest(original), "processed.wav": digest(processed)},
                    "start_sample": start_sample, "duration_samples": duration_samples,
                    "channels": sf.info(original).channels,
                    "original_compensation_db": compensation,
                    "original_lufs": original_measurement["input_i"],
                    "processed_lufs": processed_measurement["input_i"],
                    "loudness_match_db": (original_measurement["input_i"] - processed_measurement["input_i"]
                                           if original_measurement["input_i"] is not None and processed_measurement["input_i"] is not None else None),
                    "estimated_scratch_bytes": required_free,
                    "processed_mastering_mode": report["mastering_mode"],
                    "leveling": leveling_summary,
                    "bleed_reduction": bleed_summary,
                    "processing": report.get("processing", []),
                    "processed_is_crop_of_full_render": True}
        stage = cache_root / f".{key}.building-{os.getpid()}"
        stage.mkdir()
        (stage / "owner.pid").write_text(str(os.getpid()), encoding="ascii")
        shutil.copyfile(original, stage / "original.wav")
        shutil.copyfile(processed, stage / "processed.wav")
        (stage / "manifest.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        (stage / "owner.pid").unlink()

    try:
        lock = reserve_output(cache_root / ".preview-cache.lock")
    except OSError as exc:
        shutil.rmtree(stage, ignore_errors=True)
        raise RuntimeError("Another preview is updating the local preview cache. Try again shortly.") from exc
    try:
        if not _valid_cache(cache, key, start_sample, duration_samples):
            if cache.exists() or cache.is_symlink():
                replacement = _quarantine_cache(cache, key, cache_root)
                if replacement is None:
                    shutil.rmtree(stage)
                    raise FileExistsError(f"An unrecognized preview cache entry exists and was preserved: {cache}")
                quarantine = replacement
            os.rename(stage, cache)
        else:
            shutil.rmtree(stage)
        _prune_cache(cache_root, key)
        result = _copy_active(cache, active_dir, key, duration_samples, owner_pid)
        if quarantine is not None:
            shutil.rmtree(quarantine)
    finally:
        lock.close()
    result["original_compensation_db"] = metadata["original_compensation_db"]
    result["mastering_mode"] = metadata["processed_mastering_mode"]
    result["processed_is_crop_of_full_render"] = True
    result["estimated_scratch_bytes"] = required_free
    result["original_lufs"] = metadata["original_lufs"]
    result["processed_lufs"] = metadata["processed_lufs"]
    result["loudness_match_db"] = ((metadata["original_lufs"] - metadata["processed_lufs"])
                                   if metadata["original_lufs"] is not None and metadata["processed_lufs"] is not None else None)
    result["leveling"] = metadata["leveling"]
    return result
