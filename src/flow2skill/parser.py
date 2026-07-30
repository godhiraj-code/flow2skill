from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from .model import (
    ANY_PLACEHOLDER_SCAN_RE,
    PLACEHOLDER_SCAN_RE,
    Action,
    FlowValidationError,
    Selector,
    Workflow,
    classify_risk,
    redact_url,
    sanitize_fill_value,
)

_MISSING = object()

SELECTOR_METHODS = {
    "get_by_role": "role",
    "get_by_label": "label",
    "get_by_placeholder": "placeholder",
    "get_by_text": "text",
    "get_by_test_id": "test_id",
    "get_by_title": "title",
    "get_by_alt_text": "alt_text",
    "locator": "css",
}
ACTION_METHODS = {
    "goto",
    "click",
    "fill",
    "press",
    "select_option",
    "check",
    "uncheck",
    "hover",
}
ASSERT_METHODS = {
    "to_be_visible": "assert_visible",
    "to_contain_text": "assert_text",
    "to_have_text": "assert_exact_text",
    "to_have_url": "assert_url",
    "to_have_value": "assert_value",
}


def _literal(node: ast.AST | None, default: Any = None) -> Any:
    if node is None:
        return default
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError):
        return default


def _kw_node(call: ast.Call, name: str) -> ast.AST | None:
    for item in call.keywords:
        if item.arg == name:
            return item.value
    return None


def _valid_call_shape(call: ast.Call, *, positional: int, keywords: set[str] | None = None) -> bool:
    allowed = keywords or set()
    names = [item.arg for item in call.keywords]
    return (
        len(call.args) == positional
        and all(name is not None and name in allowed for name in names)
        and len(names) == len(set(names))
    )


def _root_is_page(node: ast.AST) -> bool:
    while isinstance(node, (ast.Call, ast.Attribute)):
        node = node.func if isinstance(node, ast.Call) else node.value
    return isinstance(node, ast.Name) and node.id == "page"


def _selector(node: ast.AST) -> Selector | None:
    if isinstance(node, ast.Name) and node.id == "page":
        return Selector("page")
    if isinstance(node, ast.Attribute) and node.attr == "first":
        base = _selector(node.value)
        if base:
            return Selector(**{**base.__dict__, "modifiers": (*base.modifiers, "first")})
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
        return None

    method = node.func.attr
    if method == "nth":
        if not _valid_call_shape(node, positional=1):
            return None
        base = _selector(node.func.value)
        index = _literal(node.args[0], _MISSING) if node.args else _MISSING
        if base and isinstance(index, int) and index >= 0:
            return Selector(**{**base.__dict__, "modifiers": (*base.modifiers, f"nth:{index}")})
        return None

    engine = SELECTOR_METHODS.get(method)
    if not (engine and isinstance(node.func.value, ast.Name) and node.func.value.id == "page"):
        return None
    allowed_keywords = (
        {"name", "exact"} if engine == "role" else ({"exact"} if engine != "css" else set())
    )
    if not _valid_call_shape(node, positional=1, keywords=allowed_keywords):
        return None
    exact_node = _kw_node(node, "exact")
    exact = _literal(exact_node, _MISSING) if exact_node is not None else None
    if exact is _MISSING or (exact is not None and not isinstance(exact, bool)):
        return None

    if engine == "role":
        role = _literal(node.args[0], _MISSING) if node.args else _MISSING
        if not isinstance(role, str) or not role:
            return None
        name_node = _kw_node(node, "name")
        name = _literal(name_node, _MISSING) if name_node is not None else None
        if name is _MISSING or (name is not None and not isinstance(name, str)):
            return None
        return Selector(engine="role", role=role, name=name, exact=exact)

    value = _literal(node.args[0], _MISSING) if node.args else _MISSING
    if not isinstance(value, str) or not value:
        return None
    return Selector(engine=engine, value=value, exact=exact)


def _call_arg(call: ast.Call, index: int = 0, default: Any = None) -> Any:
    return _literal(call.args[index], default) if len(call.args) > index else default


def _portable_calls(tree: ast.Module) -> list[ast.Call]:
    functions = [
        node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    if len(functions) != 1 or isinstance(functions[0], ast.AsyncFunctionDef):
        raise FlowValidationError("Recording must contain exactly one synchronous test function")
    test_function = functions[0]
    if not test_function.name.startswith("test"):
        raise FlowValidationError("Recorded function name must start with `test`")
    args = test_function.args
    if (
        test_function.decorator_list
        or len(args.args) != 1
        or args.args[0].arg != "page"
        or args.posonlyargs
        or args.kwonlyargs
        or args.vararg
        or args.kwarg
        or args.defaults
        or args.kw_defaults
    ):
        raise FlowValidationError(
            "Recorded test must be an undecorated function with exactly one `page` argument"
        )

    permitted_module_nodes = (ast.Import, ast.ImportFrom, ast.FunctionDef)
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue
        if not isinstance(node, permitted_module_nodes):
            raise FlowValidationError(
                f"Unsupported module-level statement at line {getattr(node, 'lineno', '?')}"
            )

    calls: list[ast.Call] = []
    for statement in test_function.body:
        if isinstance(statement, ast.Pass):
            continue
        if not (isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call)):
            raise FlowValidationError(
                "Control flow, assignments, context managers, and nested statements are not "
                f"portable (line {getattr(statement, 'lineno', '?')})"
            )
        calls.append(statement.value)
    return calls


def parse_codegen(
    source: str,
    *,
    name: str,
    intent: str = "Replay the demonstrated browser workflow reliably.",
    success_criteria: str = "The recorded assertions pass.",
    success_text: str | None = None,
    redact_all_inputs: bool = True,
) -> Workflow:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise FlowValidationError(
            f"Recording is not valid Python at line {exc.lineno}: {exc.msg}"
        ) from exc

    actions: list[Action] = []
    variables: list[str] = []
    start_url = ""
    value_index = 1
    protected_literals: dict[str, str] = {}

    def reject_captured_placeholder(value: str, line: int | None, context: str) -> None:
        if ANY_PLACEHOLDER_SCAN_RE.search(value):
            raise FlowValidationError(
                f"Line {line}: captured placeholder syntax is not allowed in {context}"
            )

    def protect_text(value: str) -> str:
        protected = value
        for literal, variable in sorted(
            protected_literals.items(), key=lambda item: len(item[0]), reverse=True
        ):
            if literal and literal in protected:
                if len(literal) < 3 and protected != literal:
                    raise FlowValidationError(
                        "A protected value is too short to substitute safely inside selector text"
                    )
                protected = protected.replace(literal, f"${{{variable}}}")
        return protected

    def protect_selector(selector: Selector, line: int | None) -> Selector:
        fields = {
            "value": selector.value,
            "role": selector.role,
            "name": selector.name,
        }
        for value in fields.values():
            if value is not None:
                reject_captured_placeholder(value, line, "selector")
        return Selector(
            engine=selector.engine,
            value=protect_text(selector.value) if selector.value is not None else None,
            role=protect_text(selector.role) if selector.role is not None else None,
            name=protect_text(selector.name) if selector.name is not None else None,
            exact=selector.exact,
            modifiers=selector.modifiers,
        )

    def add_placeholders(value: Any) -> None:
        if isinstance(value, str):
            for variable in PLACEHOLDER_SCAN_RE.findall(value):
                if variable not in variables:
                    variables.append(variable)

    def fail(method: str, line: int | None, reason: str) -> None:
        raise FlowValidationError(
            f"Line {line}: {reason}; `{method}(...)` cannot be replayed safely"
        )

    for call in _portable_calls(tree):
        if not isinstance(call.func, ast.Attribute):
            fail("call", getattr(call, "lineno", None), "non-Playwright call detected")
        method = call.func.attr
        line = getattr(call, "lineno", None)

        expect_call = call.func.value
        is_expect = (
            isinstance(expect_call, ast.Call)
            and isinstance(expect_call.func, ast.Name)
            and expect_call.func.id == "expect"
            and bool(expect_call.args)
        )
        if is_expect:
            if method not in ASSERT_METHODS:
                fail(method, line, "unsupported Playwright assertion")
            if not _valid_call_shape(expect_call, positional=1):
                fail(method, line, "unsupported expect(...) arguments")
            expected_arguments = 0 if method == "to_be_visible" else 1
            if not _valid_call_shape(call, positional=expected_arguments):
                fail(method, line, "unsupported assertion arguments")
            selector = _selector(expect_call.args[0])
            if not selector:
                fail(method, line, "assertion target is dynamic or not portable")
            selector = protect_selector(selector, line)
            kind = ASSERT_METHODS[method]
            expected = _call_arg(call, default=_MISSING)
            if kind == "assert_visible":
                expected = True
            elif expected is _MISSING:
                fail(method, line, "assertion value is dynamic or missing")
            if kind == "assert_url":
                if not isinstance(expected, str):
                    fail(method, line, "URL assertion must use a literal string")
                reject_captured_placeholder(expected, line, "URL assertion")
                expected = redact_url(protect_text(expected))
                add_placeholders(expected)
            elif kind in {"assert_text", "assert_exact_text", "assert_value"}:
                if not isinstance(expected, str):
                    fail(method, line, "text/value assertion must use a literal string")
                reject_captured_placeholder(expected, line, "assertion value")
                raw_expected = expected
                expected, variable = sanitize_fill_value(
                    selector,
                    expected,
                    value_index,
                    redact_all_inputs=redact_all_inputs,
                )
                value_index += 1
                if variable and variable not in variables:
                    variables.append(variable)
                if variable:
                    protected_literals[raw_expected] = variable
            actions.append(
                Action(kind=kind, selector=selector, expected=expected, source_line=line)
            )
            continue

        if method not in ACTION_METHODS:
            fail(method, line, "unsupported Playwright call")
        expected_arguments = 1 if method in {"goto", "fill", "press", "select_option"} else 0
        if not _valid_call_shape(call, positional=expected_arguments):
            fail(method, line, "unsupported action arguments")
        selector = _selector(call.func.value)
        if selector is None:
            fail(method, line, "action target is dynamic or not portable")
        selector = protect_selector(selector, line)

        if method == "goto":
            raw_url = _call_arg(call, default=_MISSING)
            if not isinstance(raw_url, str) or not raw_url:
                fail(method, line, "navigation URL is dynamic or missing")
            reject_captured_placeholder(raw_url, line, "navigation URL")
            safe_url = redact_url(protect_text(raw_url))
            if not start_url:
                start_url = safe_url
            add_placeholders(safe_url)
            actions.append(
                Action(
                    kind="goto",
                    selector=Selector("page"),
                    value=safe_url,
                    risk=classify_risk("goto", Selector("page"), safe_url),
                    source_line=line,
                )
            )
            continue

        value = _call_arg(call, default=_MISSING)
        if method in {"fill", "press", "select_option"} and value is _MISSING:
            fail(method, line, "action value is dynamic or missing")
        if value is _MISSING:
            value = None
        if isinstance(value, str):
            reject_captured_placeholder(value, line, "action value")
        if method in {"fill", "select_option"}:
            if not isinstance(value, str):
                fail(method, line, "only literal string values are portable")
            raw_value = value
            value, variable = sanitize_fill_value(
                selector,
                value,
                value_index,
                redact_all_inputs=redact_all_inputs,
            )
            value_index += 1
            if variable and variable not in variables:
                variables.append(variable)
            if variable:
                protected_literals[raw_value] = variable
        risk = classify_risk(method, selector, value)
        actions.append(
            Action(
                kind=method,
                selector=selector,
                value=value,
                risk=risk,
                source_line=line,
            )
        )

    if success_text:
        success_selector = protect_selector(Selector(engine="text", value=success_text), None)
        actions.append(
            Action(
                kind="assert_visible",
                selector=success_selector,
                expected=True,
                note="Success criterion supplied during compilation",
            )
        )
    if not actions:
        raise FlowValidationError("No supported Playwright actions were found in the recording")
    if not start_url:
        raise FlowValidationError("A literal page.goto() URL is required for portable replay")
    if not any(action.kind.startswith("assert_") for action in actions):
        raise FlowValidationError(
            "No executable assertion was captured; record an expect(...) assertion or supply "
            "--success-text"
        )

    workflow = Workflow(
        name=name,
        intent=intent.strip() or "Replay the demonstrated browser workflow reliably.",
        start_url=start_url,
        actions=actions,
        success_criteria=success_criteria.strip() or "The recorded assertions pass.",
        variables=variables,
    )
    workflow.validate()
    return workflow


def parse_codegen_file(path: str | Path, **kwargs: Any) -> Workflow:
    source = Path(path).read_text(encoding="utf-8")
    return parse_codegen(source, **kwargs)
