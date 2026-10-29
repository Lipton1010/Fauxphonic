"""Bounded synthetic multitrack speech benchmark; never a listening evaluation."""
import argparse
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from local_audio_studio.audio import Runner, digest, write_json
from local_audio_studio.contracts import Project, Track
from local_audio_studio.engine import render
from local_audio_studio.presets import make_preset


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=int, nargs="+", default=[60, 1800, 10800])
    parser.add_argument("--worker", type=Path, help="Test this packaged worker instead of source Python.")
    args = parser.parse_args()
    if any(seconds <= 0 for seconds in args.seconds):
        parser.error("Durations must be positive.")
    destination = ROOT / ".cache" / ("speech-benchmark-" + time.strftime("%Y%m%d-%H%M%S"))
    destination.mkdir(parents=True)
    fixture = ROOT / "tests/fixtures/silero-leveling-tts.wav"
    speech, rate = sf.read(fixture, dtype="float32")
    if rate != 48000 or speech.ndim != 1:
        raise ValueError("Expected the known-text mono 48 kHz fixture.")
    # One 20-second cycle, independent of the complete recording duration.
    cycle = np.zeros(20 * rate, dtype=np.float32)
    cycle[:min(len(speech), len(cycle))] = speech[:len(cycle)]
    record = {"synthetic": True, "listening_evaluation": "not performed",
              "platform": platform.platform(), "python": sys.version,
              "fixture_sha256": digest(fixture), "output_directory": str(destination),
              "packaged_worker": str(args.worker.resolve()) if args.worker else None,
              "worker_sha256": digest(args.worker) if args.worker else None,
              "source_hashes": {str(path.relative_to(ROOT)): digest(path)
                                for path in (ROOT / "src/local_audio_studio").glob("*.py")},
              "results": []}
    for seconds in args.seconds:
        paths = []
        for track_index in range(2):
            path = destination / f"speech-{seconds}-{track_index}.flac"
            remaining = seconds * rate
            repetition = 0
            with sf.SoundFile(path, "w", samplerate=rate, channels=1,
                              format="FLAC", subtype="PCM_24") as output:
                while remaining:
                    count = min(remaining, len(cycle))
                    gain = 0.25 if (repetition + track_index) % 2 else 1.0
                    output.write(cycle[:count] * gain)
                    remaining -= count
                    repetition += 1
            paths.append(path)
        project = Project([Track(str(paths[0]), role="speech"),
                           Track(str(paths[1]), role="speech", offset_samples=7 * rate,
                                 gain_db=-3)], make_preset("strong-cleanup"))
        project_path = destination / f"project-{seconds}.json"
        write_json(project_path, project.to_dict())
        last = [None, 0]

        def progress(event):
            now = time.monotonic()
            if event["stage"] != last[0] or now - last[1] >= 30:
                summary = {key: event[key] for key in ("stage", "progress", "status", "detail", "output") if key in event}
                print(json.dumps({"seconds": seconds, **summary}), flush=True)
                last[:] = [event["stage"], now]

        output = destination / f"master-{seconds}.flac"
        if args.worker:
            environment = os.environ.copy()
            environment["PATH"] = str(Path(os.environ["SystemRoot"]) / "System32")
            for key in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH"):
                environment.pop(key, None)
            started = time.perf_counter()
            with (destination / f"worker-{seconds}.stderr.txt").open("w") as errors:
                worker = subprocess.Popen([str(args.worker.resolve()), "render-project", str(project_path),
                                           "--output", str(output)], stdout=subprocess.PIPE,
                                          stderr=errors, text=True, encoding="utf-8", env=environment)
                for line in worker.stdout:
                    progress(json.loads(line))
                if worker.wait() != 0:
                    raise RuntimeError(f"Worker failed; see {destination}.")
            report = json.loads(output.with_suffix(".flac.report.json").read_text(encoding="utf-8"))
            report["measured_including_worker_start_seconds"] = time.perf_counter() - started
        else:
            report = render(project, output, Runner(progress))
        if report["verification"]["status"] != "passed":
            raise AssertionError(report["verification"])
        if report["verification"]["samples"]["frames"] != (seconds + 7) * rate:
            raise AssertionError("Multitrack duration/offset changed.")
        record["results"].append(report)
        write_json(destination / "benchmark.json", record)
        write_json(ROOT / "evidence/speech-longform.json", record)
    print("Benchmark completed: " + str(destination), flush=True)


if __name__ == "__main__":
    main()
