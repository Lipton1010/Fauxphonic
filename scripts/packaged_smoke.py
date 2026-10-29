"""Exercise a relocated portable build with local speech and no developer PATH."""
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import uuid

import soundfile as sf

root = Path(__file__).resolve().parents[1]
destination = root / ".cache" / ("portable-" + time.strftime("%Y%m%d-%H%M%S"))
shutil.copytree(root / "dist" / "Fauxphonic", destination)
work = destination.parent / (destination.name + "-work")
work.mkdir()
source = work / "portable test été.wav"
shutil.copyfile(root / "tests/fixtures/silero-leveling-tts.wav", source)
source_info = sf.info(source)
assert source_info.frames >= 12 * source_info.samplerate, source_info
output = work / "verified.wav"
environment = os.environ.copy()
environment["PATH"] = str(Path(os.environ["SystemRoot"]) / "System32")
for key in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH"):
    environment.pop(key, None)
environment["HTTP_PROXY"] = environment["HTTPS_PROXY"] = "http://127.0.0.1:9"
environment["NO_PROXY"] = ""
worker = destination / "Fauxphonic-worker.exe"
command = [str(worker), "process", str(source), "--output", str(output), "--preset", "natural-speech"]
result = subprocess.run(command, cwd=work, env=environment, capture_output=True, timeout=240)
stdout = result.stdout.decode("utf-8", "replace")
assert result.returncode == 0, stdout + result.stderr.decode("utf-8", "replace")
report = json.loads(output.with_suffix(".wav.report.json").read_text(encoding="utf-8"))
assert report["verification"]["status"] == "passed"
assert report.get("leveling", {}).get("enabled") is True, report.get("leveling")
assert report["leveling"].get("model_sha256") == "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3"

# Exercise the app's actual worker request protocol and the full-render preview path.
request_dir = work / "preview-work"
request_dir.mkdir()
data_dir = work / "app-data"
request = {
    "action": "preview",
    "project": report["project"],
    "start_sample": 0, "duration_samples": 10 * 48000,
    "cache_root": str(data_dir / "preview-cache"),
    "work_root": str(data_dir / "preview-work"),
    "active_dir": str(data_dir / "previews" / ("preview-active-" + uuid.uuid4().hex)),
    "owner_pid": os.getpid(),
}
request_path = request_dir / "preview-request.json"
request_path.write_text(json.dumps(request), encoding="utf-8")
preview = subprocess.run([str(worker), "worker", str(request_path)], cwd=work, env=environment,
                         capture_output=True, text=True, timeout=600)
preview_stdout = preview.stdout
assert preview.returncode == 0, preview_stdout + preview.stderr
events = [json.loads(line) for line in preview_stdout.splitlines() if line.startswith("{")]
completed = next((event["preview"] for event in reversed(events)
                  if event.get("stage") == "complete" and "preview" in event), None)
assert completed is not None, preview_stdout
assert completed["processed_is_crop_of_full_render"] is True
assert completed["leveling"]["enabled"] is True
assert completed["leveling"]["model_sha256"] == report["leveling"]["model_sha256"]
preview_paths = [Path(completed[name]) for name in ("original", "processed")]
for path in preview_paths:
    info = sf.info(path)
    assert info.samplerate == 48000 and info.frames == 10 * 48000 and info.channels == 1, info

gui = subprocess.Popen([str(destination / "Fauxphonic.exe")], cwd=work, env=environment)
windows = []
user = ctypes.WinDLL("user32", use_last_error=True)
callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

@callback_type
def enum_window(handle, _):
    pid = wintypes.DWORD()
    user.GetWindowThreadProcessId(handle, ctypes.byref(pid))
    if pid.value == gui.pid and user.IsWindowVisible(handle):
        buffer = ctypes.create_unicode_buffer(512)
        user.GetWindowTextW(handle, buffer, 512)
        windows.append((handle, buffer.value))
    return True

try:
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline and gui.poll() is None:
        windows.clear()
        user.EnumWindows(enum_window, 0)
        if any("Fauxphonic" in title for _, title in windows):
            break
        time.sleep(0.2)
    if gui.poll() is not None or not any("Fauxphonic" in title for _, title in windows):
        messages = []

        @callback_type
        def child_text(handle, _):
            buffer = ctypes.create_unicode_buffer(12000)
            user.GetWindowTextW(handle, buffer, len(buffer))
            messages.append(buffer.value)
            return True

        for handle, _ in windows:
            user.EnumChildWindows(handle, child_text, 0)
        raise AssertionError((gui.poll(), windows, messages))
    gui_titles = [title for _, title in windows]
    for handle, _ in windows:
        user.PostMessageW(handle, 0x0010, 0, 0)
    gui.wait(timeout=15)
finally:
    if gui.poll() is None:
        gui.kill()
        gui.wait()

evidence = {
    "relocated_folder_name": destination.name,
    "working_directory_name": work.name,
    "developer_python_path_removed": True,
    "network": "HTTP(S) proxy points to closed local port; this is not a full OS firewall test",
    "synthetic_tts": {"source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                      "voice_provider": "Known-text Microsoft David Windows SAPI fixture", "human_listening": False},
    "worker_exit_code": result.returncode,
    "preview_worker_exit_code": preview.returncode,
    "preview_cache_key": completed["cache_key"],
    "preview_pair_sha256": {name: hashlib.sha256(path.read_bytes()).hexdigest()
                             for name, path in zip(("original", "processed"), preview_paths)},
    "preview_samples": completed["duration_samples"],
    "preview_is_crop_of_full_render": completed["processed_is_crop_of_full_render"],
    "vad_model_sha256": report["leveling"]["model_sha256"],
    "gui_titles": gui_titles,
    "gui_exit_code": gui.returncode,
    "render_verification": report["verification"]["status"],
    "clean_windows_machine": False,
}
(root / "evidence" / "packaged-smoke.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
print(json.dumps({"worker": "passed", "preview": "passed", "gui": gui_titles,
                  "model_sha256": evidence["vad_model_sha256"]}))
