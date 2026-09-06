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
    page.get_by_role("tab", name="Compile codegen").click()
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


def test_page_and_element_targets_match_in_both_execution_paths(tmp_path):
    from flow2skill.exporter import write_bundle
    from flow2skill.parser import parse_codegen
    from flow2skill.replay import replay

    fixture = tmp_path / "targets.html"
    fixture.write_text('<!doctype html><h1 data-testid="result">Ready</h1>', encoding="utf-8")
    workflow = parse_codegen(
        f"""def test_targets(page):
    page.goto({fixture.as_uri()!r})
    expect(page.get_by_test_id("result")).to_be_visible()
    expect(page).to_have_url({fixture.as_uri()!r})
""",
        name="Receiver fidelity",
    )
    assert replay(workflow, live=True, evidence_dir=tmp_path / "evidence").startswith("PASS")
    paths = write_bundle(workflow, tmp_path / "bundle")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(paths["test"])],
        env={**os.environ, "FLOW2SKILL_LIVE": "1", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


def test_keyboard_capture_error_recovery_and_preview(studio):
    from playwright.sync_api import expect

    page, _ = studio
    record = page.get_by_role("tab", name="Record live")
    compile_tab = page.get_by_role("tab", name="Compile codegen")
    record.focus()
    record.press("ArrowRight")
    expect(compile_tab).to_be_focused()
    expect(compile_tab).to_have_attribute("aria-selected", "true")
    expect(record).to_have_attribute("tabindex", "-1")
    expect(page.get_by_role("tabpanel", name="Record live")).to_have_count(0)
    compile_tab.press("Home")
    expect(record).to_be_focused()
    record.press("End")
    compile_tab.press("Tab")
    panel = page.get_by_role("tabpanel", name="Compile codegen")
    expect(panel.get_by_label("Workflow name", exact=True)).to_be_focused()
    panel.get_by_label("Workflow name", exact=True).fill("Keyboard trial")
    panel.get_by_label("Playwright Python codegen", exact=True).fill("def broken(:")
    compile_button = panel.get_by_role("button", name="Compile artifacts", exact=True)
    compile_button.focus()
    compile_button.press("Enter")
    alert = page.get_by_role("alert")
    expect(alert).to_contain_text("not valid Python")
    # Error feedback must remain available after the former toast timeout.
    page.wait_for_timeout(4700)
    expect(alert).to_be_visible()
    expect(compile_button).to_be_enabled()
    demo = panel.get_by_role("button", name="Generate safe demo")
    demo.focus()
    demo.press("Enter")
    expect(page.locator("#result")).to_be_visible()
    expect(page.locator("#error-feedback")).not_to_be_visible()
    expect(page.get_by_role("status")).to_contain_text("Safe demo bundle generated")
    preview = page.get_by_role("button", name="Preview SKILL.md", exact=True)
    preview.focus()
    preview.press("Enter")
    dialog = page.get_by_role("dialog", name="SKILL.md", exact=True)
    expect(dialog).to_be_visible()
    expect(dialog.get_by_role("button", name="Close", exact=True)).to_be_focused()
    page.keyboard.press("Tab")
    expect(dialog.get_by_label("Artifact contents")).to_be_focused()
    page.keyboard.press("Escape")
    expect(dialog).not_to_be_visible()
    expect(preview).to_be_focused()

    page.route(
        "**/api/artifact?**",
        lambda route: route.fulfill(status=503, json={"error": "Workspace unavailable"}),
    )
    recent = page.locator("#recent").get_by_role("button").first
    recent.focus()
    recent.press("Enter")
    expect(alert).to_have_text("Workspace unavailable")
    dismiss = page.get_by_role("button", name="Dismiss error", exact=True)
    dismiss.focus()
    dismiss.press("Enter")
    expect(page.locator("#error-feedback")).not_to_be_visible()
    expect(compile_tab).to_be_focused()


def test_placeholder_collision_replays_in_browser_and_standalone(tmp_path, monkeypatch):
    from flow2skill.exporter import write_bundle
    from flow2skill.model import Workflow
    from flow2skill.parser import parse_codegen
    from flow2skill.replay import replay

    fixture = tmp_path / "echo.html"
    fixture.write_text(
        """<!doctype html><label>First<input></label><label>Second<input id="second"></label>
<button onclick="document.querySelector('output').textContent='Result: '+document.querySelector('#second').value">Show result</button>
<output></output>""",
        encoding="utf-8",
    )
    workflow = parse_codegen(
        f"""def test_echo(page):
    page.goto({fixture.as_uri()!r})
    page.get_by_label("First").fill("LABEL")
    page.get_by_label("Second").fill("a-long-secret")
    page.get_by_role("button", name="Show result").click()
    expect(page.get_by_text("Result: a-long-secret", exact=True)).to_be_visible()
""",
        name="Protected placeholder browser proof",
    )
    monkeypatch.setenv("F2S_LABEL_FIRST_1", "different input")
    monkeypatch.setenv("F2S_LABEL_SECOND_2", "runtime result")
    workflow = Workflow.from_dict(workflow.to_dict())
    assert replay(
        workflow, live=True, allow_side_effects=True, evidence_dir=tmp_path / "evidence"
    ).startswith("PASS")
    paths = write_bundle(workflow, tmp_path / "bundle")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-o", "addopts=", "-q", str(paths["test"])],
        env={
            **os.environ,
            "FLOW2SKILL_LIVE": "1",
            "FLOW2SKILL_ALLOW_SIDE_EFFECTS": "1",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        },
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


def test_scoped_recording_preserves_repeated_dialogs_and_buttons(tmp_path, monkeypatch):
    from flow2skill.exporter import write_bundle
    from flow2skill.model import Workflow
    from flow2skill.parser import parse_codegen
    from flow2skill.replay import replay

    fixture = tmp_path / "scoped.html"
    fixture.write_text(
        """<!doctype html><label>Search<input></label>
<section role="dialog" aria-label="Profile">
  <div class="row"><button onclick="this.closest('section').querySelector('output').textContent='wrong row'">Save</button></div>
  <div class="row"><button onclick="this.closest('section').querySelector('output').textContent='chosen row'">Save</button></div>
  <output data-testid="status">Waiting</output>
</section>
<section role="dialog" aria-label="Profile">
  <div class="row"><button>Save</button></div><div class="row"><button>Save</button></div>
  <output data-testid="status">Wrong dialog</output>
</section>""",
        encoding="utf-8",
    )
    workflow = parse_codegen(
        f"""def test_scoped(page):
    page.goto({fixture.as_uri()!r})
    page.get_by_label("Search").fill("Profile")
    page.get_by_role("dialog", name="Profile", exact=True).first.locator(".row").nth(1).get_by_role("button", name="Save", exact=True).click()
    expect(page.get_by_role("dialog", name="Profile", exact=True).first.get_by_test_id("status")).to_have_text("chosen row")
""",
        name="Scoped browser proof",
    )
    assert len(workflow.variables) == 2
    monkeypatch.setenv(workflow.actions[1].value[2:-1], "Profile")
    monkeypatch.setenv(workflow.actions[-1].expected[2:-1], "chosen row")
    # Load the manifest before replay so this also exercises parent decoding.
    workflow = Workflow.from_dict(workflow.to_dict())
    assert replay(
        workflow, live=True, allow_side_effects=True, evidence_dir=tmp_path / "evidence"
    ).startswith("PASS")
    paths = write_bundle(workflow, tmp_path / "bundle")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(paths["test"])],
        env={
            **os.environ,
            "FLOW2SKILL_LIVE": "1",
            "FLOW2SKILL_ALLOW_SIDE_EFFECTS": "1",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        },
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout
