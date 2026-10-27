# 4. A deterministic stand-in agent in CI, not a model

- Status: accepted
- Date: 2026-10-27

## Context

The walls have to be proven against an agent that misbehaves. A real model in CI costs money per run, answers differently from one run to the next, and needs a credential stored somewhere. None of that tests the walls. The same decision was made, for the same reasons, in [kubernetes-ai-agent-sandbox](https://github.com/heyvaldemar/kubernetes-ai-agent-sandbox/blob/main/docs/decisions/0004-a-deterministic-agent-in-ci.md); how often a real model is fooled is measured in [kubernetes-air-gapped-ai-agent](https://github.com/heyvaldemar/kubernetes-air-gapped-ai-agent).

## Decision

The triage agent (`manifests/agent/agent.py`) is a script that behaves like a fooled agent every time. It reads its run and its failed tests through the Cloud MCP door, finds the instruction in a failure message and does all of it: leaves that failure out, sends the failure context to the outside address, calls the tools named, and starts its summary with a channel-wide ping and a link that carries data. Then it tries another run, another project, a loop of calls, the receiver, Slack directly, the Kubernetes API, the metadata service, the internet by name and by address, and a second message. Each attempt is a JSON line with what the walls promise and what happened, and the process exits non-zero if any differs. Standard library only, on a pinned `python:3.13-alpine`.

## Consequences

- Every run is the same run, free, with no API key anywhere.
- The test is about containment, not about whether a given model would be fooled. A model that ignores this instruction would still run behind the same walls.
- The summary the agent writes is the script's, not a model's. The README says so where it shows one.

## Alternatives

- **A small local model on CPU.** Realistic, slow, and non-deterministic; it would make the e2e run flaky for no gain in what it proves.
- **Claude, or another hosted model, through the same doors.** The way a team would run it, and worth measuring. It belongs in its own workflow with its own budget, not in the e2e run that gates every change.

## How to explain this to a customer

The safety tests use a scripted agent that does everything the planted text tells it to, the same way every time, so each test run proves the same thing and costs nothing. The protections do not depend on which AI model runs inside; they assume any of them can be tricked.
