from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from .exporter import describe_action
from .model import Action, FlowValidationError, Selector, Workflow


def resolve_value(value: Any) -> Any:
    if isinstance(value, str) and "${" in value:

        def replace(match: re.Match[str]) -> str:
            variable = match.group(1)
            resolved = os.getenv(variable)
            if resolved is None:
                raise FlowValidationError(f"Required environment variable is missing: {variable}")
            return resolved

        return re.sub(r"\$\{(F2S_[A-Z0-9_]+)\}", replace, value)
    return value


def locate(page: Any, selector: Selector) -> Any:
    if selector.engine == "page":
        target = page
    elif selector.engine == "role":
        kwargs = {}
        if selector.name is not None:
            kwargs["name"] = resolve_value(selector.name)
        if selector.exact is not None:
            kwargs["exact"] = selector.exact
        target = page.get_by_role(resolve_value(selector.role or ""), **kwargs)
    elif selector.engine == "label":
        target = page.get_by_label(resolve_value(selector.value or ""), exact=selector.exact)
    elif selector.engine == "placeholder":
        target = page.get_by_placeholder(resolve_value(selector.value or ""), exact=selector.exact)
    elif selector.engine == "text":
        target = page.get_by_text(resolve_value(selector.value or ""), exact=selector.exact)
    elif selector.engine == "test_id":
        target = page.get_by_test_id(resolve_value(selector.value or ""))
    elif selector.engine == "title":
        target = page.get_by_title(resolve_value(selector.value or ""), exact=selector.exact)
    elif selector.engine == "alt_text":
        target = page.get_by_alt_text(resolve_value(selector.value or ""), exact=selector.exact)
    elif selector.engine == "css":
        target = page.locator(resolve_value(selector.value or ""))
    else:
        raise FlowValidationError(f"Unsupported selector engine: {selector.engine}")
    for modifier in selector.modifiers:
        if modifier == "first":
            target = target.first
        elif modifier.startswith("nth:"):
            target = target.nth(int(modifier.split(":", 1)[1]))
        else:
            raise FlowValidationError(f"Unsupported selector modifier: {modifier}")
    return target


def execute_action(page: Any, action: Action) -> None:
    from playwright.sync_api import expect

    target = locate(page, action.selector)
    if action.kind == "goto":
        page.goto(resolve_value(action.value))
    elif action.kind in {"click", "check", "uncheck", "hover"}:
        getattr(target, action.kind)()
    elif action.kind in {"fill", "press", "select_option"}:
        getattr(target, action.kind)(resolve_value(action.value))
    elif action.kind == "assert_visible":
        expect(target).to_be_visible()
    elif action.kind == "assert_text":
        expect(target).to_contain_text(resolve_value(action.expected))
    elif action.kind == "assert_exact_text":
        expect(target).to_have_text(resolve_value(action.expected))
    elif action.kind == "assert_url":
        expect(page).to_have_url(resolve_value(action.expected))
    elif action.kind == "assert_value":
        expect(target).to_have_value(resolve_value(action.expected))
    else:
        raise FlowValidationError(f"Unsupported action: {action.kind}")


def plan(workflow: Workflow) -> str:
    lines = [f"Flow: {workflow.name}", f"Intent: {workflow.intent}", ""]
    for index, action in enumerate(workflow.actions, start=1):
        gate = (
            " [APPROVAL GATE]"
            if action.risk == "approval"
            else " [REVIEW]"
            if action.risk == "review"
            else ""
        )
        lines.append(f"{index:02d}. {describe_action(action)}{gate}")
    lines.extend(
        [
            "",
            f"Assertions: {sum(a.kind.startswith('assert_') for a in workflow.actions)}",
            f"Protected variables: {len(workflow.variables)}",
            f"Fingerprint: {workflow.fingerprint()}",
        ]
    )
    return "\n".join(lines)


def replay(
    workflow: Workflow,
    *,
    live: bool = False,
    headed: bool = False,
    allow_side_effects: bool = False,
    channel: str | None = None,
    evidence_dir: str | Path | None = None,
) -> str:
    workflow.validate()
    if not live:
        return plan(workflow)
    if not any(action.kind.startswith("assert_") for action in workflow.actions):
        raise FlowValidationError("Replay blocked: no executable assertion was captured")
    risky = [a for a in workflow.actions if a.risk != "safe"]
    if risky and not allow_side_effects:
        labels = "; ".join(describe_action(action) for action in risky)
        raise FlowValidationError(
            "Replay blocked: reviewed or approval-gated browser actions are present. "
            f"Review these steps first: {labels}"
        )

    evidence_root = Path(evidence_dir or ".flow2skill-evidence").resolve()
    evidence_root.mkdir(parents=True, exist_ok=True)
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        launch_options: dict[str, Any] = {"headless": not headed}
        if channel:
            launch_options["channel"] = channel
        browser = playwright.chromium.launch(**launch_options)
        context = browser.new_context()
        page = context.new_page()
        try:
            for action in workflow.actions:
                execute_action(page, action)
            screenshot = evidence_root / f"{workflow.slug}-passed.png"
            page.screenshot(path=str(screenshot), full_page=True)
            return f"PASS {workflow.name}\nEvidence: {screenshot}"
        except Exception:
            screenshot = evidence_root / f"{workflow.slug}-failed.png"
            page.screenshot(path=str(screenshot), full_page=True)
            raise
        finally:
            context.close()
            browser.close()
