"""Run exported README commands in native shells against the installed package."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("FLOW2SKILL_BROWSER_TESTS") != "1", reason="Opt-in native-shell browser proof"
)


@pytest.mark.parametrize("shell", ["powershell", "cmd"] if os.name == "nt" else ["bash"])
def test_exported_shell_instructions_execute_real_proof(tmp_path, shell):
    bundle = tmp_path / "bundle with spaces"
    subprocess.run(
        [sys.executable, "-m", "flow2skill", "demo", "--out", str(bundle)],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    readme = (bundle / "README.md").read_text(encoding="utf-8")
    commands = re.findall(rf"```{shell}\n(.*?)```", readme, re.DOTALL)[-1]
    commands = commands.replace("<your value>", "runtime-demo-token")
    if shell == "powershell":
        script = bundle / "verify.ps1"
        commands += "\nexit $LASTEXITCODE\n"
        invocation = ["pwsh", "-NoProfile", "-File", str(script)]
    elif shell == "cmd":
        script = bundle / "verify.cmd"
        invocation = ["cmd", "/d", "/c", str(script)]
    else:
        script = bundle / "verify.sh"
        invocation = ["bash", str(script)]
    script.write_text(commands, encoding="utf-8")
    environment = {
        **os.environ,
        "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"],
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    }
    # Only the generated instructions may enable execution or supply the input.
    for name in ("FLOW2SKILL_LIVE", "FLOW2SKILL_ALLOW_SIDE_EFFECTS", "F2S_LABEL_API_TOKEN_1"):
        environment.pop(name, None)
    result = subprocess.run(
        invocation, cwd=bundle, env=environment, capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout, result.stdout + result.stderr
