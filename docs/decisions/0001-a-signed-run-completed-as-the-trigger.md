# 1. A signed run.completed as the trigger, sent by the CI job until Cypress Cloud sends it

- Status: accepted
- Date: 2026-10-27

## Context

A triage agent should start when a run fails, without anyone asking it to. Cypress Cloud sends a `run.completed` webhook when a run finishes, signed with a secret set on the webhook: `X-Cypress-Signature` is `sha256=` and the hex HMAC-SHA256 of `<X-Cypress-Timestamp>.<raw body>` ([docs.cypress.io, Webhooks](https://docs.cypress.io/cloud/integrations/webhooks)). Webhooks are not on the free Starter plan; on 2026-10-27 the pricing page listed them for Team and above. Cloud MCP, which the agent reads failures through, is on every plan.

Whatever starts a triage is a door anyone on the internet can knock on. Reading the docs closely, three things matter more than the HMAC itself:

- **Only the timestamp and the body are signed.** `X-Cypress-Event`, `X-Cypress-Event-Id`, `X-Cypress-Event-Version` and `X-Cypress-Request-Id` travel beside them, unsigned. Whatever the receiver decides on, it decides on the signed part: which event this is, which run, and whether it has seen this delivery before.
- **The event type is a header.** A captured `run.accessibility.completed` delivery with `X-Cypress-Event: run.completed` still verifies.
- **The docs' Node example coerces the timestamp.** It rejects a request when `Math.abs(Date.now() / 1000 - Number(timestamp)) > 300`, and `Number("NaN")` makes that comparison false. The timestamp is signed, so only a holder of the secret could send one: a mistake in an example, not a way in. The receiver here takes digits only, because a check that passes on input it cannot read will pass on the next thing it cannot read too.

## Decision

The receiver (`manifests/receiver/receiver.py`) checks in this order, and the first failure answers:

1. The signature, in constant time, before anything else is read.
2. The timestamp is digits and within 300 seconds of now, in either direction.
3. The signature has not been seen before. The signature binds the timestamp and the body, so it is the replay key; `X-Cypress-Event-Id` is logged as claimed and never trusted.
4. The body decides the event: a body with an `accessibility` or `uiCoverage` object is not a `run.completed`, whatever the header says. The project ID must be one this door serves, the run number a positive integer, the status one Cypress sends, and the run URL the one the project and number make.
5. Once per run, keyed on the signed project ID and run number. A retry with a new timestamp is answered 200 and starts nothing. The Job's name is the same key, so the API server refuses a second Job after a restart that forgot the first.

Until the project is on a plan with webhooks, the last step of the CI job that ran Cypress sends the same body, signed the same way (`scripts/send-run.py`, with the secret on stdin). The receiver cannot tell the two apart. When webhooks arrive, the step is deleted and the secret is set on the webhook instead.

## Consequences

- One code path for both triggers. The e2e run exercises the one the project has today; the day it gets a webhook, nothing in the cluster changes.
- The live webhook is not claimed. This repository verifies the receiver against the body and signature the docs describe and a body built from a real Cypress run (`scripts/results-to-run.py`), not against a delivery from Cypress Cloud. That claim waits for a run on a paid plan.
- The CI trigger puts the signing secret in the CI system's secrets. Anyone who can run a step there can start a triage, which is no more than they can do by pushing a failing test.
- The replay cache and the started set are in memory, in one replica. A restart forgets them; the Job name still stops a second triage of the same run.

## Alternatives

- **Key replays on `X-Cypress-Event-Id`.** It identifies Cypress's own retries, which keep it, but the signature does not cover it, so it cannot be what stops a delivery from being sent twice. Both the e2e and the unit tests send a replay with a new ID.
- **Poll Cloud for new runs.** No public door, but a credential on a schedule and a delay, and Cloud MCP's limit of 100 tool requests an hour is shared with the agent.
- **Run the agent inside the CI job.** No door at all, but then the agent runs where the repository's secrets and write token are, which is the opposite of what the walls here are for.

## How to explain this to a customer

A test run that fails starts the triage on its own. Only a message signed with your secret can start one, and only once per run: a copy of a real message, sent again by someone who saw it, is turned away, even if they change the parts of it that are not signed. Until your Cypress plan sends these messages itself, your CI job sends the same one, and the day the plan does, you delete one step.
