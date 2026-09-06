from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from flow2skill import exporter, storage
from flow2skill.model import FlowValidationError, Workflow
from flow2skill.parser import parse_codegen


def workflow(name="Original"):
    return parse_codegen(
        """def test_flow(page):
    page.goto("https://example.test")
    expect(page.get_by_text("Ready")).to_be_visible()
""",
        name=name,
    )


def snapshot(root):
    return {path.name: path.read_bytes() for path in root.iterdir() if path.is_file()}


@pytest.mark.parametrize("failure", ["render", "stage", "replace", "obsolete"])
def test_failed_export_restores_the_complete_previous_bundle(tmp_path, monkeypatch, failure):
    exporter.write_bundle(workflow(), tmp_path)
    (tmp_path / "test_handwritten.py").write_text("# Preserve user work\n")
    before = snapshot(tmp_path)
    if failure == "render":

        def fail_render(*args):
            raise OSError("render failed")

        monkeypatch.setattr(exporter, "render_readme", fail_render)
    elif failure == "stage":
        original = Path.write_text

        def fail_stage(path, *args, **kwargs):
            if path.name == "SKILL.md":
                raise OSError("stage failed")
            return original(path, *args, **kwargs)

        monkeypatch.setattr(Path, "write_text", fail_stage)
    elif failure == "replace":
        original = os.replace

        def fail_replace(source, target):
            if Path(source).parent.name == "new" and Path(target).name == "README.md":
                raise OSError("replace failed")
            return original(source, target)

        monkeypatch.setattr(storage.os, "replace", fail_replace)
    else:
        original = Path.unlink

        def fail_obsolete(path, *args, **kwargs):
            if path == tmp_path / "test_original.py":
                raise OSError("obsolete failed")
            return original(path, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", fail_obsolete)
    with pytest.raises(OSError, match=failure):
        exporter.write_bundle(workflow("Replacement"), tmp_path)
    assert snapshot(tmp_path) == before
    assert not list(tmp_path.glob(".flow2skill-*"))
    assert Workflow.read(tmp_path / "flow.json").name == "Original"


def test_failed_first_export_leaves_no_partial_manifest(tmp_path, monkeypatch):
    original = os.replace

    def fail(source, target):
        if Path(target).name == "README.md":
            raise OSError("disk unavailable")
        return original(source, target)

    monkeypatch.setattr(storage.os, "replace", fail)
    with pytest.raises(OSError, match="disk unavailable"):
        exporter.write_bundle(workflow(), tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_rollback_failure_retains_recovery_files(tmp_path, monkeypatch):
    exporter.write_bundle(workflow(), tmp_path)
    before = (tmp_path / "flow.yaml").read_bytes()
    original = os.replace

    def fail(source, target):
        if Path(target).name == "README.md" or Path(source).parent.name == "old":
            raise OSError("filesystem unavailable")
        return original(source, target)

    monkeypatch.setattr(storage.os, "replace", fail)
    with pytest.raises(FlowValidationError, match="rollback was incomplete"):
        exporter.write_bundle(workflow("Replacement"), tmp_path)
    recovery = list(tmp_path.glob(".flow2skill-stage-*"))
    assert len(recovery) == 1
    assert (recovery[0] / "old" / "flow.yaml").read_bytes() == before
    assert (tmp_path / ".flow2skill-write.lock").is_file()
    with pytest.raises(FlowValidationError, match="Another export"):
        exporter.write_bundle(workflow("Retry"), tmp_path)


def test_separate_process_cannot_replace_an_owned_bundle(tmp_path):
    exporter.write_bundle(workflow(), tmp_path)
    before = snapshot(tmp_path)
    code = """import sys
from pathlib import Path
from flow2skill.storage import bundle_write_lock
with bundle_write_lock(Path(sys.argv[1])):
    print("locked", flush=True)
    sys.stdin.readline()
"""
    process = subprocess.Popen(
        [sys.executable, "-c", code, str(tmp_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout.readline().strip() == "locked"
        with pytest.raises(FlowValidationError, match="Another export"):
            exporter.write_bundle(workflow("Concurrent"), tmp_path)
    finally:
        process.communicate("release\n", timeout=10)
    assert process.returncode == 0
    assert snapshot(tmp_path) == before
    exporter.write_bundle(workflow("Replacement"), tmp_path)
    assert Workflow.read(tmp_path / "flow.json").name == "Replacement"
