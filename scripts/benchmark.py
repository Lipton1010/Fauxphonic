"""Synthetic 60-second and three-hour multitrack rendering; no quality claims."""
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from local_audio_studio.audio import Runner, binary, write_json
from local_audio_studio.contracts import Preset, Project, Track
from local_audio_studio.engine import render


def main():
    destination = ROOT / "evidence" / "local" / ("benchmark-" + time.strftime("%Y%m%d-%H%M%S"))
    destination.mkdir(parents=True)
    results = []
    for seconds in (60, 10800):
        paths = []
        for i, frequency in enumerate((233, 431)):
            path = destination / f"source-{seconds}-{i}.flac"
            subprocess.run([binary("ffmpeg"), "-v", "error", "-f", "lavfi", "-i",
                            f"sine=frequency={frequency}:sample_rate=48000:duration={seconds}",
                            "-af", "volume=0.3", "-c:a", "flac", str(path)], check=True)
            paths.append(path)
        project = Project([Track(str(paths[0]), role="speech"),
                           Track(str(paths[1]), offset_samples=336000, gain_db=-3, role="speech")], Preset())
        last = [None, 0]
        def progress(event):
            now = time.monotonic()
            if event["stage"] != last[0] or now-last[1] > 15:
                print(seconds, event, flush=True)
                last[:] = [event["stage"], now]
        report = render(project, destination / f"master-{seconds}.wav", Runner(progress))
        results.append(report)
        write_json(ROOT / "evidence" / "benchmark.json", {"synthetic": True, "results": results})
    print("Benchmark complete", destination, flush=True)


if __name__ == "__main__":
    main()
