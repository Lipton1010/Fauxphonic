"""Independent measurements from the exact packaged worker; never listening approval."""
import hashlib
import json
from pathlib import Path
import subprocess
import uuid
import xml.etree.ElementTree as ET

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / ".cache" / ("release-audio-0.2.2-" + uuid.uuid4().hex)
WORK.mkdir()
WORKER = ROOT / "dist/Fauxphonic/Fauxphonic-worker.exe"
SOURCE = ROOT / "tests/fixtures/silero-leveling-tts.wav"
RATE = 48000


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def render(source, name, preset, stems=False, should_fail=False):
    output = WORK / name
    request = WORK / (name + ".request.json")
    request.write_text(json.dumps({"action": "render", "project": {
        "tracks": [{"path": str(source), "role": "speech"}], "preset": preset, "export_stems": stems},
        "output": str(output)}), encoding="utf-8")
    result = subprocess.run([str(WORKER), "worker", str(request)], capture_output=True, timeout=120)
    (WORK / (name + ".worker.log")).write_bytes(result.stdout + result.stderr)
    if should_fail:
        assert result.returncode != 0 and not output.exists()
        return {"rejected": True, "worker_exit": result.returncode}
    assert result.returncode == 0, result.stdout.decode("utf-8", "replace")
    report = json.loads(output.with_suffix(output.suffix + ".report.json").read_text(encoding="utf-8"))
    assert output.exists() and report["sha256"] == sha(output)
    return report


before = sha(SOURCE)
evidence = {"version": "0.2.2", "worker_sha256": sha(WORKER), "source_sha256": before,
            "work": str(WORK), "listening_evaluation": "not performed", "delivery": [], "cleanup": []}
for suffix in ("wav", "flac", "mp3", "m4a", "opus"):
    for target in (-19, -16, -14):
        report = render(SOURCE, f"speech-{target}.{suffix}", {"target_lufs": target})
        verification = report["verification"]
        assert verification["status"] == "passed"
        assert verification["samples"]["frames"] == sf.info(SOURCE).frames
        evidence["delivery"].append({"format": suffix, "target_lufs": target,
            "measurement": verification["measurements"], "correction_attempts": report["correction_attempts"]})

speech, _ = sf.read(SOURCE)
for color in ("white", "pink"):
    clean = np.pad(speech, (RATE*3, RATE*3))
    noise = np.random.default_rng(27491).normal(size=len(clean))
    if color == "pink":
        spectrum = np.fft.rfft(noise)
        spectrum /= np.sqrt(np.maximum(np.fft.rfftfreq(len(noise), 1/RATE), 20))
        noise = np.fft.irfft(spectrum, len(noise))
    noise *= 10**(-35/20)/np.sqrt(np.mean(noise**2))
    source = WORK / (color + "-noise.wav")
    sf.write(source, clean + noise, RATE, subtype="FLOAT")
    measurements = []
    for amount in (6, 9):
        report = render(source, f"{color}-cleanup-{amount}.wav", {"mode": "dsp-cleanup", "noise_reduction_db": amount}, True)
        actual, _ = sf.read(report["stems"][0]["path"])
        assert len(actual) == len(clean)
        interval = slice(RATE, 2*RATE)
        reduction = float(10*np.log10(np.mean(noise[interval]**2)/np.mean(actual[interval]**2)))
        retained = float(np.dot(actual[3*RATE:3*RATE+len(speech)], speech)/np.dot(speech, speech))
        assert reduction >= (2 if amount == 6 else 4) and 0.85 < retained < 1.05
        measurements.append(reduction)
        evidence["cleanup"].append({"noise": color, "setting_db": amount, "noise_rms_dbfs": -35,
            "measured_noise_attenuation_db": reduction, "speech_projection_gain": retained,
            "profile": report["processing"][0]["noise_profile"]})
    assert measurements[1] > measurements[0]+0.5

silent = WORK / "silence.wav"
sf.write(silent, np.zeros(RATE*4), RATE)
evidence["silence"] = {}
for suffix in ("wav", "mp3"):
    report = render(silent, "silence-master." + suffix, {})
    assert report["verification"]["status"] == "exception"
    json.dumps(report, allow_nan=False)
    evidence["silence"][suffix] = report["verification"]
damaged = WORK / "truncated.wav"
damaged.write_bytes(SOURCE.read_bytes()[:SOURCE.stat().st_size//2])
evidence["damaged_input"] = render(damaged, "withheld.wav", {}, should_fail=True)
assert sha(SOURCE) == before
evidence["original_unchanged"] = True
suite = ET.parse(ROOT / "evidence/audit-final-0.2.2-tests.xml").getroot().find("testsuite")
assert suite.attrib["failures"] == suite.attrib["errors"] == "0"
evidence["source_tests"] = suite.attrib
evidence["source_hashes"] = {str(path.relative_to(ROOT)): sha(path) for path in (ROOT / "src/local_audio_studio").glob("*.py")}
evidence["status"] = "passed"
(ROOT / "evidence/audit-audio-0.2.2.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
print(json.dumps({"status": "passed", "delivery_cases": len(evidence["delivery"]), "cleanup": evidence["cleanup"]}, indent=2))
