import json

import pytest

from local_audio_studio import model_store, preview
from local_audio_studio.contracts import Preset, Project, Track


def registered(tmp_path):
    model = model_store.ROOT / "models" / model_store.MODEL_NAME
    entry = {"id": model_store.MODEL_ID, "path": str(model), "bytes": model.stat().st_size,
             "sha256": model_store.MODEL_SHA256, "format": "onnx", "variant": "v6.2.1"}
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"schema": 1, "models": [entry]}))
    return registry, entry


def test_shared_resolver_reuses_exact_file_and_labels_metadata_only(tmp_path):
    registry, entry = registered(tmp_path)
    assert model_store.resolve_registered_model(registry)["hash_verified"] is False
    settings = tmp_path / "settings.json"
    selected = model_store.select_registered_model(registry, settings)
    assert selected["hash_verified"] is True
    assert selected["copied_bytes"] == 0
    assert model_store.model_path(settings_path=settings) == model_store.ROOT / "models" / model_store.MODEL_NAME
    assert json.loads(registry.read_text())["models"] == [entry]


@pytest.mark.parametrize("field,value", [("format", "gguf"), ("variant", "int8"),
    ("sha256", "0" * 64), ("id", "silero-vad/other/onnx/default"), ("bytes", 1)])
def test_shared_resolver_rejects_other_identity_or_variant(tmp_path, field, value):
    registry, entry = registered(tmp_path)
    entry[field] = value
    registry.write_text(json.dumps({"schema": 1, "models": [entry]}))
    with pytest.raises(ValueError):
        model_store.select_registered_model(registry, tmp_path / "settings.json")
    assert not (tmp_path / "settings.json").exists()


def test_shared_resolver_requires_unique_id_and_fresh_hash(tmp_path):
    registry, entry = registered(tmp_path)
    registry.write_text(json.dumps({"schema": 1, "models": [entry, entry]}))
    with pytest.raises(ValueError, match="exactly one"):
        model_store.resolve_registered_model(registry)
    altered = tmp_path / model_store.MODEL_NAME
    altered.write_bytes(b"x" * entry["bytes"])
    entry["path"] = str(altered)
    registry.write_text(json.dumps({"schema": 1, "models": [entry]}))
    assert model_store.resolve_registered_model(registry)["hash_verified"] is False
    with pytest.raises(ValueError, match="checksum"):
        model_store.select_registered_model(registry, tmp_path / "settings.json")


def test_bundled_default_and_explicit_selected_path(tmp_path):
    assert model_store.model_path() == model_store.ROOT / "models" / model_store.MODEL_NAME
    selected = tmp_path / "selected"
    selected.mkdir()
    source = model_store.ROOT / "models" / model_store.MODEL_NAME
    target = model_store.import_pinned_model(source, selected, tmp_path / "settings.json")
    assert target.read_bytes() == source.read_bytes()
    assert model_store.model_path(settings_path=tmp_path / "settings.json") == target
    config = json.loads((tmp_path / "settings.json").read_text())
    assert config == {"version": 1, "model_sha256": model_store.MODEL_SHA256,
                      "directory": str(selected.resolve())}


def test_model_import_rejects_bad_hash_and_preserves_existing_file(tmp_path):
    bad = tmp_path / "not-model.onnx"
    bad.write_bytes(b"user data")
    destination = tmp_path / "dest"
    destination.mkdir()
    with pytest.raises(ValueError, match="SHA-256"):
        model_store.import_pinned_model(bad, destination, tmp_path / "settings.json")
    target = destination / model_store.MODEL_NAME
    target.write_bytes(b"existing user data")
    pinned = model_store.ROOT / "models" / model_store.MODEL_NAME
    with pytest.raises(FileExistsError):
        model_store.import_pinned_model(pinned, destination, tmp_path / "settings.json")
    assert target.read_bytes() == b"existing user data"
    assert not (tmp_path / "settings.json").exists()


def test_selected_missing_or_corrupt_model_fails_without_bundled_fallback(tmp_path):
    selected = tmp_path / "selected"
    selected.mkdir()
    config = tmp_path / "settings.json"
    model_store.select_storage(selected, config)
    with pytest.raises(RuntimeError, match="missing"):
        model_store.model_path(settings_path=config)
    model = selected / model_store.MODEL_NAME
    model.write_bytes(b"wrong model")
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        model_store.model_path(settings_path=config)


def test_malformed_config_is_not_overwritten(tmp_path):
    config = tmp_path / "settings.json"
    config.write_text("user data", encoding="utf-8")
    selected = tmp_path / "selected"
    selected.mkdir()
    with pytest.raises(RuntimeError, match="Cannot read model storage"):
        model_store.select_storage(selected, config)
    assert config.read_text(encoding="utf-8") == "user data"


def test_preview_cache_key_uses_selected_model_path_and_hash(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    project = Project([Track("source.wav", role="speech")], Preset(leveler_enabled=True))
    sources = [{"path": "source.wav", "sha256": "a" * 64}]
    bundled_key, bundled = preview._cache_key(project, sources, 0, 480000)
    selected = tmp_path / "selected"
    selected.mkdir()
    model_store.import_pinned_model(model_store.ROOT / "models" / model_store.MODEL_NAME, selected)
    selected_key, configured = preview._cache_key(project, sources, 0, 480000)
    assert bundled_key != selected_key
    assert bundled["speech_activity"]["model_path"] != configured["speech_activity"]["model_path"]
    assert bundled["speech_activity"]["model_sha256"] == configured["speech_activity"]["model_sha256"] == model_store.MODEL_SHA256
