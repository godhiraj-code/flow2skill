"""Run a synthetic, local Flow2Skill trial; no accounts or external sites are used."""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from importlib.metadata import distribution, version
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "trial-output"
HTML = """<!doctype html><html lang="en"><meta charset="utf-8">
<title>Flow2Skill scoped replay trial</title>
<style>body{font:18px system-ui;max-width:720px;margin:40px auto}section{border:1px solid #888;padding:20px;margin:20px 0}button{font:inherit;padding:8px 20px}</style>
<h1>Two dialogs. Two Save buttons.</h1>
<section role="dialog" aria-label="Billing"><h2>Billing</h2>
<button onclick="document.getElementById('result').textContent='Saved billing instead'">Save</button></section>
<section role="dialog" aria-label="Profile"><h2>Profile</h2>
<button onclick="document.getElementById('result').textContent='Saved chosen profile'">Save</button></section>
<p id="result" role="status">Waiting for a save</p></html>
"""


def main() -> None:
    origin = json.loads(distribution("flow2skill").read_text("direct_url.json") or "{}")
    commit = origin.get("vcs_info", {}).get("commit_id")
    OUTPUT.mkdir(exist_ok=True)
    fixture = OUTPUT / "fixture.html"
    capture = OUTPUT / "recorded_flow.py"
    fixture.write_text(HTML, encoding="utf-8")
    capture.write_text(
        f"""from playwright.sync_api import Page, expect

def test_profile(page: Page):
    page.goto({fixture.as_uri()!r})
    page.get_by_role("dialog", name="Profile", exact=True).get_by_role("button", name="Save", exact=True).click()
    expect(page.get_by_text("Saved chosen profile", exact=True)).to_be_visible()
""",
        encoding="utf-8",
    )
    evidence = [
        f"Python: {platform.python_version()}",
        f"OS: {platform.system()}",
        f"Flow2Skill: {version('flow2skill')}",
        f"Commit: {commit or 'package installation; see version above'}",
        "Input: hand-written synthetic recording; not an interactive Inspector capture.",
    ]

    def run(label: str, args: list[str], expected_code: int) -> str:
        result = subprocess.run(
            [sys.executable, *args],
            cwd=OUTPUT,
            env={
                **os.environ,
                "FLOW2SKILL_LIVE": "1",
                "FLOW2SKILL_ALLOW_SIDE_EFFECTS": "1",
                "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            },
            capture_output=True,
            text=True,
            timeout=60,
        )
        evidence.extend([f"\n{label}: exit {result.returncode}", result.stdout, result.stderr])
        if result.returncode != expected_code:
            raise RuntimeError(f"{label}: expected exit {expected_code}, got {result.returncode}")
        print(f"{label}: expected exit {expected_code} observed", flush=True)
        return result.stdout

    try:
        run(
            "Compile",
            [
                "-m",
                "flow2skill",
                "compile",
                str(capture),
                "--name",
                "Scoped profile",
                "--out",
                str(OUTPUT / "bundle"),
            ],
            0,
        )
        proof = OUTPUT / "bundle" / "test_scoped_profile.py"
        args = ["-m", "pytest", "-o", "addopts=", "-q", str(proof)]
        assert "1 passed" in run("Original page passes", args, 0)
        fixture.write_text(
            HTML.replace("'Saved chosen profile'", "'Still not saved'"), encoding="utf-8"
        )
        failed = run("Changed page fails", args, 1)
        assert "1 failed" in failed and "Locator expected to be visible" in failed
        fixture.write_text(HTML, encoding="utf-8")
        assert "1 passed" in run("Restored page passes", args, 0)
    finally:
        fixture.write_text(HTML, encoding="utf-8")
        (OUTPUT / "evidence.txt").write_text("\n".join(evidence), encoding="utf-8")
    print(f"Evidence: {OUTPUT / 'evidence.txt'}")


if __name__ == "__main__":
    main()
