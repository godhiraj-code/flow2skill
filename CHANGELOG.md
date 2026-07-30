# Changelog

All notable changes to Flow2Skill are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and releases follow semantic versioning.

## [Unreleased]

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
