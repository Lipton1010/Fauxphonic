"""Explicit, receipt-based cleanup of new owned payloads. No historical purge."""
from contextlib import closing, contextmanager
import json
import os
from pathlib import Path
import stat
import sqlite3
import time
import uuid

import psutil

MARKER = ".storage-owner.json"
KINDS = {"test": ".test-runs", "build": ".build-runs", "render": None}


def safe_path(path):
    """Reject reparse points on every existing ancestor, before resolving."""
    path = Path(os.path.abspath(path))
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if (getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
                or stat.S_ISLNK(info.st_mode)):
            raise ValueError(f"Reparse path is not eligible: {part}")
    return path


def _write(path, value):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as target:
            json.dump(value, target, indent=2)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _files(folder):
    result = []
    def fail(error):
        raise error
    for base, directories, files in os.walk(folder, followlinks=False, onerror=fail):
        for name in directories:
            safe_path(Path(base) / name)
        for name in files:
            path = safe_path(Path(base) / name)
            if path == folder / MARKER:
                continue
            info = path.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError(f"Nonregular or shared file: {path}")
            result.append({"path": path.relative_to(folder).as_posix(), "bytes": info.st_size,
                           "mtime_ns": info.st_mtime_ns, "device": info.st_dev, "inode": info.st_ino})
    return sorted(result, key=lambda entry: entry["path"])


def begin(root, kind):
    root = safe_path(root)
    if kind not in KINDS or (KINDS[kind] and root.name != KINDS[kind]):
        raise ValueError("Unknown owned payload root or kind")
    root.mkdir(parents=True, exist_ok=True)
    key = uuid.uuid4().hex
    folder = root / (".fauxphonic-" + key if kind == "render" else key)
    folder.mkdir()
    _write(folder / MARKER, {"schema": 1, "app": "Fauxphonic", "kind": kind, "id": key,
           "root": str(root), "directory": str(folder), "pid": os.getpid(),
           "process_created": psutil.Process().create_time(), "state": "active", "created": time.time()})
    return folder


def complete(folder, results, queue_job=None):
    """Caller has validated success; retain compact final records outside payload."""
    folder = safe_path(folder)
    owner = json.loads((folder / MARKER).read_text(encoding="utf-8"))
    final = []
    for result in results:
        path = safe_path(result)
        if path.is_relative_to(folder) or not path.is_file():
            raise ValueError("Completion evidence must remain outside the disposable payload")
        info = path.stat()
        final.append({"path": str(path), "bytes": info.st_size, "mtime_ns": info.st_mtime_ns})
    if not final:
        raise ValueError("Validated completion requires retained evidence")
    owner.update(state="completed_validated", completed=time.time(), files=_files(folder), results=final)
    if queue_job:
        owner["queue_job"] = queue_job
    _write(folder / MARKER, owner)


@contextmanager
def render_scratch(root):
    folder = begin(root, "render")
    # Failed, cancelled and crashed jobs retain their payload and active receipt.
    try:
        yield folder
    except BaseException:
        try:
            owner = json.loads((folder / MARKER).read_text(encoding="utf-8"))
            owner["state"] = "failed_or_cancelled"
            _write(folder / MARKER, owner)
        except OSError:
            pass  # A full disk must not mask the actual processing failure.
        raise


def _eligible(folder):
    folder = safe_path(folder)
    marker = safe_path(folder / MARKER)
    owner = json.loads(marker.read_text(encoding="utf-8"))
    kind, key = owner["kind"], owner["id"]
    if (owner.get("schema") != 1 or owner.get("app") != "Fauxphonic" or kind not in KINDS
            or len(key) != 32 or any(c not in "0123456789abcdef" for c in key)
            or folder.name != (".fauxphonic-" + key if kind == "render" else key)
            or owner["root"] != str(folder.parent) or owner["directory"] != str(folder)
            or (KINDS[kind] and folder.parent.name != KINDS[kind])
            or owner.get("state") != "completed_validated" or owner.get("pinned")):
        raise ValueError("Unvalidated, pinned or unowned payload")
    from .model_store import configured_model_path
    if configured_model_path().resolve().is_relative_to(folder):
        raise ValueError("Payload contains the selected model storage; preserve it")
    try:
        if psutil.Process(owner["pid"]).create_time() == owner["process_created"]:
            raise ValueError("Owner is still active")
    except psutil.NoSuchProcess:
        pass
    except psutil.Error as exc:
        raise ValueError(f"Cannot establish whether the owner is idle: {exc}") from exc
    if not owner.get("results"):
        raise ValueError("Missing retained completion evidence")
    if owner.get("queue_job"):
        job = owner["queue_job"]
        database = safe_path(job["database"])
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=0.1)) as db:
            row = db.execute("SELECT state, request FROM jobs WHERE id=?", (job["id"],)).fetchone()
        if (not row or row[0] != "complete"
                or str(Path(json.loads(row[1])["output"]).resolve()) != owner["results"][0]["path"]):
            raise ValueError("Queue completion has not been committed")
    for result in owner["results"]:
        path = safe_path(result["path"])
        info = path.stat()
        if path.is_relative_to(folder) or (info.st_size, info.st_mtime_ns) != (result["bytes"], result["mtime_ns"]):
            raise ValueError("Retained completion evidence changed or missing")
    if _files(folder) != owner["files"]:
        raise ValueError("Payload changed or contains unexpected files")
    return owner


def preview(root, now=None):
    """Read only one selected root; unmarked historical folders remain untouched."""
    root = safe_path(root)
    now = time.time() if now is None else now
    candidates, preserved = [], []
    if root.is_dir():
        for folder in root.iterdir():
            if not folder.is_dir() or folder.name == ".quarantine":
                continue
            try:
                owner = _eligible(folder)
                candidates.append({"directory": str(folder), "kind": owner["kind"],
                                   "completed": owner["completed"], "files": owner["files"]})
            except (OSError, ValueError, KeyError, TypeError, RuntimeError, psutil.Error, sqlite3.Error) as exc:
                preserved.append({"directory": str(folder), "reason": str(exc)})
    candidates.sort(key=lambda entry: entry["completed"], reverse=True)
    eligible = []
    counts = {}
    for entry in candidates:
        kind = entry["kind"]
        counts[kind] = counts.get(kind, 0) + 1
        keep = 3 if kind == "test" else 2 if kind == "build" else 0
        if kind != "render" and counts[kind] <= keep and now - entry["completed"] < 14 * 86400:
            preserved.append({"directory": entry["directory"], "reason": "Recent successful payload retained"})
        else:
            eligible.append(entry)
    return {"schema": 1, "root": str(root), "operation": "preview_only", "candidates": eligible,
            "bytes": sum(f["bytes"] for entry in eligible for f in entry["files"]), "preserved": preserved}


def quarantine(manifest, confirmed=False):
    """Move reviewed payloads on the same volume; never unlink their contents."""
    if not confirmed:
        raise ValueError("Explicit confirmation is required")
    root = safe_path(manifest["root"])
    if manifest.get("schema") != 1 or manifest.get("operation") != "preview_only":
        raise ValueError("Unsupported preview manifest")
    entries = manifest["candidates"]
    for entry in entries:
        folder = safe_path(entry["directory"])
        if folder.parent != root or _eligible(folder)["files"] != entry["files"]:
            raise ValueError("Stale or out-of-root preview")
    destination = safe_path(root / ".quarantine")
    destination.mkdir(exist_ok=True)
    moved = []
    for entry in entries:
        folder = safe_path(entry["directory"])
        # Revalidate immediately before each move; prior moves remain recoverable on failure.
        if _eligible(folder)["files"] != entry["files"]:
            raise ValueError("Stale preview")
        target = destination / (folder.name + "-" + uuid.uuid4().hex)
        receipt = target.with_suffix(".restore.json")
        _write(receipt, {"original": str(folder), "quarantined": str(target), "state": "prepared"})
        os.rename(folder, target)
        moved.append({"original": str(folder), "quarantined": str(target), "receipt": str(receipt)})
    return {"moved": moved, "reclaimed_bytes": 0,
            "note": "Recoverable quarantine; restore and preview again before separately confirmed permanent deletion."}


def restore(receipt):
    receipt = safe_path(receipt)
    value = json.loads(receipt.read_text(encoding="utf-8"))
    source, target = safe_path(value["quarantined"]), safe_path(value["original"])
    if (receipt.parent != source.parent or receipt != source.with_suffix(".restore.json")
            or source.parent.name != ".quarantine" or target.parent != source.parent.parent or target.exists()):
        raise ValueError("Invalid restore destination or original path already exists")
    owner = json.loads(safe_path(source / MARKER).read_text(encoding="utf-8"))
    if owner["directory"] != str(target) or _files(source) != owner["files"]:
        raise ValueError("Quarantined payload changed")
    os.rename(source, target)
    return {"restored": str(target)}


def delete_reviewed(manifest, confirmed=False):
    """Explicitly delete only revalidated new intermediates, never whole trees."""
    if not confirmed:
        raise ValueError("Explicit permanent-deletion confirmation is required")
    root = safe_path(manifest["root"])
    if manifest.get("schema") != 1 or manifest.get("operation") != "preview_only":
        raise ValueError("Unsupported preview manifest")
    entries = manifest["candidates"]
    for entry in entries:
        folder = safe_path(entry["directory"])
        if folder.parent != root or _eligible(folder)["files"] != entry["files"]:
            raise ValueError("Stale or out-of-root preview")
    deleted, logical_bytes = [], 0
    for entry in entries:
        folder = safe_path(entry["directory"])
        owner = _eligible(folder)
        if owner["files"] != entry["files"]:
            raise ValueError("Stale preview")
        owner["state"] = "cleanup_in_progress"
        _write(folder / MARKER, owner)
        for item in entry["files"]:
            path = safe_path(folder / item["path"])
            info = path.stat()
            if (not path.is_relative_to(folder) or info.st_nlink != 1
                    or (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
                    != (item["device"], item["inode"], item["bytes"], item["mtime_ns"])):
                raise ValueError("File changed during cleanup; remaining files preserved")
            path.unlink()
            deleted.append(str(path))
            logical_bytes += item["bytes"]
        owner.update(state="cleaned", cleaned=time.time(), deleted_file_count=len(entry["files"]))
        _write(folder / MARKER, owner)
    return {"deleted": deleted, "logical_bytes_removed": logical_bytes,
            "note": "Retained completion receipts, results and empty folders; physical free space was not measured."}
