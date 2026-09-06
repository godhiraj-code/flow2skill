"""Opt-in real-browser Studio tests; run against the installed package."""

from __future__ import annotations

import os
import queue
import re
import subprocess
import sys
import threading
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("FLOW2SKILL_BROWSER_TESTS") != "1", reason="Opt-in Chromium integration tests"
)


@pytest.fixture
def studio(tmp_path):
    from playwright.sync_api import sync_playwright

    root = tmp_path / "workspaces"
    process = subprocess.Popen(
        [
            sys.executable,
            "-u",
            "-m",
            "flow2skill",
            "studio",
            "--port",
            "0",
            "--no-open",
            "--workspace-root",
            str(root),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    lines = queue.Queue()
    threading.Thread(target=lambda: lines.put(process.stdout.readline()), daemon=True).start()
    try:
        line = lines.get(timeout=20)
        assert line.startswith("Flow2Skill Studio: "), line
        url = line.partition(": ")[2].strip()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page()
                page.goto(url)
                yield page, root
            finally:
                browser.close()
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        process.stdout.close()


def test_studio_demo_exports_preview_and_executable_proof(studio):
    from playwright.sync_api import expect

    page, root = studio
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.get_by_role("button", name="Compile codegen").click()
    page.locator("#demo-btn").click()
    expect(page.locator("#result")).to_be_visible()
    expect(page.locator("#m-assertions")).to_have_text("1")
    page.locator("#artifact-list button").first.click()
    expect(page.locator("#preview-dialog")).to_be_visible()
    expect(page.locator("#preview")).not_to_be_empty()
    page.locator("#close-preview").click()
    proof = next(root.glob("*/test_*.py"))
    for artifact in proof.parent.iterdir():
        assert "synthetic-demo-value-7Q9X" not in artifact.read_text()
    readme = (proof.parent / "README.md").read_text()
    shell = "cmd" if os.name == "nt" else "bash"
    instructions = re.findall(rf"```{shell}\n(.*?)```", readme, re.DOTALL)[-1]
    instructions = instructions.replace("<your value>", "runtime-demo-token")
    invocation = (
        ["cmd", "/d", "/c", instructions] if os.name == "nt" else ["bash", "-c", instructions]
    )
    # Run the exported instructions, not a parallel hand-written command.
    result = subprocess.run(
        invocation,
        cwd=proof.parent,
        env={
            **os.environ,
            "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"],
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        },
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not errors


def test_recorder_status_failure_keeps_cancel_and_retries(studio):
    from playwright.sync_api import expect

    page, _ = studio
    pending = []
    polls = []
    cancelled = []
    page.route("**/api/record/start", lambda route: pending.append(route))

    def status(route):
        polls.append(route)
        if len(polls) == 1:
            route.fulfill(status=503, json={"error": "Temporary status outage"})
        else:
            route.fulfill(json={"job_id": "test-job", "state": "recording"})

    page.route("**/api/record/test-job", status)
    page.route(
        "**/api/record/test-job/cancel",
        lambda route: (cancelled.append(True), route.fulfill(json={"state": "cancelled"})),
    )
    page.locator("#record-btn").click()
    button = page.locator("#record-btn")
    expect(button).to_be_disabled()
    button.evaluate("element => element.click()")
    assert len(pending) == 1
    pending[0].fulfill(status=202, json={"job_id": "test-job", "state": "recording"})
    expect(page.locator("#recording b")).to_contain_text("Status unavailable")
    expect(button).to_be_enabled()
    expect(button).to_have_text("Cancel recording")
    expect(page.locator("#recording b")).to_have_text("Recorder is open.", timeout=8000)
    assert len(polls) >= 2
    button.click()
    expect(button).to_have_text("Open browser recorder")
    expect(page.locator("#recording")).not_to_be_visible()
    assert cancelled == [True]


def test_real_codegen_capture_compiles_and_replays(tmp_path):
    """Exercise the actual pinned Node recorder, not fabricated Python input.

    Playwright's headless test hook closes the recorder after initial navigation.
    This covers process/capture compatibility, not interactive input recording.
    """
    fixture = tmp_path / "fixture.html"
    fixture.write_text("<!doctype html><h1>Recorder smoke ready</h1>", encoding="utf-8")
    output = tmp_path / "recorded"
    environment = {
        **os.environ,
        "PWTEST_CLI_HEADLESS": "1",
        "PWTEST_CLI_IS_UNDER_TEST": "1",
        "PWTEST_CLI_EXIT_AFTER_TIMEOUT": "10000",
        "npm_config_cache": str(tmp_path / "npm-cache"),
    }
    recording = subprocess.run(
        [
            sys.executable,
            "-m",
            "flow2skill",
            "record",
            fixture.as_uri(),
            "--name",
            "Actual codegen",
            "--success-text",
            "Recorder smoke ready",
            "--out",
            str(output),
        ],
        env=environment,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert recording.returncode == 0, recording.stdout + recording.stderr
    assert not list(output.rglob(".*.raw.py"))
    assert not list(output.rglob(".*.recorder.log"))
    proof = output / "actual-codegen" / "test_actual_codegen.py"
    assert proof.is_file()
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(proof)],
        env={**os.environ, "FLOW2SKILL_LIVE": "1", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr
