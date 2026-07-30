from __future__ import annotations

import os
import re
import secrets
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .exporter import write_bundle
from .model import FlowValidationError, slugify
from .parser import parse_codegen

DEFAULT_CODEGEN_VERSION = "1.61.0"
STALE_CAPTURE_SECONDS = 60 * 60
SAFE_OPTION_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def codegen_command(
    *,
    url: str,
    raw_output: Path,
    channel: str = "chrome",
    test_id_attribute: str = "data-testid",
) -> list[str]:
    if not re.match(r"^(https?|file)://", url, re.IGNORECASE):
        raise FlowValidationError("Recorder URL must use http://, https://, or file://")
    if not SAFE_OPTION_RE.fullmatch(channel):
        raise FlowValidationError("Browser channel contains unsupported characters")
    if not SAFE_OPTION_RE.fullmatch(test_id_attribute):
        raise FlowValidationError("Test-id attribute contains unsupported characters")
    version = os.getenv("FLOW2SKILL_CODEGEN_VERSION", DEFAULT_CODEGEN_VERSION)
    if not re.fullmatch(r"\d+(?:\.\d+){1,3}", version):
        raise FlowValidationError("FLOW2SKILL_CODEGEN_VERSION must be numeric")
    args = [
        "npx",
        "--yes",
        f"playwright@{version}",
        "codegen",
        "--target=python-pytest",
        f"--output={raw_output}",
        f"--channel={channel}",
        f"--test-id-attribute={test_id_attribute}",
        "--block-service-workers",
        url,
    ]
    if os.name == "nt":
        node = shutil.which("node.exe")
        npx_cmd = shutil.which("npx.cmd") or shutil.which("npx")
        if not node or not npx_cmd:
            raise FlowValidationError("Node.js and npx are required for recording")
        npx_cli = Path(npx_cmd).resolve().parent / "node_modules" / "npm" / "bin" / "npx-cli.js"
        if not npx_cli.is_file():
            raise FlowValidationError(f"Could not locate the npx CLI beside {npx_cmd}")
        return [node, str(npx_cli), *args[1:]]
    npx = shutil.which("npx")
    if not npx:
        raise FlowValidationError("npx is required for recording but was not found on PATH")
    args[0] = npx
    return args


def compile_source(
    source: str,
    *,
    name: str,
    output_dir: str | Path,
    intent: str = "Replay the demonstrated browser workflow reliably.",
    success_criteria: str = "The recorded assertions pass.",
    success_text: str | None = None,
    redact_all_inputs: bool = True,
) -> dict[str, Any]:
    workflow = parse_codegen(
        source,
        name=name,
        intent=intent,
        success_criteria=success_criteria,
        success_text=success_text,
        redact_all_inputs=redact_all_inputs,
    )
    paths = write_bundle(workflow, output_dir)
    return {
        "workflow": workflow,
        "paths": paths,
        "output_dir": Path(output_dir).resolve(),
    }


@dataclass
class RecordingJob:
    job_id: str
    name: str
    intent: str
    success_criteria: str
    success_text: str | None
    output_dir: Path
    raw_path: Path
    log_path: Path
    process: subprocess.Popen[Any]
    redact_all_inputs: bool = True
    state: str = "recording"
    error: str | None = None
    result: dict[str, Any] | None = None
    _finalized: bool = field(default=False, repr=False)

    def status(self) -> dict[str, Any]:
        exit_code = self.process.poll()
        if exit_code is None:
            return {
                "job_id": self.job_id,
                "state": "recording",
                "message": "Recorder is open. Complete the flow, then close the Playwright window.",
            }
        if not self._finalized:
            self._finalize(exit_code)
        response: dict[str, Any] = {"job_id": self.job_id, "state": self.state}
        if self.error:
            response["error"] = self.error
        if self.result:
            workflow = self.result["workflow"]
            response.update(
                {
                    "workspace": str(self.output_dir),
                    "workflow": workflow.to_dict(),
                    "files": {key: str(value) for key, value in self.result["paths"].items()},
                }
            )
        return response

    def _finalize(self, exit_code: int) -> None:
        self._finalized = True
        try:
            if exit_code != 0:
                raise FlowValidationError(
                    f"Playwright recorder exited with code {exit_code}; ephemeral logs were deleted"
                )
            if not self.raw_path.is_file() or self.raw_path.stat().st_size == 0:
                raise FlowValidationError("Recorder closed without producing a workflow")
            source = self.raw_path.read_text(encoding="utf-8")
            self.result = compile_source(
                source,
                name=self.name,
                output_dir=self.output_dir,
                intent=self.intent,
                success_criteria=self.success_criteria,
                success_text=self.success_text,
                redact_all_inputs=self.redact_all_inputs,
            )
            self.state = "complete"
        except Exception as exc:  # surfaced to the local Studio/API
            self.state = "failed"
            self.error = str(exc)
        finally:
            # Codegen can briefly contain literal inputs. Never keep the raw capture.
            self.raw_path.unlink(missing_ok=True)
            self.log_path.unlink(missing_ok=True)

    def cancel(self) -> dict[str, Any]:
        if self.process.poll() is None:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill.exe", "/PID", str(self.process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            else:
                self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self._finalized = True
        self.state = "cancelled"
        self.raw_path.unlink(missing_ok=True)
        self.log_path.unlink(missing_ok=True)
        return {"job_id": self.job_id, "state": self.state}


class RecorderManager:
    def __init__(self, workspace_root: str | Path):
        self.workspace_root = Path(workspace_root).resolve()
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, RecordingJob] = {}
        self._lock = threading.Lock()
        self._cleanup_stale_captures()

    def _cleanup_stale_captures(self) -> None:
        cutoff = time.time() - STALE_CAPTURE_SECONDS
        for pattern in (".*.raw.py", ".*.recorder.log"):
            for path in self.workspace_root.rglob(pattern):
                try:
                    if path.is_file() and path.stat().st_mtime < cutoff:
                        path.unlink(missing_ok=True)
                except OSError:
                    continue

    def start(
        self,
        *,
        name: str,
        url: str,
        intent: str,
        success_criteria: str,
        success_text: str | None,
        redact_all_inputs: bool = True,
        channel: str = "chrome",
    ) -> RecordingJob:
        if not url.lower().startswith(("http://", "https://", "file://")):
            raise FlowValidationError("Recorder URL must use http://, https://, or file://")
        workspace = self.workspace_root / slugify(name)
        workspace.mkdir(parents=True, exist_ok=True)
        job_id = secrets.token_hex(8)
        raw_path = workspace / f".{job_id}.raw.py"
        log_path = workspace / f".{job_id}.recorder.log"
        command = codegen_command(url=url, raw_output=raw_path, channel=channel)
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        log_handle = log_path.open("w", encoding="utf-8")
        try:
            process = subprocess.Popen(
                command,
                cwd=workspace,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                text=True,
                creationflags=creationflags,
            )
        except Exception:
            log_handle.close()
            raw_path.unlink(missing_ok=True)
            log_path.unlink(missing_ok=True)
            raise
        finally:
            log_handle.close()
        job = RecordingJob(
            job_id=job_id,
            name=name,
            intent=intent,
            success_criteria=success_criteria,
            success_text=success_text,
            output_dir=workspace,
            raw_path=raw_path,
            log_path=log_path,
            process=process,
            redact_all_inputs=redact_all_inputs,
        )
        with self._lock:
            self._jobs[job_id] = job
        return job

    def get(self, job_id: str) -> RecordingJob:
        with self._lock:
            job = self._jobs.get(job_id)
        if not job:
            raise FlowValidationError("Unknown recorder job")
        return job

    def cancel(self, job_id: str) -> dict[str, Any]:
        return self.get(job_id).cancel()

    def terminate_all(self) -> None:
        with self._lock:
            jobs = list(self._jobs.values())
        for job in jobs:
            job.cancel()


def record_blocking(
    *,
    name: str,
    url: str,
    output_root: str | Path,
    intent: str,
    success_criteria: str,
    success_text: str | None = None,
    redact_all_inputs: bool = True,
    channel: str = "chrome",
) -> dict[str, Any]:
    manager = RecorderManager(output_root)
    job = manager.start(
        name=name,
        url=url,
        intent=intent,
        success_criteria=success_criteria,
        success_text=success_text,
        redact_all_inputs=redact_all_inputs,
        channel=channel,
    )
    print("Recorder opened. Complete the browser flow, then close the Playwright window.")
    job.process.wait()
    status = job.status()
    if status["state"] != "complete":
        raise FlowValidationError(status.get("error", "Recording failed"))
    return job.result or {}
