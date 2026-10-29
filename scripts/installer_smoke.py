"""Install the real setup into a fresh test folder, render, launch, and uninstall.

Refuses to run over an existing installed copy or desktop shortcut. Only the
installer's own uninstaller removes installed files; user-created files must stay.
"""
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time
import tomllib
import winreg

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
APP_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{F829F72B-6B34-4A74-A41F-0473369C4D93}_is1"


def registry_entry():
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, APP_KEY, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
                return winreg.QueryValueEx(key, "InstallLocation")[0]
        except FileNotFoundError:
            pass
    return None


def registry_version():
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, APP_KEY, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
                return winreg.QueryValueEx(key, "DisplayVersion")[0]
        except FileNotFoundError:
            pass
    return None


def shell_folder(identifier):
    buffer = ctypes.create_unicode_buffer(260)
    if ctypes.windll.shell32.SHGetFolderPathW(None, identifier, None, 0, buffer):
        raise OSError("Could not locate current-user shell folder")
    return Path(buffer.value)


def has_embedded_icon(path):
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    user = ctypes.WinDLL("user32", use_last_error=True)
    handle_type = wintypes.HANDLE
    large = (handle_type * 1)()
    small = (handle_type * 1)()
    shell.ExtractIconExW.argtypes = [wintypes.LPCWSTR, ctypes.c_int,
                                     ctypes.POINTER(handle_type), ctypes.POINTER(handle_type), wintypes.UINT]
    shell.ExtractIconExW.restype = wintypes.UINT
    user.DestroyIcon.argtypes = [handle_type]
    count = shell.ExtractIconExW(str(path), 0, large, small, 1)
    try:
        return count > 0 and bool(large[0] or small[0])
    finally:
        for icon in (large[0], small[0]):
            if icon:
                user.DestroyIcon(icon)


def shortcut_properties(path):
    quote = lambda value: "'" + str(value).replace("'", "''") + "'"
    command = (
        "$p=" + quote(path) + "; $w=(New-Object -ComObject WScript.Shell).CreateShortcut($p); "
        "$s=New-Object -ComObject Shell.Application; $f=$s.Namespace((Split-Path -Parent $p)); "
        "$i=$f.ParseName((Split-Path -Leaf $p)); "
        "[pscustomobject]@{Target=$w.TargetPath; Icon=$w.IconLocation; AppID=$i.ExtendedProperty('System.AppUserModel.ID')} "
        "| ConvertTo-Json -Compress"
    )
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout.strip())
    assert value["AppID"] == "Fauxphonic.LocalAudioStudio", value
    target = Path(value["Target"]).resolve()
    icon_path = Path(value["Icon"].rsplit(",", 1)[0]).resolve()
    assert target == icon_path, value
    return {"target": str(target), "icon": str(icon_path), "app_user_model_id": value["AppID"]}


def launch_and_close(executable, environment, during=None):
    process = subprocess.Popen([str(executable)], env=environment, cwd=executable.parent)
    user = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    windows = []
    @callback_type
    def collect(handle, _):
        pid = wintypes.DWORD()
        user.GetWindowThreadProcessId(handle, ctypes.byref(pid))
        if pid.value == process.pid and user.IsWindowVisible(handle):
            title = ctypes.create_unicode_buffer(512)
            user.GetWindowTextW(handle, title, 512)
            windows.append((handle, title.value))
        return True
    try:
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline and process.poll() is None:
            windows.clear()
            user.EnumWindows(collect, 0)
            if any("Fauxphonic" in title for _, title in windows):
                break
            time.sleep(0.2)
        assert any("Fauxphonic" in title for _, title in windows), windows
        if during:
            during(process)
        for handle, _ in windows:
            user.PostMessageW(handle, 0x0010, 0, 0)
        assert process.wait(timeout=15) == 0
        return [title for _, title in windows]
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def mutex_exists():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.OpenMutexW.restype = wintypes.HANDLE
    handle = kernel.OpenMutexW(0x00100000, False, r"Local\Fauxphonic-ApplicationOrWorker")
    if not handle:
        return False
    kernel.CloseHandle(handle)
    return True


def assert_install_actions_blocked(setup, uninstaller, installed, directory, process):
    setup_log = directory / "mutex-setup.log"
    setup_result = subprocess.run(
        [str(setup), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-",
         f"/DIR={installed}", f"/LOG={setup_log}"], timeout=30,
    )
    assert setup_result.returncode != 0, "Setup accepted an install/update while the app mutex was active."
    assert process.poll() is None and mutex_exists(), "The application mutex was not held during setup."
    assert (installed / "Fauxphonic.exe").is_file(), "Setup removed or replaced the app while it was open."
    uninstall_result = subprocess.run(
        [str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
         f"/LOG={directory / 'mutex-uninstall.log'}"], timeout=30,
    )
    assert uninstall_result.returncode != 0, "Uninstall accepted while the app mutex was active."
    assert process.poll() is None and mutex_exists(), "The application mutex was not held during uninstall."
    assert (installed / "Fauxphonic.exe").is_file(), "Uninstall removed the app while it was open."
    return {"setup_exit_while_running": setup_result.returncode,
            "uninstall_exit_while_running": uninstall_result.returncode}


def make_worker_lock_fixture(path):
    with sf.SoundFile(path, "w", samplerate=48000, channels=1, subtype="PCM_16") as output:
        for start in range(0, 48000 * 600, 65536):
            count = min(65536, 48000 * 600 - start)
            samples = np.arange(start, start + count, dtype=np.float32)
            output.write((0.06 * np.sin(samples * (2 * np.pi * 233 / 48000))).astype(np.float32))


def main():
    build = json.loads((ROOT / "evidence/installer-build.json").read_text(encoding="utf-8-sig"))
    setup = Path(build["output"])
    assert hashlib.sha256(setup.read_bytes()).hexdigest() == build["installer_sha256"]
    assert registry_entry() is None, "Existing installation; test will not overwrite it."
    desktop = shell_folder(0x10) / "Fauxphonic.lnk"
    assert not desktop.exists(), "Existing desktop shortcut; test will not replace it."
    pinned = Path(os.environ["APPDATA"]) / "Microsoft/Internet Explorer/Quick Launch/User Pinned/TaskBar/Fauxphonic.lnk"
    pinned_before = pinned.exists()
    app_data = shell_folder(0x1C) / "Fauxphonic/Fauxphonic"
    database = app_data / "jobs.sqlite"
    if database.exists():
        with sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True) as db:
            assert not db.execute("SELECT 1 FROM jobs WHERE state='queued'").fetchone(), "Queued user jobs; GUI test must wait."
    directory = ROOT / ".cache" / ("installer-test-" + time.strftime("%Y%m%d-%H%M%S"))
    directory.mkdir()
    installed = directory / "Installed App"
    assert installed.resolve().is_relative_to((ROOT / ".cache").resolve())
    group_name = "Fauxphonic Installer Test " + directory.name
    menu = shell_folder(2) / group_name
    args = [str(setup), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-",
            f"/DIR={installed}", f"/GROUP={group_name}", "/TASKS=desktopicon", f"/LOG={directory / 'install.log'}"]
    result = subprocess.run(args, timeout=240)
    assert result.returncode == 0, result.returncode
    uninstaller = installed / "unins000.exe"
    evidence = {"installer_sha256": build["installer_sha256"], "test_directory": str(directory),
                "install_exit": result.returncode, "clean_windows_machine": False}
    try:
        assert Path(registry_entry()).resolve() == installed.resolve()
        expected_version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
        assert registry_version() == expected_version, registry_version()
        assert desktop.is_file() and (menu / "Fauxphonic.lnk").is_file()
        icon_paths = [setup, installed / "Fauxphonic.exe", installed / "Fauxphonic-worker.exe"]
        assert all(has_embedded_icon(path) for path in icon_paths), "A PE executable is missing its embedded icon."
        shortcut_evidence = {"desktop": shortcut_properties(desktop),
                             "start_menu": shortcut_properties(menu / "Fauxphonic.lnk")}
        assert pinned.exists() == pinned_before, "Installer changed the taskbar pinned shortcuts."
        checked = 0
        for line in (installed / "SHA256SUMS.txt").read_text(encoding="utf-8-sig").splitlines():
            expected, relative = line.split("  ", 1)
            target = (installed / relative).resolve()
            assert target.is_relative_to(installed.resolve())
            with target.open("rb") as source:
                assert hashlib.file_digest(source, "sha256").hexdigest() == expected, relative
            checked += 1
        original = installed / "user recording.wav"
        sf.write(original, 0.06*np.sin(np.arange(48000*8)*2*np.pi*233/48000), 48000)
        source_hash = hashlib.sha256(original.read_bytes()).hexdigest()
        project = installed / "user project.json"
        project.write_text('{"preserve": true}', encoding="utf-8")
        environment = os.environ.copy()
        environment["PATH"] = str(Path(os.environ["SystemRoot"]) / "System32")
        for key in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH"):
            environment.pop(key, None)
        output = directory / "verified.wav"
        run = subprocess.run([str(installed / "Fauxphonic-worker.exe"), "process", str(original),
                              "--output", str(output)], env=environment, cwd=directory,
                             capture_output=True, timeout=60)
        assert run.returncode == 0, run.stdout.decode("utf-8", "replace")
        report = json.loads(output.with_suffix(".wav.report.json").read_text(encoding="utf-8"))
        assert report["verification"]["status"] == "passed"
        gui_mutex = {}
        gui_titles = launch_and_close(
            installed / "Fauxphonic.exe", environment,
            lambda process: gui_mutex.update(assert_install_actions_blocked(
                setup, uninstaller, installed, directory, process)),
        )

        worker_source = directory / "worker-mutex-source.wav"
        make_worker_lock_fixture(worker_source)
        worker_output = directory / "worker-mutex-output.wav"
        worker = subprocess.Popen(
            [str(installed / "Fauxphonic-worker.exe"), "process", str(worker_source),
             "--output", str(worker_output)], env=environment, cwd=directory,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline and worker.poll() is None and not mutex_exists():
                time.sleep(0.02)
            assert worker.poll() is None and mutex_exists(), "Worker ended before holding the installer mutex."
            worker_mutex = assert_install_actions_blocked(setup, uninstaller, installed, directory, worker)
        finally:
            if worker.poll() is None:
                worker.terminate()
                try:
                    worker.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    worker.kill()
                    worker.wait()
        evidence.update({"payload_files_verified": checked, "shortcuts_created": True,
                         "installed_version": registry_version(),
                         "embedded_icons_verified": [path.name for path in icon_paths],
                         "shortcut_branding": shortcut_evidence,
                         "automatic_taskbar_pin": False,
                         "gui_titles": gui_titles,
                         "mutex_install_uninstall_rejected_for_gui": gui_mutex,
                         "mutex_install_uninstall_rejected_for_worker": worker_mutex,
                         "render_verification": report["verification"], "developer_paths_removed": True})
    finally:
        # Bound the uninstaller to the just-created, verified test installation.
        assert uninstaller.resolve().is_relative_to(directory.resolve())
        cleanup = subprocess.run([str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                                  f"/LOG={directory / 'uninstall.log'}"], timeout=120)
        assert cleanup.returncode == 0, cleanup.returncode
    assert not (installed / "Fauxphonic.exe").exists()
    assert not desktop.exists() and not menu.exists()
    assert registry_entry() is None
    assert hashlib.sha256(original.read_bytes()).hexdigest() == source_hash
    assert project.read_text(encoding="utf-8") == '{"preserve": true}'
    assert output.is_file()
    evidence.update({"uninstall_exit": cleanup.returncode, "shortcuts_and_registration_removed": True,
                     "user_recording_project_and_export_preserved": True})
    (ROOT / "evidence/installer-smoke.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()
