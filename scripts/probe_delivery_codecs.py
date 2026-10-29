"""Measure actual codec timing and endpoint fidelity before enabling export profiles."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

import numpy as np
import soundfile as sf


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ffmpeg", type=Path, required=True)
    parser.add_argument("--ffprobe", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    rate, frames = 48000, 480001
    wave = (0.15 * np.sin(2*np.pi*321*np.arange(frames)/rate)).astype(np.float32)
    # Signals near both endpoints make length-only success insufficient.
    wave[:240] += 0.2*np.sin(2*np.pi*4000*np.arange(240)/rate)
    wave[-240:] += 0.2*np.sin(2*np.pi*6000*np.arange(240)/rate)
    rows = []
    for channels in (1, 2):
        samples = wave if channels == 1 else np.column_stack((wave, wave*0.37))
        source = args.output / f"source-{channels}.wav"
        sf.write(source, samples, rate, subtype="FLOAT")
        for suffix, encoding in {
            "mp3": ["-c:a", "libmp3lame", "-b:a", "192k", "-write_xing", "1"],
            "m4a": ["-c:a", "aac", "-b:a", "192k", "-movie_timescale", "48000",
                    "-use_editlist", "1", "-movflags", "+faststart"],
            "opus": ["-c:a", "libopus", "-b:a", "96k", "-application", "audio"],
        }.items():
            output = args.output / f"audio-{channels}.{suffix}"
            decoded = args.output / f"decoded-{channels}-{suffix}.wav"
            subprocess.run([str(args.ffmpeg), "-v", "error", "-i", str(source), *encoding,
                            str(output)], check=True, capture_output=True)
            metadata = json.loads(subprocess.check_output([str(args.ffprobe), "-v", "error",
                "-show_streams", "-show_format", "-of", "json", str(output)]))
            subprocess.run([str(args.ffmpeg), "-v", "error", "-i", str(output), "-c:a",
                "pcm_f32le", "-f", "wav", "-rf64", "always", str(decoded)], check=True, capture_output=True)
            audio, decoded_rate = sf.read(decoded, dtype="float32", always_2d=True)
            correlation = np.correlate(audio[:24000, 0], wave[:23900], mode="valid")
            row = {"format": suffix, "channels": channels, "encoding_args": encoding,
                   "source_frames": frames, "raw_decoded_frames": len(audio), "sample_rate": decoded_rate,
                   "first_5ms_rms": float(np.sqrt(np.mean(audio[:240]**2))),
                   "last_source_5ms_rms": float(np.sqrt(np.mean(audio[frames-240:frames]**2))),
                   "correlation_positive_lag_samples": int(np.argmax(correlation)),
                   "stream": metadata["streams"][0]}
            if suffix == "m4a":
                assert row["stream"]["duration_ts"] == frames and row["stream"]["time_base"] == "1/48000"
                assert frames <= len(audio) < frames + 1024
            else:
                assert len(audio) == frames
            assert decoded_rate == rate and audio.shape[1] == channels
            assert row["first_5ms_rms"] > 0.01 and row["last_source_5ms_rms"] > 0.01
            rows.append(row)
    evidence = {"ffmpeg_sha256": hashlib.sha256(args.ffmpeg.read_bytes()).hexdigest(),
                "ffprobe_sha256": hashlib.sha256(args.ffprobe.read_bytes()).hexdigest(),
                "listening_evaluation": "not performed", "results": rows}
    (args.output / "evidence.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
