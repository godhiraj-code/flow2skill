"""Recoverable bundle replacement; a process crash still requires manual recovery."""

from __future__ import annotations

import os
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path

from .model import FlowValidationError


class BundleRecoveryError(FlowValidationError):
    """Backups and the workspace lock must remain available for recovery."""


@contextmanager
def bundle_write_lock(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    lock = root / ".flow2skill-write.lock"
    try:
        handle = lock.open("x", encoding="utf-8")
    except FileExistsError as exc:
        raise FlowValidationError(
            "Another export owns this workspace, or an interrupted export needs recovery. "
            "Check .flow2skill-write.lock before retrying; see README bundle recovery."
        ) from exc
    preserve_lock = False
    try:
        with handle:
            handle.write(f"pid={os.getpid()}\n")
        yield
    except BundleRecoveryError:
        preserve_lock = True
        raise
    finally:
        if not preserve_lock:
            lock.unlink()


def replace_bundle(root: Path, contents: dict[str, str], obsolete: str | None) -> None:
    """Stage all bytes, then replace files with backups for exception rollback.

    The caller holds bundle_write_lock. This is not an atomic directory snapshot:
    readers should wait for the export to finish before opening its artifacts.
    """
    staging = Path(tempfile.mkdtemp(prefix=".flow2skill-stage-", dir=root))
    preserve = False
    changed: list[str] = []
    backups: dict[str, Path] = {}
    names = [*contents]
    if obsolete and obsolete not in contents:
        names.append(obsolete)
    try:
        (staging / "new").mkdir()
        (staging / "old").mkdir()
        for name, content in contents.items():
            (staging / "new" / name).write_text(content, encoding="utf-8")
        for name in names:
            target = root / name
            if target.exists() or target.is_symlink():
                backup = staging / "old" / name
                shutil.copy2(target, backup, follow_symlinks=False)
                backups[name] = backup
        try:
            for name in names:
                target = root / name
                if name in contents:
                    os.replace(staging / "new" / name, target)
                elif target.exists() or target.is_symlink():
                    target.unlink()
                changed.append(name)
        except BaseException as export_error:
            rollback_errors = []
            for name in reversed(changed):
                try:
                    if name in backups:
                        os.replace(backups[name], root / name)
                    else:
                        (root / name).unlink(missing_ok=True)
                except OSError as exc:
                    rollback_errors.append(exc)
            if rollback_errors:
                preserve = True
                raise BundleRecoveryError(
                    f"Export failed and rollback was incomplete. Recovery files retained in "
                    f"{staging}. Stop using this bundle until it is recovered."
                ) from export_error
            raise
    finally:
        if not preserve:
            shutil.rmtree(staging)
