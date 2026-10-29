"""Inventory licenses and native files present in a completed PyInstaller bundle."""
import argparse
import hashlib
import importlib.metadata as metadata
import json
from pathlib import Path
import shutil
import ssl
import sys

ROOT = Path(__file__).resolve().parents[1]
NATIVE_SUFFIXES = {".dll", ".exe", ".pyd", ".onnx"}
LICENSE_NAMES = ("license", "copying", "copyright", "notice")


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def package_for(relative):
    path = relative.as_posix().lower()
    name = relative.name.lower()
    if path.startswith("tools/") or path.startswith("_internal/tools/"):
        return {
            "name": "FFmpeg with static LAME and Opus",
            "license": "LGPL-2.1-or-later; LAME LGPL-2.0-or-later; Opus BSD-3-Clause",
            "source": "legal/sources/FFmpeg; legal/sources/libsndfile/opus-1.5.2.tar.gz",
        }
    if path.startswith("models/") or path.startswith("_internal/models/") or name.endswith(".onnx"):
        return {"name": "Silero VAD", "license": "MIT", "source": "licenses/silero-vad-LICENSE"}
    if path.startswith("_internal/pyside6/") or path.startswith("_internal/shiboken6/"):
        return {"name": "Qt for Python / Qt", "license": "LGPL-3.0-only", "source": "legal/sources/Qt"}
    if path.startswith("_internal/onnxruntime/"):
        return {"name": "ONNX Runtime", "license": "MIT", "source": "licenses/python/onnxruntime"}
    if path.startswith("_internal/numpy/"):
        return {"name": "NumPy", "license": "BSD-3-Clause and bundled notices", "source": "licenses/python/numpy"}
    if path.startswith("_internal/numpy.libs/"):
        if "openblas" in name:
            return {"name": "OpenBLAS (bundled with NumPy)", "license": "BSD-3-Clause", "source": "licenses/python/numpy"}
        if name.startswith("msvcp"):
            return {"name": "Microsoft Visual C++ Runtime", "license": "Microsoft Redistributable", "source": "Microsoft Visual C++ Redistributable"}
    if name.startswith("_cffi_backend"):
        return {"name": "CFFI", "license": "MIT", "source": "licenses/python/cffi"}
    if path.startswith("_internal/psutil/"):
        return {"name": "psutil", "license": "BSD-3-Clause", "source": "licenses/python/psutil"}
    if path.startswith("_internal/soundfile/") or "soundfile" in path or "sndfile" in name:
        return {"name": "SoundFile / libsndfile", "license": "MIT / LGPL-2.1-or-later", "source": "licenses/python/soundfile; legal/sources/libsndfile"}
    if name.startswith(("vcruntime", "msvcp")) or name == "concrt140.dll":
        return {"name": "Microsoft Visual C++ Runtime", "license": "Microsoft Redistributable", "source": "Microsoft Visual C++ Redistributable"}
    if name in {"libcrypto-3.dll", "libssl-3.dll"}:
        return {"name": "OpenSSL 3.0.16", "license": "Apache-2.0", "source": "licenses/OpenSSL-Apache-2.0.txt"}
    if name == "libffi-8.dll":
        return {"name": "libffi", "license": "MIT", "source": "licenses/python/cffi"}
    if name == "sqlite3.dll":
        return {"name": "SQLite", "license": "Public domain", "source": "https://sqlite.org/copyright.html"}
    if relative.parent.as_posix().lower() == "_internal" and name.endswith(".pyd"):
        return {"name": "Python standard library extension", "license": "PSF-2.0 and module-specific notices", "source": "licenses/Python-PSF-LICENSE.txt"}
    if name.startswith("python3") or path.startswith("_internal/python"):
        return {"name": "Python", "license": "PSF-2.0", "source": "licenses/Python-PSF-LICENSE.txt"}
    if relative.suffix.lower() == ".onnx":
        return {"name": "Silero VAD", "license": "MIT", "source": "models/silero-vad-LICENSE"}
    if relative.as_posix().lower() in {"fauxphonic.exe", "fauxphonic-worker.exe"}:
        return {"name": "Fauxphonic / PyInstaller bootloader", "license": "Project / GPL with PyInstaller exception", "source": "licenses/python/PyInstaller"}
    raise RuntimeError(f"Unattributed native payload file: {relative}")


def verify_sources(bundle, native):
    required = {
        "FFmpeg": bundle / "legal/sources/FFmpeg/ffmpeg-8.1.1.tar.xz",
        "LAME": bundle / "legal/sources/FFmpeg/lame-4.0.tar.gz",
        "Qt": bundle / "legal/sources/Qt/qtbase-everywhere-src-6.11.2.tar.xz",
        "Silero VAD": bundle / "licenses/silero-vad-LICENSE",
        "SoundFile / libsndfile": bundle / "legal/sources/libsndfile/libsndfile-1.2.2.tar.xz",
    }
    for file in native:
        owner = file["name"]
    for owner, path in required.items():
        if not path.is_file():
            raise RuntimeError(f"Matching license/source package missing for {owner}: {path}")
    for filename in ("Qt-LGPL-3.0.txt", "Qt-GPL-3.0.txt"):
        if not (bundle / "licenses" / filename).is_file():
            raise RuntimeError(f"Qt license text is missing: {filename}")
    sources = json.loads((ROOT / "tools/source_manifest.json").read_text(encoding="utf-8"))
    ffmpeg_archive = bundle / "legal/sources/FFmpeg" / sources["ffmpeg"]["source_archive"]["name"]
    if digest(ffmpeg_archive) != sources["ffmpeg"]["source_archive"]["sha256"]:
        raise RuntimeError("Bundled FFmpeg source does not match the pinned source hash.")
    lame = sources["ffmpeg"]["linked_libraries"]["lame"]
    lame_archive = bundle / "legal/sources/FFmpeg" / lame["source_archive"]["name"]
    if digest(lame_archive) != lame["source_archive"]["sha256"]:
        raise RuntimeError("Bundled LAME source does not match the pinned source hash.")
    for source_name, destination_name in (("COPYING", "LAME-COPYING"), ("LICENSE", "LAME-LICENSE")):
        path = bundle / "legal/sources/FFmpeg" / destination_name
        if not path.is_file() or digest(path) != lame["license_files_sha256"][source_name]:
            raise RuntimeError(f"Bundled LAME license text is missing or changed: {source_name}")
    for archive in sources["qt"]["archives"]:
        path = bundle / "legal/sources/Qt" / archive["name"]
        if not path.is_file() or digest(path) != archive["sha256"]:
            raise RuntimeError(f"Bundled Qt/PySide source is missing or changed: {archive['name']}")
    for archive in sources["libsndfile"]["archives"]:
        path = bundle / "legal/sources/libsndfile" / archive["name"]
        if not path.is_file() or digest(path) != archive["sha256"]:
            raise RuntimeError(f"Bundled libsndfile/codec source is missing or changed: {archive['name']}")
    if not (bundle / "licenses/OpenSSL-Apache-2.0.txt").is_file():
        raise RuntimeError("OpenSSL Apache-2.0 license text is missing.")
    if not (bundle / "licenses/MinGW-w64-runtime.txt").is_file():
        raise RuntimeError("MinGW-w64 runtime notice is missing.")


def license_files(distribution):
    for file in distribution.files or ():
        relative = Path(str(file))
        if any(token in part.lower() for token in LICENSE_NAMES for part in relative.parts):
            source = Path(distribution.locate_file(file))
            if source.is_file() and source.suffix.lower() not in {".py", ".pyc", ".dll", ".exe"}:
                yield source, str(file).replace("..", "_")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    args = parser.parse_args()
    bundle = args.bundle.resolve()
    internal = bundle / "_internal"
    if not bundle.is_dir() or not internal.is_dir():
        raise SystemExit(f"Expected a completed PyInstaller bundle: {bundle}")

    licenses = bundle / "licenses"
    licenses.mkdir(exist_ok=True)
    shutil.copyfile(Path(sys.base_prefix) / "LICENSE.txt", licenses / "Python-PSF-LICENSE.txt")

    included_metadata = list(internal.glob("*.dist-info/METADATA"))
    included = {}
    for path in included_metadata:
        distribution = metadata.PathDistribution(path.parent)
        name = distribution.metadata["Name"]
        key = name.lower().replace("_", "-")
        included[key] = distribution

    available = {dist.metadata["Name"].lower().replace("_", "-"): dist
                 for dist in metadata.distributions() if dist.metadata.get("Name")}
    package_inventory = []
    for key, bundled in sorted(included.items()):
        installed = available.get(key)
        if installed is None:
            raise RuntimeError(f"Cannot find installed license metadata for bundled package {bundled.metadata['Name']}.")
        copied = []
        for source, relative in license_files(installed):
            destination = licenses / "python" / bundled.metadata["Name"] / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            copied.append(destination.relative_to(bundle).as_posix())
        package_inventory.append({
            "name": bundled.metadata["Name"],
            "version": bundled.version,
            "license": bundled.metadata.get("License-Expression") or bundled.metadata.get("License"),
            "license_files": copied,
        })

    pyinstaller = metadata.distribution("PyInstaller")
    pyinstaller_license = licenses / "python/PyInstaller/COPYING.txt"
    pyinstaller_license.parent.mkdir(parents=True, exist_ok=True)
    source_license = next((source for source, _ in license_files(pyinstaller)), None)
    if source_license is None:
        raise RuntimeError("PyInstaller bootloader license text is missing.")
    shutil.copyfile(source_license, pyinstaller_license)

    native = []
    for path in sorted(bundle.rglob("*")):
        if path.is_file() and path.suffix.lower() in NATIVE_SUFFIXES:
            relative = path.relative_to(bundle)
            if relative.parts[:2] == ("legal", "sources"):
                continue
            native.append({"path": relative.as_posix(), "sha256": digest(path), **package_for(relative)})

    verify_sources(bundle, native)
    if any(item["name"] == "OpenSSL 3.0.16" for item in native) and not ssl.OPENSSL_VERSION.startswith("OpenSSL 3.0.16 "):
        raise RuntimeError(f"OpenSSL payload attribution expected 3.0.16, build runtime reports {ssl.OPENSSL_VERSION}.")

    inventory = {
        "python_distributions": package_inventory,
        "build_time_components": [{
            "name": "PyInstaller bootloader", "version": pyinstaller.version,
            "license": "GPL-2.0-or-later with bootloader exception",
            "license_files": [pyinstaller_license.relative_to(bundle).as_posix()],
        }, {
            "name": "MinGW-w64 runtime statically linked into FFmpeg and libsndfile",
            "version": "w64devkit 2.10.0 toolchain runtime",
            "license": "See included MinGW-w64 runtime notice",
            "license_files": ["licenses/MinGW-w64-runtime.txt"],
        }],
        "native_payload": native,
    }
    (bundle / "dependency-inventory.json").write_text(json.dumps(inventory, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
