"""Verify release artifact identity without importing or extracting package code."""

from __future__ import annotations

import argparse
import ast
import tarfile
import zipfile
from email.parser import BytesParser
from pathlib import Path


def verify_release(dist: Path, source: Path, tag: str | None = None) -> str:
    wheels = list(dist.glob("*.whl"))
    archives = list(dist.glob("*.tar.gz"))
    if len(wheels) != 1 or len(archives) != 1:
        raise ValueError("Expected exactly one wheel and one source archive")
    with zipfile.ZipFile(wheels[0]) as wheel:
        metadata = [name for name in wheel.namelist() if name.endswith(".dist-info/METADATA")]
        if len(metadata) != 1:
            raise ValueError("Wheel must contain exactly one package metadata record")
        wheel_metadata = BytesParser().parsebytes(wheel.read(metadata[0]))
    with tarfile.open(archives[0], "r:gz") as archive:
        metadata = [
            item
            for item in archive.getmembers()
            if len(Path(item.name).parts) == 2 and item.name.endswith("/PKG-INFO")
        ]
        if len(metadata) != 1 or not metadata[0].isfile():
            raise ValueError("Source archive must contain one root package metadata record")
        stream = archive.extractfile(metadata[0])
        if stream is None:
            raise ValueError("Cannot read source package metadata")
        with stream:
            source_metadata = BytesParser().parsebytes(stream.read())
    for record in (wheel_metadata, source_metadata):
        if record.get_all("Name") != ["flow2skill"] or len(record.get_all("Version", [])) != 1:
            raise ValueError("Artifacts must identify exactly one flow2skill name and version")
    artifact_version = wheel_metadata["Version"]
    if not artifact_version or source_metadata["Version"] != artifact_version:
        raise ValueError("Wheel and source archive versions disagree")
    tree = ast.parse(source.read_text(encoding="utf-8"))
    versions = [
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets
        )
    ]
    if versions != [artifact_version]:
        raise ValueError("Runtime version does not match release artifacts")
    if tag is not None and tag != f"v{artifact_version}":
        raise ValueError(f"Release tag must be v{artifact_version}, got {tag!r}")
    return artifact_version


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    parser.add_argument("--source", type=Path, default=Path("src/flow2skill/__init__.py"))
    parser.add_argument("--tag")
    args = parser.parse_args()
    try:
        verified = verify_release(args.dist, args.source, args.tag)
    except (ValueError, OSError, SyntaxError, tarfile.TarError, zipfile.BadZipFile) as exc:
        parser.exit(1, f"Release verification failed: {exc}\n")
    print(
        f"Verified flow2skill {verified}: wheel, source archive, runtime"
        + (", tag" if args.tag else "")
    )


if __name__ == "__main__":
    main()
