"""Explicit delivery profiles and decoded presentation-length verification."""
from contextlib import contextmanager
from pathlib import Path
import tempfile

from .audio import decode_source, probe
from .contracts import RATE

FORMATS = {".wav", ".flac", ".mp3", ".m4a", ".opus"}
LOSSY_FORMATS = {".mp3", ".m4a", ".opus"}


def output_paths(output, project):
    """One naming contract for the engine and queued GUI reservations."""
    output = Path(output).resolve()
    suffix = ".wav" if output.suffix.lower() in LOSSY_FORMATS else output.suffix.lower()
    stems = [output.with_name(f"{output.stem}.track-{index + 1:02d}-{track.role}-stem{suffix}")
             for index, track in enumerate(project.tracks)] if project.export_stems else []
    return [output, output.with_suffix(output.suffix + ".report.json"), *stems]


def encoding_args(suffix, channels):
    profiles = {
        ".wav": ["-c:a", "pcm_s24le", "-rf64", "auto"],
        ".flac": ["-c:a", "flac", "-sample_fmt", "s32"],
        ".mp3": ["-c:a", "libmp3lame", "-b:a", "96k" if channels == 1 else "192k", "-write_xing", "1"],
        ".m4a": ["-c:a", "aac", "-b:a", "96k" if channels == 1 else "192k",
                 "-movie_timescale", str(RATE), "-use_editlist", "1", "-movflags", "+faststart"],
        ".opus": ["-c:a", "libopus", "-b:a", "64k" if channels == 1 else "128k", "-application", "audio"],
    }
    return profiles[suffix.lower()]


@contextmanager
def verification_audio(path, runner, scratch_root=None):
    """Decode actual lossy output; AAC presentation length comes from the container.

    AAC codec frames can contain trailing padding that FFmpeg exposes after the
    declared MP4 presentation end. Never infer that end from the requested export
    length. MP3/Opus must supply their own gapless metadata and decode exactly.
    """
    path = Path(path)
    if path.suffix.lower() not in LOSSY_FORMATS:
        yield path, {"decoded_for_verification": False}
        return
    info = probe(path)
    with tempfile.TemporaryDirectory(prefix="fauxphonic-verify-", dir=scratch_root) as folder:
        decoded = Path(folder) / "decoded.wav"
        timing = decode_source(path, info, decoded, runner, "verifying", rate=int(info["stream"]["sample_rate"]))
        timing["decoded_for_verification"] = True
        yield decoded, timing
