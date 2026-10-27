# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- The triage stack on kind: a `run.completed` receiver (`manifests/receiver/`), a door in front of Cloud MCP's tools (`manifests/cloud-mcp/`), a notifier as the agent's one way out (`manifests/notifier/`), a stand-in triage agent fooled on purpose (`manifests/agent/`), admission that holds every triage Job to the receiver's template, network policies and the agents' own resolver.
- A Cypress 16.2.0 spec against an orders page with two bugs, one of them showing a customer's note in a failure message (`app/`, `cypress/`), run in the pinned `cypress/included` image by the e2e.
- The CI trigger (`scripts/send-run.py`): the `run.completed` body Cypress Cloud sends, built from the run's results (`scripts/results-to-run.py`), signed the same way, for projects without webhooks.
- `tests/e2e/run.sh` (91 checks on kind), `tests/e2e/webhook.py` (eleven refused deliveries and three replays), `tests/e2e/plants.py` (twenty refused Jobs and the admitted one), unit tests for the three doors (49), and eighteen planted violations for the static checks.
- Five decision records and a threat model.

### Found while building it

- Only the timestamp and the body of a Cypress webhook are signed. A replay keyed on `X-Cypress-Event-Id`, as the docs suggest for idempotency, can be sent again with a new ID; the receiver keys replays on the signature and runs on the signed body. The event type is a header too, so the body decides it.
- The docs' Node example computes `Number(timestamp)`, and a `NaN` passes its five-minute check. The receiver takes digits only.
- The first e2e run summarised 62 of its 91 checks: four steps fed their verdicts through a pipe, where a failure would not reach the exit status. Fixed before the first commit, and shown red by a deliberately broken check.

[Unreleased]: https://github.com/heyvaldemar/cypress-cloud-triage-agent/commits/main
