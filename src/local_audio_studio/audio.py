"""Local FFmpeg boundary, cancellation, and bounded progress/resource sampling."""
from collections import deque
import hashlib
import json
import math
from fractions import Fraction
import os
from pathlib import Path
import subprocess
import struct
import sys
import tempfile
import threading
import time

import psutil

ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
FLOAT_SCRATCH_ARGS = ("-c:a", "pcm_f32le", "-f", "wav", "-rf64", "always")


def binary(name):
    path = ROOT / "tools" / f"{name}.exe"
    if not path.is_file():
        raise RuntimeError(f"Required bundled tool is missing: {path}. Repair or reinstall Fauxphonic.")
    return str(path)


def digest(path):
    with open(path, "rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path, data):
    path = Path(path)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    os.replace(temp, path)


def validate_wave_container(path):
    """Reject incomplete WAVE containers before ffprobe clamps their duration.

    Walk chunk headers only: even RF64/W64 recordings use bounded memory.
    Other containers are validated by the decoder.
    """
    with open(path, "rb") as source:
        header = source.read(40)
        actual = os.fstat(source.fileno()).st_size
        wave64 = header[:16] == bytes.fromhex("726966662e91cf11a5d628db04c10000")
        if wave64:
            if len(header) < 40 or header[24:28] != b"wave":
                raise ValueError("Incomplete W64 header.")
            declared = struct.unpack_from("<Q", header, 16)[0]
            position, header_size, alignment = 40, 24, 8
        elif header[:4] in {b"RIFF", b"RIFX", b"RF64", b"BW64"} and header[8:12] == b"WAVE":
            endian = ">" if header[:4] == b"RIFX" else "<"
            declared = struct.unpack_from(endian + "I", header, 4)[0] + 8
            extended = header[:4] in {b"RF64", b"BW64"}
            if extended:
                declared = actual  # ds64 supplies the real extent below.
            position, header_size, alignment = 12, 8, 2
        else:
            return
        data_size = None
        if declared > actual or declared < position:
            raise ValueError("Truncated or incomplete WAVE source: declared container size disagrees with file size.")
        for _ in range(100000):
            if position == declared:
                return
            if position + header_size > declared:
                raise ValueError("Truncated WAVE chunk header.")
            source.seek(position)
            chunk = source.read(header_size)
            name = chunk[:4]
            if wave64:
                size = struct.unpack_from("<Q", chunk, 16)[0] - header_size
            else:
                size = struct.unpack_from(endian + "I", chunk, 4)[0]
                if name == b"ds64" and extended:
                    if size < 28 or position + 8 + size > actual:
                        raise ValueError("Incomplete RF64 size header.")
                    declared_size, data_size = struct.unpack("<QQ", source.read(16))
                    declared = declared_size + 8
                    if declared > actual or declared < position + 8 + size:
                        raise ValueError("Truncated RF64 source: declared container size exceeds available data.")
                if size == 0xffffffff:
                    if name != b"data" or data_size is None:
                        raise ValueError("WAVE chunk has an unresolved extended size.")
                    size = data_size
            end = position + header_size + size
            if size < 0 or end > declared or end > actual:
                raise ValueError("Truncated WAVE source: chunk declares more audio/data than the file contains.")
            position = end if end == declared else (end + alignment - 1) // alignment * alignment
        raise ValueError("WAVE source contains too many chunks to validate safely.")


def probe(path, stream=None):
    validate_wave_container(path)
    result = subprocess.run([binary("ffprobe"), "-v", "error", "-protocol_whitelist", "file,pipe", "-show_streams", "-show_format",
                             "-of", "json", str(path)], capture_output=True, creationflags=NO_WINDOW)
    if result.returncode:
        raise ValueError(result.stderr.decode("utf-8", "replace")[-3000:])
    data = json.loads(result.stdout)
    audio = [s for s in data["streams"] if s.get("codec_type") == "audio"]
    if stream is None:
        if len(audio) != 1:
            raise ValueError(f"Select an audio stream explicitly; found {[s['index'] for s in audio]}.")
        selected = audio[0]
    else:
        selected = next((s for s in audio if s["index"] == stream), None)
        if selected is None:
            raise ValueError(f"Audio stream {stream} does not exist.")
    if selected["channels"] not in (1, 2):
        raise ValueError("Only explicit mono and stereo streams are supported in this slice.")
    return {"stream": selected, "format": data["format"], "duration": float(
        selected.get("duration", data["format"].get("duration", 0)))}


def decode_source(path, info, destination, runner, stage="processing", rate=48000):
    """Decode bounded RF64 PCM and honor independently declared AAC/MP4 timing."""
    import soundfile as sf
    stream = info["stream"]
    runner.run(["-i", path, "-map", f"0:{stream['index']}", "-vn", "-ar", rate,
                *FLOAT_SCRATCH_ARGS, destination], stage, info["duration"])
    raw_frames = sf.info(destination).frames
    timing = {"raw_decoded_frames": raw_frames, "presentation_frames": raw_frames,
              "codec_tail_padding_frames": 0, "sample_rate": rate}
    duration_ts, time_base = stream.get("duration_ts"), stream.get("time_base")
    formats = set(info["format"].get("format_name", "").split(","))
    if stream.get("codec_name") == "aac" and formats.intersection({"mov", "mp4", "m4a"}):
        if duration_ts is None or not time_base:
            raise ValueError("AAC/MP4 requires a declared presentation duration to preserve timing.")
        frames = math.ceil(Fraction(int(duration_ts)) * Fraction(time_base) * rate)
        max_padding = math.ceil(1024 * rate / int(stream["sample_rate"]))
        if frames <= 0 or not 0 <= raw_frames - frames <= max_padding:
            raise ValueError("AAC decoded length disagrees with the container presentation duration.")
        if frames != raw_frames:
            with sf.SoundFile(destination, "r+") as audio:
                audio.truncate(frames)
            if sf.info(destination).frames != frames:
                raise RuntimeError("AAC presentation scratch length was not preserved.")
        timing.update(presentation_frames=frames, codec_tail_padding_frames=raw_frames-frames,
                      presentation_duration_ts=duration_ts, time_base=time_base,
                      policy="AAC/MP4 declared presentation, rational ceiling at decoded rate")
    elif stream.get("codec_name", "").startswith("pcm_") and int(stream.get("sample_rate", 0)) == rate:
        if duration_ts is not None and time_base:
            declared = Fraction(int(duration_ts)) * Fraction(time_base) * rate
            if declared.denominator == 1 and raw_frames != declared.numerator:
                raise RuntimeError(f"Decoded frame count changed: source declares {declared.numerator}, decoded {raw_frames}.")
    return timing


class Cancelled(Exception):
    pass


class Runner:
    def __init__(self, emit=lambda event: None, cancel_file=None):
        self.emit = emit
        self.cancel_file = Path(cancel_file) if cancel_file else None
        self.peak_tree_rss = 0
        self.self_process = psutil.Process()

    def check(self):
        if self.cancel_file and self.cancel_file.exists():
            raise Cancelled("Cancelled; originals unchanged. Restart renders from the beginning.")

    def sample_memory(self):
        total = 0
        for proc in [self.self_process, *self.self_process.children(recursive=True)]:
            try:
                total += proc.memory_info().rss
            except psutil.Error:
                pass
        self.peak_tree_rss = max(self.peak_tree_rss, total)

    def run(self, args, stage, duration=0):
        self.check()
        self.emit({"stage": stage, "progress": 0})
        lines = deque(maxlen=100)
        with tempfile.TemporaryFile() as errors:
            proc = subprocess.Popen([binary("ffmpeg"), "-hide_banner", "-nostdin", "-nostats", "-protocol_whitelist", "file,pipe",
                                     "-progress", "pipe:1", *map(str, args)],
                                    stdout=subprocess.PIPE, stderr=errors, creationflags=NO_WINDOW)
            def read_progress():
                for line in iter(proc.stdout.readline, b""):
                    lines.append(line.decode("utf-8", "replace").strip())
            reader = threading.Thread(target=read_progress, daemon=True)
            reader.start()
            try:
                while proc.poll() is None:
                    self.check()
                    self.sample_memory()
                    while lines:
                        line = lines.popleft()
                        if line.startswith("out_time_us=") and duration:
                            try:
                                ratio = int(line.split("=", 1)[1]) / 1e6 / duration
                                self.emit({"stage": stage, "progress": min(1, max(0, ratio))})
                            except ValueError:
                                pass
                    time.sleep(0.08)
            finally:
                if proc.poll() is None:
                    proc.kill()
                proc.wait()
                reader.join(timeout=3)
                proc.stdout.close()
            errors.seek(max(0, errors.tell() - 24000))
            stderr = errors.read().decode("utf-8", "replace").replace("\r\n", "\n")
            if proc.returncode:
                raise RuntimeError(f"{stage}: FFmpeg failed ({proc.returncode})\n{stderr[-5000:]}")
        self.emit({"stage": stage, "progress": 1})
        return stderr


def loudness(path, runner, target=-19, peak=-1, stage="analyzing", duration=0):
    log = runner.run(["-i", path, "-map", "0:a:0", "-af",
                      f"loudnorm=I={target}:TP={peak}:LRA=50:print_format=json",
                      "-f", "null", "-"], stage, duration)
    start = log.rfind('{\n')
    if start < 0:
        raise RuntimeError("FFmpeg did not return loudness measurements.")
    return json.loads(log[start:log.index('}', start) + 1])


def final_meter(path, runner, stage="verifying", duration=0):
    """Measure final loudness and flush the true-peak meter with silent padding."""
    base = ["-i", path, "-map", "0:a:0", "-f", "null", "-"]
    loud_log = runner.run([*base[:4], "-af", "ebur128=framelog=quiet", *base[4:]], stage, duration)
    peak_log = runner.run([*base[:4], "-af", "apad=pad_len=4800,ebur128=peak=true+sample:framelog=quiet",
                           *base[4:]], stage, duration)
    loud = loud_log.rfind("Integrated loudness:")
    peak = peak_log.rfind("True peak:")
    if loud < 0 or peak < 0:
        raise RuntimeError("FFmpeg did not return final loudness measurements.")
    import re
    integrated = re.search(r"I:\s*(-?inf|-?[0-9.]+) LUFS", loud_log[loud:], re.I)
    true_peak = re.search(r"Peak:\s*(-?inf|-?[0-9.]+) dBFS", peak_log[peak:], re.I)
    if not integrated or not true_peak:
        raise RuntimeError("FFmpeg final meter output was not understood.")
    return {"input_i": None if integrated[1].lower() == "-inf" else float(integrated[1]),
            "input_tp": None if true_peak[1].lower() == "-inf" else float(true_peak[1]),
            "meter": "FFmpeg ebur128 BS.1770"}
