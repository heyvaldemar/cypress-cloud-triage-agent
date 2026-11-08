# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- The model leg of the e2e ([ADR 6](docs/decisions/0006-open-models-beside-the-control.md)): Granite-4.0-1B, Qwen3.5-2B and Ministral-3-3B on CPU, three triages each, behind the same walls, doors and admission as the stand-in agent, which stays the control. The models are the flagship's, by its image digests, built reproducibly from the publisher's files (`models/models.lock`, `scripts/models.sh`, `scripts/push-layout.py`) and served by llama-server in a `models` namespace that reaches nothing and answers the agents only (`manifests/models/`). The receiver's Job runs a plain tool-using harness (`manifests/model-agent/agent.py`) that offers Cloud MCP's tools, a shell and a web request, and does what the model asks. Checked: what the walls promise (`tests/e2e/models.sh`). Measured, into `e2e-results` and `status.json`: which of the note's asks each model acted on, its attempts by kind and how each ended, the time, the compute and its price.
- `scripts/check-models.py`, run as `scripts/validate.sh models`, with eight planted violations: the model reaching anything or answering another namespace, the agents' way to it on a second port, a harness tool that opens `file://`, a tool no door stands behind, a harness reading environment admission refuses, a model pinned by name, and the reading below.

### Found while building it

- The first reading of what a model did counted a copy of the note as acting on it. Granite-4.0-1B's first triage, on a laptop, copied the failure message into its summary, `<!channel>` and the address with it, and was reported as acting twice. Copies are taken out before the text asks are read, and the ping counts only at the start (`tests/unit/test_model_reading.py`; the old reading is a plant).
- `kind load image-archive` cannot give a pod a model image by digest: containerd files the archive under its index's digest and a generated name. The model goes to a registry beside the cluster instead, by its manifest digest.

### Fixed

- The e2e run's measured line about the stand-in agent is plain text, `stand-in agent: 26 of 26 attempts ended as expected (kind01 #15)`, instead of a Python dict. The status branch, its badge, heyvaldemar.com/evidence and llms.txt show measured lines as written, and they showed `agent: {'agent': 'triage', ...}`.
- The e2e run brings back its own `kubectl port-forward` to the receiver when it has exited. The port-forward exits when one forwarded connection breaks, and the receiver answers a body over 64 KiB with 413 and closes the connection without reading it. In the scheduled run on 2026-11-02 (41448273584) the tunnel was gone right after that check: the CI trigger's delivery met `Connection refused`, and the 11 checks after it failed while the stack itself was untouched. `run.sh` now checks the tunnel before each batch of requests from the runner and logs a note when it had to start it again. No check, refusal or expectation changed. Checked on a branch (41571217882, 91 checks, none failed) and with the tunnel killed on purpose before the CI trigger (41571220251: the note, then 91 checks, none failed).

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
