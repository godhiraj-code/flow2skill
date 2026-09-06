from __future__ import annotations

import io
import runpy
import tarfile
import zipfile
from pathlib import Path

import pytest

verify_release = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "scripts" / "verify_release.py")
)["verify_release"]


def artifacts(tmp_path, wheel_version="1.2.3", source_version="1.2.3", name="flow2skill"):
    dist = tmp_path / "dist"
    dist.mkdir()
    with zipfile.ZipFile(dist / "flow2skill.whl", "w") as wheel:
        wheel.writestr("flow2skill.dist-info/METADATA", f"Name: {name}\nVersion: {wheel_version}\n")
    metadata = f"Name: {name}\nVersion: {source_version}\n".encode()
    with tarfile.open(dist / "flow2skill.tar.gz", "w:gz") as archive:
        entry = tarfile.TarInfo("flow2skill/PKG-INFO")
        entry.size = len(metadata)
        archive.addfile(entry, io.BytesIO(metadata))
    source = tmp_path / "__init__.py"
    source.write_text('__version__ = "1.2.3"\nraise RuntimeError("Must not import")\n')
    return dist, source


def test_matching_release_validates_without_executing_package(tmp_path):
    dist, source = artifacts(tmp_path)
    assert verify_release(dist, source, "v1.2.3") == "1.2.3"


@pytest.mark.parametrize("tag", ["v9.0.0", "", "1.2.3"])
def test_tag_mismatch_blocks_publication(tmp_path, tag):
    dist, source = artifacts(tmp_path)
    with pytest.raises(ValueError, match="Release tag"):
        verify_release(dist, source, tag)


def test_stale_source_archive_is_rejected(tmp_path):
    dist, source = artifacts(tmp_path, source_version="1.2.2")
    with pytest.raises(ValueError, match="versions disagree"):
        verify_release(dist, source)


def test_runtime_version_mismatch_is_rejected(tmp_path):
    dist, source = artifacts(tmp_path)
    source.write_text('__version__ = "1.2.2"')
    with pytest.raises(ValueError, match="Runtime version"):
        verify_release(dist, source)


def test_wrong_package_is_rejected(tmp_path):
    dist, source = artifacts(tmp_path, name="different-package")
    with pytest.raises(ValueError, match="flow2skill name"):
        verify_release(dist, source)


def test_extra_artifact_is_rejected(tmp_path):
    dist, source = artifacts(tmp_path)
    (dist / "stale.whl").touch()
    with pytest.raises(ValueError, match="exactly one wheel"):
        verify_release(dist, source)
