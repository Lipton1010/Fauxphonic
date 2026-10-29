"""Shared, offline Silero model storage for the GUI and worker."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))

MODEL_NAME = "silero_vad.onnx"
MODEL_SHA256 = "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3"
CONFIG_VERSION = 1
MODEL_ID = "silero-vad/v6.2.1/onnx/default"
MODEL_BYTES = 2327524


def resolve_registered_model(registry=None, verify=False):
    """Read the staged shared schema without modifying it or searching other stores."""
    if registry is None:
        path = configured_model_path()
        if not path.is_file() or path.stat().st_size != MODEL_BYTES:
            raise ValueError("Exact selected model is missing or size differs; no fallback or download")
        if verify and _digest(path) != MODEL_SHA256:
            raise ValueError("Selected model checksum mismatch")
        return {"id": MODEL_ID, "path": str(path), "hash_verified": verify,
                "format": "onnx", "variant": "v6.2.1", "sha256": MODEL_SHA256}
    data = json.loads(Path(registry).read_text(encoding="utf-8"))
    if data.get("schema") != 1:
        raise ValueError("Unsupported model registry schema")
    matches = [entry for entry in data["models"] if entry.get("id") == MODEL_ID]
    if len(matches) != 1:
        raise ValueError("Registry must contain exactly one exact pinned Silero artifact")
    entry = matches[0]
    path = Path(entry["path"])
    if (entry.get("sha256") != MODEL_SHA256 or entry.get("format") != "onnx"
            or entry.get("variant") != "v6.2.1" or path.name != MODEL_NAME):
        raise ValueError("Registry identity, format, version or checksum differs from the pinned artifact")
    if (entry.get("bytes") != MODEL_BYTES or not path.is_absolute() or not path.is_file()
            or path.stat().st_size != MODEL_BYTES):
        raise ValueError("Exact registered model is missing or size differs; no fallback or download")
    if verify and _digest(path) != MODEL_SHA256:
        raise ValueError("Registered model checksum mismatch")
    return {"id": MODEL_ID, "path": str(path), "hash_verified": verify,
            "format": "onnx", "variant": "v6.2.1", "sha256": MODEL_SHA256}


def select_registered_model(registry, settings_path=None):
    result = resolve_registered_model(registry, verify=True)
    select_storage(Path(result["path"]).parent, settings_path)
    return {**result, "selected": True, "copied_bytes": 0}


def _digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def config_path():
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CONFIG_HOME")
    if base:
        return Path(base) / "Fauxphonic" / "model-store.json"
    return Path.home() / ".config" / "Fauxphonic" / "model-store.json"


def _configuration(path=None):
    path = Path(path or config_path())
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(value, dict) or value.get("version") != CONFIG_VERSION
                or value.get("model_sha256") != MODEL_SHA256
                or not isinstance(value.get("directory"), str)):
            raise ValueError("unsupported or invalid model-store configuration")
        directory = Path(value["directory"])
        if not directory.is_absolute():
            raise ValueError("configured model directory must be absolute")
        return directory
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        raise RuntimeError(f"Cannot read model storage settings at {path}: {exc}") from exc


def model_path(root=ROOT, settings_path=None):
    path = configured_model_path(root, settings_path)
    if not path.is_file():
        raise RuntimeError(f"Pinned Silero VAD model is missing: {path}. Import the pinned model or repair the selected storage folder.")
    actual = _digest(path)
    if actual != MODEL_SHA256:
        raise RuntimeError(f"Pinned Silero VAD model checksum mismatch: {path}.")
    return path


def configured_model_path(root=ROOT, settings_path=None):
    directory = _configuration(settings_path)
    return (directory / MODEL_NAME) if directory is not None else Path(root) / "models" / MODEL_NAME


def select_storage(directory, settings_path=None):
    """Persist an explicit absolute folder; a bad chosen folder never falls back."""
    directory = Path(directory).expanduser()
    if not directory.is_absolute() or not directory.is_dir():
        raise ValueError("Choose an existing absolute folder for the pinned model.")
    path = Path(settings_path or config_path())
    # A malformed existing config may contain user-selected state. Preserve it
    # until the user repairs it rather than silently replacing it.
    _configuration(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps({"version": CONFIG_VERSION, "model_sha256": MODEL_SHA256,
                       "directory": str(directory.resolve())}, sort_keys=True, indent=2).encode("utf-8")
    fd, temp_name = tempfile.mkstemp(prefix=".model-store-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as target:
            target.write(data)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
    return directory / MODEL_NAME


def import_pinned_model(source, directory, settings_path=None):
    """Install only the exact pinned artifact, without replacing user data."""
    source, directory = Path(source), Path(directory).expanduser()
    if not source.is_file() or _digest(source) != MODEL_SHA256:
        raise ValueError("Selected file is not the exact pinned Silero VAD artifact (SHA-256 mismatch).")
    if not directory.is_absolute() or not directory.is_dir():
        raise ValueError("Choose an existing absolute folder for the pinned model.")
    target = directory / MODEL_NAME
    if target.exists():
        if target.is_file() and _digest(target) == MODEL_SHA256:
            select_storage(directory, settings_path)
            return target
        raise FileExistsError(f"Existing file will not be overwritten: {target}")
    fd, temp_name = tempfile.mkstemp(prefix=".silero-import-", suffix=".tmp", dir=directory)
    try:
        with source.open("rb") as src, os.fdopen(fd, "wb") as out:
            for block in iter(lambda: src.read(1024 * 1024), b""):
                out.write(block)
            out.flush()
            os.fsync(out.fileno())
        if _digest(temp_name) != MODEL_SHA256:
            raise RuntimeError("Imported model failed its post-copy SHA-256 check.")
        # os.rename fails if another process created target on Windows.
        os.rename(temp_name, target)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
    select_storage(directory, settings_path)
    return target
