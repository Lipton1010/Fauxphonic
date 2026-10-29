import numpy as np
import pytest
import soundfile as sf
import uuid
import time
from pathlib import Path
import os
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QUrl
from PySide6.QtMultimedia import QMediaPlayer

from local_audio_studio.audio import Cancelled, Runner, probe
from local_audio_studio.playback import prepare_playback
from local_audio_studio.gui import Window


def test_switching_playback_sources_starts_at_zero_but_same_source_resumes():
    class Player:
        def __init__(self):
            self.current = QUrl.fromLocalFile("C:/old.wav")
            self.at = 42000
        def source(self):
            return self.current
        def position(self):
            return self.at
        def setSource(self, source):
            self.current = source
            self.at = 0
        def setPosition(self, position):
            self.at = position
        def play(self):
            pass
        def mediaStatus(self):
            return QMediaPlayer.LoadedMedia

    player = Player()
    Window.play(type("View", (), {"player": player})(), "C:/new.wav")
    assert player.at == 0
    player.at = 12000
    Window.play(type("View", (), {"player": player})(), "C:/new.wav")
    assert player.at == 12000


def _tone(path, hz, frames=48000):
    time = np.arange(frames) / 48000
    sf.write(path, 0.2 * np.sin(2 * np.pi * hz * time), 48000, subtype="PCM_24")


def test_opus_audition_decodes_to_exact_pcm24_timeline(tmp_path):
    source = tmp_path / "source.wav"
    _tone(source, 997, 48000)
    runner = Runner()
    opus = tmp_path / "source.opus"
    runner.run(["-i", source, "-c:a", "libopus", "-b:a", "64k", opus], "fixture", 1)
    before = source.read_bytes()
    data = tmp_path / "appdata"
    active = data / "previews" / ("preview-active-" + uuid.uuid4().hex)
    result = prepare_playback(opus, None, active, data, runner)
    details = sf.info(result["path"])
    assert (details.frames, details.samplerate, details.channels, details.subtype) == (48000, 48000, 1, "PCM_24")
    assert result["frames"] == 48000 and result["stream"] == 0
    assert source.read_bytes() == before
    assert (active / "owner.pid").is_file()


def test_selected_container_stream_is_decoded_without_default_stream_leak(tmp_path):
    first, second = tmp_path / "first.wav", tmp_path / "second.wav"
    _tone(first, 440); _tone(second, 1760)
    container = tmp_path / "two-streams.mka"
    runner = Runner()
    runner.run(["-i", first, "-i", second, "-map", "0:a:0", "-map", "1:a:0",
                "-c:a", "pcm_s16le", "-f", "matroska", container], "fixture", 1)
    with pytest.raises(ValueError, match="Select an audio stream explicitly"):
        probe(container)
    info = probe(container, 1)
    data = tmp_path / "appdata"
    active = data / "previews" / ("preview-active-" + uuid.uuid4().hex)
    result = prepare_playback(container, 1, active, data, runner)
    samples, rate = sf.read(result["path"])
    spectrum = np.abs(np.fft.rfft(samples))
    peak_hz = np.fft.rfftfreq(len(samples), 1 / rate)[np.argmax(spectrum[1:]) + 1]
    assert info["stream"]["channels"] == 1
    assert result["stream"] == 1 and result["frames"] == 48000
    assert abs(peak_hz - 1760) < 2


def test_cancelled_playback_removes_owned_scratch(tmp_path):
    source = tmp_path / "cancel.wav"
    _tone(source, 440)
    data = tmp_path / "appdata"
    active = data / "previews" / ("preview-active-" + uuid.uuid4().hex)
    cancel = tmp_path / "cancel.flag"
    cancel.touch()
    with pytest.raises(Cancelled):
        prepare_playback(source, None, active, data, Runner(cancel_file=cancel))
    assert not active.exists()


def test_gui_plays_selected_stream_and_cleans_cancelled_audition(tmp_path):
    app = QApplication.instance() or QApplication([])
    first, second = tmp_path / "first.wav", tmp_path / "second.wav"
    _tone(first, 440, 4 * 48000); _tone(second, 1760, 4 * 48000)
    container = tmp_path / "selected.mkv"
    runner = Runner()
    runner.run(["-i", first, "-i", second, "-map", "0:a:0", "-map", "1:a:0",
                "-c:a", "pcm_s16le", "-f", "matroska", container], "fixture", 1)
    appdata = tmp_path / "appdata"
    appdata.mkdir()
    window = Window(appdata)
    window.audio.setMuted(True)
    window.add_paths([container])
    window.tracks.item(0, 4).setText("1")
    window.last_output = "preserved-export.wav"
    window.last_report = "preserved-report.json"
    window.play_original()
    deadline = time.monotonic() + 30
    while window.process and time.monotonic() < deadline:
        app.processEvents(); QTest.qWait(25)
    assert window.process is None and window.playback_directory
    assert (window.last_output, window.last_report) == ("preserved-export.wav", "preserved-report.json")
    completed_folder = window.playback_directory
    assert (completed_folder / "owner.pid").read_text() == str(os.getpid())
    assert Path(window.player.source().toLocalFile()).resolve() == (completed_folder / "audition.wav").resolve()
    deadline = time.monotonic() + 10
    while window.player.position() < 2000 and time.monotonic() < deadline:
        app.processEvents(); QTest.qWait(25)
    assert window.player.position() >= 2000
    window.pause()
    position = window.player.position()
    window.play_original()
    assert window.process is None and window.pending_playback_directory is None
    assert window.player.position() >= position
    assert window.playback_directory == completed_folder

    launch = window.launch
    def launch_then_cancel(request):
        launch(request)
        if request["action"] == "playback":
            Path(window.current_request["cancel_file"]).touch()
    window.launch = launch_then_cancel
    window._clear_playback_audition()
    window.play_original()
    cancelled_folder = window.pending_playback_directory
    deadline = time.monotonic() + 30
    while window.process and time.monotonic() < deadline:
        app.processEvents(); QTest.qWait(25)
    assert window.process is None and cancelled_folder is not None and not cancelled_folder.exists()
    assert (window.last_output, window.last_report) == ("preserved-export.wav", "preserved-report.json")
    assert "Cancelled" in window.log.toPlainText()
    window.jobs.db.close()
    window.close()
    app.processEvents()


def test_pending_preparation_cannot_override_pause_or_new_source(tmp_path):
    app = QApplication.instance() or QApplication([])
    source = tmp_path / "source.wav"
    _tone(source, 500, 48000*4)
    opus = tmp_path / "source.opus"
    Runner().run(["-i", source, "-c:a", "libopus", opus], "fixture", 4)
    window = Window(tmp_path)
    window.audio.setMuted(True)
    try:
        for newer_action in (window.pause, lambda: window._play_source(source)):
            window._play_source(opus)
            folder = window.pending_playback_directory
            newer_action()
            deadline = time.monotonic() + 30
            while window.process and time.monotonic() < deadline:
                app.processEvents()
                QTest.qWait(25)
            assert window.process is None
            assert window.playback_directory is None
            assert not folder.exists()
        assert Path(window.player.source().toLocalFile()).resolve() == source.resolve()
    finally:
        window.close()
        window.jobs.db.close()
        app.processEvents()


def test_original_playback_continues_past_one_second_and_resumes_during_analysis(tmp_path):
    app = QApplication.instance() or QApplication([])
    source = tmp_path / "original.wav"
    _tone(source, 440, 8 * 48000)
    window = Window(tmp_path)
    window.audio.setMuted(True)
    window.add_paths([source])
    window.audio.setVolume(0.2)  # A prior loudness-matched preview must not attenuate normal playback.
    window.play_original()
    deadline = time.monotonic() + 10
    while window.player.position() < 2200 and time.monotonic() < deadline:
        app.processEvents(); QTest.qWait(25)
    assert window.player.position() >= 2200
    assert window.audio.volume() == pytest.approx(1.0)
    window.player.pause()
    position = window.player.position()
    window.analyze_selected()
    window.play_original()
    assert window.player.position() >= position
    deadline = time.monotonic() + 30
    while window.process and time.monotonic() < deadline:
        app.processEvents(); QTest.qWait(25)
    assert window.process is None
    assert "Wait for the active worker" not in window.log.toPlainText()
    assert window.player.position() >= position
    window.close()
    window.jobs.db.close()
    app.processEvents()
