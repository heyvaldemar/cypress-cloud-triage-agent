# 5. Admission holds every triage Job to the receiver's template

- Status: accepted
- Date: 2026-10-27

## Context

The receiver is the most exposed piece: it takes requests from the internet and its token can create Jobs. If it were tricked or replaced, its RBAC alone (`create` on `jobs` in `triage-agents`) would let it run any image, any command, with any Secret in the namespace and any token, for as long as it liked.

## Decision

A ValidatingAdmissionPolicy (`manifests/admission.yaml`), evaluated by the API server itself, holds every Pod and Job in a namespace labelled `triage.heyvaldemar.com/agents` to the shape of the template the receiver fills (`manifests/receiver/job.json`):

- Jobs only; once (`backoffLimit: 0`) and for ten minutes at most.
- The `triage-agent` ServiceAccount, with no Kubernetes API token.
- Two images by digest: the agent's interpreter, and the gate's shell before it. The agent runs `python3 /agent/agent.py` with no arguments; the gate mounts nothing.
- Volumes: the agent's script from the `triage-agent` ConfigMap, and projected tokens for the two doors' audiences, an hour at most. No Secret, no other volume.
- Environment: five names, as plain values; `RUN_PROJECT` a Cypress project ID and `RUN_NUMBER` a run number.
- The agents' own resolver at 10.96.0.53, and no other.

Pod Security `restricted` is on every namespace underneath it. A second policy holds the three doors to a pinned, read-only, limited shape.

## Consequences

- A receiver that is tricked or replaced can start the triage agent on a run, and nothing else. The e2e run shows the API server twenty Jobs made from the real template with one thing changed each, and the receiver's own Job unchanged: twenty refused for the reason each names, one admitted.
- The plants are made from `job.json` by `tests/e2e/plants.py`, so they cannot drift from what the receiver sends.
- Changing the agent's image or its environment is a change to the template and the policy together; `scripts/validate.sh` fails when they disagree.

## Alternatives

- **Trust the receiver's code.** It is the piece most likely to be attacked.
- **A webhook-based policy engine (Kyverno, Gatekeeper).** More expressive, and a webhook that is down either fails closed and stops triage or fails open and stops nothing. A ValidatingAdmissionPolicy has no pod to go down.

## How to explain this to a customer

Even if the public-facing part of this system were taken over, the cluster itself would refuse to let it run anything but the triage agent, on one test run, with its two limited keys, for ten minutes. That rule is enforced by Kubernetes, not by our code.
