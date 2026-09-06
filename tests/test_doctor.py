from __future__ import annotations

import json
from importlib.metadata import PackageNotFoundError
from unittest.mock import MagicMock

from flow2skill.cli import main
from flow2skill.recorder import DEFAULT_CODEGEN_VERSION


def test_compile_doctor_does_not_require_browser_or_node(monkeypatch, capsys):
    monkeypatch.setattr("flow2skill.cli.version", lambda _: DEFAULT_CODEGEN_VERSION)
    monkeypatch.setattr(
        "flow2skill.cli.shutil.which",
        lambda _: (_ for _ in ()).throw(AssertionError("Unexpected Node check")),
    )
    monkeypatch.setattr(
        "playwright.sync_api.sync_playwright",
        lambda: (_ for _ in ()).throw(AssertionError("Unexpected browser check")),
    )
    assert main(["doctor", "--mode", "compile", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["healthy"]
    assert {item["name"] for item in report["checks"]} == {"Python", "Python Playwright"}


def test_replay_doctor_warns_about_pytest_without_requiring_node(monkeypatch, tmp_path, capsys):
    binary = tmp_path / "chromium"
    binary.touch()
    runtime = MagicMock()
    runtime.__enter__.return_value.chromium.executable_path = str(binary)
    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: runtime)
    monkeypatch.setattr("flow2skill.cli.shutil.which", lambda _: None)

    def installed(name):
        if name == "pytest":
            raise PackageNotFoundError(name)
        return DEFAULT_CODEGEN_VERSION

    monkeypatch.setattr("flow2skill.cli.version", installed)
    assert main(["doctor", "--mode", "replay", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    missing = [item for item in report["checks"] if not item["passed"]]
    assert len(missing) == 1
    assert not missing[0]["required"]
    assert "flow2skill[test]" in missing[0]["remedy"]
    assert "do not launch" in report["note"]


def test_missing_distribution_returns_repair_instruction(monkeypatch, capsys):
    def absent(name):
        raise PackageNotFoundError(name)

    monkeypatch.setattr("flow2skill.cli.version", absent)
    assert main(["doctor", "--mode", "compile", "--json"]) == 2
    report = json.loads(capsys.readouterr().out)
    assert not report["healthy"]
    assert f"playwright=={DEFAULT_CODEGEN_VERSION}" in report["checks"][1]["remedy"]
