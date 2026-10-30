import json
from pathlib import Path
import time

import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QFrame, QLabel, QMessageBox, QPushButton, QScrollArea, QStyle, QStyleOptionSpinBox
import soundfile as sf

from local_audio_studio.contracts import GainPoint
from local_audio_studio.desktop_status import _status_html, capability_snapshot
from local_audio_studio.gui import CapabilityDialog, GainEnvelopeDialog, MetadataDialog, SUPPORTED_MEDIA, Window


def wait_until(app, predicate, seconds=30):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        QTest.qWait(25)
    raise AssertionError("Native UI condition timed out")


def click(window, text):
    button = next(b for b in window.findChildren(QPushButton) if b.text() == text)
    QTest.mouseClick(button, Qt.LeftButton)


def test_capabilities_failure_is_visible_instead_of_silently_losing_dialog(tmp_path, monkeypatch):
    import local_audio_studio.gui as gui
    app = QApplication.instance() or QApplication([])
    window = Window(tmp_path)
    warnings = []
    def broken(*args):
        raise OSError("No inherited console handle")
    monkeypatch.setattr(gui, "CapabilityDialog", broken)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[1:]))
    window.show_capabilities()
    assert warnings == [("Capabilities unavailable", "No inherited console handle")]
    assert "OSError: No inherited console handle" in window.log.toPlainText()
    window.close()
    app.processEvents()


def test_capabilities_handles_frozen_gui_onnx_namespace(monkeypatch):
    import sys
    from types import SimpleNamespace
    monkeypatch.setitem(sys.modules, "onnxruntime", SimpleNamespace())
    snapshot = capability_snapshot()
    assert snapshot["model"]["ok"]
    assert snapshot["model"]["providers"] == []
    assert "Silero" in _status_html(snapshot)


def test_native_import_analysis_worker_render_save_playback(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    source = tmp_path / "native été.wav"
    t = np.arange(48000*8)/48000
    sf.write(source, 0.05*np.sin(2*np.pi*337*t), 48000)
    window = Window(tmp_path)
    window.show()
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *a: ([str(source)], ""))
    click(window, "Import audio…")
    assert window.tracks.rowCount() == 1
    click(window, "Analyze selected")
    wait_until(app, lambda: window.process is None)
    assert len(window.waveform.peaks) > 100
    assert "Measured" in window.log.toPlainText()
    output = tmp_path / "native verified.wav"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **kw: (str(output), ""))
    click(window, "Export audio…")
    wait_until(app, lambda: window.process is None)
    assert output.exists(), window.log.toPlainText()
    assert window.jobs.rows()[0][2] == "complete"
    assert json.loads(Path(window.last_report).read_text())["verification"]["status"] == "passed"
    assert "Mastering mode:" in window.log.toPlainText()
    project = tmp_path / "session.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (str(project), ""))
    click(window, "Save project…")
    window.tracks.setRowCount(0)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a: (str(project), ""))
    click(window, "Open project…")
    assert window.tracks.rowCount() == 1
    # Playback is muted for automation. This tests transport; it is not listening.
    window.audio.setMuted(True)
    assert window.last_output is None  # Opening a project clears results from the previous session.
    click(window, "Play original")
    wait_until(app, lambda: window.player.duration() > 0)
    wait_until(app, lambda: window.player.position() > 2000)
    window.player.setPosition(4000)
    wait_until(app, lambda: window.player.position() >= 4000)
    click(window, "Pause")
    window.grab().save(str(tmp_path / "workflow.png"))
    window.close()
    app.processEvents()


def test_analysis_uses_selected_data_directory(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    source = tmp_path / "source.wav"
    sf.write(source, np.zeros(48000), 48000)
    window = Window(tmp_path)
    window.add_paths([source])
    requests = []
    monkeypatch.setattr(window, "launch", requests.append)
    window.analyze_selected()
    assert Path(requests[0]["scratch_dir"]) == tmp_path / "analysis"
    window.close()
    app.processEvents()


@pytest.mark.parametrize("preset", ["natural-speech", "strong-cleanup", "multitrack"])
def test_cleanup_blocks_export_and_preview_without_a_speech_track(tmp_path, monkeypatch, preset):
    app = QApplication.instance() or QApplication([])
    source = tmp_path / "program.wav"
    sf.write(source, np.zeros(48000), 48000)
    window = Window(tmp_path)
    window.add_paths([source])
    window.mode.setCurrentIndex(window.mode.findData(preset))
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[1:3]))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args, **kw: pytest.fail("No output dialog without an eligible role"))
    window.queue_render()
    window.build_preview()
    assert len(warnings) == 2 and all("no target" in title for title, _ in warnings)
    assert "No Speech track" in window.processing_note.text()
    assert window.process is None
    assert not window.jobs.rows()
    window.jobs.db.close()
    window.close()
    app.processEvents()


def test_named_presets_map_to_project_processing(tmp_path):
    app = QApplication.instance() or QApplication([])
    source = tmp_path / "preset.wav"
    sf.write(source, np.zeros(48000), 48000)
    window = Window(tmp_path)
    window.add_paths([source])
    cases = [("natural-speech", "dsp-cleanup", True), ("strong-cleanup", "dsp-cleanup", True),
             ("preserve-music", "master-only", False), ("multitrack", "dsp-cleanup", True),
             ("master-only", "master-only", False)]
    for name, mode, leveler in cases:
        window.mode.setCurrentIndex(window.mode.findData(name))
        project = window.project()
        assert project.preset.mode == mode
        assert project.preset.leveler_enabled is leveler
        assert project.preset.deesser_enabled is False
        assert project.preset.broad_eq_enabled is False
    assert window.project().preset.leveler_strength == window.leveler_strength.value()
    window.jobs.db.close()
    window.close()
    app.processEvents()


def test_new_project_resets_metadata_and_optional_dsp(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = Window(tmp_path)
    window.metadata = {"title": "old project"}
    window.deesser.setChecked(True)
    window.broad_eq.setChecked(True)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
    window.new_project()
    assert window.metadata == {}
    assert window.tracks.rowCount() == 0
    assert not window.deesser.isChecked() and not window.broad_eq.isChecked()
    assert window.mode.currentData() == "master-only"
    window.jobs.db.close()
    window.close()
    app.processEvents()


def test_native_window_builds_and_switches_sample_aligned_preview(tmp_path):
    app = QApplication.instance() or QApplication([])
    source = tmp_path / "preview.wav"
    samples = np.arange(25 * 48000, dtype=np.float32)
    sf.write(source, 0.08 * np.sin(2 * np.pi * 337 * samples / 48000), 48000)
    window = Window(tmp_path)
    window.audio.setMuted(True)
    window.add_paths([source])
    window.tracks.cellWidget(0, 1).setCurrentIndex(window.tracks.cellWidget(0, 1).findData("speech"))
    window.deesser.setChecked(True)
    window.deesser_amount.setValue(1.25)
    window.broad_eq.setChecked(True)
    window.broad_eq_frequency.setValue(2400)
    window.broad_eq_gain.setValue(-1.5)
    window.preview_start.setValue(5.001)
    window.preview_duration.setValue(10)
    click(window, "Build A/B preview")
    wait_until(app, lambda: window.process is None, seconds=60)
    assert window.preview_files
    assert "full program" in window.log.toPlainText()
    click(window, "Play original preview")
    wait_until(app, lambda: window.player.duration() >= 10000)
    wait_until(app, lambda: window.player.position() > 1500)
    click(window, "Play processed preview")
    assert Path(window.player.source().toLocalFile()).resolve() == Path(window.preview_files["processed"]).resolve()
    wait_until(app, lambda: window.player.position() > 1500)
    assert "A/B ready:" in window.log.toPlainText()
    assert "processed mastering" in window.log.toPlainText()
    assert window.preview_files
    assert "Track 1 de-essing: applied (Amount 1.25)" in window.log.toPlainText()
    assert "Track 1 broad EQ: applied at 2400 Hz, -1.5 dB" in window.log.toPlainText()
    window.close()
    app.processEvents()


def test_gain_envelope_dialog_and_project_round_trip(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    source = tmp_path / "envelope.wav"
    sf.write(source, np.zeros(2 * 48000), 48000)
    dialog = GainEnvelopeDialog([])
    dialog.add_point()
    dialog.table.item(0, 2).setText("-3")
    dialog.add_point()
    assert dialog.table.item(1, 0).text() == "48000"
    assert dialog.table.item(1, 1).text() == "1.000000"
    assert dialog.values() == [GainPoint(0, -3), GainPoint(48000, 0)]
    dialog.undo()
    assert dialog.table.rowCount() == 1
    assert dialog.values() == [GainPoint(0, -3)]
    dialog.undo()
    assert dialog.values() == [GainPoint(0, 0)]
    dialog.table.item(0, 2).setText("-3")

    window = Window(tmp_path)
    window.add_paths([source])
    window.tracks.item(0, 0).setData(Qt.UserRole + 1, [vars(point) for point in dialog.values()])
    window.deesser.setChecked(True)
    window.deesser_amount.setValue(1.35)
    window.broad_eq.setChecked(True)
    window.broad_eq_frequency.setValue(2700)
    window.broad_eq_gain.setValue(2.25)
    project = window.project()
    assert project.tracks[0].gain_envelope == dialog.values()
    assert (project.preset.deesser_enabled, project.preset.deesser_range) == (True, 1.35)
    assert (project.preset.broad_eq_enabled, project.preset.broad_eq_frequency_hz,
            project.preset.broad_eq_gain_db) == (True, 2700, 2.25)
    def accept_tags(dialog):
        dialog.fields["title"].setText("Rehearsal")
        dialog.fields["artist"].setText("Fauxphonic test")
        return QDialog.Accepted
    monkeypatch.setattr(MetadataDialog, "exec", accept_tags)
    window.edit_metadata()
    assert window.project().metadata == {"title": "Rehearsal", "artist": "Fauxphonic test"}
    saved = tmp_path / "automation.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(saved), ""))
    click(window, "Save project…")
    window.tracks.setRowCount(0)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(saved), ""))
    click(window, "Open project…")
    assert [GainPoint(**point) for point in window.tracks.item(0, 0).data(Qt.UserRole + 1)] == dialog.values()
    reopened = window.project().preset
    assert (reopened.deesser_enabled, reopened.deesser_range) == (True, 1.35)
    assert (reopened.broad_eq_enabled, reopened.broad_eq_frequency_hz, reopened.broad_eq_gain_db) == (True, 2700, 2.25)
    assert window.project().metadata == {"title": "Rehearsal", "artist": "Fauxphonic test"}
    window.jobs.db.close()
    window.close()
    app.processEvents()


def test_leveler_curve_import_and_role_aware_duck_stems(tmp_path):
    app = QApplication.instance() or QApplication([])
    speech, music = tmp_path / "speech.wav", tmp_path / "music.wav"
    sf.write(speech, np.zeros(48000), 48000); sf.write(music, np.zeros(48000), 48000)
    window = Window(tmp_path)
    window.add_paths([speech, music])
    window.tracks.cellWidget(0, 1).setCurrentIndex(1)
    window.tracks.cellWidget(1, 1).setCurrentIndex(2)
    assert window.ducking.isEnabled()
    assert window.export_stems.isEnabled()
    window.ducking.setChecked(True)
    window.export_stems.setChecked(True)
    project = window.project()
    assert project.preset.music_ducking_enabled and project.export_stems
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"sources": [{"path": str(speech)}, {"path": str(music)}],
        "leveling": {"tracks": [{"track": 0, "gain_curve": [
            {"sample": 0, "gain_db": 2.5}, {"sample": 48000, "gain_db": -1.0}]}]}}), encoding="utf-8")
    window.last_report = str(report)
    window.import_leveler_curves()
    curve = [GainPoint(**point) for point in window.tracks.item(0, 0).data(Qt.UserRole + 1)]
    assert curve == [GainPoint(0, 2.5), GainPoint(48000, -1.0)]
    assert not window.leveler_enabled.isChecked()
    window.mode.setCurrentIndex(window.mode.findData("natural-speech"))
    assert window.project().tracks[0].leveler_curve_imported
    assert window.project().preset.leveler_enabled
    window.tracks.cellWidget(1, 1).setCurrentIndex(0)
    assert not window.ducking.isEnabled()
    assert not window.project().preset.music_ducking_enabled
    window.jobs.db.close()
    window.close()
    app.processEvents()


def test_edits_invalidate_results_and_unfocused_wheel_preserves_settings(tmp_path):
    from PySide6.QtCore import QPoint, QPointF, QEvent
    from PySide6.QtGui import QWheelEvent
    app = QApplication.instance() or QApplication([])
    source = tmp_path / "source.wav"
    sf.write(source, np.zeros(48000), 48000)
    window = Window(tmp_path)
    window.show()
    window.add_paths([source])
    try:
        for widget in (window.target, window.mode, window.tracks.cellWidget(0, 1)):
            window.log.setFocus()
            app.processEvents()
            before = widget.currentIndex() if hasattr(widget, "currentIndex") else widget.value()
            wheel = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(), QPoint(0, 120),
                                Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
            QApplication.sendEvent(widget, wheel)
            assert (widget.currentIndex() if hasattr(widget, "currentIndex") else widget.value()) == before
        window.preview_files = {"original": "old.wav", "processed": "old-master.wav"}
        window.last_output, window.last_report = "old-master.wav", "old.report.json"
        window.target.setValue(-16)
        assert window.preview_files is None
        assert window.last_output is None and window.last_report is None
        assert not window.action_buttons["play_preview_processed"].isEnabled()
        window.tracks.item(0, 4).setText("invalid")
        window.play_original()
        assert "nonnegative integer" in window.log.toPlainText()
    finally:
        window.close()
        window.jobs.db.close()
        app.processEvents()


def test_queued_stems_reserve_names_against_other_masters(tmp_path):
    from local_audio_studio.contracts import Project, Track
    app = QApplication.instance() or QApplication([])
    source = tmp_path / "source.wav"
    sf.write(source, np.zeros(48000), 48000)
    window = Window(tmp_path)
    project = Project([Track(str(source), role="speech")], export_stems=True)
    window.jobs.add({"action": "render", "project": project.to_dict(), "output": str(tmp_path / "master.mp3")})
    assert window.output_in_use(tmp_path / "master.track-01-speech-stem.wav", project)
    window.closing = True
    window.close()
    window.jobs.db.close()
    app.processEvents()


def test_bleed_controls_persist_and_bypass_disables_automatic_stages(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    first, second = tmp_path / "speech-a.wav", tmp_path / "speech-b.wav"
    sf.write(first, np.zeros(48000), 48000); sf.write(second, np.zeros(48000), 48000)
    window = Window(tmp_path)
    window.add_paths([first, second])
    for row in range(2):
        window.tracks.cellWidget(row, 1).setCurrentIndex(1)
    assert window.bleed_reduction.isEnabled()
    window.bleed_reduction.setChecked(True)
    window.bleed_depth.setValue(4.5)
    window.bleed_attack.setValue(175)
    window.bleed_hold.setValue(450)
    window.bleed_release.setValue(700)
    window.tracks.selectRow(1)
    window.bleed_protect.setChecked(True)
    project = window.project()
    assert project.preset.bleed_reduction_enabled
    assert (project.preset.bleed_max_attenuation_db, project.preset.bleed_attack_ms,
            project.preset.bleed_hold_ms, project.preset.bleed_release_ms) == (4.5, 175, 450, 700)
    assert [track.bleed_protected for track in project.tracks] == [False, True]

    saved = tmp_path / "bleed-project.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(saved), ""))
    click(window, "Save project…")
    window.tracks.setRowCount(0)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(saved), ""))
    click(window, "Open project…")
    reopened = window.project()
    assert reopened.preset.bleed_reduction_enabled
    assert (reopened.preset.bleed_max_attenuation_db, reopened.preset.bleed_attack_ms,
            reopened.preset.bleed_hold_ms, reopened.preset.bleed_release_ms) == (4.5, 175, 450, 700)
    assert [track.bleed_protected for track in reopened.tracks] == [False, True]

    window.ducking.setChecked(True)
    window.mode.setCurrentIndex(window.mode.findData("bypass"))
    assert not window.bleed_reduction.isEnabled() and not window.bleed_reduction.isChecked()
    assert not window.ducking.isEnabled() and not window.ducking.isChecked()
    assert not window.project().preset.bleed_reduction_enabled
    window.jobs.db.close()
    window.close()
    app.processEvents()


def test_render_dialog_offers_all_formats_and_stems_notice(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    source = tmp_path / "source.wav"
    sf.write(source, np.zeros(48000), 48000)
    window = Window(tmp_path)
    window.add_paths([source])
    window.export_stems.setChecked(True)
    observed = {}
    def choose(*args, **kwargs):
        observed["filters"] = args[-1]
        return str(tmp_path / "master"), "MP3 (*.mp3)"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", choose)
    window.start_next = lambda: None
    window.queue_render()
    request = json.loads(window.jobs.rows()[0][1])
    assert Path(request["output"]) == tmp_path / "master.mp3"
    assert all(extension in observed["filters"] for extension in ("*.wav", "*.flac", "*.mp3", "*.m4a", "*.opus"))
    assert "Stems are always WAV" in window.export_stems.toolTip()
    assert "stems will be written as WAV" in window.log.toPlainText()
    window.jobs.db.close()
    window.close()
    app.processEvents()


def test_compact_window_keeps_actions_inside_850_by_650_and_shows_branding(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = Window(tmp_path)
    window.resize(850, 650)
    window.show()
    app.processEvents()
    visible = [b for b in window.findChildren(QPushButton) if b.isVisible()]
    toolbar = [window.findChild(QPushButton, "toolbarAction")]
    assert toolbar[0] is not None
    for label in ("New project", "Import audio…", "Open project…", "Save project…", "Edit gain envelope…"):
        button = next(b for b in visible if b.text() == label)
        assert window.mapFromGlobal(button.mapToGlobal(button.rect().bottomRight())).y() < window.height()
        assert window.mapFromGlobal(button.mapToGlobal(button.rect().bottomRight())).x() < window.width()
    assert window.minimumSize().width() <= 850 and window.minimumSize().height() <= 650
    left = window.findChild(QScrollArea, "mixOverviewScroll")
    inspector = window.findChild(QScrollArea, "masteringScroll")
    assert left is not None and left.verticalScrollBar().maximum() > 0
    assert not left.horizontalScrollBar().isVisible()
    assert inspector is not None and not inspector.horizontalScrollBar().isVisible()
    assert inspector.verticalScrollBar().maximum() > 0
    monitor = window.findChild(QFrame, "transportCard")
    assert monitor is not None
    for control in (window.devices, window.seek):
        right = control.mapTo(monitor, control.rect().bottomRight()).x()
        assert right <= monitor.contentsRect().right()
    assert all(window.mapToGlobal(b.geometry().center()).x() <= window.mapToGlobal(window.rect().bottomRight()).x()
               for b in visible)
    brand_icon = window.findChild(QLabel, "brand_icon")
    assert brand_icon is not None and brand_icon.pixmap() is not None and not brand_icon.pixmap().isNull()
    assert brand_icon.accessibleName() == "Fauxphonic waveform F icon"
    assert not window.windowIcon().isNull()
    assert "#0D2A2E" in window.styleSheet() and "#B74739" in window.styleSheet()
    assert window.findChild(QPushButton, "primaryAction").text() == "Export audio…"
    window.jobs.db.close()
    window.close()
    app.processEvents()


def test_capability_status_uses_installed_hashes_and_real_filter_list():
    snapshot = capability_snapshot()
    assert snapshot["model"]["ok"]
    assert snapshot["model"]["version"] == "v6.2.1"
    assert snapshot["model"]["bytes"] > 0
    assert snapshot["cpu_proof"]["ok"]
    assert snapshot["ffmpeg"]["ok"]
    assert {"afftdn", "highpass", "loudnorm", "equalizer"} <= set(snapshot["ffmpeg"]["filters"])
    assert "deesser" not in snapshot["ffmpeg"]["filters"]
    text = _status_html(snapshot).lower()
    assert "dynamic-equalizer de-essing" in text and "conservative correlation-gated bleed attenuation" in text
    for reason in ("checkpoint redistribution rights are not explicit", "checkpoint permission remains unresolved",
                   "no validated dereverb-specific backend", "no rights-cleared restoration model",
                   "missing or changed model files fail instead of falling back"):
        assert reason in text


def test_capabilities_toolbar_opens_native_status_dialog(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = Window(tmp_path)
    observed = []
    def inspect(dialog):
        observed.append(dialog.details.toPlainText())
        return 0
    monkeypatch.setattr(CapabilityDialog, "exec", inspect)
    click(window, "Capabilities…")
    assert observed and "Installed speech activity model" in observed[0]
    assert "Unavailable enhancement and restoration" in observed[0]
    window.jobs.db.close()
    window.close()
    app.processEvents()


def test_capability_dialog_imports_pinned_model_offline(tmp_path, monkeypatch):
    from local_audio_studio import model_store
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    destination = tmp_path / "models"
    destination.mkdir()
    choices = iter([str(destination), str(destination)])
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        lambda *args: (str(model_store.ROOT / "models" / model_store.MODEL_NAME), ""))
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *args: next(choices))
    monkeypatch.setattr(QMessageBox, "information", lambda *args: QMessageBox.Ok)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: QMessageBox.Ok)
    dialog = CapabilityDialog()
    dialog.choose_model_folder()
    dialog.import_model()
    assert model_store.model_path() == destination / model_store.MODEL_NAME
    assert "Verified" in dialog.details.toPlainText()
    dialog.close()
    app.processEvents()


def test_file_dialog_and_folder_import_share_supported_media_set(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    folder = tmp_path / "media"
    folder.mkdir()
    supported, unsupported = folder / "audio.aac", folder / "notes.txt"
    supported.write_bytes(b"placeholder")
    unsupported.write_text("not media")
    window = Window(tmp_path)
    seen = []
    def choose(*args):
        seen.append(args[-1])
        return ([str(supported), str(unsupported)], "")
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", choose)
    window.import_dialog()
    assert window.tracks.rowCount() == 1
    patterns = set(seen[0].removeprefix("Media (").removesuffix(")").split())
    assert patterns == {"*" + extension for extension in SUPPORTED_MEDIA}
    window.tracks.setRowCount(0)
    window.add_paths([folder])
    assert window.tracks.rowCount() == 1
    assert window.tracks.item(0, 0).text() == str(supported.resolve())
    assert "Unsupported media skipped" in window.log.toPlainText()
    window.jobs.db.close()
    window.close()
    app.processEvents()


def test_output_selection_preserves_existing_audio_reports_and_queued_names(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    source = tmp_path / "source.wav"
    sf.write(source, np.zeros(48000), 48000)
    existing = tmp_path / "source_master.wav"
    existing.write_bytes(b"keep audio")
    report = tmp_path / "source_master_2.wav.report.json"
    report.write_bytes(b"keep report")
    window = Window(tmp_path)
    window.add_paths([source])
    monkeypatch.setattr(window, "start_next", lambda: None)
    warnings, suggestions = [], []
    choices = iter([(str(existing), ""), (str(tmp_path / "bad.json"), ""),
                    (str(tmp_path / "source_master_3.wav"), ""),
                    (str(tmp_path / "source_master_4.wav"), "")])
    def choose(*args, **kwargs):
        suggestions.append(Path(args[2]))
        return next(choices)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", choose)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[1]))
    window.queue_render()
    window.queue_render()
    assert suggestions[0].name == "source_master_3.wav"
    assert suggestions[-1].name == "source_master_4.wav"
    assert warnings == ["Choose a new output name", "Choose an audio format"]
    assert existing.read_bytes() == b"keep audio" and report.read_bytes() == b"keep report"
    assert len(window.jobs.rows()) == 2
    assert window.output_in_use(source, window.project())
    window.close()
    window.jobs.db.close()
    app.processEvents()


def test_loudness_and_peak_arrows_are_stable_when_gaining_focus(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = Window(tmp_path)
    window.show()
    app.processEvents()
    for spin, step in [(window.target, 1.0), (window.peak, 0.1)]:
        for control, delta in [(QStyle.SC_SpinBoxUp, step), (QStyle.SC_SpinBoxDown, -step)]:
            window.mode.setFocus()
            app.processEvents()
            option = QStyleOptionSpinBox()
            spin.initStyleOption(option)
            bounds = spin.style().subControlRect(QStyle.CC_SpinBox, option, control, spin)
            before = spin.value()
            QTest.mouseClick(spin, Qt.LeftButton, pos=bounds.center())
            assert spin.value() == pytest.approx(before + delta)
            spin.initStyleOption(option)
            assert spin.style().subControlRect(QStyle.CC_SpinBox, option, control, spin) == bounds
    window.grab().save(str(tmp_path / "desktop.png"))
    window.close()
    window.jobs.db.close()
    app.processEvents()


def test_strong_cleanup_exports_real_wav_and_mp3_through_native_worker(tmp_path, monkeypatch):
    from local_audio_studio.audio import digest
    app = QApplication.instance() or QApplication([])
    speech, rate = sf.read(Path(__file__).parent / "fixtures" / "silero-leveling-tts.wav")
    samples = np.pad(speech, (rate*2, rate*2))
    samples += np.random.default_rng(41).normal(0, 0.01, len(samples))
    source = tmp_path / "noisy-speech.wav"
    sf.write(source, samples, rate, subtype="FLOAT")
    before = digest(source)
    window = Window(tmp_path)
    window.add_paths([source])
    window.tracks.cellWidget(0, 1).setCurrentIndex(window.tracks.cellWidget(0, 1).findData("speech"))
    window.mode.setCurrentIndex(window.mode.findData("strong-cleanup"))
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: QMessageBox.Ok)
    for extension in ("wav", "mp3"):
        output = tmp_path / f"strong-cleanup.{extension}"
        monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args, **kw: (str(output), ""))
        window.queue_render()
        assert window.process is not None
        assert not window.action_buttons["analyze_selected"].isEnabled()
        assert window.action_buttons["cancel"].isEnabled()
        assert window.action_buttons["queue_render"].isEnabled()
        wait_until(app, lambda: window.process is None, seconds=60)
        assert output.is_file(), window.log.toPlainText()
        report = json.loads(Path(window.last_report).read_text(encoding="utf-8"))
        assert report["verification"]["status"] == "passed"
        assert report["verification"]["samples"]["frames"] == sf.info(source).frames
        assert window.action_buttons["play_export"].isEnabled()
        assert window.action_buttons["open_output_folder"].isEnabled()
        assert not window.action_buttons["cancel"].isEnabled()
        assert "Audio exported:" in window.activity.text()
        assert "measured noise profile" in report["processing"][0]["cleanup"]
    assert digest(source) == before
    window.close()
    window.jobs.db.close()
    app.processEvents()


def test_inflight_render_does_not_publish_results_into_edited_project(tmp_path):
    from local_audio_studio.contracts import Project, Track
    app = QApplication.instance() or QApplication([])
    source = Path(__file__).parent / "fixtures" / "silero-leveling-tts.wav"
    window = Window(tmp_path)
    window.add_paths([source])
    output = tmp_path / "snapshot.wav"
    window.launch({"action": "render", "project": window.project().to_dict(), "output": str(output)})
    window.target.setValue(-16)
    wait_until(app, lambda: window.process is None)
    assert output.exists()
    assert window.last_output is None and window.last_report is None
    assert "earlier project/settings snapshot" in window.log.toPlainText()
    window.close()
    window.jobs.db.close()
    app.processEvents()
