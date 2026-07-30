from __future__ import annotations

import html
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import Locator, Page, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
PORT = 18771
BASE_URL = f"http://127.0.0.1:{PORT}"
WORKSPACES = ROOT / ".tmp" / "public-demo-workspaces"
VIDEO_DIR = ROOT / ".tmp" / "public-demo-video"
DOCS = ROOT / "docs"

OVERLAY_CSS = """
#f2s-demo-caption{position:fixed;left:50%;bottom:26px;transform:translateX(-50%);z-index:2147483646;
max-width:1050px;padding:13px 22px;border:1px solid #d8ff5266;border-radius:999px;background:#080a09e8;
box-shadow:0 18px 50px #000b;color:#f4f1e8;font:700 18px/1.25 'Segoe UI',sans-serif;letter-spacing:.01em;
text-align:center;backdrop-filter:blur(12px);transition:opacity .25s ease}
#f2s-demo-caption b{color:#d8ff52}#f2s-demo-watermark{position:fixed;right:20px;top:18px;z-index:2147483645;
padding:7px 10px;border:1px solid #ffffff22;border-radius:7px;background:#080a09bb;color:#aeb6b0;
font:700 10px/1 ui-monospace,monospace;letter-spacing:.13em}#f2s-demo-cursor{position:fixed;left:0;top:0;z-index:2147483647;
width:20px;height:20px;border:2px solid #d8ff52;border-radius:50%;background:#d8ff5222;box-shadow:0 0 0 7px #d8ff5215;
pointer-events:none;transform:translate(-100px,-100px);transition:transform .6s cubic-bezier(.2,.8,.2,1)}
"""


def wait_for_server() -> None:
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{BASE_URL}/", timeout=1) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(0.2)
    raise RuntimeError("Studio did not become ready")


def inject_overlay(page: Page) -> None:
    page.add_style_tag(content=OVERLAY_CSS)
    page.evaluate(
        """() => {
          for (const [id, text] of [
            ['f2s-demo-caption', ''],
            ['f2s-demo-watermark', 'FLOW2SKILL · REAL LOCAL PROOF'],
            ['f2s-demo-cursor', '']
          ]) {
            const node = document.createElement('div'); node.id=id; node.textContent=text; document.body.appendChild(node);
          }
        }"""
    )


def caption(page: Page, html: str, hold: float = 2.2) -> None:
    page.locator("#f2s-demo-caption").evaluate("(node, value) => node.innerHTML=value", html)
    page.wait_for_timeout(int(hold * 1000))


def move_to(page: Page, locator: Locator) -> None:
    locator.scroll_into_view_if_needed()
    box = locator.bounding_box()
    if not box:
        raise RuntimeError("Demo target has no visible bounding box")
    x = box["x"] + box["width"] / 2 - 10
    y = box["y"] + box["height"] / 2 - 10
    page.locator("#f2s-demo-cursor").evaluate(
        "(node, point) => node.style.transform=`translate(${point.x}px,${point.y}px)`",
        {"x": x, "y": y},
    )
    page.wait_for_timeout(750)


def move_and_click(page: Page, locator: Locator) -> None:
    move_to(page, locator)
    locator.click()
    page.wait_for_timeout(500)


def run_proofs() -> tuple[str, str, str]:
    workspace = WORKSPACES / "agent-release-gate"
    flow = workspace / "flow.json"
    generated_test = workspace / "test_agent_release_gate.py"
    evidence = ROOT / ".tmp" / "public-demo-evidence"
    shutil.rmtree(evidence, ignore_errors=True)
    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": "",
            "F2S_LABEL_API_TOKEN_1": "runtime-demo-token",
            "FLOW2SKILL_LIVE": "1",
            "FLOW2SKILL_ALLOW_SIDE_EFFECTS": "1",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        }
    )
    commands = [
        [sys.executable, "-m", "flow2skill", "inspect", str(flow)],
        [
            sys.executable,
            "-m",
            "flow2skill",
            "replay",
            str(flow),
            "--live",
            "--allow-side-effects",
            "--evidence-dir",
            str(evidence),
        ],
        [sys.executable, "-m", "pytest", "-q", str(generated_test)],
    ]
    outputs: list[str] = []
    for command in commands:
        completed = subprocess.run(
            command,
            cwd=workspace,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(completed.stdout + completed.stderr)
        outputs.append(completed.stdout.strip())
    screenshot = evidence / "agent-release-gate-passed.png"
    shutil.copy2(screenshot, DOCS / "replay-evidence.png")
    return outputs[0], outputs[1], outputs[2]


def proof_page(inspect_output: str, replay_output: str, pytest_output: str) -> str:
    def safe(value: str) -> str:
        normalized = value.replace(str(ROOT), "[LOCAL_PROJECT]")
        return html.escape(normalized)

    return f"""<!doctype html><html><head><meta charset='utf-8'><style>
    *{{box-sizing:border-box}}body{{margin:0;min-height:100vh;background:radial-gradient(circle at 80% 0,#1a2210,transparent 34%),#090b0a;color:#f4f1e8;font:16px/1.5 'Segoe UI',sans-serif;padding:52px}}
    .top{{display:flex;justify-content:space-between;color:#d8ff52;font:700 12px ui-monospace,monospace;letter-spacing:.14em}}h1{{font-size:58px;line-height:.95;letter-spacing:-.05em;margin:24px 0 30px}}h1 span{{color:#d8ff52}}
    .grid{{display:grid;grid-template-columns:1fr 1fr;gap:18px}}.card{{border:1px solid #2c332e;border-radius:16px;background:#111412;padding:22px;min-height:210px}}.wide{{grid-column:1/-1;min-height:150px}}
    .label{{color:#62e6ff;font:700 11px ui-monospace,monospace;letter-spacing:.12em;margin-bottom:12px}}pre{{margin:0;white-space:pre-wrap;color:#d8ddd8;font:14px/1.45 ui-monospace,monospace}}
    .pass{{display:inline-block;margin-top:28px;padding:10px 16px;border-radius:999px;background:#d8ff52;color:#0a0c0b;font-weight:900}}
    </style></head><body>
    <div class='top'><span>FLOW2SKILL · VERIFICATION LEDGER</span><span>REAL COMMAND OUTPUT</span></div>
    <h1>The procedure ran.<br><span>The proof passed.</span></h1>
    <div class='grid'>
      <section class='card'><div class='label'>01 · INSPECTED PLAN</div><pre>{safe(inspect_output)}</pre></section>
      <section class='card'><div class='label'>02 · ALLOWED REPLAY</div><pre>{safe(replay_output)}</pre></section>
      <section class='card wide'><div class='label'>03 · GENERATED PYTEST</div><pre>{safe(pytest_output)}</pre><div class='pass'>LOCAL · REDACTED · REVIEWED · REPLAYED</div></section>
    </div></body></html>"""


def main() -> int:
    shutil.rmtree(WORKSPACES, ignore_errors=True)
    shutil.rmtree(VIDEO_DIR, ignore_errors=True)
    WORKSPACES.mkdir(parents=True)
    VIDEO_DIR.mkdir(parents=True)
    DOCS.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["PYTHONPATH"] = ""
    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "flow2skill",
            "studio",
            "--port",
            str(PORT),
            "--workspace-root",
            str(WORKSPACES),
            "--no-open",
        ],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    try:
        wait_for_server()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                viewport={"width": 1440, "height": 810},
                record_video_dir=str(VIDEO_DIR),
                record_video_size={"width": 1440, "height": 810},
                color_scheme="dark",
            )
            page = context.new_page()
            video = page.video
            page.goto(f"{BASE_URL}/", wait_until="networkidle")
            inject_overlay(page)
            caption(
                page,
                "One successful browser run becomes an <b>agent skill + executable proof</b>.",
                3,
            )

            compile_tab = page.get_by_role("button", name="COMPILE CODEGEN")
            move_and_click(page, compile_tab)
            caption(page, "The demo target is packaged, deterministic, and <b>local only</b>.", 2.5)

            demo_button = page.get_by_role("button", name="Generate safe demo")
            move_and_click(page, demo_button)
            page.locator("#result.show").wait_for()
            caption(
                page,
                "One capture produced <b>five inspectable artifacts</b> with one protected value.",
                3,
            )
            page.screenshot(path=str(DOCS / "studio.png"), full_page=True)

            flow_row = page.locator(".artifact").filter(has_text="flow.json")
            move_and_click(page, flow_row.get_by_role("button"))
            page.locator("#preview-dialog[open]").wait_for()
            caption(
                page,
                "The captured synthetic token is gone. The manifest stores only <b>${F2S_LABEL_API_TOKEN_1}</b>.",
                4,
            )
            move_and_click(page, page.get_by_role("button", name="Close"))

            page.goto(f"{BASE_URL}/demo", wait_until="networkidle")
            inject_overlay(page)
            caption(
                page, "Now replay the same flow against the real <b>local release gate</b>.", 2.5
            )
            token = page.get_by_label("API token")
            move_to(page, token)
            token.fill("runtime-demo-token")
            page.wait_for_timeout(900)
            validate = page.get_by_role("button", name="Validate workflow")
            move_and_click(page, validate)
            page.get_by_text("Ready for deterministic replay", exact=True).wait_for()
            caption(
                page,
                "The exact mechanical success state appears. <b>No external calls. No persistent writes.</b>",
                4,
            )
            page.screenshot(path=str(DOCS / "release-gate.png"), full_page=True)

            page.goto(f"{BASE_URL}/", wait_until="networkidle")
            inject_overlay(page)
            recent = page.locator(".recent-item").filter(has_text="Agent release gate")
            move_and_click(page, recent.get_by_role("button", name="OPEN"))
            skill_row = page.locator(".artifact").filter(has_text="SKILL.md")
            move_and_click(page, skill_row.get_by_role("button"))
            page.locator("#preview-dialog[open]").wait_for()
            caption(
                page,
                "The agent gets a review-gated procedure. <b>QA gets a standalone Playwright test.</b>",
                4,
            )
            move_and_click(page, page.get_by_role("button", name="Close"))
            caption(page, "Running the real replay and generated pytest now…", 2)
            inspect_output, replay_output, pytest_output = run_proofs()
            page.set_content(proof_page(inspect_output, replay_output, pytest_output))
            page.wait_for_timeout(8000)
            page.screenshot(path=str(DOCS / "verification-ledger.png"), full_page=True)

            context.close()
            browser.close()
            recorded = Path(video.path())
            target = DOCS / "flow2skill-demo.webm"
            shutil.copy2(recorded, target)
            print(f"video={target} bytes={target.stat().st_size}")
            print(f"studio_screenshot={DOCS / 'studio.png'}")
            print(f"fixture_screenshot={DOCS / 'release-gate.png'}")
        return 0
    finally:
        server.terminate()
        try:
            server.wait(timeout=8)
        except subprocess.TimeoutExpired:
            server.kill()


if __name__ == "__main__":
    raise SystemExit(main())
