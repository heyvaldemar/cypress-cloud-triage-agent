# 9. A live delivery: Cypress Cloud's own run.completed, through a tunnel open only while the job runs

- Status: accepted (first live delivery [43513818839](https://github.com/heyvaldemar/cypress-cloud-triage-agent/actions/runs/43513818839), 2026-11-13)
- Date: 2026-11-13

## Context

ADR 1 built the receiver for a `run.completed` signed the way Cypress documents it, and let the CI job send that delivery until the project had webhooks: "When webhooks arrive, the step is deleted and the secret is set on the webhook instead." The maintainer's project can now add a webhook. What it lacks is a public HTTPS URL: the receiver runs in a kind cluster inside a CI job, and the job is the only place it runs.

Cypress's webhooks page ([docs.cypress.io, Webhooks](https://docs.cypress.io/cloud/integrations/webhooks), read 2026-11-13) says what a delivery is: a POST to the webhook's Payload URL with `Content-Type`, `X-Cypress-Event`, `X-Cypress-Event-Id`, `X-Cypress-Event-Version`, `X-Cypress-Request-Id`, `X-Cypress-Timestamp`, `X-Cypress-Idempotency-Key`, and `X-Cypress-Signature` when a secret of at least 16 characters is set. A delivery is retried, up to ten attempts with backoff, after a network error, a 408, a 429 or a 5xx; another 3xx or 4xx is not retried.

Three ways to give the receiver a public URL:

- a server of our own, always on, which is a door to defend on the days no run needs it;
- a quick tunnel, which gets a new random hostname every run, while the webhook's URL is set by hand in Cypress Cloud;
- a named tunnel with a fixed hostname, whose connector runs only inside the job.

## Decision

A named Cloudflare tunnel, on a zone of the maintainer's that serves nothing else, with its connector, `cloudflared`, run inside the live job and nowhere else. `cloudflared` is pinned in `tunnel.lock`, by version and sha256, in `tools.lock`'s format; only the live job installs it. The tunnel's token (`CLOUDFLARE_TUNNEL_TOKEN`), its hostname (`WEBHOOK_HOST`) and the webhook's signing secret (`CYPRESS_WEBHOOK_SECRET`) are secrets of the `cypress-cloud` environment, which only main may use. GitHub masks the hostname in the logs, the kept logs have it taken out, and this repository names it nowhere.

1. **The order of the job.** With all three secrets set, the stack comes up first: the receiver takes the webhook's secret in place of the one `scripts/install.sh` generated (`scripts/live.sh signing`), the door is pointed at Cloud MCP, and the tunnel is opened (`scripts/live.sh tunnel`). Only then is the spec recorded, so Cypress Cloud's delivery finds the receiver up. With none of the three secrets set, the job runs as ADR 8 has it. With some but not all, it fails.
2. **The tunnel.** The token goes to a file only the job's user can read, and `cloudflared` reads it with `--token-file`, never from a command line. The tunnel ends at `tests/e2e/tap.py` on the runner, which hands each request to the receiver unchanged, through a port-forward, and answers with the receiver's answer. It decides nothing. It keeps each delivery in a file of the job's own, mode 600, outside `out/`, and that file is never uploaded: a real delivery names the project and the commit. The job goes on only when the public URL answers with the receiver's health check. When a redirect answers instead, the job says that the zone's forwarding rule catches the hostname.
3. **The trigger.** Cypress Cloud's delivery starts the triage, and the CI trigger sends nothing. `tests/e2e/run.sh` with `DELIVERY` set finds the delivery for the recorded run among what the tap kept, within 300 seconds, and checks two things: that it started a triage (202), and that it came as Cypress documents it. Then the delivery is sent again at the public URL. Sent as is, and with a new `X-Cypress-Event-Id`, it gets 409, replayed. Without its signature, or with a byte added to its body, it gets 401. None of those starts a Job. Step 3's other refusals and replays run against the receiver as before, signed with the webhook's secret.
4. **The Jobs.** Every Job in the cluster must be one the receiver started for a run Cypress Cloud delivered, and one of them must be the recorded run's. A run recorded while no tunnel was up, such as a probe's, gets its delivery retried. A retry can arrive during a later run, and the receiver starts its triage too.

`tests/unit/test_delivery.py` holds the tap and the delivery check to this, with three plants. On kind, with `scripts/send-run.py` standing in for Cypress Cloud and sending through the tap, the webhook mode ran whole, and a stand-in that sent the delivery unsigned failed it.

The first live run with the webhook, [43513818839](https://github.com/heyvaldemar/cypress-cloud-triage-agent/actions/runs/43513818839) on main, passed all 110 checks. Cypress Cloud's delivery for the recorded run arrived as documented: `run.completed`, version 1, signed over its timestamp and body. Its body passed the receiver's checks as they stand, written from the docs, and it started the triage. The four copies sent again at the public URL were refused, and the triage read the run through the real Cloud MCP like the CI trigger's did.

## Consequences

- The receiver does not change (ADR 1). What Cypress Cloud sends is checked the way the CI trigger's delivery was, and a difference between Cypress's real body and the documented one shows up as a refused delivery and a red run.
- While the job runs, anyone can reach the receiver at the hostname, and what they get is its refusals. Each run shows those refusals at that same URL. Between runs no connector is up, and the hostname answers with Cloudflare's error for a tunnel with no connector.
- The zone's own forwarding rule must not catch the hostname. A Page Rule pattern `*zone/*` matches every subdomain, and only the highest-priority matching rule applies, so a rule for the hostname with no redirect is placed above it.
- The webhook's secret is in GitHub's secrets and in Cypress Cloud. Anyone who can run a step on main can start a triage, as with the CI trigger.
- When a delivery is not sent, or arrives more than 300 seconds after the run, the run is red. That is the failure a claim about a live webhook has to be able to show.
- The live webhook is claimed with the run that shows it, citing the run, and not before.

## Alternatives

- **A quick tunnel.** It needs no account, but its hostname changes every run, and the webhook's URL is set in Cypress Cloud by hand.
- **A receiver on a server of our own.** It is always on, which means a door to defend when no run needs it, and a machine outside this repository's CI.
- **Keep the CI trigger.** Then the claim stays "verified against the documented payload", and a difference in Cypress's real delivery is never seen.

## How to explain this to a customer

Cypress Cloud itself tells the triage a run failed, with a message signed with your webhook's secret. The address it sends to exists only while your CI job runs, and between runs nothing is listening. Every run sends a copy of that message back to the same public address, unchanged, with a new ID, unsigned and edited, and shows that each copy was turned away.
