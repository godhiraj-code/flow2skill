from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

SCHEMA_VERSION = "1.0"
PLACEHOLDER_RE = re.compile(r"^\$\{(F2S_[A-Z0-9_]+)\}$")
PLACEHOLDER_SCAN_RE = re.compile(r"\$\{(F2S_[A-Z0-9_]+)\}")
ANY_PLACEHOLDER_SCAN_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
SENSITIVE_SELECTOR_RE = re.compile(
    r"password|passcode|pin|otp|one.?time|token|secret|api.?key|credit|card|cvv|cvc|"
    r"account|phone|mobile|e.?mail|username|login|address|ssn|aadhaar|pan.?card",
    re.IGNORECASE,
)
SECRET_QUERY_RE = re.compile(
    r"token|secret|key|password|passcode|code|auth|signature|session|jwt|bearer",
    re.IGNORECASE,
)
RISKY_ACTION_RE = re.compile(
    r"publish|post|send|submit|buy|purchase|checkout|pay|delete|remove|destroy|"
    r"confirm|approve|reject|cancel subscription|place order|book now|apply now",
    re.IGNORECASE,
)


class FlowValidationError(ValueError):
    """Raised when a workflow cannot be represented or executed safely."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:64] or "recorded-flow"


def url_variable(prefix: str, key: str) -> str:
    cleaned = re.sub(r"[^A-Z0-9]+", "_", key.upper()).strip("_") or "SECRET"
    return f"F2S_URL_{prefix}{cleaned[:24]}"


def redact_url(url: str) -> str:
    """Redact URL credentials and secret-bearing query/fragment values."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    netloc = parts.netloc
    if "@" in netloc:
        netloc = "${F2S_URL_USERINFO}@" + netloc.rsplit("@", 1)[1]
    safe_query = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        safe_query.append(
            (key, f"${{{url_variable('', key)}}}" if SECRET_QUERY_RE.search(key) else value)
        )
    query = urlencode(safe_query, quote_via=quote, safe="${}")
    fragment = parts.fragment
    if fragment:
        fragment_pairs = parse_qsl(fragment, keep_blank_values=True)
        if fragment_pairs and any(SECRET_QUERY_RE.search(key) for key, _ in fragment_pairs):
            fragment = urlencode(
                [
                    (
                        key,
                        f"${{{url_variable('FRAGMENT_', key)}}}"
                        if SECRET_QUERY_RE.search(key)
                        else value,
                    )
                    for key, value in fragment_pairs
                ],
                quote_via=quote,
                safe="${}",
            )
        elif SECRET_QUERY_RE.search(fragment):
            fragment = "${F2S_URL_FRAGMENT}"
    return urlunsplit((parts.scheme, netloc, parts.path, query, fragment))


def placeholder_name(selector_text: str, index: int) -> str:
    cleaned = re.sub(r"[^A-Z0-9]+", "_", selector_text.upper()).strip("_")
    cleaned = re.sub(r"^(ENTER|YOUR|THE)_", "", cleaned)
    if not cleaned or cleaned in {"INPUT", "FIELD", "TEXT"}:
        cleaned = "SENSITIVE_VALUE"
    return f"F2S_{cleaned[:32]}_{index}"


@dataclass(frozen=True)
class Selector:
    engine: str
    value: str | None = None
    role: str | None = None
    name: str | None = None
    exact: bool | None = None
    modifiers: tuple[str, ...] = ()

    def label(self) -> str:
        if self.engine == "page":
            return "page"
        if self.engine == "role":
            suffix = f" named {self.name!r}" if self.name else ""
            return f"{self.role or 'element'}{suffix}"
        return f"{self.engine} {self.value!r}"

    def searchable_text(self) -> str:
        return " ".join(part for part in (self.engine, self.value, self.role, self.name) if part)


@dataclass(frozen=True)
class Action:
    kind: str
    selector: Selector = field(default_factory=lambda: Selector("page"))
    value: Any = None
    expected: Any = None
    risk: str = "safe"
    source_line: int | None = None
    note: str | None = None


@dataclass
class Workflow:
    name: str
    intent: str
    start_url: str
    actions: list[Action]
    success_criteria: str = "The recorded assertions pass."
    variables: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now)
    schema_version: str = SCHEMA_VERSION
    source: str = "playwright-codegen"

    @property
    def slug(self) -> str:
        return slugify(self.name)

    def validate(self) -> None:
        for field_name, value in (
            ("name", self.name),
            ("intent", self.intent),
            ("start_url", self.start_url),
            ("success_criteria", self.success_criteria),
            ("created_at", self.created_at),
            ("schema_version", self.schema_version),
            ("source", self.source),
        ):
            if not isinstance(value, str):
                raise FlowValidationError(f"Workflow {field_name} must be a string")
        if self.schema_version != SCHEMA_VERSION:
            raise FlowValidationError(f"Unsupported schema version: {self.schema_version}")
        if self.source != "playwright-codegen":
            raise FlowValidationError("Workflow source must be `playwright-codegen`")
        if not self.name.strip():
            raise FlowValidationError("Workflow name is required")
        if not self.actions:
            raise FlowValidationError("Workflow must contain at least one action")
        if self.start_url and not re.match(r"^(https?|file)://", self.start_url, re.IGNORECASE):
            raise FlowValidationError("start_url must use http://, https://, or file://")
        allowed = {
            "goto",
            "click",
            "fill",
            "press",
            "select_option",
            "check",
            "uncheck",
            "hover",
            "assert_visible",
            "assert_text",
            "assert_exact_text",
            "assert_url",
            "assert_value",
        }
        selector_engines = {
            "page",
            "role",
            "label",
            "placeholder",
            "text",
            "test_id",
            "title",
            "alt_text",
            "css",
        }
        placeholders: set[str] = set()
        for action in self.actions:
            if not isinstance(action, Action):
                raise FlowValidationError("Workflow actions must contain Action objects")
            if not isinstance(action.kind, str):
                raise FlowValidationError("Action kind must be a string")
            if not isinstance(action.selector, Selector):
                raise FlowValidationError("Action selector must be a Selector")
            if action.kind not in allowed:
                raise FlowValidationError(f"Unsupported action kind: {action.kind}")
            if not isinstance(action.risk, str) or action.risk not in {
                "safe",
                "review",
                "approval",
            }:
                raise FlowValidationError(f"Invalid risk classification: {action.risk}")
            if not isinstance(action.selector.engine, str):
                raise FlowValidationError("Selector engine must be a string")
            if action.selector.engine not in selector_engines:
                raise FlowValidationError(f"Unsupported selector engine: {action.selector.engine}")
            for field_name, value in (
                ("value", action.selector.value),
                ("role", action.selector.role),
                ("name", action.selector.name),
            ):
                if value is not None and not isinstance(value, str):
                    raise FlowValidationError(f"Selector {field_name} must be a string or null")
            if action.selector.exact is not None and not isinstance(action.selector.exact, bool):
                raise FlowValidationError("Selector exact must be a boolean or null")
            if not isinstance(action.selector.modifiers, tuple) or not all(
                isinstance(item, str) for item in action.selector.modifiers
            ):
                raise FlowValidationError("Selector modifiers must be a tuple of strings")
            for modifier in action.selector.modifiers:
                if modifier != "first" and not re.fullmatch(r"nth:[0-9]+", modifier):
                    raise FlowValidationError(f"Unsupported selector modifier: {modifier}")
            expected_risk = classify_risk(action.kind, action.selector, action.value)
            if action.risk != expected_risk:
                raise FlowValidationError(
                    f"Risk classification mismatch for {action.kind}: "
                    f"expected {expected_risk}, got {action.risk}"
                )
            if action.kind == "goto" and (
                not isinstance(action.value, str)
                or not re.match(r"^(https?|file)://", action.value, re.IGNORECASE)
            ):
                raise FlowValidationError("goto actions require a literal http(s) or file URL")
            if action.source_line is not None and not isinstance(action.source_line, int):
                raise FlowValidationError("Action source_line must be an integer or null")
            if action.note is not None and not isinstance(action.note, str):
                raise FlowValidationError("Action note must be a string or null")
            for value in (
                action.value,
                action.expected,
                action.selector.value,
                action.selector.name,
            ):
                if isinstance(value, str):
                    foreign = [
                        name
                        for name in ANY_PLACEHOLDER_SCAN_RE.findall(value)
                        if not re.fullmatch(r"F2S_[A-Z0-9_]+", name)
                    ]
                    if foreign:
                        raise FlowValidationError(
                            "Only compiler-owned F2S_* placeholders are allowed"
                        )
                    placeholders.update(PLACEHOLDER_SCAN_RE.findall(value))
        if not all(isinstance(item, str) for item in self.variables):
            raise FlowValidationError("Workflow variables must be strings")
        if not all(re.fullmatch(r"F2S_[A-Z0-9_]+", item) for item in self.variables):
            raise FlowValidationError("Workflow variables must use the F2S_* namespace")
        if len(set(self.variables)) != len(self.variables):
            raise FlowValidationError("Workflow variables must not contain duplicates")
        if not all(isinstance(item, str) for item in self.warnings):
            raise FlowValidationError("Workflow warnings must be strings")
        if set(self.variables) != placeholders:
            raise FlowValidationError(
                "Workflow variables must exactly match placeholders used by actions"
            )
        first_goto = next((a.value for a in self.actions if a.kind == "goto"), "")
        if first_goto and self.start_url != first_goto:
            raise FlowValidationError("start_url must match the first recorded goto action")
        if not any(action.kind.startswith("assert_") for action in self.actions):
            raise FlowValidationError("Workflow requires at least one executable assertion")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        payload = asdict(self)
        payload["slug"] = self.slug
        payload["fingerprint"] = self.fingerprint(include_fingerprint=False)
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> Workflow:
        if not isinstance(payload, dict):
            raise FlowValidationError("Workflow manifest must be a JSON object")
        known = {
            "name",
            "intent",
            "start_url",
            "actions",
            "success_criteria",
            "variables",
            "warnings",
            "created_at",
            "schema_version",
            "source",
            "slug",
            "fingerprint",
        }
        extras = set(payload) - known
        if extras:
            raise FlowValidationError(f"Unknown workflow fields: {sorted(extras)}")
        if not isinstance(payload.get("fingerprint"), str):
            raise FlowValidationError("Workflow fingerprint is required")
        for field_name in (
            "name",
            "intent",
            "start_url",
            "success_criteria",
            "created_at",
            "schema_version",
            "source",
        ):
            if field_name in payload and not isinstance(payload[field_name], str):
                raise FlowValidationError(f"Workflow {field_name} must be a string")
        for field_name in ("variables", "warnings"):
            if field_name in payload and not isinstance(payload[field_name], list):
                raise FlowValidationError(f"Workflow {field_name} must be a list")
        actions = []
        action_fields = {"kind", "selector", "value", "expected", "risk", "source_line", "note"}
        selector_fields = {"engine", "value", "role", "name", "exact", "modifiers"}
        raw_actions = payload.get("actions", [])
        if not isinstance(raw_actions, list):
            raise FlowValidationError("Workflow actions must be a list")
        try:
            for raw in raw_actions:
                if not isinstance(raw, dict):
                    raise FlowValidationError("Every workflow action must be an object")
                action_extras = set(raw) - action_fields
                if action_extras:
                    raise FlowValidationError(f"Unknown action fields: {sorted(action_extras)}")
                selector_raw = raw.get("selector") or {"engine": "page"}
                if not isinstance(selector_raw, dict):
                    raise FlowValidationError("Action selector must be an object")
                selector_extras = set(selector_raw) - selector_fields
                if selector_extras:
                    raise FlowValidationError(f"Unknown selector fields: {sorted(selector_extras)}")
                selector_raw = dict(selector_raw)
                selector_raw["modifiers"] = tuple(selector_raw.get("modifiers") or ())
                actions.append(Action(**{**raw, "selector": Selector(**selector_raw)}))
        except TypeError as exc:
            raise FlowValidationError(f"Malformed workflow action: {exc}") from exc
        workflow = cls(
            name=payload.get("name", ""),
            intent=payload.get("intent", ""),
            start_url=payload.get("start_url", ""),
            actions=actions,
            success_criteria=payload.get("success_criteria", "The recorded assertions pass."),
            variables=list(payload.get("variables") or []),
            warnings=list(payload.get("warnings") or []),
            created_at=payload.get("created_at") or utc_now(),
            schema_version=payload.get("schema_version", SCHEMA_VERSION),
            source=payload.get("source", "playwright-codegen"),
        )
        workflow.validate()
        expected = payload["fingerprint"]
        if expected != workflow.fingerprint(include_fingerprint=False):
            raise FlowValidationError("Workflow fingerprint does not match its contents")
        if payload.get("slug") not in {None, workflow.slug}:
            raise FlowValidationError("Workflow slug does not match its name")
        return workflow

    @classmethod
    def read(cls, path: str | Path) -> Workflow:
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    def fingerprint(self, include_fingerprint: bool = False) -> str:
        payload = asdict(self)
        if include_fingerprint:
            payload["fingerprint"] = ""
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def classify_risk(kind: str, selector: Selector, value: Any = None) -> str:
    if kind == "goto" and isinstance(value, str) and PLACEHOLDER_SCAN_RE.search(value):
        return "approval"
    mutating = {"click", "press", "fill", "select_option", "check", "uncheck"}
    if kind not in mutating:
        return "safe"
    text = f"{selector.searchable_text()} {value or ''}"
    if kind in {"click", "press"} and RISKY_ACTION_RE.search(text):
        return "approval"
    return "review"


def sanitize_fill_value(
    selector: Selector, value: str, index: int, redact_all_inputs: bool = True
) -> tuple[str, str | None]:
    if PLACEHOLDER_RE.match(value):
        return value, PLACEHOLDER_RE.match(value).group(1)  # type: ignore[union-attr]
    if redact_all_inputs or SENSITIVE_SELECTOR_RE.search(selector.searchable_text()):
        variable = placeholder_name(selector.searchable_text(), index)
        return f"${{{variable}}}", variable
    return value, None
