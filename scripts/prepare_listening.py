"""Create reproducible, level-matched blind comparisons; never score listening."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import subprocess

import numpy as np
import soundfile as sf


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    worker, output = args.worker.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    fixture = Path(__file__).resolve().parents[1] / "tests/fixtures/silero-leveling-tts.wav"
    speech, rate = sf.read(fixture, dtype="float32")
    rng = np.random.default_rng(20260926)
    time = np.arange(len(speech), dtype=np.float64) / rate
    variants = {
        "clean": speech,
        "fan-hum": speech + rng.normal(0, 0.007, len(speech)) + 0.01 * np.sin(2 * np.pi * 60 * time),
        "quiet-loud": np.concatenate((speech * 0.25, np.zeros(rate), speech)),
    }
    env = os.environ.copy()
    for name in ("PYTHONPATH", "PYTHONHOME", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH"):
        env.pop(name, None)
    env["PATH"] = os.path.join(env.get("SystemRoot", r"C:\Windows"), "System32")

    def run(arguments, log):
        result = subprocess.run([str(worker), *map(str, arguments)], env=env,
                                capture_output=True, text=True,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        log.write_text(result.stdout + result.stderr, encoding="utf-8")
        if result.returncode:
            raise RuntimeError(f"Worker failed; see {log}")

    key = {"human_listening": "pending", "fixture_sha256": hashlib.sha256(fixture.read_bytes()).hexdigest(),
           "worker_sha256": hashlib.sha256(worker.read_bytes()).hexdigest(), "comparisons": []}
    labels = random.Random(20260926)
    for name, samples in variants.items():
        folder = output / name
        folder.mkdir()
        source = folder / "source.wav"
        sf.write(source, samples, rate, subtype="PCM_24")
        candidates = []
        for preset in ("bypass", "master-only", "natural-speech"):
            target = folder / f"{preset}.wav"
            run(["process", source, "--preset", preset, "--output", target], folder / f"{preset}.log")
            analysis = folder / f"{preset}.analysis.json"
            run(["analyze", target, "--report", analysis], folder / f"{preset}-analysis.log")
            measured = json.loads(analysis.read_text(encoding="utf-8"))["loudness"]
            loudness = float(measured["input_i"])
            if not np.isfinite(loudness):
                raise ValueError("Comparison has no measurable integrated loudness.")
            candidates.append((preset, target, loudness))
        # Attenuation only: comparison gain cannot introduce clipping or change exports.
        target_lufs = min(item[2] for item in candidates)
        labels.shuffle(candidates)
        row = {"fixture": name, "target_lufs": target_lufs, "labels": {}}
        for label, (preset, path, loudness) in zip("ABC", candidates):
            audio, sample_rate = sf.read(path, dtype="float32", always_2d=True)
            if len(audio) != len(samples) or sample_rate != rate:
                raise ValueError("Blind comparison timing differs from the source.")
            gain_db = target_lufs - loudness
            blind = folder / f"Listen-{label}.wav"
            sf.write(blind, audio * 10 ** (gain_db / 20), rate, subtype="PCM_24")
            row["labels"][label] = {"preset": preset, "comparison_gain_db": gain_db,
                                    "sha256": hashlib.sha256(blind.read_bytes()).hexdigest()}
        key["comparisons"].append(row)
    (output / "answer-key.json").write_text(json.dumps(key, indent=2), encoding="utf-8")
    (output / "LISTENING.txt").write_text(
        "Listening approval is pending. These are synthetic Microsoft SAPI speech fixtures,\n"
        "not evidence of human preference or Auphonic equivalence.\n\n"
        "For each folder, compare Listen-A/B/C.wav before opening answer-key.json.\n"
        "They share timing and measured integrated loudness (attenuation only).\n"
        "Assess words/endings, voice character, expressive changes, noise, and artifacts.\n"
        "Record preferences and problems; do not infer quality from loudness alone.\n"
        "The folder names disclose degradation type, not processing identity.\n"
        "Representative consenting human speech, whispers, laughter, overlap, music,\n"
        "and real room reverb still require a separate listening corpus.\n",
        encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
