from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from flow2skill.exporter import render_test
from flow2skill.model import Action, Selector, Workflow
from flow2skill.replay import replay


@pytest.mark.parametrize("entrypoint", ["cli", "exported"])
@pytest.mark.parametrize("failure_stage", ["context", "page", "action", "cleanup"])
def test_original_failure_survives_evidence_and_cleanup_errors(
    monkeypatch, tmp_path, entrypoint, failure_stage
):
    workflow = Workflow(
        name="Failure proof",
        intent="Verify a page",
        start_url="https://example.test",
        actions=[
            Action("goto", Selector("page"), value="https://example.test"),
            Action("assert_visible", Selector("text", value="Ready"), expected=True),
        ],
    )
    original = RuntimeError(f"Original {failure_stage} failure")
    runtime = MagicMock()
    browser = runtime.chromium.launch.return_value
    context = browser.new_context.return_value
    page = context.new_page.return_value
    manager = MagicMock()
    manager.__enter__.return_value = runtime
    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: manager)
    monkeypatch.setattr("playwright.sync_api.expect", MagicMock())
    monkeypatch.setenv("FLOW2SKILL_LIVE", "1")

    browser.close.side_effect = RuntimeError("Secondary browser cleanup failure")
    context.close.side_effect = RuntimeError("Secondary context cleanup failure")
    if failure_stage == "context":
        browser.new_context.side_effect = original
    elif failure_stage == "page":
        context.new_page.side_effect = original
    elif failure_stage == "action":
        page.goto.side_effect = original
        page.screenshot.side_effect = RuntimeError("Secondary evidence capture failure")
    else:
        context.close.side_effect = original

    with pytest.raises(RuntimeError) as caught:
        if entrypoint == "cli":
            replay(workflow, live=True, evidence_dir=tmp_path)
        else:
            namespace = {}
            exec(compile(render_test(workflow), "generated.py", "exec"), namespace)
            namespace["test_failure_proof"]()
    assert caught.value is original
    browser.new_context.assert_called_once_with(service_workers="block")
    browser.close.assert_called_once()
    if failure_stage != "context":
        context.close.assert_called_once()
    if entrypoint == "cli" and failure_stage == "action":
        page.screenshot.assert_called_once()
