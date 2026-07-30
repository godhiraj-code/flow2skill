# Security Policy

## Supported versions

Security fixes are applied to the latest released minor version of Flow2Skill.

| Version | Supported |
| --- | --- |
| 0.1.x | Yes |
| Earlier | No |

## Report a vulnerability

Use GitHub's **Private vulnerability reporting** for this repository. Do not open a public issue containing credentials, raw Playwright recordings, private URLs, or an exploit that has not been fixed.

Include:

- affected Flow2Skill version and operating system;
- the smallest safe reproduction;
- expected and observed behavior;
- whether a raw recording, generated artifact, Studio endpoint, or replay boundary is involved.

You should receive an acknowledgement within seven days. A fix and disclosure timeline will be coordinated based on severity.

## Threat model

Flow2Skill treats all recordings and workflow manifests as untrusted input.

Security boundaries include:

- AST parsing without importing or executing recorded Python;
- a strict allowlist of portable Playwright calls;
- fail-closed rejection of nested locator scopes and unknown selector modifiers;
- default protection of typed and asserted values;
- correlation and replacement of protected values echoed in later selector text;
- URL credential, query-secret, and fragment-secret redaction;
- compiler-owned `F2S_*` runtime variables; captured placeholder syntax is rejected;
- fingerprint and schema validation for manifests;
- review or approval gates for mutating browser actions;
- loopback-only Studio binding, local Host validation, same-origin enforcement, and request tokens;
- deletion of recorder-owned raw captures and logs.

## Non-goals and residual risks

- A SHA-256 manifest fingerprint detects modification but does not authenticate an author.
- Selector text and public page labels are preserved and may reveal application structure.
- A hard crash can leave a temporary raw recording until startup cleanup removes files older than one hour.
- `flow2skill compile recording.py` does not delete the user-supplied source file.
- Environment variables can be exposed by the host process, shell history, CI logs, or malicious test code. Flow2Skill does not provide a secret store.
- Navigation containing a runtime value is approval-gated because replay sends that value to the destination.
- Bundle output directories are Flow2Skill-managed: refreshing a bundle removes obsolete generated `test_*.py` files but preserves unrelated filenames.
- Generated tests drive a real browser. Review the plan and target environment before enabling live replay.
