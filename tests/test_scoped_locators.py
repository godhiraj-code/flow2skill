from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from flow2skill.exporter import render_skill, render_test, selector_expression
from flow2skill.model import FlowValidationError, Workflow
from flow2skill.parser import parse_codegen

SOURCE = """def test_scoped(page):
    page.goto("https://example.test")
    page.get_by_role("dialog", name="Profile", exact=True).first.locator(".row").nth(1).get_by_role("button", name="Save", exact=True).click()
    expect(page.get_by_role("dialog", name="Profile").get_by_test_id("status")).to_be_visible()
"""


def test_scope_modifiers_and_round_trip_preserve_the_full_expression():
    workflow = parse_codegen(SOURCE, name="Scopes")
    loaded = Workflow.from_dict(workflow.to_dict())
    expression = selector_expression(loaded.actions[1].selector)
    assert expression == (
        "page.get_by_role('dialog', name='Profile', exact=True).first"
        ".locator('.row').nth(1).get_by_role('button', name='Save', exact=True)"
    )
    assert loaded.actions == workflow.actions
    assert "within" in render_skill(loaded)
    assert "[nth:1]" in render_skill(loaded)


def test_legacy_manifest_preserves_its_original_fingerprint_and_payload():
    payload = json.loads((Path(__file__).parent / "fixtures" / "legacy_flow_v1.json").read_text())
    workflow = Workflow.from_dict(payload)
    assert workflow.schema_version == "1.0"
    assert workflow.fingerprint() == payload["fingerprint"]
    assert json.loads(json.dumps(workflow.to_dict())) == payload
    assert "page.get_by_text('Ready')" in render_test(workflow)


def test_parent_values_are_protected_and_collected_for_preflight():
    source = """def test_parent_secret(page):
    page.goto("https://example.test")
    page.get_by_label("Search").fill("PRIVATE_PARENT_VALUE")
    page.get_by_role("dialog", name="Results: PRIVATE_PARENT_VALUE").get_by_role("button", name="Open").click()
    expect(page.get_by_role("dialog", name="Results: PRIVATE_PARENT_VALUE").get_by_test_id("status")).to_be_visible()
"""
    workflow = parse_codegen(source, name="Parent protection")
    assert workflow.variables == ["F2S_LABEL_SEARCH_1"]
    output = json.dumps(workflow.to_dict()) + render_skill(workflow) + render_test(workflow)
    assert "PRIVATE_PARENT_VALUE" not in output
    assert workflow.actions[2].selector.parent.name == "Results: ${F2S_LABEL_SEARCH_1}"
    payload = workflow.to_dict()
    payload["variables"] = []
    with pytest.raises(FlowValidationError, match="exactly match placeholders"):
        Workflow.from_dict(payload)


def test_parent_context_cannot_lose_its_approval_gate():
    workflow = parse_codegen(SOURCE.replace('name="Profile"', 'name="Delete account"'), name="Risk")
    assert workflow.actions[1].risk == "approval"
    payload = workflow.to_dict()
    payload["actions"][1]["risk"] = "review"
    with pytest.raises(FlowValidationError, match="Risk classification mismatch"):
        Workflow.from_dict(payload)


@pytest.mark.parametrize(
    "parent",
    [
        "invalid",
        {"engine": "page"},
        {"engine": "css", "value": ".row", "exact": True},
        {"engine": "css", "value": ".row", "unknown": 1},
    ],
)
def test_invalid_parent_manifest_is_rejected(parent):
    payload = parse_codegen(SOURCE, name="Invalid parents").to_dict()
    payload["actions"][1]["selector"]["parent"] = parent
    with pytest.raises(FlowValidationError):
        Workflow.from_dict(payload)


def test_scopes_cannot_be_smuggled_into_the_legacy_schema():
    payload = parse_codegen(SOURCE, name="Downgrade").to_dict()
    payload["schema_version"] = "1.0"
    with pytest.raises(FlowValidationError, match="require schema 1.1"):
        Workflow.from_dict(payload)


def test_parent_tampering_and_excessive_nesting_are_rejected():
    payload = parse_codegen(SOURCE, name="Tampering").to_dict()
    payload["actions"][1]["selector"]["parent"]["value"] = ".different-row"
    with pytest.raises(FlowValidationError, match="fingerprint"):
        Workflow.from_dict(payload)
    nested = {"engine": "css", "value": ".row"}
    for _ in range(34):
        nested = {"engine": "css", "value": ".row", "parent": copy.deepcopy(nested)}
    payload["actions"][1]["selector"] = nested
    with pytest.raises(FlowValidationError, match="depth exceeds"):
        Workflow.from_dict(payload)


@pytest.mark.parametrize(
    "target",
    [
        'page.frame_locator("iframe")',
        'page.locator(".row").filter(has_text="Ready")',
        "page.locator(dynamic)",
        "page.first",
    ],
)
def test_unmodeled_scope_operations_still_fail_closed(target):
    source = f"""def test_unsupported(page):
    page.goto("https://example.test")
    {target}.get_by_role("button", name="Save").click()
    expect(page.get_by_text("Ready")).to_be_visible()
"""
    with pytest.raises(FlowValidationError, match="cannot be replayed safely"):
        parse_codegen(source, name="Unsupported scope")
