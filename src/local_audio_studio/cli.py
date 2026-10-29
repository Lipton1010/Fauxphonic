import argparse
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile

from .audio import Cancelled, NO_WINDOW, Runner, binary, decode_source, digest, loudness, probe, write_json
from .contracts import Preset, Project, RATE, Track
from .lifecycle import analysis_scratch_root, sweep_analysis_scratch
from .presets import default_track_role, make_preset


def emit(event):
    print(json.dumps({"protocol": 1, **event}, ensure_ascii=True), flush=True)


def doctor():
    import psutil
    import importlib.metadata
    info = {"platform": platform.platform(), "python": platform.python_version(),
            "cpu": platform.processor(), "host_ram_bytes": psutil.virtual_memory().total,
            "dependencies": {name: importlib.metadata.version(name) for name in
                             ("PySide6", "numpy", "soundfile", "psutil")},
            "execution": "CPU", "gpu_compatibility": "not validated", "tools": {}}
    if os.name == "nt":
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
            info["cpu"] = winreg.QueryValueEx(key, "ProcessorNameString")[0].strip()
    for name in ("ffmpeg", "ffprobe"):
        path = binary(name)
        version = subprocess.run([path, "-version"], capture_output=True, creationflags=NO_WINDOW, check=True)
        info["tools"][name] = {"path": path, "sha256": digest(path), "version": version.stdout.decode().splitlines()[0]}
    try:
        gpu = subprocess.run(["nvidia-smi", "--query-gpu=name,driver_version,memory.total,memory.free",
                              "--format=csv,noheader"], capture_output=True, creationflags=NO_WINDOW, timeout=10)
        info["gpu_observation"] = gpu.stdout.decode().strip() if gpu.returncode == 0 else "unavailable"
    except (OSError, subprocess.TimeoutExpired):
        info["gpu_observation"] = "nvidia-smi unavailable"
    return info


def analyze(path, runner, stream=None, scratch_root=None):
    import numpy as np
    import soundfile as sf
    info = probe(path, stream)
    scratch_root = Path(scratch_root or analysis_scratch_root())
    scratch_root.mkdir(parents=True, exist_ok=True)
    sweep_analysis_scratch(scratch_root)
    with tempfile.TemporaryDirectory(prefix="fauxphonic-analysis-", dir=scratch_root) as temp:
        (Path(temp) / "owner.pid").write_text(str(os.getpid()), encoding="ascii")
        decoded = Path(temp) / "analysis.wav"
        decode_timing = decode_source(path, info, decoded, runner, "analyzing")
        regions, activity_info = [], {"status": "not run: stereo analysis preserves channels"}
        if info["stream"]["channels"] == 1:
            from .leveling import analyze_speech_activity
            found, activity_info = analyze_speech_activity(
                [Track(str(path), role="speech")], [decoded], Path(temp), runner)
            regions = found.get(0, [])
            activity_info.update(status="measured", phrase_count=len(regions))
        with sf.SoundFile(decoded) as audio:
            frames, channels = len(audio), audio.channels
            stride = max(1, (frames + 1199) // 1200)
            peaks = np.zeros((frames + stride - 1) // stride, dtype=np.float32)
            total_energy = speech_energy = 0.0
            speech_samples = near_full_scale = 0
            clipping_locations = []
            histogram = np.zeros(265, dtype=np.int64)  # 0.5 dB bins, -120..+12 dBFS.
            position = region_index = 0
            while position < frames:
                runner.check()
                block = audio.read(4800, dtype="float32", always_2d=True)
                if not np.isfinite(block).all():
                    raise ValueError("Nonfinite input samples.")
                absolute = np.abs(block)
                energy = float(np.sum(block.astype(np.float64) ** 2))
                total_energy += energy
                db = 10 * np.log10(max(energy / block.size, 1e-12))
                histogram[int(np.clip(np.floor((db + 120) * 2), 0, 264))] += len(block)
                hot = np.any(absolute >= 0.999, axis=1)
                near_full_scale += int(np.count_nonzero(hot))
                if len(clipping_locations) < 32:
                    clipping_locations.extend((position + np.flatnonzero(hot)[:32-len(clipping_locations)]).tolist())
                end = position + len(block)
                left = position
                while left < end:
                    bucket = left // stride
                    right = min(end, (bucket + 1) * stride)
                    peaks[bucket] = max(peaks[bucket], float(np.max(absolute[left-position:right-position])))
                    left = right
                while region_index < len(regions) and regions[region_index][1] <= position:
                    region_index += 1
                current = region_index
                while current < len(regions) and regions[current][0] < end:
                    left, right = max(position, regions[current][0]), min(end, regions[current][1])
                    if right > left:
                        active = block[left-position:right-position].astype(np.float64)
                        speech_energy += float(np.sum(active ** 2))
                        speech_samples += active.size
                    current += 1
                position = end
                runner.sample_memory()

        def rms_db(energy, count):
            return float(10 * np.log10(energy / count)) if energy > 0 and count else None

        percentile_bin = int(np.searchsorted(np.cumsum(histogram), max(1, frames * 0.1)))
        levels = {"program_rms_dbfs": rms_db(total_energy, frames * channels),
                  "speech_active_rms_dbfs": rms_db(speech_energy, speech_samples),
                  "speech_active_samples": speech_samples // channels,
                  "nonspeech_rms_dbfs": rms_db(max(0, total_energy-speech_energy), frames*channels-speech_samples)
                      if activity_info["status"] == "measured" else None,
                  "low_energy_10th_percentile_dbfs": -120 + percentile_bin * 0.5,
                  "sample_peak": float(np.max(peaks)) if len(peaks) else 0,
                  "near_full_scale_frames": near_full_scale,
                  "first_near_full_scale_samples": clipping_locations,
                  "near_full_scale_threshold": 0.999}
        overview = peaks.tolist()
        return {"source": str(path), "sha256": digest(path), "probe": info, "waveform": overview,
                "waveform_levels": [overview, [max(overview[i:i+4]) for i in range(0, len(overview), 4)],
                                    [max(overview[i:i+16]) for i in range(0, len(overview), 16)]],
                "waveform_stride_samples": stride, "frames": frames, "levels": levels, "decode_timing": decode_timing,
                "speech_activity": activity_info,
                "loudness": loudness(decoded, runner, duration=info["duration"]),
                "analysis_limitations": "Mono VAD regions include context and are not speaker identity. "
                    "Nonspeech energy and the 100 ms low-energy percentile only approximate a noise floor; "
                    "silence/music can bias them. Near-full-scale samples are clipping candidates, not proof "
                    "of clipping. No perceptual quality score or listening evaluation."}


def execute(request):
    from .engine import render
    from .windows_job import contain_worker, hold_application_mutex
    hold_application_mutex()
    contain_worker()
    runner = Runner(emit, request.get("cancel_file"))
    request_path = Path(request.get("request_path", ""))
    marker = request_path.with_name(request_path.name.removesuffix(".request.json") + ".worker.pid") if request_path.name.endswith(".request.json") else None
    if marker:
        marker.write_text(str(os.getpid()), encoding="ascii")
    if request["action"] == "analyze":
        result = analyze(request["path"], runner, request.get("stream"), request.get("scratch_dir"))
        emit({"stage": "complete", "analysis": result})
    elif request["action"] == "render":
        render(Project.from_dict(request["project"]), request["output"], runner,
               storage_job=request.get("storage_job"))
    elif request["action"] == "preview":
        from .preview import create_preview
        result = create_preview(Project.from_dict(request["project"]), request["start_sample"],
            request["duration_samples"], request["cache_root"], request["work_root"],
            request["active_dir"], request["owner_pid"], runner)
        emit({"stage": "complete", "preview": result})
    elif request["action"] == "playback":
        from .playback import prepare_playback
        result = prepare_playback(request["path"], request.get("stream"), request["active_dir"],
                                  request["data_dir"], runner)
        emit({"stage": "complete", "playback": result})
    else:
        raise ValueError("Unknown worker action.")


def main(argv=None):
    from .windows_job import hold_application_mutex
    hold_application_mutex()
    parser = argparse.ArgumentParser(prog="local-audio")
    commands = parser.add_subparsers(dest="command", required=True)
    doc = commands.add_parser("doctor")
    doc.add_argument("--json", action="store_true")
    storage = commands.add_parser("storage", help="Review new owned payloads; changes require confirmation")
    storage.add_argument("operation", choices=["preview", "quarantine", "restore", "delete"])
    storage.add_argument("path", help="Selected payload root, saved preview, or restore receipt")
    storage.add_argument("--report", help="Write a new preview JSON file")
    storage.add_argument("--confirm", action="store_true", help="Confirm the reviewed move or permanent deletion")
    resolver = commands.add_parser("resolve-model", help="Resolve the exact pinned Silero artifact offline")
    resolver.add_argument("--registry", help="Explicit shared registry; otherwise use the selected/default model")
    resolver.add_argument("--verify", action="store_true")
    resolver.add_argument("--select", action="store_true", help="Verify and select the existing file without copying")
    worker = commands.add_parser("worker")
    worker.add_argument("request")
    analysis = commands.add_parser("analyze")
    analysis.add_argument("input")
    analysis.add_argument("--report", required=True)
    analysis.add_argument("--stream", type=int)
    process = commands.add_parser("process")
    process.add_argument("input")
    process.add_argument("--output", required=True)
    process.add_argument("--preset", choices=["master-only", "natural-speech", "strong-cleanup",
        "preserve-music", "multitrack", "dsp-cleanup", "bypass"], default="master-only")
    process.add_argument("--target-lufs", type=float)
    process.add_argument("--true-peak", type=float)
    process.add_argument("--highpass", type=int, default=0)
    process.add_argument("--noise-reduction", type=int)
    process.add_argument("--deesser", action="store_true")
    process.add_argument("--deesser-range", type=float, default=1.8)
    process.add_argument("--broad-eq", action="store_true")
    process.add_argument("--eq-frequency", type=int, default=1000)
    process.add_argument("--eq-gain", type=float, default=0.0)
    process.add_argument("--role", choices=["program", "speech", "music", "ambience"])
    process.add_argument("--stream", type=int)
    project = commands.add_parser("render-project")
    project.add_argument("project")
    project.add_argument("--output", required=True)
    verification = commands.add_parser("verify")
    verification.add_argument("input")
    verification.add_argument("--target-lufs", type=float, default=-19)
    verification.add_argument("--true-peak", type=float, default=-1)
    args = parser.parse_args(argv)
    try:
        if args.command == "storage":
            from .storage import preview, quarantine, restore, delete_reviewed
            if args.operation == "preview":
                result = preview(args.path)
                if args.report:
                    with Path(args.report).open("x", encoding="utf-8") as target:
                        json.dump(result, target, indent=2)
            elif args.operation == "quarantine":
                result = quarantine(json.loads(Path(args.path).read_text(encoding="utf-8")), args.confirm)
            elif args.operation == "delete":
                result = delete_reviewed(json.loads(Path(args.path).read_text(encoding="utf-8")), args.confirm)
            else:
                result = restore(args.path)
            print(json.dumps(result, indent=2))
        elif args.command == "resolve-model":
            from .model_store import resolve_registered_model, select_registered_model
            result = (select_registered_model(args.registry) if args.select else
                      resolve_registered_model(args.registry, verify=args.verify))
            print(json.dumps(result, indent=2))
        elif args.command == "doctor":
            print(json.dumps(doctor(), indent=2))
        elif args.command == "worker":
            execute(json.loads(Path(args.request).read_text(encoding="utf-8")))
        elif args.command == "analyze":
            if Path(args.report).exists():
                raise FileExistsError("Analysis report already exists.")
            write_json(args.report, analyze(args.input, Runner(emit), args.stream))
        elif args.command == "verify":
            from .engine import verify
            result = verify(args.input, Preset(target_lufs=args.target_lufs, true_peak=args.true_peak))
            print(json.dumps(result, indent=2))
            return 2 if result["status"] == "failed" else 0
        else:
            from .engine import render
            if args.command == "process":
                role = args.role or default_track_role(args.preset)
                preset = make_preset(args.preset, target_lufs=args.target_lufs, true_peak=args.true_peak,
                                     highpass_hz=args.highpass, noise_reduction_db=args.noise_reduction,
                                     deesser_enabled=args.deesser, deesser_range=args.deesser_range,
                                     broad_eq_enabled=args.broad_eq,
                                     broad_eq_frequency_hz=args.eq_frequency, broad_eq_gain_db=args.eq_gain)
                project = Project([Track(str(Path(args.input).resolve()), role=role, stream=args.stream)], preset)
            else:
                project = Project.load(args.project)
            render(project, args.output, Runner(emit))
        return 0
    except Cancelled as exc:
        emit({"stage": "cancelled", "detail": str(exc)})
        return 3
    except Exception as exc:
        emit({"stage": "failed", "detail": f"{type(exc).__name__}: {exc}"})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
