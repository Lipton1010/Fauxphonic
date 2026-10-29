"""Native Widgets UI. Audio work is always delegated to a supervised QProcess."""
import json
import os
os.environ["QT_MEDIA_BACKEND"] = "windows"
from pathlib import Path
import shutil
import sys
import uuid

import psutil

from PySide6.QtCore import QEvent, QLockFile, QProcess, QProcessEnvironment, QSize, QStandardPaths, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont, QIcon, QPainter, QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaPlayer
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFileDialog, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QMainWindow,
    QLineEdit, QMessageBox, QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSlider, QSpinBox, QSplitter,
    QTableWidget, QTableWidgetItem, QTextEdit, QToolButton, QVBoxLayout, QWidget)

from .audio import ROOT, write_json
from .contracts import GainPoint, Preset, Project, RATE, Track
from .desktop_status import CapabilityDialog
from .exports import FORMATS, output_paths
from .jobs import Jobs
from .lifecycle import analysis_scratch_root, sweep_analysis_scratch, sweep_preview_files, sweep_requests
from .presets import make_preset
from .windows_job import hold_application_mutex

SUPPORTED_MEDIA = {".wav", ".flac", ".mp3", ".m4a", ".aac", ".opus", ".ogg", ".w64", ".mp4", ".mkv"}


class GainEnvelopeDialog(QDialog):
    """Edit source-local automation points; time is a display of integer samples."""
    def __init__(self, points, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Track gain envelope")
        self.resize(520, 360)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Points use source-local samples at 48 kHz. Gain is linearly interpolated between points."))
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Sample", "Time (s)", "Gain (dB)"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        layout.addWidget(self.table)
        controls = QHBoxLayout()
        add = QPushButton("Add point")
        remove = QPushButton("Remove selected")
        undo = QPushButton("Undo")
        controls.addWidget(add); controls.addWidget(remove); controls.addWidget(undo)
        layout.addLayout(controls)
        self._undo = []
        self._loading = False
        for point in points:
            self._append(point.sample, point.gain_db)
        add.clicked.connect(self.add_point)
        remove.clicked.connect(self.remove_point)
        undo.clicked.connect(self.undo)
        self.table.itemChanged.connect(self._sample_changed)
        self.table.itemChanged.connect(self._record_edit)
        self._last_state = self._state()
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _append(self, sample, gain):
        row = self.table.rowCount()
        was_loading = self._loading
        self._loading = True
        self.table.insertRow(row)
        sample = int(sample)
        sample_item = QTableWidgetItem(str(sample))
        time_item = QTableWidgetItem(f"{sample / RATE:.6f}")
        time_item.setFlags(time_item.flags() & ~Qt.ItemIsEditable)
        self.table.setItem(row, 0, sample_item)
        self.table.setItem(row, 1, time_item)
        self.table.setItem(row, 2, QTableWidgetItem(f"{gain:g}"))
        self._loading = was_loading

    def values(self):
        points = []
        for row in range(self.table.rowCount()):
            points.append(GainPoint(int(self.table.item(row, 0).text()), float(self.table.item(row, 2).text())))
        previous = -1
        for point in points:
            if point.sample <= previous or not 0 <= point.sample <= 2**63 - 1:
                raise ValueError("Samples must be unique, increasing, nonnegative integers.")
            if not -60 <= point.gain_db <= 24:
                raise ValueError("Envelope gain must be between -60 and +24 dB.")
            previous = point.sample
        return points

    def _snapshot(self):
        self._undo.append(self._state())

    def _state(self):
        return [(self.table.item(r, 0).text(), self.table.item(r, 2).text())
                for r in range(self.table.rowCount())
                if self.table.item(r, 0) is not None and self.table.item(r, 2) is not None]

    def _record_edit(self, item):
        if item.column() not in (0, 2) or self._loading:
            return
        state = self._state()
        if state != self._last_state:
            self._undo.append(self._last_state)
            self._last_state = state

    def _sample_changed(self, item):
        if item.column() != 0 or self._loading:
            return
        try:
            sample = int(item.text())
            self._loading = True
            time_item = self.table.item(item.row(), 1)
            if time_item is not None:
                time_item.setText(f"{sample / RATE:.6f}")
        except ValueError:
            pass
        finally:
            self._loading = False

    def add_point(self):
        self._snapshot()
        samples = [int(self.table.item(r, 0).text()) for r in range(self.table.rowCount())]
        self._append((max(samples) + RATE) if samples else 0, 0)
        self._last_state = self._state()

    def remove_point(self):
        row = self.table.currentRow()
        if row >= 0:
            self._snapshot()
            self.table.removeRow(row)
            self._last_state = self._state()

    def undo(self):
        if self._undo:
            rows = self._undo.pop()
            self._loading = True
            self.table.setRowCount(0)
            for sample, gain in rows:
                self._append(int(sample), float(gain))
            self._loading = False
            self._last_state = self._state()


class MetadataDialog(QDialog):
    FIELDS = ("title", "artist", "album", "date", "genre", "comment", "copyright")

    def __init__(self, metadata=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Output metadata")
        self.resize(430, 330)
        layout = QVBoxLayout(self)
        note = QLabel("Only tags entered here are embedded in new outputs. Source metadata is never copied automatically.")
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        self.fields = {}
        for name in self.FIELDS:
            field = QLineEdit((metadata or {}).get(name, ""))
            field.setMaxLength(4096)
            self.fields[name] = field
            form.addRow(name.title(), field)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self):
        return {name: field.text() for name, field in self.fields.items() if field.text()}


class Waveform(QWidget):
    def __init__(self):
        super().__init__()
        self.peaks = []
        self.setMinimumHeight(104)
        self.setAccessibleName("Waveform peak overview, teal bars show measured sample peaks")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor("#0B272B"))
        painter.setPen(QColor(255, 248, 235, 32))
        for fraction in (0.2, 0.4, 0.6, 0.8):
            y = round(self.height() * fraction)
            painter.drawLine(0, y, self.width(), y)
        for fraction in range(1, 12):
            x = round(self.width() * fraction / 12)
            painter.drawLine(x, 8, x, self.height() - 8)
        painter.setPen(QColor("#DDAF49"))
        middle = self.height() / 2
        painter.drawLine(0, int(middle), self.width(), int(middle))
        painter.setPen(QColor("#55B8A7"))
        if not self.peaks:
            painter.setPen(QColor("#F4EBDD"))
            painter.drawText(self.rect(), Qt.AlignCenter, "No waveform yet  ·  Analyze a track to inspect its signal")
            return
        for i, peak in enumerate(self.peaks):
            x = int(i * self.width() / max(1, len(self.peaks)))
            amplitude = min(1, peak) * (middle - 12)
            painter.drawLine(x, int(middle-amplitude), x, int(middle+amplitude))


def app_icon():
    for name in ("fauxphonic.ico", "fauxphonic-icon.png"):
        path = ROOT / "assets" / name
        if path.is_file():
            return QIcon(str(path))
    return QIcon()


APP_STYLE = """
QMainWindow, QWidget#appBody { background: #0D2A2E; color: #F4EBDD; }
QFrame#masthead { background: #12383D; border: 1px solid #2D5557; border-radius: 15px; }
QLabel#brandTitle { color: #FFF8EB; font-size: 25px; font-weight: 700; }
QLabel#brandTagline { color: #C7DAD3; font-size: 10px; letter-spacing: 1px; }
QLabel#brandBadge { color: #F3CE6A; background: #1A4849; border: 1px solid #55786E; border-radius: 10px; padding: 7px 11px; font-weight: 700; }
QFrame#projectBar { background: #15373B; border: 1px solid #2D5557; border-radius: 13px; }
QFrame#trackCard, QFrame#transportCard { background: #F4EBDD; border: 1px solid #D4C8B3; border-radius: 13px; }
QFrame#waveformPanel { background: #F4EBDD; border: 1px solid #D4C8B3; border-radius: 13px; }
QWidget#inspectorPanel { background: #F4EBDD; color: #18292C; }
QLabel#sectionTitle { color: #1B3F40; font-size: 11px; font-weight: 800; letter-spacing: 1px; }
QLabel#sectionMeta { color: #61716D; font-size: 10px; }
QLabel#inspectorPanel { color: #18292C; }
QLabel { color: #F4EBDD; }
QWidget#inspectorPanel QLabel, QFrame#trackCard QLabel, QFrame#transportCard QLabel { color: #18292C; }
QFrame#waveformPanel QLabel { color: #18292C; }
QTableWidget, QTableView { background: #FFF9EF; alternate-background-color: #F2EADB; color: #18292C; gridline-color: #D8CDBA; border: 1px solid #D4C8B3; border-radius: 8px; selection-background-color: #267C75; selection-color: #FFFFFF; }
QHeaderView::section { background: #E7DDCA; color: #17383B; border: 0; border-right: 1px solid #D2C4AD; border-bottom: 1px solid #D2C4AD; padding: 6px 5px; font-weight: 700; }
QWidget#inspectorPanel QComboBox, QWidget#inspectorPanel QSpinBox, QWidget#inspectorPanel QDoubleSpinBox,
QWidget#inspectorPanel QLineEdit { min-height: 27px; background: #FFF9EF; color: #18292C; border: 1px solid #A8AA9E; border-radius: 7px; padding: 3px 7px; selection-background-color: #267C75; }
QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit { min-height: 25px; background: #FFF9EF; color: #18292C; border: 1px solid #879287; border-radius: 7px; padding: 2px 6px; selection-background-color: #267C75; }
QWidget#inspectorPanel QSpinBox, QWidget#inspectorPanel QDoubleSpinBox { padding-right: 25px; }
QSpinBox::up-button, QDoubleSpinBox::up-button { subcontrol-origin: border; subcontrol-position: top right; width: 22px; height: 17px; }
QSpinBox::down-button, QDoubleSpinBox::down-button { subcontrol-origin: border; subcontrol-position: bottom right; width: 22px; height: 17px; }
QWidget#inspectorPanel QComboBox:focus, QWidget#inspectorPanel QSpinBox:focus, QWidget#inspectorPanel QDoubleSpinBox:focus,
QWidget#inspectorPanel QLineEdit:focus { border: 1px solid #C65443; }
QWidget#inspectorPanel QCheckBox, QWidget#inspectorPanel QToolButton { color: #18292C; }
QWidget#inspectorPanel QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid #647570; border-radius: 4px; background: #FFF9EF; }
QWidget#inspectorPanel QCheckBox::indicator:checked { background: #267C75; border: 1px solid #1C625D; }
QWidget#inspectorPanel QToolButton { font-weight: 700; padding: 6px 3px; }
QWidget#inspectorPanel QScrollArea, QWidget#inspectorPanel QScrollArea QWidget { background: #F4EBDD; border: 0; }
QPushButton { min-height: 24px; padding: 3px 7px; border: 1px solid #8F9A8C; border-radius: 7px; background: #FFF8EB; color: #183337; font-weight: 650; }
QPushButton:hover { background: #F2E1C6; border-color: #267C75; }
QPushButton:focus { border: 2px solid #D96850; }
QPushButton:disabled { color: #84918C; background: #E7E2D7; border-color: #C6C2B9; }
QPushButton#primaryAction { background: #B74739; border-color: #963D32; color: #FFFFFF; font-weight: 750; }
QPushButton#primaryAction:hover { background: #963D32; }
QPushButton#primaryAction:disabled { color: #84918C; background: #E7E2D7; border-color: #C6C2B9; }
QPushButton#toolbarAction { background: #EADFCB; border-color: #8D9B8B; }
QFrame#transportCard QSlider::groove:horizontal { height: 5px; background: #CFC6B4; border-radius: 2px; }
QFrame#transportCard QSlider::handle:horizontal { width: 15px; margin: -6px 0; border-radius: 7px; background: #CE5A48; }
QDialog { background: #F4EBDD; color: #18292C; }
QDialog QLabel { color: #18292C; }
QDialog QLineEdit, QDialog QComboBox, QDialog QSpinBox, QDialog QDoubleSpinBox,
QDialog QTableWidget, QDialog QTextEdit { background: #FFF9EF; color: #18292C; border: 1px solid #879287; border-radius: 6px; selection-background-color: #267C75; }
QDialog QPushButton { background: #FFF8EB; color: #183337; }
QComboBox QAbstractItemView { background: #FFF9EF; color: #18292C; selection-background-color: #267C75; selection-color: #FFFFFF; }
QProgressBar { min-height: 17px; background: #17383B; color: #FFF8EB; border: 1px solid #53716C; border-radius: 7px; text-align: center; }
QProgressBar::chunk { background: #45A99A; border-radius: 6px; }
QTextEdit { background: #F4EBDD; color: #18292C; border: 1px solid #B9B19F; border-radius: 9px; padding: 5px; selection-background-color: #267C75; }
QSplitter::handle { background: #0D2A2E; width: 7px; }
QScrollBar:vertical { width: 12px; background: #E7DDCA; margin: 2px; }
QScrollBar::handle:vertical { background: #82938A; border-radius: 5px; min-height: 28px; }
"""


class Window(QMainWindow):
    def __init__(self, data_dir):
        super().__init__()
        self.data_dir = Path(data_dir)
        self.metadata = {}
        sweep_requests(self.data_dir)
        sweep_analysis_scratch(analysis_scratch_root())
        sweep_preview_files(self.data_dir)
        self.jobs = Jobs(self.data_dir / "jobs.sqlite")
        self.jobs.recover()
        self.process = None
        self.current_job = None
        self.current_request = None
        self.last_output = None
        self.last_report = None
        self.preview_files = None
        self.preview_directories = []
        self.preview_match_db = None
        self.playback_directory = None
        self.pending_playback_directory = None
        self.play_when_loaded = False
        self.transport_generation = 0
        self.prepared_playback = None
        self.project_revision = 0
        self.last_stage = None
        self.completed_event = False
        self.closing = False
        self.buffer = b""
        self.action_buttons = {}
        self.setWindowTitle("Fauxphonic — Local Audio Studio")
        self.setMinimumSize(850, 650)
        self.resize(1180, 800)
        self.setWindowIcon(app_icon())
        self.setStyleSheet(APP_STYLE)
        self.setAcceptDrops(True)
        body = QWidget()
        body.setObjectName("appBody")
        self.setCentralWidget(body)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(8)
        masthead = QFrame()
        masthead.setObjectName("masthead")
        masthead_layout = QHBoxLayout(masthead)
        masthead_layout.setContentsMargins(10, 5, 14, 5)
        masthead_layout.setSpacing(11)
        brand_icon = QLabel()
        brand_icon.setObjectName("brand_icon")
        brand_icon.setAccessibleName("Fauxphonic waveform F icon")
        brand_icon.setFixedSize(70, 70)
        brand_icon.setAlignment(Qt.AlignCenter)
        brand_icon_path = ROOT / "assets" / "fauxphonic-icon.png"
        if brand_icon_path.is_file():
            ratio = self.devicePixelRatioF()
            pixmap = QPixmap(str(brand_icon_path)).scaled(QSize(round(66 * ratio), round(66 * ratio)),
                Qt.KeepAspectRatio, Qt.SmoothTransformation)
            pixmap.setDevicePixelRatio(ratio)
            brand_icon.setPixmap(pixmap)
        masthead_layout.addWidget(brand_icon)
        brand_copy = QVBoxLayout()
        brand_copy.setSpacing(1)
        title = QLabel("Fauxphonic")
        title.setObjectName("brandTitle")
        title.setFont(QFont("Segoe UI", 24, QFont.DemiBold))
        tagline = QLabel("LOCAL MASTERING CONSOLE  ·  ORIGINALS STAY YOURS")
        tagline.setObjectName("brandTagline")
        brand_copy.addWidget(title)
        brand_copy.addWidget(tagline)
        masthead_layout.addLayout(brand_copy)
        masthead_layout.addStretch(1)
        badge = QLabel("48 kHz   /   CPU")
        badge.setObjectName("brandBadge")
        badge.setAlignment(Qt.AlignCenter)
        badge.setAccessibleName("48 kilohertz project timeline, CPU processing")
        masthead_layout.addWidget(badge)
        layout.addWidget(masthead)
        self.activity = QLabel("Ready — import audio, choose processing, then Export audio.")
        self.activity.setWordWrap(True)
        layout.addWidget(self.activity)
        project_bar = QFrame()
        project_bar.setObjectName("projectBar")
        project_layout = QGridLayout(project_bar)
        project_layout.setContentsMargins(8, 5, 8, 5)
        project_layout.setHorizontalSpacing(6)
        project_layout.setVerticalSpacing(3)
        for index, (label, callback) in enumerate([("New project", self.new_project), ("Import audio…", self.import_dialog),
                                ("Open project…", self.open_project),
                                ("Save project…", self.save_project), ("Edit gain envelope…", self.edit_gain_envelope),
                                ("Output tags…", self.edit_metadata),
                                ("Import last leveler curves…", self.import_leveler_curves),
                                ("Capabilities…", self.show_capabilities),
                                ("Storage…", self.show_storage),
                                ("Remove selected", self.remove_track)]):
            button = QPushButton(label)
            button.clicked.connect(callback)
            shortcuts = {"Import audio…": "Ctrl+I", "Open project…": "Ctrl+O", "Save project…": "Ctrl+S"}
            if label in shortcuts:
                button.setShortcut(shortcuts[label])
            if label == "Import last leveler curves…":
                self.import_curves_button = button
                button.setEnabled(False)
            button.setObjectName("toolbarAction")
            project_layout.addWidget(button, 1 + index // 5, index % 5)
        project_heading = QLabel("PROJECT & TRACK TOOLS")
        project_heading.setObjectName("brandTagline")
        project_layout.addWidget(project_heading, 0, 0, 1, 5)
        layout.addWidget(project_bar)
        split = QSplitter(Qt.Horizontal)
        split.setObjectName("mixDeck")
        layout.addWidget(split, 1)
        left_scroll = QScrollArea()
        left_scroll.setObjectName("mixOverviewScroll")
        left_scroll.setWidgetResizable(True)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        left_content = QWidget()
        left_content.setObjectName("mixOverview")
        left_layout = QVBoxLayout(left_content)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(7)
        left_scroll.setWidget(left_content)
        split.addWidget(left_scroll)
        self.tracks = QTableWidget(0, 5)
        self.tracks.setObjectName("trackTable")
        self.tracks.setHorizontalHeaderLabels(["Source", "Role", "Offset (samples)", "Gain (dB)", "Stream index"])
        self.tracks.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tracks.setMinimumHeight(140)
        self.tracks.setColumnWidth(0, 165)
        self.tracks.setSelectionBehavior(QTableWidget.SelectRows)
        self.tracks.setToolTip("Offsets are integer samples at 48 kHz. Blank stream requires exactly one audio stream.")
        track_card = QFrame()
        track_card.setObjectName("trackCard")
        track_layout = QVBoxLayout(track_card)
        track_layout.setContentsMargins(9, 8, 9, 9)
        track_layout.setSpacing(6)
        track_heading = QHBoxLayout()
        track_title = QLabel("01  /  INPUT ROUTING")
        track_title.setObjectName("sectionTitle")
        track_meta = QLabel("48 kHz samples · explicit role & stream")
        track_meta.setObjectName("sectionMeta")
        track_heading.addWidget(track_title)
        track_heading.addStretch(1)
        track_heading.addWidget(track_meta)
        track_layout.addLayout(track_heading)
        track_layout.addWidget(self.tracks)
        left_layout.addWidget(track_card)
        inspector_content = QWidget()
        inspector_content.setObjectName("inspectorPanel")
        form = QFormLayout(inspector_content)
        form.setContentsMargins(12, 10, 12, 10)
        form.setVerticalSpacing(7)
        form.setHorizontalSpacing(8)
        inspector_title = QLabel("02  /  MIX & MASTER")
        inspector_title.setObjectName("sectionTitle")
        form.addRow(inspector_title)
        self.mode = QComboBox()
        for label, value in [("Master only", "master-only"), ("Natural speech", "natural-speech"),
                             ("Strong cleanup", "strong-cleanup"), ("Preserve music", "preserve-music"),
                             ("Multitrack", "multitrack"), ("Bypass / format conversion", "bypass")]:
            self.mode.addItem(label, value)
        self.target = QDoubleSpinBox()
        self.target.setRange(-36, -5)
        self.target.setValue(-19)
        self.target.setSuffix(" LUFS")
        self.peak = QDoubleSpinBox()
        self.peak.setRange(-9, -0.3)
        self.peak.setValue(-1)
        self.peak.setSingleStep(0.1)
        self.peak.setSuffix(" dBTP")
        self.highpass = QSpinBox()
        self.highpass.setRange(0, 150)
        self.highpass.setSuffix(" Hz (0 = off)")
        self.reduction = QSpinBox()
        self.reduction.setRange(0, 12)
        self.reduction.setValue(6)
        self.reduction.setSuffix(" dB")
        self.leveler_strength = QDoubleSpinBox()
        self.leveler_strength.setRange(0, 1)
        self.leveler_strength.setSingleStep(0.05)
        self.leveler_strength.setValue(0.8)
        self.dynamics = QDoubleSpinBox()
        self.dynamics.setRange(0, 1)
        self.dynamics.setSingleStep(0.05)
        self.dynamics.setValue(0.25)
        self.max_boost = QDoubleSpinBox()
        self.max_boost.setRange(0, 18)
        self.max_boost.setSuffix(" dB")
        self.max_boost.setValue(9)
        self.max_attenuation = QDoubleSpinBox()
        self.max_attenuation.setRange(0, 24)
        self.max_attenuation.setSuffix(" dB")
        self.max_attenuation.setValue(12)
        self.leveler_enabled = QCheckBox("Enable speech leveler")
        self.leveler_enabled.toggled.connect(self.leveler_controls)
        self.preview_start = QDoubleSpinBox()
        self.preview_start.setRange(0, 86400)
        self.preview_start.setDecimals(3)
        self.preview_start.setSuffix(" s")
        self.preview_duration = QDoubleSpinBox()
        self.preview_duration.setRange(0.1, 30)
        self.preview_duration.setValue(15)
        self.preview_duration.setSuffix(" s")
        self.match_loudness = QCheckBox("Loudness match A/B")
        for label, widget in [("Preset", self.mode), ("Loudness", self.target), ("True peak", self.peak),
                              ("Leveler", self.leveler_enabled), ("Preview start", self.preview_start),
                              ("Preview length", self.preview_duration), ("A/B level match", self.match_loudness)]:
            form.addRow(label, widget)
        advanced = QToolButton()
        advanced.setText("Advanced processing controls")
        advanced.setCheckable(True)
        advanced.setToolButtonStyle(Qt.ToolButtonTextOnly)
        advanced.setArrowType(Qt.RightArrow)
        advanced_fields = QWidget()
        advanced_form = QFormLayout(advanced_fields)
        for label, widget in [("High-pass", self.highpass), ("Noise reduction", self.reduction),
                              ("Leveler strength", self.leveler_strength), ("Preserve dynamics", self.dynamics),
                              ("Maximum boost", self.max_boost), ("Maximum attenuation", self.max_attenuation)]:
            advanced_form.addRow(label, widget)
        self.ducking = QCheckBox("Duck Music tracks during Speech activity")
        self.duck_depth = QDoubleSpinBox(); self.duck_depth.setRange(0, 12); self.duck_depth.setValue(6); self.duck_depth.setSuffix(" dB")
        self.duck_attack = QSpinBox(); self.duck_attack.setRange(50, 150); self.duck_attack.setValue(100); self.duck_attack.setSuffix(" ms")
        self.duck_release = QSpinBox(); self.duck_release.setRange(500, 1500); self.duck_release.setValue(800); self.duck_release.setSuffix(" ms")
        self.bleed_reduction = QCheckBox("Conservative bleed attenuation")
        self.bleed_reduction.setToolTip("Optional heuristic for highly correlated, quieter Speech tracks. Uncertain overlap is left unchanged; this does not identify speakers or prove audio quality.")
        self.bleed_depth = QDoubleSpinBox(); self.bleed_depth.setRange(0, 6); self.bleed_depth.setValue(6); self.bleed_depth.setSuffix(" dB")
        self.bleed_attack = QSpinBox(); self.bleed_attack.setRange(50, 500); self.bleed_attack.setValue(100); self.bleed_attack.setSuffix(" ms")
        self.bleed_hold = QSpinBox(); self.bleed_hold.setRange(100, 1000); self.bleed_hold.setValue(300); self.bleed_hold.setSuffix(" ms")
        self.bleed_hold.setToolTip("Require a continuous match for this long before attenuation begins. Uncertain blocks remain unchanged; ramps stay inside confirmed regions.")
        self.bleed_release = QSpinBox(); self.bleed_release.setRange(100, 2000); self.bleed_release.setValue(500); self.bleed_release.setSuffix(" ms")
        self.bleed_protect = QCheckBox("Protect selected Speech track")
        self.bleed_protect.setToolTip("Keeps this track at its normal level when conservative bleed attenuation is enabled.")
        self.export_stems = QCheckBox("Export per-track stems (pre-master, full timeline)")
        self.export_stems.setToolTip("Creates a separate full-timeline file for each track before final mastering. Stems are always WAV, including when the master is MP3, M4A/AAC, or Opus.")
        self.deesser = QCheckBox("Enable de-essing on eligible Speech tracks")
        self.deesser.setToolTip("Uses the validated dynamic equalizer only on mono tracks explicitly assigned the Speech role. Other tracks report why it was bypassed.")
        self.deesser_amount = QDoubleSpinBox()
        self.deesser_amount.setRange(0, 1.8)
        self.deesser_amount.setDecimals(2)
        self.deesser_amount.setSingleStep(0.1)
        self.deesser_amount.setValue(1.8)
        self.deesser_amount.setToolTip("FFmpeg dynamic equalizer range control; this is not a dB cut setting.")
        self.broad_eq = QCheckBox("Enable broad tone EQ")
        self.broad_eq.setToolTip("Applies the same broad equalizer to explicitly assigned tracks in non-bypass modes.")
        self.broad_eq_frequency = QSpinBox()
        self.broad_eq_frequency.setRange(200, 8000)
        self.broad_eq_frequency.setValue(1000)
        self.broad_eq_frequency.setSuffix(" Hz")
        self.broad_eq_gain = QDoubleSpinBox()
        self.broad_eq_gain.setRange(-3, 3)
        self.broad_eq_gain.setSingleStep(0.25)
        self.broad_eq_gain.setValue(0)
        self.broad_eq_gain.setSuffix(" dB")
        self.deesser.toggled.connect(self.deesser_controls)
        self.broad_eq.toggled.connect(self.broad_eq_controls)
        for label, widget in [("Music ducking", self.ducking), ("Duck depth", self.duck_depth),
                              ("Attack", self.duck_attack), ("Release", self.duck_release),
                              ("Speech bleed", self.bleed_reduction), ("Maximum attenuation", self.bleed_depth),
                              ("Bleed attack", self.bleed_attack), ("Detection hold", self.bleed_hold),
                              ("Bleed release", self.bleed_release), ("Selected track", self.bleed_protect),
                              ("Track stems", self.export_stems), ("De-essing", self.deesser),
                              ("De-essing amount", self.deesser_amount), ("Broad tone EQ", self.broad_eq),
                              ("EQ frequency", self.broad_eq_frequency), ("EQ gain", self.broad_eq_gain)]:
            advanced_form.addRow(label, widget)
        self.ducking.toggled.connect(self.duck_controls)
        self.bleed_reduction.toggled.connect(self.bleed_controls)
        self.bleed_protect.toggled.connect(self.set_selected_bleed_protection)
        self.tracks.itemSelectionChanged.connect(self.update_track_controls)
        self.tracks.cellChanged.connect(self.update_track_controls)
        advanced_fields.setVisible(False)
        def toggle_advanced(checked):
            advanced_fields.setVisible(checked)
            advanced.setArrowType(Qt.DownArrow if checked else Qt.RightArrow)
        advanced.toggled.connect(toggle_advanced)
        form.addRow(advanced)
        form.addRow(advanced_fields)
        self.processing_note = QLabel()
        self.processing_note.setWordWrap(True)
        form.addRow(self.processing_note)
        note = QLabel("Leveling affects Speech-role tracks. The first preview processes the full program, then extracts up to 30 seconds and stops at the recording's end. Original A/B includes track gain and its manual envelope. Strong cleanup can add artifacts.")
        note.setWordWrap(True)
        form.addRow(note)
        self.mode.currentIndexChanged.connect(self.mode_changed)
        self.mode_changed()
        inspector = QScrollArea()
        inspector.setObjectName("masteringScroll")
        inspector.setWidgetResizable(True)
        inspector.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inspector.setMinimumWidth(260)
        inspector.setMaximumWidth(390)
        inspector.setWidget(inspector_content)
        inspector_content.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        inspector_content.setMinimumWidth(0)
        split.addWidget(inspector)
        split.setStretchFactor(0, 5)
        split.setStretchFactor(1, 3)
        split.setSizes([560, 320])
        self.waveform = Waveform()
        waveform_panel = QFrame()
        waveform_panel.setObjectName("waveformPanel")
        waveform_layout = QVBoxLayout(waveform_panel)
        waveform_layout.setContentsMargins(9, 7, 9, 8)
        waveform_layout.setSpacing(4)
        waveform_heading = QHBoxLayout()
        waveform_title = QLabel("03  /  PROGRAM SIGNAL")
        waveform_title.setObjectName("sectionTitle")
        waveform_meta = QLabel("MEASURED SAMPLE PEAKS")
        waveform_meta.setObjectName("sectionMeta")
        waveform_heading.addWidget(waveform_title)
        waveform_heading.addStretch(1)
        waveform_heading.addWidget(waveform_meta)
        waveform_layout.addLayout(waveform_heading)
        waveform_layout.addWidget(self.waveform)
        left_layout.addWidget(waveform_panel)
        self.player = QMediaPlayer(self)
        self.player.mediaStatusChanged.connect(self.media_status_changed)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        transport_card = QFrame()
        transport_card.setObjectName("transportCard")
        transport_layout = QVBoxLayout(transport_card)
        transport_layout.setContentsMargins(9, 6, 10, 7)
        transport_layout.setSpacing(4)
        transport_heading = QHBoxLayout()
        transport_title = QLabel("04  /  MONITOR")
        transport_title.setObjectName("sectionTitle")
        transport_meta = QLabel("Original · export · A/B")
        transport_meta.setObjectName("sectionMeta")
        transport_heading.addWidget(transport_title)
        transport_heading.addStretch(1)
        transport_heading.addWidget(transport_meta)
        transport_layout.addLayout(transport_heading)
        transport = QHBoxLayout()
        for label, callback in [("Play original", self.play_original), ("Play export", self.play_export),
                                ("Pause", self.pause)]:
            button = QPushButton(label)
            button.clicked.connect(callback)
            self.action_buttons[callback.__name__] = button
            transport.addWidget(button)
        self.devices = QComboBox()
        self.devices.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.devices.setMinimumWidth(0)
        self.devices.setMaximumWidth(230)
        self.media_devices = QMediaDevices(self)
        self.media_devices.audioOutputsChanged.connect(self.refresh_audio_devices)
        self.refresh_audio_devices()
        self.devices.currentIndexChanged.connect(self.select_audio_device)
        transport.addWidget(self.devices)
        transport_layout.addLayout(transport)
        seek_row = QHBoxLayout()
        seek_row.setContentsMargins(2, 0, 2, 0)
        self.seek = QSlider(Qt.Horizontal)
        self.seek.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.seek.setMinimumWidth(0)
        self.seek.setAccessibleName("Playback position")
        self.seek.sliderMoved.connect(self.player.setPosition)
        self.player.positionChanged.connect(lambda p: self.seek.setValue(p) if not self.seek.isSliderDown() else None)
        self.player.durationChanged.connect(lambda d: self.seek.setRange(0, min(d, 2147483647)))
        self.player.errorOccurred.connect(lambda error, message: self.log.append("Playback: " + message))
        seek_row.addWidget(self.seek)
        transport_layout.addLayout(seek_row)
        left_layout.addWidget(transport_card)
        actions = QGridLayout()
        actions.setHorizontalSpacing(7)
        actions.setVerticalSpacing(6)
        for label, callback in [("Analyze selected", self.analyze_selected), ("Build A/B preview", self.build_preview),
                                ("Play original preview", self.play_preview_original),
                                ("Play processed preview", self.play_preview_processed),
                                ("Export audio…", self.queue_render),
                                ("Cancel active", self.cancel), ("Retry selected job", self.retry_job),
                                ("Open report", self.open_report), ("Open output folder", self.open_output_folder)]:
            button = QPushButton(label)
            button.clicked.connect(callback)
            button.setObjectName("primaryAction" if label == "Export audio…" else "toolbarAction")
            self.action_buttons[callback.__name__] = button
            index = actions.count()
            actions.addWidget(button, index // 3, index % 3)
        left_layout.addLayout(actions)
        self.queue = QTableWidget(0, 2)
        self.queue.setHorizontalHeaderLabels(["Output", "State"])
        self.queue.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.queue.setEditTriggers(QTableWidget.NoEditTriggers)
        self.queue.setMaximumHeight(130)
        self.queue.itemSelectionChanged.connect(self.update_actions)
        left_layout.addWidget(self.queue)
        self.progress = QProgressBar()
        left_layout.addWidget(self.progress)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.document().setMaximumBlockCount(300)
        self.log.setMaximumHeight(90)
        left_layout.addWidget(self.log)
        self.log.append("Ready. Choose -19 LUFS for mono or -16 LUFS for stereo as starting targets.\n"
                        "Speech leveling is optional and targets Speech-role tracks. Neural enhancement and listening approval are not part of this build.")
        self.refresh_queue()
        self.update_actions()
        for widget in self.findChildren(QWidget):
            if isinstance(widget, (QDoubleSpinBox, QSpinBox, QComboBox)):
                widget.setFocusPolicy(Qt.StrongFocus)
                widget.installEventFilter(self)
                if widget is not self.devices:
                    signal = widget.currentIndexChanged if isinstance(widget, QComboBox) else widget.valueChanged
                    signal.connect(self.invalidate_results)
            elif isinstance(widget, QCheckBox) and widget is not self.match_loudness:
                widget.toggled.connect(self.invalidate_results)
        self.tracks.itemChanged.connect(self.invalidate_results)
        self.tracks.model().rowsRemoved.connect(self.invalidate_results)
        self.tracks.itemSelectionChanged.connect(self.clear_waveform)
        QTimer.singleShot(0, self.start_next)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Wheel and not watched.hasFocus():
            event.ignore()
            return True
        return super().eventFilter(watched, event)

    def refresh_audio_devices(self):
        selected = self.audio.device().id()
        self.devices.blockSignals(True)
        self.devices.clear()
        for device in QMediaDevices.audioOutputs():
            self.devices.addItem(device.description(), device)
        index = next((i for i in range(self.devices.count())
                      if self.devices.itemData(i).id() == selected), -1)
        if index < 0:
            default = QMediaDevices.defaultAudioOutput().id()
            index = next((i for i in range(self.devices.count())
                          if self.devices.itemData(i).id() == default), -1)
        self.devices.setCurrentIndex(index)
        self.devices.setEnabled(index >= 0)
        self.devices.blockSignals(False)
        self.select_audio_device()

    def select_audio_device(self):
        device = self.devices.currentData()
        if device is not None:
            self.audio.setDevice(device)
        elif hasattr(self, "log"):
            self.pause()
            self.log.append("No audio output device is available. Connect one to resume playback.")

    def invalidate_results(self, *_):
        self.project_revision += 1
        if self.preview_files and self.player.source().toLocalFile() in self.preview_files.values():
            self.pause()
        self.preview_files = None
        self.last_output = self.last_report = None
        self.import_curves_button.setEnabled(False)
        self.clear_waveform()
        self.update_actions()

    def clear_waveform(self):
        self.waveform.peaks = []
        self.waveform.update()

    def update_actions(self):
        busy = self.process is not None
        row = self.tracks.currentRow()
        selected = row >= 0
        original_ready = selected and (not busy or (
            self.tracks.item(row, 0) is not None and self.tracks.item(row, 4) is not None
            and Path(self.tracks.item(row, 0).text()).suffix.lower() in {".wav", ".flac"}
            and not self.tracks.item(row, 4).text().strip()))
        export_ready = bool(self.last_output) and (not busy or Path(self.last_output).suffix.lower() in {".wav", ".flac"})
        retry_ready = (hasattr(self, "queue") and self.queue.currentRow() >= 0
                       and self.queue.item(self.queue.currentRow(), 1) is not None
                       and self.queue.item(self.queue.currentRow(), 1).text().split(":", 1)[0]
                       in {"failed", "cancelled", "interrupted"})
        states = {
            "analyze_selected": (selected and not busy, "Select a track; wait for processing to finish before analyzing."),
            "build_preview": (self.tracks.rowCount() > 0 and not busy, "Import audio; wait for processing to finish before building A/B."),
            "queue_render": (self.tracks.rowCount() > 0, "Export a WAV, FLAC, MP3, M4A or Opus file. Exports wait in the queue while another task runs."),
            "play_original": (original_ready, "Play the selected source. Compressed audio needs worker preparation; wait for the active task."),
            "play_export": (export_ready, "Available after a verified audio export. Compressed audio needs worker preparation; wait for the active task."),
            "play_preview_original": (bool(self.preview_files), "Build an A/B preview first."),
            "play_preview_processed": (bool(self.preview_files), "Build an A/B preview first."),
            "cancel": (busy, "Cancel the active task and wait for cleanup."),
            "retry_job": (retry_ready, "Select a failed, cancelled or interrupted job to restart from originals."),
            "open_report": (bool(self.last_report), "Open the JSON measurement report for the last export."),
            "open_output_folder": (bool(self.last_output), "Open the folder containing the exported audio and its separate JSON report."),
        }
        for name, (enabled, explanation) in states.items():
            if name in self.action_buttons:
                self.action_buttons[name].setEnabled(enabled)
                self.action_buttons[name].setToolTip(explanation)

    def mode_changed(self):
        preset = self.mode.currentData()
        defaults = make_preset(preset)
        bypass = preset == "bypass"
        for widget in (self.target, self.peak, self.highpass):
            widget.setEnabled(not bypass)
        self.reduction.setEnabled(defaults.mode == "dsp-cleanup")
        self.leveler_enabled.setEnabled(not bypass)
        self.leveler_enabled.setChecked(defaults.leveler_enabled)
        # Each named preset starts with the newly added optional DSP stages off.
        self.deesser.setChecked(False)
        self.broad_eq.setChecked(False)
        self.bleed_reduction.setChecked(False)
        self.deesser.setEnabled(not bypass)
        self.broad_eq.setEnabled(not bypass)
        self.deesser_controls(self.deesser.isChecked())
        self.broad_eq_controls(self.broad_eq.isChecked())
        self.bleed_controls(self.bleed_reduction.isChecked())
        self.leveler_controls(self.leveler_enabled.isChecked())
        self.reduction.setValue(defaults.noise_reduction_db)
        self.update_track_controls()

    def duck_controls(self, enabled):
        for widget in (self.duck_depth, self.duck_attack, self.duck_release):
            widget.setEnabled(enabled and self.ducking.isEnabled())

    def deesser_controls(self, enabled):
        self.deesser_amount.setEnabled(enabled and self.deesser.isEnabled())

    def broad_eq_controls(self, enabled):
        for widget in (self.broad_eq_frequency, self.broad_eq_gain):
            widget.setEnabled(enabled and self.broad_eq.isEnabled())

    def bleed_controls(self, enabled):
        for widget in (self.bleed_depth, self.bleed_attack, self.bleed_hold, self.bleed_release):
            widget.setEnabled(enabled and self.bleed_reduction.isEnabled())

    def update_track_controls(self, *_):
        roles = [self.tracks.cellWidget(row, 1).currentData() for row in range(self.tracks.rowCount())
                 if self.tracks.cellWidget(row, 1)]
        relevant = "speech" in roles and "music" in roles
        bypass = self.mode.currentData() == "bypass"
        if bypass:
            for automatic in (self.leveler_enabled, self.deesser, self.broad_eq):
                automatic.setChecked(False)
            self.deesser.setEnabled(False)
            self.broad_eq.setEnabled(False)
        self.ducking.setEnabled(relevant and not bypass)
        if not relevant or bypass:
            self.ducking.setChecked(False)
        self.duck_controls(self.ducking.isChecked())
        self.export_stems.setEnabled(len(roles) >= 2)
        speech_count = roles.count("speech")
        bleed_available = speech_count >= 2 and not bypass
        self.bleed_reduction.setEnabled(bleed_available)
        if not bleed_available:
            self.bleed_reduction.setChecked(False)
        self.bleed_controls(self.bleed_reduction.isChecked())
        row = self.tracks.currentRow()
        selected_speech = row >= 0 and self.tracks.cellWidget(row, 1).currentData() == "speech"
        self.bleed_protect.blockSignals(True)
        self.bleed_protect.setChecked(bool(self.tracks.item(row, 0).data(Qt.UserRole + 2)) if row >= 0 else False)
        self.bleed_protect.setEnabled(selected_speech and not bypass)
        self.bleed_protect.blockSignals(False)
        if hasattr(self, "processing_note"):
            if self.mode.currentData() in {"natural-speech", "strong-cleanup", "multitrack"}:
                self.processing_note.setText("Cleanup and de-essing require mono Speech tracks; leveling also supports stereo Speech. "
                    + ("No Speech track selected: change the Role column to Speech for voice-only recordings. "
                       if not speech_count else "Stereo Speech tracks bypass cleanup; use a mono voice source. ")
                    + "Program, Music and Ambience tracks keep their background content.")
            else:
                self.processing_note.setText("Master only adjusts loudness and peaks; already-mastered audio may sound unchanged. "
                    "For mono voice cleanup, choose Natural speech and set Role to Speech."
                    if not bypass else "Bypass converts format and preserves explicit mix gains; automatic processing is off.")
        self.update_actions()

    def set_selected_bleed_protection(self, enabled):
        row = self.tracks.currentRow()
        if row >= 0:
            self.tracks.item(row, 0).setData(Qt.UserRole + 2, bool(enabled))

    def edit_gain_envelope(self):
        row = self.tracks.currentRow()
        if row < 0:
            self.log.append("Select a track to edit its gain envelope.")
            return
        item = self.tracks.item(row, 0)
        points = [GainPoint(**point) for point in (item.data(Qt.UserRole + 1) or [])]
        dialog = GainEnvelopeDialog(points, self)
        if dialog.exec() == QDialog.Accepted:
            try:
                item.setData(Qt.UserRole + 1, [vars(point) for point in dialog.values()])
                if not dialog.values():
                    item.setData(Qt.UserRole + 3, False)
                self.log.append(f"Track {row + 1} gain envelope saved with {len(dialog.values())} points.")
            except (ValueError, TypeError) as exc:
                QMessageBox.warning(self, "Invalid gain envelope", str(exc))

    def import_leveler_curves(self):
        if not self.last_report:
            return
        try:
            report = json.loads(Path(self.last_report).read_text(encoding="utf-8"))
            curves = report.get("leveling", {}).get("tracks", [])
            reported_sources = [Path(item["path"]).resolve() for item in report.get("sources", [])]
            if any(not isinstance(entry.get("track"), int) or not 0 <= entry["track"] < self.tracks.rowCount()
                   or entry["track"] >= len(reported_sources)
                   or reported_sources[entry["track"]] != Path(self.tracks.item(entry["track"], 0).text()).resolve()
                   or self.tracks.cellWidget(entry["track"], 1).currentData() != "speech"
                   for entry in curves):
                self.log.append("Leveler curves were not imported: the current source/role layout differs from the report. Restore it first so importing all curves preserves the rendered balance.")
                return
            imported = 0
            for entry in curves:
                index = entry.get("track")
                points = entry.get("gain_curve", [])
                if not isinstance(index, int) or not 0 <= index < self.tracks.rowCount() or not points:
                    continue
                normalized = sorted((GainPoint(int(point["sample"]), float(point["gain_db"])) for point in points),
                                    key=lambda point: point.sample)
                Track(self.tracks.item(index, 0).text(), role=self.tracks.cellWidget(index, 1).currentData(),
                      gain_envelope=normalized).validate()
                self.tracks.item(index, 0).setData(Qt.UserRole + 1, [vars(point) for point in normalized])
                self.tracks.item(index, 0).setData(Qt.UserRole + 3, True)
                imported += 1
            if imported:
                self.leveler_enabled.setChecked(False)
                self.log.append(f"Imported all {imported} actual leveler curve(s) as editable manual envelopes; global dynamic leveling is disabled so those gains are not applied twice.")
            else:
                self.log.append("The last report has no leveler curves to import.")
        except (OSError, ValueError, TypeError, KeyError) as exc:
            QMessageBox.warning(self, "Could not import leveler curves", str(exc))

    def leveler_controls(self, enabled):
        for widget in (self.leveler_strength, self.dynamics, self.max_boost, self.max_attenuation):
            widget.setEnabled(enabled)

    def import_dialog(self):
        patterns = " ".join(f"*{extension}" for extension in sorted(SUPPORTED_MEDIA))
        paths, _ = QFileDialog.getOpenFileNames(self, "Import audio", "", f"Media ({patterns})")
        self.add_paths(paths)

    def add_paths(self, paths):
        for path in paths:
            path = Path(path)
            if path.is_dir():
                self.add_paths(sorted(p for p in path.iterdir() if p.suffix.lower() in SUPPORTED_MEDIA))
                continue
            if not path.is_file():
                continue
            if path.suffix.lower() not in SUPPORTED_MEDIA:
                self.log.append(f"Unsupported media skipped: {path.name}")
                continue
            row = self.tracks.rowCount()
            if row >= 32:
                self.log.append("32-track limit reached.")
                break
            self.tracks.insertRow(row)
            for col, value in enumerate((str(path.resolve()), "", "0", "0", "")):
                item = QTableWidgetItem(value)
                if col == 0:
                    item.setToolTip(str(path.resolve()))
                self.tracks.setItem(row, col, item)
            roles = QComboBox()
            for role in ("program", "speech", "music", "ambience"):
                roles.addItem(role.title(), role)
            self.tracks.setCellWidget(row, 1, roles)
            roles.currentIndexChanged.connect(self.update_track_controls)
            roles.currentIndexChanged.connect(self.invalidate_results)
            roles.setFocusPolicy(Qt.StrongFocus)
            roles.installEventFilter(self)
            self.tracks.item(row, 0).setData(Qt.UserRole + 1, [])
            self.tracks.item(row, 0).setData(Qt.UserRole + 2, False)
        if self.tracks.rowCount():
            self.tracks.selectRow(self.tracks.rowCount()-1)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        self.add_paths([url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()])

    def remove_track(self):
        for row in sorted({i.row() for i in self.tracks.selectedIndexes()}, reverse=True):
            self.tracks.removeRow(row)
        self.update_track_controls()

    def project(self):
        tracks = []
        for row in range(self.tracks.rowCount()):
            values = [self.tracks.item(row, c).text() for c in range(5)]
            tracks.append(Track(values[0], int(values[2]), float(values[3]),
                                self.tracks.cellWidget(row, 1).currentData(), int(values[4]) if values[4] else None,
                                [GainPoint(**point) for point in (self.tracks.item(row, 0).data(Qt.UserRole + 1) or [])],
                                bool(self.tracks.item(row, 0).data(Qt.UserRole + 2)),
                                bool(self.tracks.item(row, 0).data(Qt.UserRole + 3))))
        preset = make_preset(self.mode.currentData(), target_lufs=self.target.value(),
            true_peak=self.peak.value(), highpass_hz=self.highpass.value(),
            noise_reduction_db=self.reduction.value(), leveler_strength=self.leveler_strength.value(),
            preserve_dynamics=self.dynamics.value(), max_boost_db=self.max_boost.value(),
            max_attenuation_db=self.max_attenuation.value(),
            leveler_enabled=self.leveler_enabled.isChecked(), music_ducking_enabled=self.ducking.isChecked(),
            music_duck_db=self.duck_depth.value(), duck_attack_ms=self.duck_attack.value(),
            duck_release_ms=self.duck_release.value(), deesser_enabled=self.deesser.isChecked(),
            deesser_range=self.deesser_amount.value(), broad_eq_enabled=self.broad_eq.isChecked(),
            broad_eq_frequency_hz=self.broad_eq_frequency.value(), broad_eq_gain_db=self.broad_eq_gain.value())
        preset.bleed_reduction_enabled = self.bleed_reduction.isChecked()
        preset.bleed_max_attenuation_db = self.bleed_depth.value()
        preset.bleed_attack_ms = self.bleed_attack.value()
        preset.bleed_hold_ms = self.bleed_hold.value()
        preset.bleed_release_ms = self.bleed_release.value()
        project = Project(tracks, preset, export_stems=self.export_stems.isChecked(), metadata=self.metadata.copy())
        project.validate()
        return project

    def edit_metadata(self):
        dialog = MetadataDialog(self.metadata, self)
        if dialog.exec() == QDialog.Accepted:
            self.metadata = dialog.values()
            self.invalidate_results()

    def new_project(self):
        if self.process:
            self.log.append("Wait for the active worker or cancel it before starting a new project.")
            return
        if (self.tracks.rowCount() or self.metadata) and QMessageBox.question(
                self, "New project", "Clear the current project settings and output tags? Saved files and queued jobs are kept.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self.tracks.setRowCount(0)
        self.metadata = {}
        self.mode.setCurrentIndex(self.mode.findData("master-only"))
        self.target.setValue(-19)
        self.peak.setValue(-1)
        self.highpass.setValue(0)
        self.reduction.setValue(6)
        self.leveler_enabled.setChecked(False)
        self.leveler_strength.setValue(0.8)
        self.dynamics.setValue(0.25)
        self.max_boost.setValue(9)
        self.max_attenuation.setValue(12)
        self.ducking.setChecked(False)
        self.duck_depth.setValue(6)
        self.duck_attack.setValue(100)
        self.duck_release.setValue(800)
        self.bleed_reduction.setChecked(False)
        self.bleed_depth.setValue(6)
        self.bleed_attack.setValue(100)
        self.bleed_hold.setValue(300)
        self.bleed_release.setValue(500)
        self.export_stems.setChecked(False)
        self.deesser.setChecked(False)
        self.deesser_amount.setValue(1.8)
        self.broad_eq.setChecked(False)
        self.broad_eq_frequency.setValue(1000)
        self.broad_eq_gain.setValue(0)
        self.waveform.peaks = []
        self.waveform.update()
        self.player.stop()
        self.play_when_loaded = False
        self.player.setSource(QUrl())
        self._clear_playback_audition(unload=False)
        self.last_report = self.last_output = None
        self.preview_files = None
        self.import_curves_button.setEnabled(False)
        self.update_track_controls()
        self.log.append("Started a new project with blank output tags.")
        self.activity.setText("Ready — import audio, choose processing, then Export audio.")

    def save_project(self):
        try:
            project = self.project()
            path, _ = QFileDialog.getSaveFileName(self, "Save project", "session.fauxphonic.json", "Project (*.json)")
            if path:
                if Path(path).suffix.lower() != ".json" or Path(path).resolve() in [Path(t.path).resolve() for t in project.tracks]:
                    raise ValueError("Choose a JSON project file, separate from source media.")
                write_json(path, project.to_dict())
        except Exception as exc:
            self.log.append(str(exc))

    def open_project(self):
        if self.process:
            self.log.append("Wait for the active worker or cancel it before opening a project.")
            return
        path, _ = QFileDialog.getOpenFileName(self, "Open project", "", "Project (*.json)")
        if not path:
            return
        try:
            project = Project.load(path)
            self.tracks.setRowCount(0)
            self.add_paths([t.path for t in project.tracks])
            for row, track in enumerate(project.tracks):
                self.tracks.cellWidget(row, 1).setCurrentIndex(self.tracks.cellWidget(row, 1).findData(track.role))
                for col, value in ((2, track.offset_samples), (3, track.gain_db), (4, "" if track.stream is None else track.stream)):
                    self.tracks.item(row, col).setText(str(value))
                self.tracks.item(row, 0).setData(Qt.UserRole + 1, [vars(point) for point in track.gain_envelope])
                self.tracks.item(row, 0).setData(Qt.UserRole + 2, track.bleed_protected)
                self.tracks.item(row, 0).setData(Qt.UserRole + 3, track.leveler_curve_imported)
            selected = ("bypass" if project.preset.mode == "bypass" else
                        ("strong-cleanup" if project.preset.noise_reduction_db > 6 else
                         "multitrack" if len(project.tracks) > 1 else "natural-speech")
                        if project.preset.mode == "dsp-cleanup" else
                        "preserve-music" if all(t.role == "music" for t in project.tracks) else "master-only")
            self.mode.setCurrentIndex(self.mode.findData(selected))
            self.leveler_enabled.setChecked(project.preset.leveler_enabled)
            self.leveler_strength.setValue(project.preset.leveler_strength)
            self.dynamics.setValue(project.preset.preserve_dynamics)
            self.max_boost.setValue(project.preset.max_boost_db)
            self.max_attenuation.setValue(project.preset.max_attenuation_db)
            self.target.setValue(project.preset.target_lufs)
            self.peak.setValue(project.preset.true_peak)
            self.highpass.setValue(project.preset.highpass_hz)
            self.reduction.setValue(project.preset.noise_reduction_db)
            self.ducking.setChecked(project.preset.music_ducking_enabled)
            self.duck_depth.setValue(project.preset.music_duck_db)
            self.duck_attack.setValue(project.preset.duck_attack_ms)
            self.duck_release.setValue(project.preset.duck_release_ms)
            self.bleed_reduction.setChecked(project.preset.bleed_reduction_enabled)
            self.bleed_depth.setValue(project.preset.bleed_max_attenuation_db)
            self.bleed_attack.setValue(project.preset.bleed_attack_ms)
            self.bleed_hold.setValue(project.preset.bleed_hold_ms)
            self.bleed_release.setValue(project.preset.bleed_release_ms)
            self.deesser.setChecked(project.preset.deesser_enabled)
            self.deesser_amount.setValue(project.preset.deesser_range)
            self.broad_eq.setChecked(project.preset.broad_eq_enabled)
            self.broad_eq_frequency.setValue(project.preset.broad_eq_frequency_hz)
            self.broad_eq_gain.setValue(project.preset.broad_eq_gain_db)
            self.export_stems.setChecked(project.export_stems)
            self.metadata = project.metadata.copy()
            self.update_track_controls()
        except Exception as exc:
            self.log.append(str(exc))

    def analyze_selected(self):
        if self.process:
            self.log.append("Wait for the active worker or cancel it first.")
            return
        try:
            project = self.project()
            row = self.tracks.currentRow()
            if row < 0:
                raise ValueError("Select a source track.")
            track = project.tracks[row]
            self.launch({"action": "analyze", "path": track.path, "stream": track.stream,
                         "scratch_dir": str(analysis_scratch_root())})
        except Exception as exc:
            self.log.append(str(exc))

    def build_preview(self):
        if self.process:
            self.log.append("Wait for the active worker or cancel it first.")
            return
        try:
            project = self.project()
            if not self.confirm_processing(project):
                return
            if project.preset.mode == "dsp-cleanup" and project.preset.noise_reduction_db > 6:
                QMessageBox.warning(self, "Strong cleanup", "Strong cleanup can add artifacts. Review the A/B preview before exporting.")
            if self.preview_directories:
                self.player.stop()
                self.player.setSource(QUrl())
                for folder in self.preview_directories:
                    self._remove_preview_directory(folder)
                self.preview_files = None
                self.preview_directories.clear()
            key = uuid.uuid4().hex
            active_dir = self.data_dir / "previews" / f"preview-active-{key}"
            self.current_job = None
            self.log.append("Building A/B preview. The first build processes the full program; the selected segment is extracted after final mastering.")
            self.launch({"action": "preview", "project": project.to_dict(),
                "start_sample": round(self.preview_start.value() * RATE),
                "duration_samples": round(self.preview_duration.value() * RATE),
                "cache_root": str(self.data_dir / "preview-cache"),
                "work_root": str(self.data_dir / "preview-work"), "active_dir": str(active_dir),
                "owner_pid": os.getpid()})
        except Exception as exc:
            self.log.append(str(exc))

    def _remove_preview_directory(self, folder):
        folder = Path(folder)
        root = (self.data_dir / "previews").resolve()
        try:
            if (folder.is_symlink() or getattr(folder, "is_junction", lambda: False)()
                    or folder.resolve().parent != root or not folder.name.startswith("preview-active-")):
                return
            owner_pid = int((folder / "owner.pid").read_text(encoding="ascii"))
            if owner_pid == os.getpid() or not psutil.pid_exists(owner_pid):
                shutil.rmtree(folder)
        except (OSError, ValueError):
            pass

    def _clear_playback_audition(self, unload=True):
        folder, self.playback_directory = self.playback_directory, None
        self.prepared_playback = None
        if unload:
            self.play_when_loaded = False
            self.player.stop()
            self.player.setSource(QUrl())
        if folder:
            self._remove_preview_directory(folder)

    def _play_source(self, path, stream=None):
        path = Path(path)
        self.transport_generation += 1
        try:
            stat = path.stat()
            identity = (str(path.resolve()), stream, stat.st_size, stat.st_mtime_ns)
        except OSError as exc:
            self.log.append("Playback: " + str(exc))
            return
        if self.prepared_playback and self.prepared_playback[0] == identity:
            self.audio.setVolume(1.0)
            self.play(self.prepared_playback[1])
            return
        if path.suffix.lower() in {".wav", ".flac"} and stream is None:
            if self.playback_directory:
                self._clear_playback_audition()
            self.audio.setVolume(1.0)
            self.play(path)
            return
        if self.process:
            self.log.append("Wait for the active worker or cancel it before preparing playback.")
            return
        self._clear_playback_audition()
        key = uuid.uuid4().hex
        active_dir = self.data_dir / "previews" / f"preview-active-{key}"
        self.pending_playback_directory = active_dir
        detail = f"Preparing native playback for stream {stream}." if stream is not None else "Preparing native playback."
        self.log.append(detail)
        self.launch({"action": "playback", "path": str(path.resolve()), "stream": stream,
            "transport_generation": self.transport_generation, "source_identity": identity,
            "active_dir": str(active_dir), "data_dir": str(self.data_dir.resolve())})

    def _play_preview(self, kind):
        if not self.preview_files:
            self.log.append("Build an A/B preview first.")
            return
        match_db = self.preview_match_db or 0.0
        if self.match_loudness.isChecked():
            original_volume = 10 ** (-max(0.0, match_db) / 20)
            processed_volume = 10 ** (min(0.0, match_db) / 20)
        else:
            original_volume = processed_volume = 1.0
        self.audio.setVolume(original_volume if kind == "original" else processed_volume)
        self.play(self.preview_files[kind], restart=True)

    def play_preview_original(self):
        self._play_preview("original")

    def play_preview_processed(self):
        self._play_preview("processed")

    def queue_render(self):
        try:
            project = self.project()
            if not self.confirm_processing(project):
                return
            if project.preset.mode == "dsp-cleanup" and project.preset.noise_reduction_db > 6:
                QMessageBox.warning(self, "Strong cleanup", "Strong cleanup can add artifacts. Review the A/B preview before exporting.")
            source = Path(project.tracks[0].path)
            label = "cleanup" if project.preset.mode == "dsp-cleanup" else "master"
            output = self.choose_output(source.with_name(f"{source.stem}_{label}.wav"), project)
            if output:
                self.jobs.add({"action": "render", "project": project.to_dict(), "output": output})
                self.log.append(f"Audio export queued: {output}. The JSON report is saved separately after verification.")
                if project.export_stems and Path(output).suffix.lower() in {".mp3", ".m4a", ".opus"}:
                    self.log.append("Per-track stems will be written as WAV alongside the lossy master.")
                self.refresh_queue()
                self.start_next()
        except Exception as exc:
            self.log.append(str(exc))

    def confirm_processing(self, project):
        needs_speech = (project.preset.mode == "dsp-cleanup" and project.preset.noise_reduction_db > 0
                        or project.preset.leveler_enabled or project.preset.deesser_enabled)
        if needs_speech and not any(track.role == "speech" for track in project.tracks):
            QMessageBox.warning(self, "Speech processing has no target",
                "These stages require Speech tracks. Cleanup and de-essing require mono; leveling also supports stereo. In the Role column, choose Speech "
                "for voice-only recordings, then try again. Use Master only with the leveler off for mixed programs.")
            return False
        return True

    def output_in_use(self, output, project):
        paths = output_paths(output, project)
        sources = {Path(track.path).resolve() for track in project.tracks}
        reserved = set()
        for _, request, state, _ in self.jobs.rows():
            if state not in {"complete", "failed", "cancelled", "interrupted"}:
                request = json.loads(request)
                reserved.update(output_paths(request["output"], Project.from_dict(request["project"])))
        return any(path.exists() or path.resolve() in sources or path.resolve() in reserved
                   or path.with_name(path.name + ".render-lock").exists() for path in paths)

    def choose_output(self, suggested, project):
        suffixes = {"WAV (lossless) (*.wav)": ".wav", "FLAC (lossless) (*.flac)": ".flac",
                    "MP3 (*.mp3)": ".mp3", "AAC / M4A (*.m4a)": ".m4a", "Opus (*.opus)": ".opus"}
        while True:
            candidate, number = suggested, 2
            while self.output_in_use(candidate, project):
                candidate = suggested.with_stem(f"{suggested.stem}_{number}")
                number += 1
            output, selected_filter = QFileDialog.getSaveFileName(self, "Export audio — choose a new filename",
                str(candidate), ";;".join(suffixes), options=QFileDialog.DontConfirmOverwrite)
            if not output:
                return None
            path = Path(output)
            if not path.suffix:
                path = path.with_suffix(suffixes.get(selected_filter, suggested.suffix))
            if path.suffix.lower() not in FORMATS:
                QMessageBox.warning(self, "Choose an audio format", "Choose WAV, FLAC, MP3, M4A or Opus. JSON is a report, not audio.")
                continue
            if self.output_in_use(path, project):
                QMessageBox.warning(self, "Choose a new output name",
                    "This audio filename, report or stem already exists or is queued. Existing files are kept. "
                    "A new filename will be suggested.")
                suggested = path
                continue
            return str(path.resolve())

    def refresh_queue(self):
        rows = self.jobs.rows()
        self.queue.setRowCount(len(rows))
        for index, (job_id, request, state, detail) in enumerate(rows):
            cell = QTableWidgetItem(json.loads(request)["output"])
            cell.setData(Qt.UserRole, job_id)
            self.queue.setItem(index, 0, cell)
            self.queue.setItem(index, 1, QTableWidgetItem(state + (": " + detail if detail else "")))

    def start_next(self):
        if self.process or self.closing:
            return
        for job_id, request, state, _ in self.jobs.rows():
            if state == "queued":
                self.current_job = job_id
                self.launch(json.loads(request))
                break

    def retry_job(self):
        row = self.queue.currentRow()
        if row < 0:
            return
        job_id = self.queue.item(row, 0).data(Qt.UserRole)
        for saved_id, request, state, _ in self.jobs.rows():
            if saved_id == job_id and state in {"interrupted", "failed", "cancelled"}:
                request = json.loads(request)
                output = Path(request["output"])
                project = Project.from_dict(request["project"])
                if self.output_in_use(output, project):
                    replacement = self.choose_output(output, project)
                    if not replacement:
                        return
                    request["output"] = replacement
                self.jobs.add(request)
                self.log.append("Restart queued from originals. Existing outputs/reports will not be overwritten.")
                self.refresh_queue()
                self.start_next()
                break

    def launch(self, request):
        self.request_revision = self.project_revision
        key = uuid.uuid4().hex
        request["cancel_file"] = str(self.data_dir / f"{key}.cancel")
        self.request_path = self.data_dir / f"{key}.request.json"
        request["request_path"] = str(self.request_path)
        if request["action"] == "render" and self.current_job:
            request["storage_job"] = {"database": str((self.data_dir / "jobs.sqlite").resolve()),
                                      "id": self.current_job}
        write_json(self.request_path, request)
        self.request_path.with_name(key + ".worker.pid").write_text(str(os.getpid()), encoding="ascii")
        self.current_request = request
        self.completed_event = False
        self.last_stage = None
        self.buffer = b""
        self.process = QProcess(self)
        if getattr(sys, "frozen", False):
            executable = str(Path(sys.executable).with_name("Fauxphonic-worker.exe"))
            arguments = ["worker", str(self.request_path)]
        else:
            executable = str(Path(sys.executable).with_name("python.exe"))
            arguments = [str(ROOT / "run_worker.py"), "worker", str(self.request_path)]
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUTF8", "1")
        self.process.setProcessEnvironment(env)
        self.process.readyReadStandardOutput.connect(self.read_events)
        self.process.readyReadStandardError.connect(self.read_errors)
        self.process.finished.connect(self.finished)
        self.process.errorOccurred.connect(self.worker_error)
        action = request["action"]
        self.activity.setText({"render": "Exporting audio", "analyze": "Analyzing selected audio",
            "preview": "Building A/B — the first preview processes the full recording",
            "playback": "Preparing audio for playback"}.get(action, action) + "… You can cancel below.")
        self.progress.setRange(0, 0)
        self.update_actions()
        self.process.start(executable, arguments)

    def worker_error(self, error):
        if error == QProcess.FailedToStart:
            self.log.append("Worker could not start: " + self.process.errorString())
            self.finished(1, QProcess.CrashExit)

    def read_errors(self):
        if self.process:
            self.log.append(bytes(self.process.readAllStandardError()).decode("utf-8", "replace")[-4000:])

    def read_events(self):
        if not self.process:
            return
        self.buffer += bytes(self.process.readAllStandardOutput())
        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)
            try:
                event = json.loads(line)
                if event.get("protocol") != 1:
                    raise ValueError("Unsupported worker protocol")
                stage = event["stage"]
                if event.get("progress", 0) > 0 or stage in {"complete", "failed", "cancelled"}:
                    self.progress.setRange(0, 100)
                self.progress.setValue(int(event.get("progress", 0) * 100))
                self.progress.setFormat(stage.title() + " %p%")
                if stage != self.last_stage:
                    self.log.append(stage.title() + (": " + event["detail"] if event.get("detail") else ""))
                    if self.current_job:
                        self.jobs.update(self.current_job, event)
                        self.refresh_queue()
                    self.last_stage = stage
                if stage in {"failed", "cancelled"}:
                    self.activity.setText(stage.title() + ": " + event.get("detail", "See the log below."))
                    if event.get("report"):
                        self.last_report = event["report"]
                        self.update_actions()
                if stage == "complete":
                    self.completed_event = True
                    action = self.current_request["action"]
                    current = self.request_revision == self.project_revision
                    if action == "analyze":
                        row = self.tracks.currentRow()
                        stream = self.tracks.item(row, 4).text().strip() if row >= 0 else ""
                        current = (current and row >= 0
                            and self.tracks.item(row, 0).text() == self.current_request["path"]
                            and stream == (str(self.current_request["stream"]) if self.current_request.get("stream") is not None else ""))
                    if action in {"render", "preview"}:
                        try:
                            current = current and self.current_request["project"] == self.project().to_dict()
                        except (ValueError, TypeError, AttributeError):
                            current = False
                    if action == "playback" and self.current_request.get("transport_generation") != self.transport_generation:
                        Path(event["playback"]["active_dir"], "owner.pid").write_text(str(os.getpid()), encoding="ascii")
                        self._remove_preview_directory(event["playback"]["active_dir"])
                        self.pending_playback_directory = None
                        self.log.append("Playback preparation finished; keeping your newer transport selection.")
                        continue
                    if action != "playback" and not current:
                        if action == "preview":
                            self._remove_preview_directory(event["preview"]["active_dir"])
                        self.log.append("Finished an earlier project/settings snapshot: " + event.get("output", action)
                                        + ". Current results were not replaced.")
                        self.activity.setText("Ready — settings changed; build or export the current version.")
                        continue
                    self.activity.setText("Ready — " + {"analyze": "analysis complete. Choose processing and Export audio.",
                        "preview": "A/B preview ready. Use the original and processed preview buttons.",
                        "playback": "playing original audio."}.get(self.current_request["action"], "audio export verified."))
                    if "analysis" in event:
                        result = event["analysis"]
                        self.waveform.peaks = result["waveform"]
                        self.waveform.update()
                        self.log.append("Measured " + result["loudness"]["input_i"] + " LUFS; " + result["loudness"]["input_tp"] + " dBTP")
                    elif "preview" in event:
                        result = event["preview"]
                        self.preview_files = {"original": result["original"], "processed": result["processed"]}
                        self.preview_directories = [result["active_dir"]]
                        Path(result["active_dir"], "owner.pid").write_text(str(os.getpid()), encoding="ascii")
                        self.preview_match_db = result.get("loudness_match_db")
                        self.log.append(f"A/B ready: {result['duration_samples']/RATE:g} s from {self.preview_start.value():g} s; "
                            f"original common attenuation {result['original_compensation_db']:.2f} dB; "
                            f"processed mastering {result['mastering_mode']}.")
                        self.log_processing_outcomes(result.get("processing", []))
                        if self.preview_match_db is not None:
                            self.log.append(f"Loudness match difference: {self.preview_match_db:+.2f} dB "
                                            "(Original relative to Processed).")
                        self.log.append(f"Preview cache key {result['cache_key']}; first-render space estimate "
                                        f"{result['estimated_scratch_bytes'] / 1024**3:.2f} GiB.")
                        leveling = result["leveling"]
                        leveler_status = (f"enabled for {leveling['phrase_count']} speech regions"
                                          if leveling.get("enabled") else leveling.get("reason", "not applied"))
                        self.log.append("Speech leveler outcome: " + leveler_status)
                    elif "playback" in event:
                        result = event["playback"]
                        folder = Path(result["active_dir"])
                        (folder / "owner.pid").write_text(str(os.getpid()), encoding="ascii")
                        self.playback_directory = folder
                        self.pending_playback_directory = None
                        self.prepared_playback = (self.current_request["source_identity"], result["path"])
                        self.audio.setVolume(1.0)
                        self.play(result["path"])
                        self.log.append(f"Audition ready: stream {result['stream']}, {result['channels']} channel(s), "
                            f"{result['frames']} samples at {result['sample_rate']} Hz. Original file unchanged.")
                    else:
                        self.last_output, self.last_report = event["output"], event["report"]
                        self.activity.setText("Audio exported: " + self.last_output + " — use Play export or Open output folder.")
                        if event["status"] == "exception":
                            reason = event.get("verification", {}).get("exception", "See the report for the unmet target.")
                            self.activity.setText("Exported with an exception: " + reason)
                            self.log.append("Export exception: " + reason)
                        self.log.append("Export verification: " + event["status"] + " — " + self.last_output)
                        self.log.append("Mastering mode: " + event.get("mastering_mode", "see verified report"))
                        for item in event.get("processing", []):
                            if "cleanup" in item:
                                self.log.append(f"Track {item['track'] + 1}: {item['cleanup']}")
                        self.log_processing_outcomes(event.get("processing", []))
                        for stem in event.get("stems", []):
                            self.log.append(f"Stem track {stem['track_index'] + 1} ({stem['role']}): {stem['path']} — {stem['mastering']}")
                        ducking = event.get("music_ducking", {})
                        if isinstance(ducking, dict):
                            self.log.append("Music ducking: " + ("applied" if ducking.get("enabled")
                                else ducking.get("reason", "not applied")))
                        else:
                            self.log.append("Music ducking: " + ("applied" if ducking else "not applied; see report for reason"))
                        bleed = event.get("bleed_reduction", {})
                        if isinstance(bleed, dict) and bleed.get("enabled"):
                            self.log.append(f"Conservative bleed attenuation: analyzed {bleed.get('track_count', 0)} tracks; "
                                f"{bleed.get('candidate_events', 0)} candidate event(s). Heuristic only; uncertain overlap is unchanged.")
                        elif isinstance(bleed, dict):
                            self.log.append("Conservative bleed attenuation: " + bleed.get("reason", "not applied"))
                        leveling = event.get("leveling", {})
                        if leveling.get("has_curves", leveling.get("phrase_count", 0) > 0):
                            self.import_curves_button.setEnabled(True)
                            self.log.append("Actual leveler gain curves are available to import as manual envelopes.")
                    self.update_actions()
            except (ValueError, KeyError) as exc:
                self.log.append("Worker protocol error: " + str(exc))

    def finished(self, code, status):
        if not self.process:
            return
        self.read_events()
        if code != 0 or not self.completed_event:
            if self.current_request.get("action") == "preview":
                self._remove_preview_directory(self.current_request["active_dir"])
            elif self.current_request.get("action") == "playback":
                self._remove_preview_directory(self.current_request["active_dir"])
                self.pending_playback_directory = None
            if self.last_stage not in {"failed", "cancelled"}:
                message = "Worker interrupted; no verified completion. Restart from originals."
                self.log.append(message)
                self.activity.setText(message)
                if self.current_job:
                    self.jobs.update(self.current_job, {"stage": "interrupted", "detail": message})
        self.process.deleteLater()
        self.process = None
        Path(self.current_request["cancel_file"]).unlink(missing_ok=True)
        self.request_path.unlink(missing_ok=True)
        self.request_path.with_name(self.request_path.name.removesuffix(".request.json") + ".worker.pid").unlink(missing_ok=True)
        self.current_job = None
        self.progress.setRange(0, 100)
        self.update_actions()
        self.refresh_queue()
        if self.closing:
            self.close()
        else:
            self.start_next()

    def cancel(self):
        if self.process:
            Path(self.current_request["cancel_file"]).touch()
            self.log.append("Cancellation requested; waiting for worker cleanup.")
            process = self.process
            QTimer.singleShot(15000, lambda: self.stop_unresponsive_worker(process))

    def stop_unresponsive_worker(self, process):
        if self.process is process and process.state() != QProcess.NotRunning:
            self.log.append("The worker did not finish cancelling. Stopping it; any unverified output remains withheld.")
            process.kill()

    def media_status_changed(self, status):
        if status == QMediaPlayer.LoadedMedia and self.play_when_loaded:
            # Media Foundation can stall at ~400 ms if play races source loading.
            QTimer.singleShot(0, self.start_loaded_playback)

    def start_loaded_playback(self):
        if self.play_when_loaded and self.player.mediaStatus() == QMediaPlayer.LoadedMedia:
            self.play_when_loaded = False
            self.player.play()

    def pause(self):
        self.transport_generation += 1
        self.play_when_loaded = False
        self.player.pause()

    def play(self, path, restart=False):
        self.transport_generation = getattr(self, "transport_generation", 0) + 1
        source = QUrl.fromLocalFile(str(path))
        if self.player.source() != source:
            self.play_when_loaded = True
            self.player.setSource(source)
            return
        if self.player.mediaStatus() == QMediaPlayer.LoadingMedia:
            self.play_when_loaded = True
            return
        self.play_when_loaded = False
        if restart or self.player.mediaStatus() == QMediaPlayer.EndOfMedia:
            self.player.setPosition(0)
        self.player.play()

    def play_original(self):
        row = self.tracks.currentRow()
        if row >= 0:
            stream_text = self.tracks.item(row, 4).text().strip()
            try:
                stream = int(stream_text) if stream_text else None
                if stream is not None and stream < 0:
                    raise ValueError("negative index")
            except ValueError:
                self.log.append("Audio stream index must be a nonnegative integer, or blank for a single audio stream.")
                return
            self._play_source(self.tracks.item(row, 0).text(), stream)

    def play_export(self):
        if self.last_output:
            self._play_source(self.last_output)

    def open_report(self):
        if self.last_report:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.last_report))

    def open_output_folder(self):
        if self.last_output:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self.last_output).parent)))

    def log_processing_outcomes(self, processing):
        for item in processing or []:
            track_number = item.get("track", 0) + 1
            for stage, label in (("deesser", "de-essing"), ("broad_eq", "broad EQ")):
                outcome = item.get(stage)
                if not isinstance(outcome, dict) or outcome.get("status") == "disabled":
                    continue
                if outcome.get("status") == "applied":
                    detail = (f"applied (Amount {outcome.get('range_setting', 0):g})"
                              if stage == "deesser" else
                              f"applied at {outcome.get('frequency_hz')} Hz, {outcome.get('gain_db')} dB")
                else:
                    detail = outcome.get("reason", "bypassed")
                self.log.append(f"Track {track_number} {label}: {detail}")

    def show_capabilities(self):
        try:
            CapabilityDialog(self).exec()
        except Exception as exc:
            self.log.append(f"Capabilities unavailable: {type(exc).__name__}: {exc}")
            QMessageBox.warning(self, "Capabilities unavailable", str(exc))

    def show_storage(self):
        if self.process or any(row[2] == "queued" for row in self.jobs.rows()):
            QMessageBox.information(self, "Storage cleanup", "Wait for active and queued work to finish before reviewing storage.")
            return
        from .storage_dialog import StorageDialog
        StorageDialog(self).exec()

    def closeEvent(self, event):
        if self.process:
            self.closing = True
            self.cancel()
            self.log.append("Closing after worker cleanup; queued jobs are saved.")
            event.ignore()
        else:
            self.closing = True
            self.play_when_loaded = False
            self.player.stop()
            self.player.setSource(QUrl())
            for folder in self.preview_directories:
                self._remove_preview_directory(folder)
            self._clear_playback_audition()
            event.accept()


def main():
    import argparse
    parser = argparse.ArgumentParser(prog="Fauxphonic")
    parser.add_argument("--data-dir", type=Path, help="Use a separate absolute queue/cache folder")
    settings, qt_arguments = parser.parse_known_args()
    if settings.data_dir is not None and not settings.data_dir.is_absolute():
        parser.error("--data-dir must be an absolute folder")
    if os.name == "nt":
        import ctypes
        app_user_model_id = "Fauxphonic.LocalAudioStudio"
        result = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_user_model_id)
        if result != 0:
            raise OSError(f"Could not set Windows application identity ({result:#x}).")
    app = QApplication([sys.argv[0], *qt_arguments])
    app.setApplicationName("Fauxphonic")
    app.setOrganizationName("Fauxphonic")
    app.setFont(QFont("Segoe UI", 10))
    app.setWindowIcon(app_icon())
    data_dir = settings.data_dir or Path(QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation))
    data_dir.mkdir(parents=True, exist_ok=True)
    hold_application_mutex()
    lock = QLockFile(str(data_dir / "application.lock"))
    if not lock.tryLock(100):
        QMessageBox.information(None, "Fauxphonic", "Fauxphonic is already open. Use the existing window.")
        return
    window = Window(data_dir)
    window.show()
    app.exec()
