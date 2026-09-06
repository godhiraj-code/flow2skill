# Changelog

All notable changes to Flow2Skill are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and releases follow semantic versioning.

## [Unreleased]

### Fixed

- Studio fields have associated labels; capture tabs support arrow/Home/End navigation and expose selection. Artifact previews have descriptive names, a named dialog, and keyboard-scrollable content. Errors persist until dismissed or superseded by success; workspace-load failures are shown instead of becoming unhandled rejections.

- Compilation and manifest loading reject incompatible page/element targets and unsupported selector options instead of changing their meaning during export. Boolean locator indices are rejected. Real-browser coverage verifies valid test-id and page URL assertions in CLI replay and standalone proofs.

- CI executes exported README commands in native Windows PowerShell and Command Prompt, and bash on Linux, against the built wheel. Tests require a real passing browser proof and exercise bundle paths containing spaces.

- Exported README and skill share complete standalone verification instructions, pinned Playwright setup, browser installation, runtime inputs, action gates, and bash/PowerShell/Command Prompt examples. Generated documentation no longer claims compilation proves a successful execution.
- Studio browser verification executes the generated README commands to detect documentation drift.

- Release verification requires the GitHub tag, wheel/source metadata, and source runtime version to agree; duplicate or incorrect package artifacts are rejected before publishing.
- Release builds run the installed-package Studio and real-codegen integration tests.

- Doctor supports compile/record/replay scopes and JSON output, reports per-check repair steps, handles missing package metadata, and distinguishes optional pytest from required dependencies.

- POSIX recorder cancellation stops its dedicated process group, including children left behind by an exited npm wrapper, before removing captures. Failed Windows tree termination is reported as unconfirmed.
- Status finalization and cancellation are serialized; cancelling an already completed job preserves its result.

- Compiler accepts the exact service-worker-blocking context fixture emitted by Flow2Skill’s pinned recorder. Replay and standalone proofs retain that setting; unknown fixture behavior is rejected.
- Browser CI runs the actual headless codegen process, compiles its output, checks temporary capture cleanup, and executes the generated proof.

- Studio preserves active recorder controls during transient status failures, retries with bounded backoff, and prevents duplicate starts while a request is pending.
- Browser CI exercises Studio demo export, artifact preview, executable proof, and recorder status recovery against the installed wheel.

- Recording and Studio now default to managed Chromium, matching installation instructions and doctor checks; explicit browser channels remain supported.
- CLI replay and exported tests close resources even when context/page creation fails, without replacing the original error with cleanup failures. Failed screenshots no longer mask replay errors.

- CLI replay and generated tests check every required runtime variable before opening the browser, avoiding partial execution when a later input is missing. Explicit empty strings remain valid; dry runs need no values.

- Added a `test` installation extra so the documented demo includes its pytest dependency.
- Browser CI now verifies the built wheel in an isolated environment without a source checkout or development dependencies. Release verification uses the same public extra.
- Development setup installs into the newly created virtual environment.

## [0.1.0] - 2026-07-30

### Added

- Local Studio for recording, compiling, inspecting, and previewing workflows.
- Strict AST compiler for synchronous Playwright Python pytest recordings.
- Fingerprinted JSON and YAML workflow contracts.
- Portable `SKILL.md` generation.
- Standalone Playwright/pytest proof generation.
- Default environment protection for typed and asserted values.
- Echo-aware protection for captured values repeated in selector and result text.
- URL credential, secret-query, and secret-fragment redaction.
- Review and approval classification for mutating browser actions.
- Recorder cancellation and stale-capture cleanup.
- Loopback, Host, Origin, token, body-size, and path-containment protections.
- Executable packaged local demo and Windows launcher for source checkouts.
- `flow2skill doctor` prerequisite validation.

### Security

- Recorded Python is parsed but never imported or executed.
- Dynamic expressions, control flow, multiple tests, unsupported calls, malformed manifests, fingerprint changes, and risk downgrades fail closed.
- Unknown call arguments, foreign environment placeholders, and templated navigation without approval fail closed.
- Overlapping protected values are replaced longest-first so credential suffixes cannot leak.
- Nested locator scopes, unknown selector modifiers, and untrusted source metadata fail closed.
- JWT-bearing URL parameters are protected and obsolete generated tests are removed on bundle refresh.
- Workflow compilation requires at least one executable assertion.
- Live generated proofs fail nonzero rather than false-green skipping when review permission is missing.

[Unreleased]: https://github.com/godhiraj-code/flow2skill/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/godhiraj-code/flow2skill/releases/tag/v0.1.0
