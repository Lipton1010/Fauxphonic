"""Runtime-backed native desktop capability and model status."""
import html
import importlib.metadata
import json
import re
import subprocess

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel,
                               QMessageBox, QPushButton, QTextBrowser, QVBoxLayout)

from .audio import NO_WINDOW, ROOT, digest
from .model_store import (MODEL_SHA256, config_path, configured_model_path,
                          import_pinned_model, model_path, select_storage, select_registered_model)


def capability_snapshot(root=ROOT):
    root = root.resolve()
    metadata = json.loads((root / "models" / "silero-vad-manifest.json").read_text(encoding="utf-8"))
    try:
        active_model_path = model_path(root)
        model_error = None
    except RuntimeError as exc:
        try:
            active_model_path = configured_model_path(root)
        except RuntimeError:
            active_model_path = root / "models" / metadata["model"]
        model_error = str(exc)
    license_path = root / "models" / metadata["license_file"]
    model_sha = digest(active_model_path) if active_model_path.is_file() else None
    license_sha = digest(license_path) if license_path.is_file() else None
    try:
        runtime = importlib.metadata.version("onnxruntime")
    except importlib.metadata.PackageNotFoundError:
        runtime = "unavailable"
    try:
        import onnxruntime as ort
        providers = ort.get_available_providers()
    except (ImportError, RuntimeError, AttributeError):
        # The frozen GUI omits ONNX Python code; the shared native folder can
        # still appear as an importable namespace. Inference lives in the worker.
        providers = []

    tool_manifest = json.loads((root / "tools" / "manifest.json").read_text(encoding="utf-8"))
    ffmpeg = root / "tools" / "ffmpeg.exe"
    ffmpeg_sha = digest(ffmpeg) if ffmpeg.is_file() else None
    expected_ffmpeg_sha = tool_manifest.get("files", {}).get("ffmpeg.exe")
    version_text, filters_text = "unavailable", ""
    if ffmpeg.is_file():
        version_result = subprocess.run([str(ffmpeg), "-hide_banner", "-version"], capture_output=True,
            stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace", timeout=10, creationflags=NO_WINDOW)
        filter_result = subprocess.run([str(ffmpeg), "-hide_banner", "-filters"], capture_output=True,
            stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace", timeout=10, creationflags=NO_WINDOW)
        if version_result.returncode == 0:
            version_text = version_result.stdout.splitlines()[0].strip()
        if filter_result.returncode == 0:
            filters_text = filter_result.stdout
    filters = set()
    for line in filters_text.splitlines():
        parts = line.split()
        if len(parts) >= 4 and re.fullmatch(r"[A-Z.]{2,3}", parts[0]) and "->" in parts[2]:
            filters.add(parts[1])

    cpu_evidence_path = root / "evidence" / "silero-artifact-verification.json"
    regression_path = root / "evidence" / "leveler-regression.json"
    cpu_evidence = json.loads(cpu_evidence_path.read_text(encoding="utf-8")) if cpu_evidence_path.is_file() else {}
    regression = json.loads(regression_path.read_text(encoding="utf-8")) if regression_path.is_file() else {}
    model_ok = model_error is None and model_sha == metadata.get("model_sha256") == MODEL_SHA256
    license_ok = license_sha == metadata.get("license_sha256")
    tool_ok = ffmpeg_sha == expected_ffmpeg_sha
    regression_model = regression.get("model", {})
    cpu_proof_ok = ("CPUExecutionProvider" in cpu_evidence.get("providers", [])
                    and cpu_evidence.get("sha256", {}).get(metadata["model"]) == metadata["model_sha256"]
                    and regression_model.get("provider") == "CPUExecutionProvider"
                    and regression_model.get("sha256") == metadata["model_sha256"])
    return {
        "model": {"path": str(active_model_path), "version": metadata["version"], "sha256": model_sha,
                  "expected_sha256": metadata["model_sha256"], "bytes": active_model_path.stat().st_size if active_model_path.is_file() else 0,
                  "license": metadata["license"], "license_path": str(license_path), "license_sha256": license_sha,
                  "runtime": runtime, "providers": providers, "ok": model_ok and license_ok,
                  "error": model_error, "settings_path": str(config_path())},
        "cpu_proof": {"ok": cpu_proof_ok, "evidence_path": str(regression_path),
                      "before_db": regression.get("exported_audio_comparison", {}).get("before_absolute_utterance_level_difference_db"),
                      "after_db": regression.get("exported_audio_comparison", {}).get("after_absolute_utterance_level_difference_db"),
                      "limitation": regression.get("purpose", "CPU-only test evidence unavailable.")},
        "ffmpeg": {"path": str(ffmpeg), "version": version_text, "sha256": ffmpeg_sha,
                   "expected_sha256": expected_ffmpeg_sha, "license": tool_manifest.get("license"),
                   "ok": tool_ok, "filters": sorted(filters)},
    }


def _status_html(snapshot):
    model, proof, ffmpeg = snapshot["model"], snapshot["cpu_proof"], snapshot["ffmpeg"]
    esc = lambda value: html.escape(str(value))
    model_state = "Verified" if model["ok"] else "Missing or checksum mismatch"
    proof_state = "Verified CPUExecutionProvider evidence" if proof["ok"] else "CPU evidence unavailable or no longer matches the installed model"
    tools_state = "Hash matches tools manifest" if ffmpeg["ok"] else "Missing or hash differs from tools manifest"
    filters = ", ".join(ffmpeg["filters"]) or "No audio filters were reported."
    before, after = proof["before_db"], proof["after_db"]
    regression_line = (f"Synthetic TTS export test: utterance level gap {before:.3f} → {after:.3f} dB; no listening-quality claim."
                       if isinstance(before, (int, float)) and isinstance(after, (int, float)) else proof["limitation"])
    return f"""<h2>Installed speech activity model</h2>
<p><b>Silero VAD {esc(model['version'])}</b> — {esc(model_state)}<br>
SHA-256: <code>{esc(model['sha256'] or 'missing')}</code><br>
Disk size: {model['bytes']:,} bytes ({model['bytes'] / 1024 / 1024:.2f} MiB)<br>
License: {esc(model['license'])} — <a href="file:///{esc(model['license_path'])}">license file</a><br>
Runtime: ONNX Runtime {esc(model['runtime'])}; available providers: {esc(', '.join(model['providers']) or 'none')}<br>
Resolved path: <code>{esc(model['path'])}</code><br>
Storage settings: <code>{esc(model['settings_path'])}</code>{('<br>' + esc(model['error'])) if model.get('error') else ''}</p>
<p><b>CPU evidence:</b> {esc(proof_state)}. The app uses CPUExecutionProvider for this analysis. {esc(regression_line)}</p>
<p>Silero detects speech activity for leveling and music ducking; it does not denoise or restore speech. When you select a model folder, both the GUI and worker use it. Missing or changed model files fail instead of falling back.</p>
<h2>FFmpeg DSP capabilities</h2>
<p>{esc(ffmpeg['version'])}<br>SHA-256: <code>{esc(ffmpeg['sha256'] or 'missing')}</code> — {esc(tools_state)}<br>
License: {esc(ffmpeg['license'])}. Compiled audio filters: {esc(filters)}.</p>
<p>The app exposes optional high-pass filtering, mono Speech-role afftdn cleanup, dynamic-equalizer de-essing for eligible Speech tracks, broad tone EQ, loudness and linked true-peak mastering, music ducking, and conservative correlation-gated bleed attenuation. Speech activity analysis is a heuristic; uncertain overlap is left unchanged. Compiled filters without a validated app control are not presented as available features.</p>
<h2>Unavailable enhancement and restoration</h2>
<ul>
<li><b>FastEnhancer:</b> research-only. Checkpoint redistribution rights are not explicit, and representative speech-quality and listening gates are not passed; no model is bundled.</li>
<li><b>DeepFilterNet3:</b> not integrated. Checkpoint permission remains unresolved; exact build, delay behavior, and packaged Windows inference have not been verified.</li>
<li><b>Dereverberation:</b> no validated dereverb-specific backend or checkpoint is installed. Ordinary denoising is not labeled as room-echo removal.</li>
<li><b>Speech restoration:</b> no rights-cleared restoration model or validated region-replacement workflow is installed.</li>
</ul>
<p>Sources: <a href="https://github.com/aask1357/fastenhancer">FastEnhancer upstream</a>, <a href="https://github.com/Rikorose/DeepFilterNet">DeepFilterNet upstream</a>, and the verified decisions in docs/model_decisions.md.</p>"""


class CapabilityDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Capabilities and model status")
        self.resize(740, 680)
        layout = QVBoxLayout(self)
        title = QLabel("Installed processing capabilities")
        title.setStyleSheet("font-size: 18px; font-weight: 600")
        layout.addWidget(title)
        self.details = QTextBrowser()
        self.details.setOpenExternalLinks(True)
        self.snapshot = capability_snapshot()
        self.details.setHtml(_status_html(self.snapshot))
        layout.addWidget(self.details)
        actions = QHBoxLayout()
        choose = QPushButton("Choose model folder…")
        choose.clicked.connect(self.choose_model_folder)
        actions.addWidget(choose)
        import_model = QPushButton("Import pinned model…")
        import_model.clicked.connect(self.import_model)
        actions.addWidget(import_model)
        shared = QPushButton("Use shared registry…")
        shared.clicked.connect(self.choose_registry)
        actions.addWidget(shared)
        actions.addStretch(1)
        layout.addLayout(actions)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    def _refresh(self):
        self.snapshot = capability_snapshot()
        self.details.setHtml(_status_html(self.snapshot))

    def choose_registry(self):
        registry, _ = QFileDialog.getOpenFileName(self, "Choose existing shared model registry", "", "JSON (*.json)")
        if not registry:
            return
        try:
            result = select_registered_model(registry)
            self._refresh()
            QMessageBox.information(self, "Existing model selected", f"Verified and selected {result['path']}. No copy or download.")
        except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
            QMessageBox.warning(self, "Shared model not selected", str(exc))

    def choose_model_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose Silero model folder")
        if not folder:
            return
        try:
            select_storage(folder)
            self._refresh()
        except (OSError, RuntimeError, ValueError) as exc:
            QMessageBox.warning(self, "Model storage not changed", str(exc))

    def import_model(self):
        source, _ = QFileDialog.getOpenFileName(self, "Import exact pinned Silero model", str(ROOT / "models"),
                                                "ONNX model (*.onnx)")
        if not source:
            return
        folder = QFileDialog.getExistingDirectory(self, "Choose destination model folder")
        if not folder:
            return
        try:
            import_pinned_model(source, folder)
            self._refresh()
            QMessageBox.information(self, "Model imported", "The pinned Silero VAD model was verified and installed.")
        except (OSError, RuntimeError, ValueError) as exc:
            QMessageBox.warning(self, "Model import failed", str(exc))
