"""Cleanup for process-owned temporary files left by crashed runs."""
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

import psutil


_KEY = re.compile(r"^[0-9a-f]{32}$")


def sweep_render_scratch(root):
    """Legacy crash folders lack a completion allowlist; preserve their recovery data."""
    return


def analysis_scratch_root():
    base = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir())
    return base / "Fauxphonic" / "analysis"


def sweep_analysis_scratch(root):
    _sweep_owned_folders(root, lambda name: name.startswith("fauxphonic-analysis-"))


def _sweep_owned_folders(root, matches):
    root = Path(root)
    if not root.is_dir():
        return
    for folder in root.iterdir():
        if not matches(folder.name):
            continue
        try:
            if (folder.is_symlink() or getattr(folder, "is_junction", lambda: False)()
                    or folder.resolve().parent != root.resolve()):
                continue
        except OSError:
            continue
        marker = folder / "owner.pid"
        try:
            pid = int(marker.read_text(encoding="ascii"))
            if not psutil.pid_exists(pid):
                shutil.rmtree(folder)
        except (OSError, ValueError):
            continue


def sweep_preview_files(data_dir):
    data_dir = Path(data_dir)
    _sweep_owned_folders(data_dir / "preview-work", lambda name: name.startswith("fauxphonic-preview-"))
    _sweep_owned_folders(data_dir / "previews", lambda name: name.startswith("preview-active-"))
    _sweep_owned_folders(data_dir / "preview-cache", lambda name: name.startswith(".") and ".building-" in name)
    _sweep_owned_folders(data_dir / "preview-cache",
        lambda name: re.fullmatch(r"\.[0-9a-f]{64}\.corrupt-[0-9a-f]{32}", name) is not None)


def sweep_requests(data_dir):
    """Remove only UUID-named requests whose recorded worker has exited."""
    data_dir = Path(data_dir)
    for request in data_dir.glob("*.request.json"):
        key = request.name[:-len(".request.json")]
        if not _KEY.fullmatch(key) or request.is_symlink():
            continue
        marker = data_dir / f"{key}.worker.pid"
        try:
            if marker.is_symlink():
                continue
            pid = int(marker.read_text(encoding="ascii"))
            if psutil.pid_exists(pid):
                continue
            payload = json.loads(request.read_text(encoding="utf-8"))
            if payload.get("action") == "render" and payload.get("output"):
                sweep_render_scratch(Path(payload["output"]).parent)
            cancel = Path(payload.get("cancel_file", ""))
            if (cancel.parent.resolve() == data_dir.resolve() and cancel.name == f"{key}.cancel"
                    and not cancel.is_symlink()):
                cancel.unlink(missing_ok=True)
            request.unlink(missing_ok=True)
            marker.unlink(missing_ok=True)
        except (OSError, ValueError, TypeError):
            continue
