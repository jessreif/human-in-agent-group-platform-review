"""INV-0006: release records are created with every gate not_run and seal immutably."""

from __future__ import annotations

import json
import os
from pathlib import Path

import new_release_record as releases
import pytest


@pytest.fixture
def release_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(releases, "RELEASES", tmp_path)
    return tmp_path


def _make_writable(path: Path) -> None:
    for item in [path, *path.rglob("*")]:
        os.chmod(item, 0o755 if item.is_dir() else 0o644)


def test_create_writes_complete_unrun_record(release_root: Path) -> None:
    target = releases.create("20260928")
    assert target.parent == release_root
    assert target.name.startswith("inv-20260928-") and len(target.name) == len("inv-20260928-") + 12
    for name in ("RELEASE.json", "SOURCE_MANIFEST.sha256", "INPUTS.sha256", "build.json", "gates.json", "reviews.md"):
        assert (target / name).is_file(), name
    gates = json.loads((target / "gates.json").read_text())
    assert set(gates) == set(releases.GATES)
    assert {g["status"] for g in gates.values()} == {"not_run"}
    release = json.loads((target / "RELEASE.json").read_text())
    assert release["source_sha256"].startswith(target.name.rsplit("-", 1)[1])
    with pytest.raises(SystemExit, match="immutable"):
        releases.create("20260928")


def test_seal_refuses_unsupported_pass(release_root: Path) -> None:
    target = releases.create("20260929")
    gates = json.loads((target / "gates.json").read_text())
    gates["G1"]["status"] = "pass"
    (target / "gates.json").write_text(json.dumps(gates))
    with pytest.raises(SystemExit, match="pass without evidence"):
        releases.seal(target)


def test_seal_writes_manifest_and_removes_write_permission(release_root: Path) -> None:
    target = releases.create("20260930")
    (target / "evidence" / "check.log").write_text("ok\n")
    gates = json.loads((target / "gates.json").read_text())
    gates["G1"] = {"status": "pass", "evidence": ["evidence/check.log"], "reviewers": ["tester"]}
    (target / "gates.json").write_text(json.dumps(gates))
    digest = releases.seal(target)
    try:
        assert len(digest) == 64
        seal = (target / "SEAL.sha256").read_text()
        assert "evidence/check.log" in seal and "gates.json" in seal
        assert not os.access(target / "gates.json", os.W_OK) or os.geteuid() == 0
    finally:
        _make_writable(target)
