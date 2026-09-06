from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from contextlib import suppress

import pytest

from flow2skill.recorder import RecorderManager


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group behavior")
def test_cancel_stops_descendants_after_wrapper_exits(monkeypatch, tmp_path):
    heartbeat = tmp_path / "heartbeat"
    child = (
        "import signal,time,pathlib; "
        "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
        f"p=pathlib.Path({str(heartbeat)!r}); "
        "exec('while True:\\n p.write_text(str(time.time_ns()))\\n time.sleep(.02)')"
    )
    wrapper = (
        "import subprocess,sys,time; "
        f"subprocess.Popen([sys.executable,'-c',{child!r}]); time.sleep(60)"
    )
    monkeypatch.setattr(
        "flow2skill.recorder.codegen_command", lambda **_: [sys.executable, "-c", wrapper]
    )
    manager = RecorderManager(tmp_path / "workspaces")
    job = manager.start(
        name="Cancellation",
        url="https://example.test",
        intent="Test cancellation",
        success_criteria="No surviving writers",
        success_text=None,
    )
    try:
        deadline = time.monotonic() + 10
        while not heartbeat.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert heartbeat.exists(), "Child process did not start"
        assert os.getpgid(job.process.pid) == job.process.pid
        job.raw_path.write_text("synthetic raw capture")
        # Simulate npm exiting while its browser remains running.
        job.process.terminate()
        job.process.wait(timeout=5)
        assert job.cancel()["state"] == "cancelled"
        assert not job.raw_path.exists()
        assert not job.log_path.exists()
        time.sleep(0.05)
        observed = heartbeat.read_text()
        time.sleep(0.15)
        assert heartbeat.read_text() == observed, "Descendant kept executing after cancellation"
        assert job.cancel()["state"] == "cancelled"
    finally:
        with suppress(ProcessLookupError):
            os.killpg(job.process.pid, signal.SIGKILL)
        job.process.wait(timeout=5)


def test_cancel_does_not_relabel_a_completed_job(monkeypatch, tmp_path):
    from flow2skill.recorder import RecordingJob

    process = subprocess.CompletedProcess([], 0)
    job = RecordingJob(
        job_id="done",
        name="Done",
        intent="Done",
        success_criteria="Done",
        success_text=None,
        output_dir=tmp_path,
        raw_path=tmp_path / "raw",
        log_path=tmp_path / "log",
        process=process,
        state="complete",
        _finalized=True,
    )
    assert job.cancel() == {"job_id": "done", "state": "complete"}


def test_failed_windows_tree_stop_does_not_claim_cancellation(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from flow2skill.model import FlowValidationError
    from flow2skill.recorder import RecordingJob

    raw, log = tmp_path / "raw", tmp_path / "log"
    raw.write_text("synthetic capture")
    log.write_text("synthetic log")
    process = MagicMock(pid=123)
    process.poll.return_value = None
    job = RecordingJob(
        job_id="running",
        name="Running",
        intent="Test",
        success_criteria="Test",
        success_text=None,
        output_dir=tmp_path,
        raw_path=raw,
        log_path=log,
        process=process,
    )
    monkeypatch.setattr("flow2skill.recorder.os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(
        "flow2skill.recorder.subprocess.run", lambda *a, **k: subprocess.CompletedProcess([], 1)
    )
    with pytest.raises(FlowValidationError, match="cancellation is unconfirmed"):
        job.cancel()
    assert job.state == "recording"
    assert not job._finalized
    assert raw.exists() and log.exists()
