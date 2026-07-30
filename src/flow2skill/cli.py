from __future__ import annotations

import argparse
import json
import shutil
import sys
from importlib.metadata import version
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
    record.add_argument("--channel", default="chrome")
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

    commands.add_parser("doctor", help="Check recorder and replay prerequisites")

    studio = commands.add_parser("studio", help="Launch the local Flow2Skill Studio UI")
    studio.add_argument("--host", default="127.0.0.1")
    studio.add_argument("--port", type=int, default=8765)
    studio.add_argument("--workspace-root", type=Path, default=DEFAULT_WORKSPACES)
    studio.add_argument("--no-open", action="store_true")
    return parser


def doctor() -> int:
    checks: list[tuple[str, bool, str]] = []
    checks.append(("Python", sys.version_info >= (3, 10), sys.version.split()[0]))
    playwright_version = version("playwright")
    checks.append(
        (
            "Python Playwright",
            playwright_version == DEFAULT_CODEGEN_VERSION,
            f"{playwright_version} (expected {DEFAULT_CODEGEN_VERSION})",
        )
    )
    checks.append(("Node.js", shutil.which("node") is not None, shutil.which("node") or "missing"))
    checks.append(("npx", shutil.which("npx") is not None, shutil.which("npx") or "missing"))
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser_path = Path(playwright.chromium.executable_path)
        checks.append(("Managed Chromium", browser_path.is_file(), str(browser_path)))
    except Exception as exc:
        checks.append(("Managed Chromium", False, f"{type(exc).__name__}: {exc}"))

    for label, passed, detail in checks:
        print(f"[{'PASS' if passed else 'FAIL'}] {label}: {detail}")
    if all(passed for _, passed, _ in checks):
        print("Flow2Skill is ready to record and replay workflows.")
        return 0
    print("Run `python -m playwright install chromium` after fixing missing prerequisites.")
    return 2


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
            return doctor()
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
