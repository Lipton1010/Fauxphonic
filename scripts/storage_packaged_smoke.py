"""Verify the real portable storage/model implementation with private fixtures."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import soundfile as sf
from PyInstaller.archive.readers import CArchiveReader

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from local_audio_studio import storage
from local_audio_studio.contracts import Project, Track
from local_audio_studio.presets import make_preset


def digest(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--powershell", type=Path, required=True, help="Existing workspace PowerShell host; no policy overrides")
    args = parser.parse_args()
    bundle, registry = args.bundle.resolve(), args.registry.resolve()
    run = storage.begin(ROOT / ".test-runs", "test")
    payload = run / "payload"
    payload.mkdir()
    results = ROOT / "evidence" / "local" / ("storage-package-" + run.name)
    results.mkdir(parents=True)
    evidence_path = ROOT / "evidence" / "storage-packaged-acceptance.json"
    run_evidence = results / "acceptance.json"
    evidence = {"status": "running", "bundle": str(bundle), "test_run": str(run),
                "results": str(results), "installed_app_modified": False,
                "network": "No download code used; HTTP(S) proxy disabled. Not an OS firewall test.",
                "human_listening": "not performed", "checks": {}}
    environment = os.environ.copy()
    environment.update(LOCALAPPDATA=str(payload / "private-model-settings"),
                       PATH=str(Path(os.environ["SystemRoot"]) / "System32"),
                       HTTP_PROXY="http://127.0.0.1:9", HTTPS_PROXY="http://127.0.0.1:9", NO_PROXY="",
                       OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", PYTHONUTF8="1")
    for key in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "QT_QPA_PLATFORM", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH"):
        environment.pop(key, None)
    worker = bundle / "Fauxphonic-worker.exe"
    registry_before = digest(registry)
    fixture = ROOT / "tests" / "fixtures" / "silero-leveling-tts.wav"
    source_before = digest(fixture)

    def call(*arguments, success=True):
        process = subprocess.run([str(worker), *map(str, arguments)], cwd=payload, env=environment,
                                 capture_output=True, text=True, encoding="utf-8", timeout=120)
        assert (process.returncode == 0) == success, process.stdout + process.stderr
        return json.loads(process.stdout) if success else process.stdout

    def save(name, value):
        path = payload / name
        path.write_text(json.dumps(value, indent=2), encoding="utf-8")
        return path

    try:
        # Verify the exact code embedded in both executables, not imported source.
        parity = {}
        for executable, modules in {
            "Fauxphonic.exe": ["gui", "storage_dialog", "desktop_status", "storage", "model_store"],
            "Fauxphonic-worker.exe": ["cli", "engine", "storage", "model_store"],
        }.items():
            archive = CArchiveReader(str(bundle / executable)).open_embedded_archive("PYZ.pyz")
            for module in modules:
                embedded = archive.extract("local_audio_studio." + module)
                source = ROOT / "src" / "local_audio_studio" / (module + ".py")
                assert embedded == compile(source.read_bytes(), embedded.co_filename, "exec"), module
                parity[executable + ":" + module] = True
        evidence["checks"]["embedded_code_matches_source"] = parity
        count = 0
        for line in (bundle / "SHA256SUMS.txt").read_text(encoding="utf-8-sig").splitlines():
            checksum, name = line.split("  ", 1)
            assert digest(bundle / name) == checksum, name
            count += 1
        evidence["checks"]["verified_payload_files"] = count
        assert call("resolve-model", "--registry", registry)["hash_verified"] is False
        resolved = call("resolve-model", "--registry", registry, "--select")
        assert resolved["hash_verified"] and resolved["copied_bytes"] == 0
        model_before = digest(resolved["path"])
        settings = Path(environment["LOCALAPPDATA"]) / "Fauxphonic" / "model-store.json"
        selected_settings = settings.read_bytes()
        bad_registry = json.loads(registry.read_text(encoding="utf-8"))
        next(entry for entry in bad_registry["models"] if entry["id"] == resolved["id"])["format"] = "gguf"
        call("resolve-model", "--registry", save("wrong-format.json", bad_registry), "--select", success=False)
        assert settings.read_bytes() == selected_settings
        output = results / "cli-final.wav"
        subprocess_result = subprocess.run([str(worker), "process", str(fixture), "--output", str(output),
            "--preset", "natural-speech"], cwd=payload, env=environment, capture_output=True,
            text=True, encoding="utf-8", timeout=120)
        assert subprocess_result.returncode == 0, subprocess_result.stdout + subprocess_result.stderr
        report = json.loads(output.with_suffix(".wav.report.json").read_text(encoding="utf-8"))
        assert report["verification"]["status"] == "passed"
        assert report["leveling"]["model_sha256"] == model_before
        assert sf.info(output).frames == sf.info(fixture).frames
        evidence["checks"]["shared_model_inference"] = {"resolved": resolved,
            "frames": sf.info(output).frames, "verification": report["verification"]}
        # A missing selected model must fail, never use the bundled copy.
        missing = json.loads(selected_settings)
        missing["directory"] = str(payload / "missing-selected-model")
        settings.write_text(json.dumps(missing), encoding="utf-8")
        call("process", fixture, "--output", results / "must-not-exist.wav", "--preset", "natural-speech", success=False)
        assert not (results / "must-not-exist.wav").exists()
        settings.write_bytes(selected_settings)
        evidence["checks"]["wrong_format_and_missing_selection_refused"] = True
        preview = call("storage", "preview", results)
        assert len(preview["candidates"]) == 1  # Completed CLI render; GUI is tested below.
        manifest = save("render-preview.json", preview)
        call("storage", "quarantine", manifest, success=False)
        before_outputs = {path.name: digest(path) for path in results.glob("*-final.wav*")}
        moved = call("storage", "quarantine", manifest, "--confirm")
        for entry in moved["moved"]:
            call("storage", "restore", entry["receipt"])
        assert before_outputs == {path.name: digest(path) for path in results.glob("*-final.wav*")}
        evidence["checks"]["quarantine_restore_preserves_finals"] = True
        # Deletion is limited to a fresh 16-byte synthetic intermediate.
        tiny = payload / "tiny-delete-root"
        completed = payload / "tiny-result.json"
        completed.write_text('{"status":"passed"}', encoding="utf-8")
        creator = "from pathlib import Path; from local_audio_studio import storage; import sys; p=storage.begin(Path(sys.argv[1]),'render'); (p/'tiny.pcm').write_bytes(b'0123456789abcdef'); storage.complete(p,[Path(sys.argv[2])])"
        subprocess.run([sys.executable, "-c", creator, str(tiny), str(completed)], cwd=ROOT,
                       env={**os.environ, "PYTHONPATH": str(ROOT / "src")}, check=True)
        tiny_preview = call("storage", "preview", tiny)
        assert tiny_preview["bytes"] == 16 and len(tiny_preview["candidates"]) == 1
        tiny_manifest = save("tiny-preview.json", tiny_preview)
        call("storage", "delete", tiny_manifest, success=False)
        injected = Path(tiny_preview["candidates"][0]["directory"]) / "unknown.txt"
        injected.write_text("preserve", encoding="utf-8")
        call("storage", "delete", tiny_manifest, "--confirm", success=False)
        assert injected.read_text() == "preserve"
        injected.unlink()  # Exact temporary injection created immediately above.
        deleted = call("storage", "delete", tiny_manifest, "--confirm")
        assert deleted["logical_bytes_removed"] == 16 and completed.exists()
        evidence["checks"]["confirmed_delete_and_stale_refusal"] = deleted
        # Actual packaged GUI, its native controls and its QProcess worker.
        project = Project([Track(str(fixture), role="speech")], make_preset("natural-speech"))
        (results / "gui-project.json").write_text(json.dumps(project.to_dict()), encoding="utf-8")
        gui = subprocess.run([str(args.powershell), "-NoProfile", "-File", str(ROOT / "scripts/storage_packaged_ui.ps1"),
            "-Bundle", str(bundle), "-Work", str(results), "-Registry", str(registry)],
            cwd=payload, env=environment, capture_output=True, text=True, encoding="utf-8", timeout=240)
        (results / "gui-harness.stdout.txt").write_text(gui.stdout, encoding="utf-8")
        (results / "gui-harness.stderr.txt").write_text(gui.stderr, encoding="utf-8")
        assert (results / "gui-result.json").is_file(), gui.stdout + gui.stderr
        gui_result = json.loads((results / "gui-result.json").read_text(encoding="utf-8-sig"))
        evidence["checks"]["native_gui"] = gui_result
        assert gui.returncode == 0 and gui_result["status"] == "passed", gui.stdout + gui.stderr
        assert Path(gui_result["model_directory"]) == Path(resolved["path"]).parent
        database = results / "gui-data" / "jobs.sqlite"
        with sqlite3.connect(database) as db:
            assert db.execute("SELECT state FROM jobs").fetchall() == [("complete",)]
        assert len(call("storage", "preview", results)["candidates"]) == 2
        assert digest(fixture) == source_before and digest(registry) == registry_before
        assert digest(resolved["path"]) == model_before
        evidence["checks"]["original_registry_and_model_unchanged"] = True
        evidence["executables"] = {name: digest(bundle / name) for name in ("Fauxphonic.exe", "Fauxphonic-worker.exe")}
        evidence["bundle_bytes"] = sum(p.stat().st_size for p in bundle.rglob("*") if p.is_file())
        evidence["status"] = "passed"
    except BaseException as exc:
        evidence.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        run_evidence.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
        evidence_path.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    storage.complete(run, [run_evidence])
    print(json.dumps({"status": evidence["status"], "evidence": str(evidence_path), "results": str(results)}, indent=2))


if __name__ == "__main__":
    main()
