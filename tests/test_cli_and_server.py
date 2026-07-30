from __future__ import annotations

import json

from flow2skill.cli import DEMO_CAPTURE_VALUE, main
from flow2skill.model import Workflow
from flow2skill.parser import parse_codegen
from flow2skill.server import sample_recording, valid_local_authority


def test_local_authority_validation_blocks_dns_rebinding_shapes() -> None:
    assert valid_local_authority("127.0.0.1:8765", 8765)
    assert valid_local_authority("localhost:8765", 8765)
    assert valid_local_authority("[::1]:8765", 8765)
    assert not valid_local_authority("attacker.example:8765", 8765)
    assert not valid_local_authority("127.0.0.1:9999", 8765)
    assert not valid_local_authority("user@127.0.0.1:8765", 8765)
    assert not valid_local_authority("127.0.0.1:8765/path", 8765)


def test_server_sample_is_local_executable_and_assertion_backed() -> None:
    workflow = parse_codegen(
        sample_recording("http://127.0.0.1:8765/demo"),
        name="Local demo",
    )
    assert workflow.start_url == "http://127.0.0.1:8765/demo"
    assert any(action.kind.startswith("assert_") for action in workflow.actions)
    assert workflow.variables == ["F2S_LABEL_API_TOKEN_1"]


def test_cli_demo_generates_canonical_bundle(tmp_path) -> None:
    output = tmp_path / "demo"
    assert main(["demo", "--out", str(output)]) == 0
    workflow = Workflow.read(output / "flow.json")
    assert workflow.name == "Agent release gate"
    assert (output / "SKILL.md").is_file()
    assert (output / "test_agent_release_gate.py").is_file()
    combined = "\n".join(path.read_text(encoding="utf-8") for path in output.iterdir())
    assert DEMO_CAPTURE_VALUE not in combined


def test_cli_compile_protects_inputs_and_never_deletes_explicit_source(tmp_path) -> None:
    recording = tmp_path / "recording.py"
    recording.write_text(
        """def test_flow(page):
    page.goto("https://example.test")
    page.get_by_label("Search").fill("private query")
    expect(page.get_by_text("Ready")).to_be_visible()
""",
        encoding="utf-8",
    )
    output = tmp_path / "compiled"
    assert (
        main(
            [
                "compile",
                str(recording),
                "--name",
                "Protected compile",
                "--out",
                str(output),
            ]
        )
        == 0
    )
    assert recording.is_file()
    payload = json.loads((output / "flow.json").read_text(encoding="utf-8"))
    assert payload["actions"][1]["value"] == "${F2S_LABEL_SEARCH_1}"
    assert "private query" not in json.dumps(payload)
