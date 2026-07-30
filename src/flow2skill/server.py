from __future__ import annotations

import json
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .model import FlowValidationError, Workflow, slugify
from .recorder import RecorderManager, compile_source

MAX_BODY = 1_000_000


def sample_recording(url: str) -> str:
    return f"""from playwright.sync_api import Page, expect


def test_agent_release_gate(page: Page) -> None:
    page.goto({url!r})
    page.get_by_label("API token").fill("synthetic-demo-value-7Q9X")
    page.get_by_role("button", name="Validate workflow").click()
    expect(page.get_by_text("Ready for deterministic replay", exact=True)).to_be_visible()
"""


def valid_local_authority(authority: str, expected_port: int) -> bool:
    try:
        parsed = urlsplit(f"http://{authority}")
        request_port = parsed.port or 80
    except ValueError:
        return False
    return (
        parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        and parsed.username is None
        and parsed.password is None
        and parsed.path in {"", "/"}
        and request_port == expected_port
    )


def serve(
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    workspace_root: str | Path,
    open_browser: bool = True,
) -> None:
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise FlowValidationError("Flow2Skill Studio is local-only and may bind only to loopback")
    token = secrets.token_hex(24)
    root = Path(workspace_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    manager = RecorderManager(root)
    index_template = files("flow2skill").joinpath("ui/index.html").read_text(encoding="utf-8")
    demo_fixture = files("flow2skill").joinpath("ui/demo_form.html").read_text(encoding="utf-8")
    index = index_template.replace("__FLOW2SKILL_TOKEN__", token)

    class Handler(BaseHTTPRequestHandler):
        server_version = "Flow2SkillStudio/0.1"

        def log_message(self, fmt: str, *args: object) -> None:
            print(f"[studio] {self.client_address[0]} {fmt % args}")

        def _headers(self, status: int, content_type: str, length: int) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                "script-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                "connect-src 'self'; frame-ancestors 'none'",
            )
            self.end_headers()

        def _send(self, payload: bytes, content_type: str, status: int = 200) -> None:
            self._headers(status, content_type, len(payload))
            self.wfile.write(payload)

        def _json(self, payload: object, status: int = 200) -> None:
            self._send(
                json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
                status,
            )

        def _authorized(self) -> bool:
            return secrets.compare_digest(self.headers.get("X-Flow2Skill-Token", ""), token)

        def _valid_authority(self, authority: str) -> bool:
            return valid_local_authority(authority, server.server_port)

        def _valid_host(self) -> bool:
            return self._valid_authority(self.headers.get("Host", ""))

        def _same_origin(self) -> bool:
            origin = self.headers.get("Origin")
            if not origin:
                return True
            parsed = urlsplit(origin)
            return parsed.scheme == "http" and self._valid_authority(parsed.netloc)

        def _require_auth(self) -> bool:
            if self._valid_host() and self._authorized() and self._same_origin():
                return True
            self._json({"error": "Unauthorized local request"}, 403)
            return False

        def _read_json(self) -> dict[str, object]:
            try:
                size = int(self.headers.get("Content-Length", "0"))
            except ValueError as exc:
                raise FlowValidationError("Invalid request size") from exc
            if size <= 0 or size > MAX_BODY:
                raise FlowValidationError("Request body is empty or too large")
            raw = self.rfile.read(size)
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise FlowValidationError("Request body must be valid JSON") from exc
            if not isinstance(payload, dict):
                raise FlowValidationError("Request body must be a JSON object")
            return payload

        def _workspace(self, name: str) -> Path:
            workspace = (root / slugify(name)).resolve()
            if root not in workspace.parents:
                raise FlowValidationError("Invalid workspace")
            return workspace

        def _payload_text(self, payload: dict[str, object], key: str, *, default: str = "") -> str:
            value = payload.get(key, default)
            if not isinstance(value, str):
                raise FlowValidationError(f"Request field `{key}` must be a string")
            return value

        def _payload_bool(
            self, payload: dict[str, object], key: str, *, default: bool = True
        ) -> bool:
            value = payload.get(key, default)
            if not isinstance(value, bool):
                raise FlowValidationError(f"Request field `{key}` must be a boolean")
            return value

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlsplit(self.path)
            if not self._valid_host():
                self._json({"error": "Invalid local Host header"}, 403)
                return
            if parsed.path == "/":
                self._send(index.encode("utf-8"), "text/html; charset=utf-8")
                return
            if parsed.path == "/demo":
                self._send(demo_fixture.encode("utf-8"), "text/html; charset=utf-8")
                return
            if parsed.path == "/favicon.ico":
                self._send(b"", "image/x-icon", 204)
                return
            if not self._require_auth():
                return
            try:
                if parsed.path == "/api/health":
                    self._json(
                        {
                            "ok": True,
                            "local_only": True,
                            "workspace_root": str(root),
                        }
                    )
                    return
                if parsed.path == "/api/workspaces":
                    items = []
                    for child in sorted(
                        root.iterdir(), key=lambda path: path.stat().st_mtime, reverse=True
                    ):
                        manifest = child / "flow.json"
                        if not child.is_dir() or not manifest.is_file():
                            continue
                        try:
                            workflow = Workflow.read(manifest)
                            items.append(
                                {
                                    "slug": child.name,
                                    "name": workflow.name,
                                    "actions": len(workflow.actions),
                                    "assertions": sum(
                                        action.kind.startswith("assert_")
                                        for action in workflow.actions
                                    ),
                                    "warnings": len(workflow.warnings),
                                    "path": str(child),
                                }
                            )
                        except Exception:
                            continue
                    self._json({"workspaces": items})
                    return
                if parsed.path.startswith("/api/record/"):
                    job_id = parsed.path.rsplit("/", 1)[-1]
                    self._json(manager.get(job_id).status())
                    return
                if parsed.path == "/api/artifact":
                    params = parse_qs(parsed.query)
                    workspace = self._workspace((params.get("workspace") or [""])[0])
                    filename = (params.get("file") or [""])[0]
                    allowed = {
                        "SKILL.md",
                        "README.md",
                        "flow.json",
                        "flow.yaml",
                    }
                    if filename not in allowed and not (
                        filename.startswith("test_") and filename.endswith(".py")
                    ):
                        raise FlowValidationError("Artifact is not previewable")
                    artifact = (workspace / filename).resolve()
                    if workspace not in artifact.parents or not artifact.is_file():
                        raise FlowValidationError("Artifact was not found")
                    self._json(
                        {
                            "workspace": workspace.name,
                            "file": filename,
                            "content": artifact.read_text(encoding="utf-8"),
                        }
                    )
                    return
                self._json({"error": "Not found"}, 404)
            except (FlowValidationError, OSError, ValueError) as exc:
                self._json({"error": str(exc)}, 400)

        def do_POST(self) -> None:  # noqa: N802
            if not self._require_auth():
                return
            try:
                payload = self._read_json()
                if self.path == "/api/compile":
                    name = self._payload_text(payload, "name").strip()
                    code = self._payload_text(payload, "code")
                    if not name:
                        raise FlowValidationError("Workflow name is required")
                    workspace = self._workspace(name)
                    result = compile_source(
                        code,
                        name=name,
                        output_dir=workspace,
                        intent=self._payload_text(payload, "intent"),
                        success_criteria=self._payload_text(payload, "success_criteria"),
                        success_text=self._payload_text(payload, "success_text").strip() or None,
                        redact_all_inputs=self._payload_bool(payload, "redact_all_inputs"),
                    )
                    workflow = result["workflow"]
                    self._json(
                        {
                            "state": "complete",
                            "workspace": workspace.name,
                            "path": str(workspace),
                            "workflow": workflow.to_dict(),
                            "files": {key: value.name for key, value in result["paths"].items()},
                        }
                    )
                    return
                if self.path == "/api/demo":
                    name = "Agent release gate"
                    workspace = self._workspace(name)
                    result = compile_source(
                        sample_recording(f"http://127.0.0.1:{server.server_port}/demo"),
                        name=name,
                        output_dir=workspace,
                        intent="Validate a protected agent configuration and prove it is ready.",
                        success_criteria="The exact deterministic ready state is visible.",
                    )
                    self._json(
                        {
                            "state": "complete",
                            "workspace": workspace.name,
                            "path": str(workspace),
                            "workflow": result["workflow"].to_dict(),
                            "files": {key: value.name for key, value in result["paths"].items()},
                        }
                    )
                    return
                if self.path == "/api/record/start":
                    name = self._payload_text(payload, "name").strip()
                    if not name:
                        raise FlowValidationError("Workflow name is required")
                    job = manager.start(
                        name=name,
                        url=self._payload_text(payload, "url").strip(),
                        intent=self._payload_text(payload, "intent").strip()
                        or "Replay the demonstrated browser workflow reliably.",
                        success_criteria=self._payload_text(payload, "success_criteria").strip()
                        or "The recorded assertions pass.",
                        success_text=self._payload_text(payload, "success_text").strip() or None,
                        redact_all_inputs=self._payload_bool(payload, "redact_all_inputs"),
                        channel=self._payload_text(payload, "channel", default="chrome")
                        or "chrome",
                    )
                    self._json(
                        {
                            "job_id": job.job_id,
                            "state": "recording",
                            "message": "Complete the flow in the recorder, then close its window.",
                        },
                        202,
                    )
                    return
                if self.path.startswith("/api/record/") and self.path.endswith("/cancel"):
                    job_id = self.path.split("/")[-2]
                    self._json(manager.cancel(job_id))
                    return
                self._json({"error": "Not found"}, 404)
            except (FlowValidationError, OSError, ValueError) as exc:
                self._json({"error": str(exc)}, 400)

    server = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{server.server_port}/"
    print(f"Flow2Skill Studio: {url}")
    print(f"Workspaces: {root}")
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        manager.terminate_all()
        server.server_close()
