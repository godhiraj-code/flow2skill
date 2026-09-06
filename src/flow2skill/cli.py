from __future__ import annotations

import argparse
import json
import shutil
import sys
from importlib.metadata import PackageNotFoundError, version
from importlib.resources import files
from pathlib import Path

from . import __version__
from .exporter import write_bundle
from .model import FlowValidationError, Workflow
from .parser import parse_codegen_file
from .recorder import DEFAULT_CODEGEN_VERSION, compile_source, record_blocking
from .replay import replay

DEFAULT_WORKSPACES = Path.home() / "Flow2SkillWorkspaces"


DEMO_CAPTURE_VALUE = "synthetic-demo-value-7Q9X"


def demo_recording() -> str:
    fixture = Path(str(files("flow2skill").joinpath("ui/demo_form.html"))).resolve().as_uri()
    return f"""from playwright.sync_api import Page, expect


def test_agent_release_gate(page: Page) -> None:
    page.goto({fixture!r})
    page.get_by_label("API token").fill({DEMO_CAPTURE_VALUE!r})
    page.get_by_role("button", name="Validate workflow").click()
    expect(page.get_by_text("Ready for deterministic replay", exact=True)).to_be_visible()
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="flow2skill",
        description="Demonstrate a browser flow once; compile it into an agent skill and a test.",
    )
    parser.add_argument("--version", action="version", version=f"Flow2Skill {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    record = commands.add_parser(
        "record", help="Open Playwright codegen and capture a browser flow"
    )
    record.add_argument("url")
    record.add_argument("--name", required=True)
    record.add_argument("--intent", default="Replay the demonstrated browser workflow reliably.")
    record.add_argument("--success", default="The recorded assertions pass.")
    record.add_argument("--success-text")
    record.add_argument("--out", type=Path, default=DEFAULT_WORKSPACES)
    record.add_argument(
        "--channel",
        help="Use an installed browser channel, e.g. chrome; defaults to managed Chromium",
    )
    record.add_argument(
        "--protect-inputs",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Replace typed and asserted values with environment variables (default: enabled)",
    )

    compile_cmd = commands.add_parser("compile", help="Compile Playwright Python codegen output")
    compile_cmd.add_argument("recording", type=Path)
    compile_cmd.add_argument("--name", required=True)
    compile_cmd.add_argument(
        "--intent", default="Replay the demonstrated browser workflow reliably."
    )
    compile_cmd.add_argument("--success", default="The recorded assertions pass.")
    compile_cmd.add_argument("--success-text")
    compile_cmd.add_argument("--out", type=Path, required=True)
    compile_cmd.add_argument(
        "--protect-inputs",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Replace typed and asserted values with environment variables (default: enabled)",
    )

    export = commands.add_parser("export", help="Regenerate artifacts from flow.json")
    export.add_argument("flow", type=Path)
    export.add_argument("--out", type=Path, required=True)

    inspect = commands.add_parser("inspect", help="Print a safe execution plan")
    inspect.add_argument("flow", type=Path)

    replay_cmd = commands.add_parser("replay", help="Dry-run or execute a compiled workflow")
    replay_cmd.add_argument("flow", type=Path)
    replay_cmd.add_argument("--live", action="store_true")
    replay_cmd.add_argument("--headed", action="store_true")
    replay_cmd.add_argument("--allow-side-effects", action="store_true")
    replay_cmd.add_argument("--channel")
    replay_cmd.add_argument("--evidence-dir", type=Path)

    demo = commands.add_parser("demo", help="Generate an executable local sample bundle")
    demo.add_argument("--out", type=Path, default=Path("flow2skill-demo"))

    doctor_cmd = commands.add_parser("doctor", help="Check prerequisites and show repair steps")
    doctor_cmd.add_argument("--mode", choices=["all", "compile", "record", "replay"], default="all")
    doctor_cmd.add_argument("--json", action="store_true", help="Print machine-readable checks")

    studio = commands.add_parser("studio", help="Launch the local Flow2Skill Studio UI")
    studio.add_argument("--host", default="127.0.0.1")
    studio.add_argument("--port", type=int, default=8765)
    studio.add_argument("--workspace-root", type=Path, default=DEFAULT_WORKSPACES)
    studio.add_argument("--no-open", action="store_true")
    return parser


def doctor(*, mode: str = "all", json_output: bool = False) -> int:
    if mode not in {"all", "compile", "record", "replay"}:
        raise ValueError("Unknown doctor mode")
    checks = []

    def check(name, passed, detail, remedy, required=True):
        checks.append(
            {
                "name": name,
                "passed": passed,
                "required": required,
                "detail": detail,
                "remedy": None if passed else remedy,
            }
        )

    check(
        "Python",
        sys.version_info >= (3, 10),
        sys.version.split()[0],
        "Install Python 3.10 or newer and recreate the environment.",
    )
    try:
        installed = version("playwright")
    except PackageNotFoundError:
        installed = "missing"
    check(
        "Python Playwright",
        installed == DEFAULT_CODEGEN_VERSION,
        f"{installed} (expected {DEFAULT_CODEGEN_VERSION})",
        f'python -m pip install "playwright=={DEFAULT_CODEGEN_VERSION}"',
    )

    if mode in {"all", "record"}:
        for label, command in [("Node.js", "node"), ("npx", "npx")]:
            location = shutil.which(command)
            check(
                label,
                location is not None,
                location or "missing",
                "Install Node.js with npm/npx, then reopen the terminal so PATH is refreshed.",
            )
    if mode in {"all", "record", "replay"}:
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as playwright:
                browser_path = Path(playwright.chromium.executable_path)
            check(
                "Managed Chromium",
                browser_path.is_file(),
                str(browser_path),
                "python -m playwright install chromium",
            )
        except Exception as exc:
            check(
                "Managed Chromium",
                False,
                f"{type(exc).__name__}: {exc}",
                "Repair the Python Playwright installation, then run python -m playwright install chromium.",
            )
    if mode in {"all", "replay"}:
        try:
            pytest_version = version("pytest")
        except PackageNotFoundError:
            pytest_version = None
        check(
            "pytest (exported proofs)",
            pytest_version is not None,
            pytest_version or "missing; CLI replay does not require pytest",
            'python -m pip install "flow2skill[test]"',
            required=False,
        )

    healthy = all(item["passed"] for item in checks if item["required"])
    note = "These checks inspect prerequisites; they do not launch a browser or prove a recorded workflow."
    if json_output:
        print(
            json.dumps({"mode": mode, "healthy": healthy, "checks": checks, "note": note}, indent=2)
        )
    else:
        for item in checks:
            state = "PASS" if item["passed"] else "FAIL" if item["required"] else "WARN"
            print(f"[{state}] {item['name']}: {item['detail']}")
            if item["remedy"]:
                print(f"  Fix: {item['remedy']}")
        print(
            "Selected prerequisites found."
            if healthy
            else "Required prerequisites are missing or mismatched."
        )
        print(note)
    return 0 if healthy else 2


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "record":
            result = record_blocking(
                name=args.name,
                url=args.url,
                output_root=args.out,
                intent=args.intent,
                success_criteria=args.success,
                success_text=args.success_text,
                redact_all_inputs=args.protect_inputs,
                channel=args.channel,
            )
            print(f"Compiled: {result['output_dir']}")
        elif args.command == "compile":
            workflow = parse_codegen_file(
                args.recording,
                name=args.name,
                intent=args.intent,
                success_criteria=args.success,
                success_text=args.success_text,
                redact_all_inputs=args.protect_inputs,
            )
            paths = write_bundle(workflow, args.out)
            print(json.dumps({key: str(value) for key, value in paths.items()}, indent=2))
        elif args.command == "export":
            paths = write_bundle(Workflow.read(args.flow), args.out)
            print(json.dumps({key: str(value) for key, value in paths.items()}, indent=2))
        elif args.command == "inspect":
            print(replay(Workflow.read(args.flow), live=False))
        elif args.command == "replay":
            print(
                replay(
                    Workflow.read(args.flow),
                    live=args.live,
                    headed=args.headed,
                    allow_side_effects=args.allow_side_effects,
                    channel=args.channel,
                    evidence_dir=args.evidence_dir,
                )
            )
        elif args.command == "demo":
            result = compile_source(
                demo_recording(),
                name="Agent release gate",
                output_dir=args.out,
                intent="Validate a protected agent configuration and prove it is ready for replay.",
                success_criteria="The exact deterministic ready state is visible.",
            )
            test_path = result["paths"]["test"]
            print(f"Generated executable demo: {result['output_dir']}")
            print(
                "Verify with: F2S_LABEL_API_TOKEN_1='runtime-demo-token' "
                "FLOW2SKILL_LIVE=1 FLOW2SKILL_ALLOW_SIDE_EFFECTS=1 "
                f"pytest -q {test_path}"
            )
        elif args.command == "doctor":
            return doctor(mode=args.mode, json_output=args.json)
        elif args.command == "studio":
            from .server import serve

            serve(
                host=args.host,
                port=args.port,
                workspace_root=args.workspace_root,
                open_browser=not args.no_open,
            )
        return 0
    except (FlowValidationError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
