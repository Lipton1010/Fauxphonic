import json
import os
from pathlib import Path
import time

import pytest

from local_audio_studio import cli, storage


def finished(tmp_path, monkeypatch, kind="test"):
    root = tmp_path / (storage.KINDS[kind] or "exports")
    folder = storage.begin(root, kind)
    (folder / "payload").mkdir()
    (folder / "payload" / "intermediate.pcm").write_bytes(b"disposable")
    result = tmp_path / (folder.name + ".result.json")
    result.write_text('{"verification":"passed"}')
    storage.complete(folder, [result])
    monkeypatch.setattr(storage.psutil, "Process", dead_process)
    return root, folder, result


def dead_process(pid):
    raise storage.psutil.NoSuchProcess(pid)


def old_preview(root):
    return storage.preview(root, now=time.time() + 15 * 86400)


def test_preview_confirmation_quarantine_restore_preserve_result(tmp_path, monkeypatch):
    root, folder, result = finished(tmp_path, monkeypatch)
    manifest = old_preview(root)
    assert manifest["bytes"] == 10
    assert folder.is_dir()
    with pytest.raises(ValueError, match="confirmation"):
        storage.quarantine(manifest)
    moved = storage.quarantine(manifest, confirmed=True)
    assert moved["reclaimed_bytes"] == 0
    assert not folder.exists()
    assert result.exists()
    record = moved["moved"][0]
    assert Path(record["quarantined"]).is_dir()
    assert storage.restore(record["receipt"]) == {"restored": str(folder)}
    assert (folder / "payload" / "intermediate.pcm").read_bytes() == b"disposable"
    with pytest.raises(ValueError):
        storage.restore(record["receipt"])


@pytest.mark.parametrize("change", ["unexpected", "same_size", "result", "pinned", "active"])
def test_stale_or_protected_manifest_never_moves(tmp_path, monkeypatch, change):
    root, folder, result = finished(tmp_path, monkeypatch)
    manifest = old_preview(root)
    if change == "unexpected":
        (folder / "keep.wav").write_bytes(b"user file")
    elif change == "same_size":
        path = folder / "payload" / "intermediate.pcm"
        path.write_bytes(b"different!")
        os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 1))
    elif change == "result":
        result.unlink()
    elif change == "pinned":
        owner = json.loads((folder / storage.MARKER).read_text())
        owner["pinned"] = True
        storage._write(folder / storage.MARKER, owner)
    else:
        class Active:
            def create_time(self):
                return json.loads((folder / storage.MARKER).read_text())["process_created"]
        monkeypatch.setattr(storage.psutil, "Process", lambda pid: Active())
    with pytest.raises((ValueError, OSError)):
        storage.quarantine(manifest, confirmed=True)
    assert folder.exists()
    assert not (root / ".quarantine").exists()


def test_unknown_failed_and_recent_runs_stay(tmp_path, monkeypatch):
    root, folder, result = finished(tmp_path, monkeypatch)
    assert not storage.preview(root)["candidates"]
    unknown = root / "historical-test-audio"
    unknown.mkdir()
    (unknown / "original.wav").write_bytes(b"user source")
    # A failed/active receipt can never be inferred successful from a dead PID.
    monkeypatch.undo()
    failed = storage.begin(root, "test")
    monkeypatch.setattr(storage.psutil, "Process", dead_process)
    manifest = old_preview(root)
    assert [item["directory"] for item in manifest["candidates"]] == [str(folder)]
    assert {item["directory"] for item in manifest["preserved"]} == {str(unknown), str(failed)}
    assert result.exists()


def test_root_escape_and_active_owner_and_final_in_payload_rejected(tmp_path):
    root = tmp_path / ".test-runs"
    folder = storage.begin(root, "test")
    assert not old_preview(root)["candidates"]
    inside = folder / "final.json"
    inside.write_text("keep")
    with pytest.raises(ValueError, match="outside"):
        storage.complete(folder, [inside])
    with pytest.raises(ValueError):
        storage.begin(tmp_path / "source", "test")


def test_manifest_cannot_target_other_root(tmp_path, monkeypatch):
    root, folder, _ = finished(tmp_path, monkeypatch)
    manifest = old_preview(root)
    manifest["root"] = str(tmp_path)
    with pytest.raises(ValueError, match="out-of-root"):
        storage.quarantine(manifest, confirmed=True)
    assert folder.exists()


def test_selected_model_storage_is_preserved_even_inside_owned_payload(tmp_path, monkeypatch):
    from local_audio_studio import model_store
    root, folder, _ = finished(tmp_path, monkeypatch)
    manifest = old_preview(root)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    model_store.select_storage(folder / "payload")
    with pytest.raises(ValueError, match="selected model"):
        storage.quarantine(manifest, confirmed=True)
    assert folder.exists()


def test_permanent_delete_requires_confirmation_and_keeps_compact_records(tmp_path, monkeypatch):
    root, folder, result = finished(tmp_path, monkeypatch)
    manifest = old_preview(root)
    with pytest.raises(ValueError, match="confirmation"):
        storage.delete_reviewed(manifest)
    deleted = storage.delete_reviewed(manifest, confirmed=True)
    assert deleted["logical_bytes_removed"] == 10
    assert not (folder / "payload" / "intermediate.pcm").exists()
    assert result.exists()
    assert json.loads((folder / storage.MARKER).read_text())["state"] == "cleaned"
    assert not old_preview(root)["candidates"]


def test_permanent_delete_preserves_stale_unknown_files(tmp_path, monkeypatch):
    root, folder, result = finished(tmp_path, monkeypatch)
    manifest = old_preview(root)
    user_file = folder / "original.wav"
    user_file.write_bytes(b"preserve")
    with pytest.raises(ValueError, match="unexpected"):
        storage.delete_reviewed(manifest, confirmed=True)
    assert user_file.read_bytes() == b"preserve"
    assert (folder / "payload" / "intermediate.pcm").exists()
    assert result.exists()


def test_native_permanent_delete_needs_separate_confirmation(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication, QMessageBox
    from local_audio_studio.storage_dialog import StorageDialog
    app = QApplication.instance() or QApplication([])
    root, folder, _ = finished(tmp_path, monkeypatch)
    dialog = StorageDialog()
    dialog.manifest = old_preview(root)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.No)
    dialog.delete()
    assert (folder / "payload" / "intermediate.pcm").exists()
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
    dialog.delete()
    assert not (folder / "payload" / "intermediate.pcm").exists()
    assert dialog.manifest is None
    dialog.close()
    app.processEvents()


def test_shared_or_reparse_files_refused_without_creating_links(tmp_path, monkeypatch):
    root, folder, _ = finished(tmp_path, monkeypatch)
    original = Path.stat
    def shared(path, *args, **kwargs):
        info = original(path, *args, **kwargs)
        if path.name == "intermediate.pcm":
            class Shared:
                st_mode = info.st_mode
                st_nlink = 2
                st_file_attributes = info.st_file_attributes
            return Shared()
        return info
    monkeypatch.setattr(Path, "stat", shared)
    assert not old_preview(root)["candidates"]


def test_cli_preview_does_not_move_or_overwrite_reports(tmp_path, monkeypatch, capsys):
    root, folder, _ = finished(tmp_path, monkeypatch)
    report = tmp_path / "preview.json"
    assert cli.main(["storage", "preview", str(root), "--report", str(report)]) == 0
    assert json.loads(report.read_text())["operation"] == "preview_only"
    assert folder.exists()
    assert cli.main(["storage", "preview", str(root), "--report", str(report)]) == 1


def test_native_storage_dialog_requires_explicit_confirmation(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication, QMessageBox
    from local_audio_studio.storage_dialog import StorageDialog
    app = QApplication.instance() or QApplication([])
    root, folder, _ = finished(tmp_path, monkeypatch)
    dialog = StorageDialog()
    assert dialog.manifest is None
    dialog.manifest = old_preview(root)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.No)
    dialog.apply()
    assert folder.exists()
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
    dialog.apply()
    assert not folder.exists()
    assert dialog.manifest is None
    dialog.close()
    app.processEvents()


def test_queued_render_waits_for_committed_completion(tmp_path, monkeypatch):
    from local_audio_studio.jobs import Jobs
    root, folder, result = finished(tmp_path, monkeypatch, "render")
    database = tmp_path / "jobs.sqlite"
    jobs = Jobs(database)
    key = jobs.add({"output": str(result)})
    storage.complete(folder, [result], {"database": str(database), "id": key})
    assert not storage.preview(root)["candidates"]
    jobs.update(key, {"stage": "complete"})
    assert len(storage.preview(root)["candidates"]) == 1
    jobs.db.close()


def test_reparse_ancestor_is_refused_without_link_creation(tmp_path, monkeypatch):
    original = Path.lstat
    class Reparse:
        st_file_attributes = 0x400
        st_mode = 0
    monkeypatch.setattr(Path, "lstat", lambda path, *a, **kw:
                        Reparse() if path == tmp_path else original(path, *a, **kw))
    with pytest.raises(ValueError, match="Reparse"):
        storage.safe_path(tmp_path / "child")


@pytest.mark.parametrize("kind,keep", [("test", 3), ("build", 2)])
def test_retention_preview_preserves_recent_payloads(tmp_path, monkeypatch, kind, keep):
    root = tmp_path / storage.KINDS[kind]
    folders = []
    for index in range(keep + 1):
        folder = storage.begin(root, kind)
        result = tmp_path / f"{index}.result.json"
        result.write_text("passed")
        storage.complete(folder, [result])
        folders.append(folder)
    monkeypatch.setattr(storage.psutil, "Process", dead_process)
    manifest = storage.preview(root)
    assert [item["directory"] for item in manifest["candidates"]] == [str(folders[0])]
    assert len(manifest["preserved"]) == keep


def test_successful_export_is_preserved_when_cleanup_receipt_cannot_be_written(tmp_path, monkeypatch):
    import numpy as np
    import soundfile as sf
    from local_audio_studio import engine
    from local_audio_studio.contracts import Preset, Project, Track
    source = tmp_path / "original.wav"
    sf.write(source, 0.05 * np.sin(np.arange(10001) * 2 * np.pi * 300 / 48000), 48000)
    def full(*args, **kwargs):
        raise OSError("No space for cleanup receipt")
    monkeypatch.setattr(engine, "complete_storage", full)
    output = tmp_path / "final.wav"
    report = engine.render(Project([Track(str(source))], Preset(mode="bypass")), output)
    assert report["verification"]["status"] != "failed"
    assert output.exists() and output.with_suffix(".wav.report.json").exists()
    assert not storage.preview(tmp_path)["candidates"]


def test_small_native_worker_render_and_cleanup_receipt(tmp_path):
    import numpy as np
    import soundfile as sf
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from local_audio_studio.audio import digest
    from local_audio_studio.contracts import Project, Track, Preset
    from local_audio_studio.gui import Window
    app = QApplication.instance() or QApplication([])
    source = tmp_path / "original.wav"
    sf.write(source, 0.05 * np.sin(np.arange(192001) * 2 * np.pi * 300 / 48000), 48000)
    before = digest(source)
    output = tmp_path / "final.wav"
    project = Project([Track(str(source))], Preset())
    data = tmp_path / "appdata"
    data.mkdir()
    window = Window(data)
    try:
        request = {"action": "render", "project": project.to_dict(), "output": str(output)}
        window.jobs.add(request)
        window.start_next()
        deadline = time.monotonic() + 20
        while window.process is not None and time.monotonic() < deadline:
            app.processEvents()
            QTest.qWait(20)
        assert window.process is None, window.log.toPlainText()
        assert window.jobs.rows()[0][2] == "complete", window.log.toPlainText()
        report_path = output.with_suffix(".wav.report.json")
        report = json.loads(report_path.read_text())
        assert report["verification"]["status"] == "passed"
        assert sf.info(output).frames == 192001
        assert digest(source) == before
        manifest = storage.preview(tmp_path)
        assert len(manifest["candidates"]) == 1
        # Worker exited and SQLite completion committed before eligibility.
        moved = storage.quarantine(manifest, confirmed=True)
        assert source.exists() and output.exists() and report_path.exists()
        storage.restore(moved["moved"][0]["receipt"])
    finally:
        window.close()
        app.processEvents()
