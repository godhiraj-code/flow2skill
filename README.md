# Flow2Skill

[![Flow2Skill: record, sanitize, review and prove](https://raw.githubusercontent.com/godhiraj-code/flow2skill/main/docs/flow2skill-demo.gif)](https://github.com/godhiraj-code/flow2skill/raw/refs/heads/main/docs/flow2skill-demo-narrated.mp4)

**[Watch the 50-second narrated proof](https://github.com/godhiraj-code/flow2skill/raw/refs/heads/main/docs/flow2skill-demo-narrated.mp4)** · [Studio screenshot](https://raw.githubusercontent.com/godhiraj-code/flow2skill/main/docs/studio.png) · [Replay evidence](https://raw.githubusercontent.com/godhiraj-code/flow2skill/main/docs/replay-evidence.png)

> **Browser agents improvise. Flow2Skill preserves what already worked.**

Flow2Skill is a local-first compiler for demonstrated browser workflows. Record a successful flow with Playwright codegen. Flow2Skill turns it into an inspectable contract, a portable agent skill, and an executable regression test.

```text
human demonstration → strict AST compiler → protected workflow → agent skill + pytest proof
```

It exports:

- `flow.json` and `flow.yaml`: fingerprinted workflow contracts;
- `SKILL.md`: reviewable instructions for Hermes, Codex, Claude Code, Cursor, and other agents;
- `test_<workflow>.py`: a standalone Playwright/pytest proof;
- `README.md`: run instructions for the generated bundle.

Flow2Skill is not an autonomous browser agent. It preserves a workflow that already worked.

## Why use it

Browser agents are useful when the path is unknown. They are expensive and inconsistent when the path is already known. Flow2Skill converts a successful run into a reusable asset with stable selectors, protected inputs, explicit review gates, and mechanical success criteria.

- **Local-first:** Studio only binds to loopback.
- **Parse, never execute:** recordings are inspected through Python's AST and are never imported or run.
- **Protected by default:** typed values and text/value assertions become environment variables.
- **Echo-aware:** a protected input repeated in later selector or result text is replaced with the same runtime variable.
- **Fail closed:** dynamic values, nested locator scopes, control flow, multiple tests, missing assertions, unknown selector modifiers, and unsupported calls stop compilation.
- **Review before mutation:** all clicks, key presses, fills, selections, and check operations are review-gated. Publish, send, buy, submit, delete, and similar actions are approval-gated.
- **Proof required:** a workflow without an executable assertion is rejected.
- **Portable:** the generated pytest does not depend on Flow2Skill at runtime.

## Install

Prerequisites:

- Python 3.10–3.13;
- Node.js with `npx`, required only for recording;
- Chromium installed through Playwright, required for default replay.

```bash
python -m pip install flow2skill
python -m playwright install chromium
flow2skill doctor
flow2skill studio
```

From a source checkout:

```bash
python -m venv .venv
# Windows
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe -m playwright install chromium
.venv\Scripts\flow2skill.exe doctor
.venv\Scripts\flow2skill.exe studio
```

Windows source checkouts also include `launch-flow2skill.bat`. Installed wheels use the `flow2skill` console command; the batch launcher is not installed by the wheel.

Studio opens at `http://127.0.0.1:8765` and writes bundles to `~/Flow2SkillWorkspaces` unless another root is supplied.

## Thirty-second proof

Generate an entirely local sample bundle:

```bash
flow2skill demo --out ./flow2skill-demo
```

Run it against Flow2Skill's packaged local fixture:

```bash
# macOS/Linux
F2S_LABEL_API_TOKEN_1="runtime-demo-token" \
FLOW2SKILL_LIVE=1 \
FLOW2SKILL_ALLOW_SIDE_EFFECTS=1 \
pytest -q ./flow2skill-demo/test_agent_release_gate.py
```

Windows Command Prompt:

```cmd
set F2S_LABEL_API_TOKEN_1=runtime-demo-token
set FLOW2SKILL_LIVE=1
set FLOW2SKILL_ALLOW_SIDE_EFFECTS=1
pytest -q flow2skill-demo\test_agent_release_gate.py
```

The side-effect flag is required because the proof fills a synthetic API-token field and clicks a validation button, even though the packaged fixture is local and harmless. The captured synthetic value is absent from the generated bundle.

## Record a workflow

```bash
flow2skill record https://example.com ^
  --name "Documentation search" ^
  --success-text "Results"
```

Complete the flow in Playwright Inspector, capture an assertion or provide `--success-text`, and close the recorder window. Flow2Skill compiles the capture and deletes its temporary raw codegen and recorder log.

Recording uses the pinned Playwright codegen version declared by this package. `flow2skill doctor` verifies version alignment and prerequisites.

## Compile existing codegen

```bash
flow2skill compile recording.py \
  --name "Documentation search" \
  --intent "Search the docs and prove results appear." \
  --out ./compiled-flow
```

Protection is enabled by default. `--no-protect-inputs` preserves non-sensitive literal values and should only be used for known-public test data. Sensitive fields remain protected.

**Input-file contract:** `flow2skill compile` never deletes the file you explicitly provide. Playwright codegen files can contain credentials or typed data; store and remove that source according to your own retention policy.

## Inspect and replay

Dry-run without opening a browser:

```bash
flow2skill inspect ./compiled-flow/flow.json
```

Run the generated standalone proof:

```bash
FLOW2SKILL_LIVE=1 pytest -q ./compiled-flow/test_documentation_search.py
```

Use `FLOW2SKILL_HEADED=1` to watch. Any workflow containing review or approval actions also requires `FLOW2SKILL_ALLOW_SIDE_EFFECTS=1`. Set it only after reviewing the exact generated plan. Use `FLOW2SKILL_CHANNEL=chrome` to replay with an installed Chrome channel instead of Playwright's managed Chromium.

## Supported capture surface

- one synchronous pytest-style test function;
- direct Playwright call statements only;
- `page.goto`;
- role, label, placeholder, text, test-id, title, alt-text, and CSS selectors;
- `.first` and literal `.nth(...)` modifiers;
- `click`, `fill`, `press`, `select_option`, `check`, `uncheck`, and `hover`;
- `expect(...).to_be_visible()`;
- exact `to_have_text()` and substring `to_contain_text()` assertions;
- `expect(page).to_have_url()`;
- `expect(...).to_have_value()`.

Unsupported calls, dynamic expressions, assignments, loops, branches, context managers, nested functions, and multiple tests are rejected. They are never flattened or silently omitted.

## Security and privacy model

### Recording boundary

During live recording, Playwright briefly writes raw Python to a hidden workspace file. Flow2Skill deletes this file and the recorder log on completion, cancellation, launch failure, or compile failure. On startup it removes abandoned raw captures older than one hour. A hard power loss can therefore leave a temporary file until the next cleanup window. Do not record real production credentials when synthetic test credentials are available.

### Manifest integrity

`flow.json` carries a SHA-256 fingerprint. Loading rejects unknown fields, malformed selector types, missing fingerprints, content tampering, stale slugs, and risk-classification downgrades. The fingerprint detects accidental or local modification; it is not a cryptographic signature from a trusted publisher.

Runtime variables are restricted to the compiler-owned `F2S_*` namespace. Captured `${VARIABLE}` syntax is rejected instead of being allowed to read arbitrary host environment variables. Navigation containing protected values is approval-gated because it can transmit the value to the destination.

Common credential-bearing URL parameters, including JWT, token, signature, session, authentication, and password fields, are replaced with runtime variables. Recompiling into an existing managed bundle directory removes obsolete generated `test_*.py` files so stale executable proofs cannot survive unnoticed.

### Studio boundary

Studio enforces:

- loopback-only binding;
- strict local `Host` validation against DNS rebinding;
- same-origin checks;
- a random per-process request token;
- a one-megabyte JSON body limit;
- strict request field types;
- workspace and artifact path containment;
- no-store and browser hardening headers.

### Approval boundary

Flow2Skill compiles instructions; it does not grant permission. Generated skills call out approval-gated actions. Generated tests are disabled unless `FLOW2SKILL_LIVE=1` is present. Once live execution is requested, a missing side-effect flag fails the test rather than reporting a successful skip.

See [SECURITY.md](SECURITY.md) for the threat model and reporting process.

## Development

```bash
python -m pip install -e ".[dev]"
ruff format --check src tests scripts
ruff check src tests scripts
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q
python -m build
python -m twine check dist/*
```

The release gate also installs the wheel into a clean environment, runs the packaged demo against a real browser, checks source/wheel contents, and scans tracked files for high-confidence secrets and private paths.

## Intentional boundaries

Flow2Skill does not include cloud sync, a browser extension, shared secret storage, autonomous planning, or LLM selector healing. Those features create a platform before the deterministic compiler has earned it. The current product does one job: preserve a successful browser workflow as a reviewable procedure and executable proof.

## License

MIT
