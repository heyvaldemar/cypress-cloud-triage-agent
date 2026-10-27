# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

_(no unreleased changes yet)_

## [1.0.0] - 2026-10-27

### Added

- The triage stack on kind: a `run.completed` receiver (`manifests/receiver/`), a door in front of Cloud MCP's tools (`manifests/cloud-mcp/`), a notifier as the agent's one way out (`manifests/notifier/`), a stand-in triage agent fooled on purpose (`manifests/agent/`), admission that holds every triage Job to the receiver's template, network policies and the agents' own resolver.
- A Cypress 16.2.0 spec against an orders page with two bugs, one of them showing a customer's note in a failure message (`app/`, `cypress/`), run in the pinned `cypress/included` image by the e2e.
- The CI trigger (`scripts/send-run.py`): the `run.completed` body Cypress Cloud sends, built from the run's results (`scripts/results-to-run.py`), signed the same way, for projects without webhooks.
- `tests/e2e/run.sh` (91 checks on kind), `tests/e2e/webhook.py` (eleven refused deliveries and three replays), `tests/e2e/plants/` (twenty refused Jobs and the admitted one, written from the real template by `tests/e2e/plants.py`), unit tests for the three doors (50), and nineteen planted violations for the static checks.
- Five decision records and a threat model.

### Found while building it

- Only the timestamp and the body of a Cypress webhook are signed; the event ID and the event type are headers beside them. The receiver keys replays on the signature and runs on the signed body, and the body decides the event.
- The docs' Node example computes `Number(timestamp)`, and a `NaN` passes its five-minute check. The timestamp is signed, so only a holder of the secret could send one: a mistake in an example, not a way in. The receiver takes digits only.
- The notifier read a link's host as the text after the last `@` before the first `/`, so `https://example.net?x=@cloud.cypress.io/` and `https://example.net\@cloud.cypress.io/` passed as Cypress links while a browser opens `example.net`. Found by an automated review of the first commit; the new test failed on that code. The host now comes from `urllib.parse.urlsplit`, a link with `@`, `\` or a port other than 443 is broken, and a broken link has every dot broken, so a host after `?` or `#` is too.
- The first e2e run summarised 62 of its 91 checks: four steps fed their verdicts through a pipe, where a failure would not reach the exit status. Fixed before the first commit, and shown red by a deliberately broken check.

[Unreleased]: https://github.com/heyvaldemar/cypress-cloud-triage-agent/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/heyvaldemar/cypress-cloud-triage-agent/releases/tag/v1.0.0
