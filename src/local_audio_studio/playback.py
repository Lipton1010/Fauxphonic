"""Prepare unsupported native playback formats as app-owned PCM audition files."""
import math
import os
from pathlib import Path
import re
import shutil
import soundfile as sf

from .audio import decode_source, digest, probe
from .contracts import RATE


def prepare_playback(source, stream, active_dir, data_dir, runner):
    source = Path(source).resolve()
    data_dir, active_dir = Path(data_dir).resolve(), Path(active_dir).resolve()
    if (not re.fullmatch(r"preview-active-[0-9a-f]{32}", active_dir.name)
            or active_dir.parent != data_dir / "previews"):
        raise ValueError("Playback scratch must use an app-owned preview folder.")
    if not source.is_file():
        raise ValueError(f"Playback source is missing: {source}")
    before = digest(source)
    data_dir.joinpath("previews").mkdir(parents=True, exist_ok=True)
    active_dir.mkdir()
    (active_dir / "owner.pid").write_text(str(os.getpid()), encoding="ascii")
    keep = False
    try:
        info = probe(source, stream)
        seconds = info["duration"]
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("Playback source has no usable duration metadata.")
        estimated_frames = math.ceil(seconds * RATE)
        estimated_bytes = estimated_frames * info["stream"]["channels"] * 7 + 64 * 1024**2
        free_bytes = shutil.disk_usage(data_dir).free
        if estimated_bytes > free_bytes:
            raise ValueError(f"Playback needs about {estimated_bytes / 1024**3:.2f} GiB of temporary space; "
                             f"only {free_bytes / 1024**3:.2f} GiB is available.")
        decoded = active_dir / "decoded-float.wav"
        output_part = active_dir / "audition.partial.wav"
        output = active_dir / "audition.wav"
        timing = decode_source(source, info, decoded, runner, "Preparing native playback", RATE)
        remaining_bytes = shutil.disk_usage(data_dir).free
        output_bytes = timing["presentation_frames"] * info["stream"]["channels"] * 3 + 1024**2
        if output_bytes > remaining_bytes:
            raise ValueError(f"Decoded audio needs another {output_bytes / 1024**3:.2f} GiB for the native audition; "
                             f"only {remaining_bytes / 1024**3:.2f} GiB remains.")
        runner.run(["-i", decoded, "-map", "0:a:0", "-c:a", "pcm_s24le", "-f", "wav", "-rf64", "always",
                    output_part], "Preparing native playback", seconds)
        with sf.SoundFile(decoded) as source_pcm, sf.SoundFile(output_part) as audition:
            if (source_pcm.frames != timing["presentation_frames"] or audition.frames != source_pcm.frames
                    or audition.samplerate != RATE or audition.channels != source_pcm.channels
                    or audition.subtype != "PCM_24"):
                raise RuntimeError("Audition WAV verification found a changed channel layout or sample count.")
        decoded.unlink()
        os.replace(output_part, output)
        if digest(source) != before:
            raise RuntimeError("Playback source changed while preparing audition audio.")
        runner.check()
        keep = True
        return {"path": str(output), "active_dir": str(active_dir), "source": str(source),
                "source_sha256": before, "stream": info["stream"]["index"],
                "frames": timing["presentation_frames"], "sample_rate": RATE,
                "channels": info["stream"]["channels"], "scratch_bytes_estimate": estimated_bytes}
    finally:
        if not keep:
            shutil.rmtree(active_dir, ignore_errors=True)
