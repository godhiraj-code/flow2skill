from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from flow2skill.exporter import render_test
from flow2skill.model import Action, FlowValidationError, Selector, Workflow
from flow2skill.recorder import RecorderManager, RecordingJob, codegen_command, compile_source
from flow2skill.replay import plan, replay, resolve_value


def workflow_with_risk() -> Workflow:
    return Workflow(
        name="Publish proof",
        intent="Publish an approved proof",
        start_url="https://example.test",
        actions=[
            Action("goto", Selector("page"), value="https://example.test"),
            Action(
                "click",
                Selector("role", role="button", name="Publish now"),
                risk="approval",
            ),
            Action("assert_visible", Selector("text", value="Published"), expected=True),
        ],
    )


def test_dry_run_surfaces_approval_gate_without_importing_browser_runtime() -> None:
    output = plan(workflow_with_risk())
    assert "APPROVAL GATE" in output
    assert "Publish now" in output


def test_live_replay_blocks_risky_action_before_browser_launch() -> None:
    with pytest.raises(FlowValidationError, match="Replay blocked"):
        replay(workflow_with_risk(), live=True, allow_side_effects=False)


def test_template_resolution_uses_environment_without_persisting_value(monkeypatch) -> None:
    monkeypatch.setenv("F2S_TOKEN", "runtime-only")
    assert resolve_value("https://example.test?token=${F2S_TOKEN}") == (
        "https://example.test?token=runtime-only"
    )
    monkeypatch.delenv("F2S_TOKEN")
    with pytest.raises(FlowValidationError, match="F2S_TOKEN"):
        resolve_value("${F2S_TOKEN}")


def test_compile_source_writes_canonical_bundle(tmp_path) -> None:
    result = compile_source(
        """def test_flow(page):
    page.goto("https://example.test")
    page.get_by_text("Ready").click()
    expect(page.get_by_text("Ready")).to_be_visible()
""",
        name="Ready flow",
        output_dir=tmp_path,
    )
    assert result["workflow"].slug == "ready-flow"
    assert {path.name for path in result["paths"].values()} == {
        "flow.json",
        "flow.yaml",
        "SKILL.md",
        "test_ready_flow.py",
        "README.md",
    }


def test_codegen_command_is_pinned_and_writes_only_to_raw_path(tmp_path) -> None:
    raw = tmp_path / ".capture.raw.py"
    command = codegen_command(url="https://example.test", raw_output=raw)
    joined = " ".join(str(part) for part in command)
    assert "playwright@1.61.0" in joined
    assert str(raw) in joined
    assert "--save-storage" not in joined
    assert "--block-service-workers" in joined
    assert "cmd.exe" not in joined.lower()


def test_codegen_command_keeps_shell_metacharacters_in_one_argument(tmp_path) -> None:
    url = "https://example.test/?next=a&whoami"
    command = codegen_command(url=url, raw_output=tmp_path / ".capture.raw.py")
    assert command[-1] == url
    with pytest.raises(FlowValidationError, match="channel"):
        codegen_command(
            url="https://example.test",
            raw_output=tmp_path / ".capture.raw.py",
            channel="chrome&whoami",
        )


def test_workflow_read_rejects_unknown_fields(tmp_path) -> None:
    manifest = tmp_path / "flow.json"
    manifest.write_text(
        '{"name":"x","intent":"y","start_url":"https://example.test",'
        '"actions":[{"kind":"goto","selector":{"engine":"page"},'
        '"value":"https://example.test"}],"unexpected":"no"}',
        encoding="utf-8",
    )
    with pytest.raises(FlowValidationError, match="Unknown workflow fields"):
        Workflow.read(manifest)


def test_cancelled_finished_recording_removes_raw_capture(tmp_path) -> None:
    class FinishedProcess:
        def poll(self) -> int:
            return 0

    raw = tmp_path / ".job.raw.py"
    log = tmp_path / ".job.log"
    raw.write_text("captured input", encoding="utf-8")
    log.write_text("recorder log", encoding="utf-8")
    job = RecordingJob(
        job_id="job",
        name="Cancelled",
        intent="test",
        success_criteria="test",
        success_text=None,
        output_dir=tmp_path,
        raw_path=raw,
        log_path=log,
        process=FinishedProcess(),  # type: ignore[arg-type]
    )

    assert job.cancel()["state"] == "cancelled"
    assert not raw.exists()
    assert not log.exists()


def test_manager_removes_stale_raw_captures_but_keeps_recent_files(tmp_path) -> None:
    workspace = tmp_path / "flow"
    workspace.mkdir()
    stale_raw = workspace / ".old.raw.py"
    stale_log = workspace / ".old.recorder.log"
    recent_raw = workspace / ".recent.raw.py"
    for path in (stale_raw, stale_log, recent_raw):
        path.write_text("ephemeral", encoding="utf-8")
    old = time.time() - 7200
    os.utime(stale_raw, (old, old))
    os.utime(stale_log, (old, old))

    RecorderManager(tmp_path)

    assert not stale_raw.exists()
    assert not stale_log.exists()
    assert recent_raw.exists()


def test_live_generated_proof_fails_nonzero_without_review_flag(tmp_path) -> None:
    generated = tmp_path / "test_review_gate.py"
    generated.write_text(render_test(workflow_with_risk()), encoding="utf-8")
    env = os.environ.copy()
    env["FLOW2SKILL_LIVE"] = "1"
    env.pop("FLOW2SKILL_ALLOW_SIDE_EFFECTS", None)
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    env["PYTHONPATH"] = ""
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(generated)],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode != 0
    assert "1 failed" in completed.stdout


def test_foreign_placeholder_is_not_resolved_from_host_environment(monkeypatch) -> None:
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "host-secret")
    assert resolve_value("${AWS_SECRET_ACCESS_KEY}") == "${AWS_SECRET_ACCESS_KEY}"
