"""Reject receiver semantics that replay would otherwise change or fail to execute."""

from __future__ import annotations

from dataclasses import replace

import pytest

from flow2skill.exporter import render_test
from flow2skill.model import FlowValidationError, Selector, Workflow
from flow2skill.parser import parse_codegen

SOURCE = """def test_targets(page):
    page.goto("https://example.test")
    expect(page.get_by_test_id("result")).to_be_visible()
    expect(page).to_have_url("https://example.test")
"""


@pytest.mark.parametrize(
    "statement",
    [
        'page.get_by_text("PRIVATE_CAPTURE").goto("https://example.test")',
        'expect(page.get_by_text("PRIVATE_CAPTURE")).to_have_url("https://example.test")',
        'page.get_by_test_id("PRIVATE_CAPTURE", exact=True).click()',
        'page.locator("PRIVATE_CAPTURE", exact=False).hover()',
        'page.press("PRIVATE_CAPTURE")',
        "page.click()",
        "expect(page).to_be_visible()",
        'page.first.goto("https://example.test")',
        'page.nth(0).goto("https://example.test")',
        'page.get_by_text("PRIVATE_CAPTURE").nth(True).hover()',
    ],
)
def test_invalid_recorded_receivers_fail_at_the_source_line(statement):
    source = SOURCE.replace(
        '    expect(page).to_have_url("https://example.test")', f"    {statement}"
    )
    with pytest.raises(FlowValidationError) as caught:
        parse_codegen(source, name="Receiver fidelity")
    assert "Line 4:" in str(caught.value)
    assert "cannot be replayed safely" in str(caught.value)
    assert "PRIVATE_CAPTURE" not in str(caught.value)


@pytest.mark.parametrize(
    ("index", "selector"),
    [
        (0, Selector("text", value="wrong navigation target")),
        (1, Selector("page")),
        (2, Selector("text", value="wrong URL target")),
        (0, Selector("page", modifiers=("first",))),
        (1, Selector("test_id", value="result", exact=True)),
        (1, Selector("css", value="#result", exact=False)),
    ],
)
def test_invalid_targets_cannot_enter_through_manifests_or_exports(index, selector):
    workflow = parse_codegen(SOURCE, name="Manifest fidelity")
    payload = workflow.to_dict()
    workflow.actions[index] = replace(workflow.actions[index], selector=selector)
    # Recomputed integrity hashes must not make unsupported semantics valid.
    payload["actions"][index]["selector"] = selector.__dict__
    payload["fingerprint"] = workflow.fingerprint()
    with pytest.raises(FlowValidationError):
        Workflow.from_dict(payload)
    with pytest.raises(FlowValidationError):
        render_test(workflow)


def test_valid_page_assertion_and_test_id_round_trip():
    workflow = parse_codegen(SOURCE, name="Valid targets")
    loaded = Workflow.from_dict(workflow.to_dict())
    assert loaded.actions == workflow.actions
    generated = render_test(loaded)
    assert "expect(page.get_by_test_id('result')).to_be_visible()" in generated
    assert "expect(page).to_have_url('https://example.test')" in generated
