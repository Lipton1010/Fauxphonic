"""On-demand native storage preview. Opening the dialog never scans a disk."""
import json
import sqlite3

from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout,
    QLabel, QMessageBox, QPushButton, QTextEdit, QVBoxLayout)

from . import storage


class StorageDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Storage cleanup preview")
        self.resize(760, 540)
        self.manifest = None
        layout = QVBoxLayout(self)
        note = QLabel("Choose an output folder, .test-runs or .build-runs. Only new, owned, validated "
                      "payloads can be quarantined. Finals and recovery data stay in place. "
                      "Quarantine is reversible and frees no space. Permanent deletion requires separate confirmation.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.details = QTextEdit()
        self.details.setReadOnly(True)
        layout.addWidget(self.details)
        actions = QHBoxLayout()
        for label, callback in [("Choose folder and preview…", self.choose),
                                ("Save preview…", self.save),
                                ("Quarantine reviewed payloads…", self.apply),
                                ("Restore quarantine…", self.restore)]:
            button = QPushButton(label)
            button.clicked.connect(callback)
            actions.addWidget(button)
        layout.addLayout(actions)
        delete = QPushButton("Permanently delete reviewed intermediates…")
        delete.clicked.connect(self.delete)
        layout.addWidget(delete)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def choose(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose one owned payload root or output folder")
        if folder:
            try:
                self.manifest = storage.preview(folder)
                self.details.setPlainText(json.dumps(self.manifest, indent=2))
            except (OSError, ValueError) as exc:
                self.manifest = None
                QMessageBox.warning(self, "Preview unavailable", str(exc))

    def save(self):
        if self.manifest is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save new cleanup preview", "cleanup-preview.json", "JSON (*.json)")
        if path:
            try:
                with open(path, "x", encoding="utf-8") as target:
                    json.dump(self.manifest, target, indent=2)
            except OSError as exc:
                QMessageBox.warning(self, "Preview not saved", str(exc))

    def apply(self):
        if not self.manifest or not self.manifest["candidates"]:
            return
        reply = QMessageBox.question(self, "Confirm recoverable quarantine",
            f"Move these {len(self.manifest['candidates'])} reviewed payload folders to the same-volume "
            ".quarantine folder? Files will be revalidated. No disk space will be reclaimed.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        try:
            result = storage.quarantine(self.manifest, confirmed=True)
            self.manifest = None
            self.details.setPlainText(json.dumps(result, indent=2))
        except (OSError, ValueError, KeyError, TypeError, RuntimeError, sqlite3.Error) as exc:
            QMessageBox.warning(self, "Quarantine stopped", str(exc) + "\nAny prior moves have restore receipts in .quarantine.")

    def restore(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose quarantine restore receipt", "", "Restore receipt (*.restore.json)")
        if path:
            try:
                self.details.setPlainText(json.dumps(storage.restore(path), indent=2))
                self.manifest = None
            except (OSError, ValueError, KeyError) as exc:
                QMessageBox.warning(self, "Restore stopped", str(exc))

    def delete(self):
        if not self.manifest or not self.manifest["candidates"]:
            return
        count = sum(len(entry["files"]) for entry in self.manifest["candidates"])
        reply = QMessageBox.question(self, "Confirm permanent deletion",
            f"Permanently delete the {count} exact reviewed intermediate files "
            f"({self.manifest['bytes']:,} logical bytes)? This cannot be undone. "
            "Final outputs, result records and recovery folders are preserved.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        try:
            result = storage.delete_reviewed(self.manifest, confirmed=True)
            self.manifest = None
            self.details.setPlainText(json.dumps(result, indent=2))
        except (OSError, ValueError, KeyError, TypeError, RuntimeError, sqlite3.Error) as exc:
            self.manifest = None
            QMessageBox.warning(self, "Deletion stopped", str(exc) + "\nRemaining files and cleanup receipts were preserved for review.")
