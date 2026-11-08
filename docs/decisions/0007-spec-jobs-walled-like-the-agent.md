# 7. The specs run as Jobs in the cluster, walled like the agent

- Status: proposed (layer 2, to be built in December 2026; nothing here is claimed until a run proves it)
- Date: 2026-11-08

## Context

In layer 1 the CI job runs Cypress, and its last step signs the `run.completed` body with the webhook secret (ADR 1). Layer 2 moves the spec run into the cluster: one Kubernetes Job per run, the official `cypress/included` image by digest, so the cluster that triages a failure also produced it.

A spec is code, and whoever opens a pull request writes it. A spec Job runs that code with whatever the Job holds and can reach. The obvious way to build it, the CI job's steps moved into a pod, would put the webhook signing secret in a pod that runs a stranger's code: a spec could then sign deliveries for runs that never failed, and read the Cypress record key on the way.

## Decision

The spec Job gets the same treatment as the triage Job: a template, admission that holds it to the template, and walls that make what it can reach a short list. What it may hold is less than the CI job held.

1. **What runs.** A Job in a `specs` namespace from a fixed template: `cypress/included` by digest, a fixed command, the specs and the application under test of one commit mounted read-only as an image volume by digest, built the way the models are (reproducibly, `models/Dockerfile` style), so the bytes that ran are named. Pod Security `restricted`; whatever Cypress needs to write goes to `emptyDir`.
2. **Admission.** A ValidatingAdmissionPolicy bound to the `specs` namespace's label, like ADR 5's: only Jobs; that image, that command, no arguments; volumes only the commit's image, `emptyDir` and one projected token; no Kubernetes API token; limits, a deadline and no retries; the environment from an allowlist. Plants in `tests/e2e/plants/` as for the triage Job, each refused for its own reason, the template's own Job admitted.
3. **What it can reach.** The application under test's Service, and its own results door. When a run is recorded to Cypress Cloud, also Cypress Cloud's API through an egress proxy that allows Cypress's hostnames only, with a resolver that answers those names and the cluster's and relays nothing else (as `agent-dns` does for the agents). Nothing else, by NetworkPolicy, and the receiver, Slack, the doors and the models refuse the `specs` namespace on their own side as well.
4. **What it holds.** Never the webhook signing secret. The spec Job hands its results to a results door in the `triage` namespace, with a projected token for that door's audience. The door names the run from the Job's own name, as the Cloud MCP door scopes the agent to its pod's run, builds the `run.completed` body from the results, and signs it with the secret that stays in `triage`. A spec can at most report its own run's results, and those are Cypress's own. The record key, when recording, is the one secret in `specs`: Cypress reads it from the environment, so a spec can read it too, and the threat model says so.
5. **What the triage then sees.** Exactly what layer 1 sees: a signed `run.completed` for one run, its failure messages, and the walls from ADR 2 to ADR 6 around whichever agent reads them.

## Consequences

- The secret that starts a triage never shares a pod with test code.
- A malicious spec can still fail its own run on purpose and write a note into its failure message. That is the attack layers 1 and 2 are built for, and the triage side already assumes it.
- The record key is exposed to the specs, as it is in any CI job that records. It allows writing runs to one project, not reading them; it is rotated in the project's settings.
- The results door is a new door, with its own unit tests and its own plants, before any spec Job is admitted.

## Alternatives

- **The CI job's steps in a pod.** Fewer pieces; the signing secret beside a stranger's code.
- **Specs from a ConfigMap.** Easy to build, but nothing names the bytes that ran, and a ConfigMap is edited in place. An image by digest is the same content every time it is named.
- **Keep Cypress in CI for good.** Fine for most teams. The point of the layer is a cluster that can show, by its own records, which code produced the failure its agent triaged.

## How to explain this to a customer

The tests themselves run in the cluster, in a locked box like the agent's. The box can reach the application being tested and, if you record to Cypress Cloud, Cypress's servers, and nothing else. It never sees the key that tells the agent to start: the cluster checks the results and signs them itself, so a test written to cause trouble can report only its own run.
