"""New pytest payloads have one owned root; no startup cleanup of older runs."""
import json
from pathlib import Path
import re
import uuid

import pytest

from local_audio_studio import storage


@pytest.fixture
def tmp_path(request, tmp_path_factory):
    # Pytest's numbered directories create `current` junction aliases on Windows.
    # Unique owned paths need no aliases, keeping the cleanup tree link-free.
    name = re.sub(r"[^a-zA-Z0-9_-]", "_", request.node.name)[:40]
    return tmp_path_factory.mktemp(name + "-" + uuid.uuid4().hex, numbered=False)


def pytest_configure(config):
    if config.option.basetemp or config.option.collectonly:
        return  # Explicit external harness ownership remains with that harness.
    folder = storage.begin(Path(config.rootpath) / ".test-runs", "test")
    config._fauxphonic_payload = folder
    config.option.basetemp = str(folder / "payload")


def pytest_sessionfinish(session, exitstatus):
    folder = getattr(session.config, "_fauxphonic_payload", None)
    if folder is None:
        return
    # Failed runs stay active/unvalidated and are never cleanup candidates.
    report = folder.parent / (folder.name + ".result.json")
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    counts = {key: len(value) for key, value in reporter.stats.items()} if reporter else {}
    failures = [{"test": item.nodeid, "summary": str(item.longrepr)[-2000:]}
                for key in ("failed", "error") for item in (reporter.stats.get(key, []) if reporter else [])][:100]
    report.write_text(json.dumps({"exitstatus": int(exitstatus), "counts": counts,
                                 "failures": failures}, indent=2), encoding="utf-8")
    if exitstatus == 0:
        try:
            storage.complete(folder, [report])
        except (OSError, ValueError) as exc:
            if reporter:
                reporter.write_line(f"Storage payload preserved without cleanup eligibility: {exc}")
