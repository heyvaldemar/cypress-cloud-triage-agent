# Decisions

Each record states the context, the decision, what it costs, what was considered instead, and how the choice would be explained to a customer.

| # | Decision | Status |
|---|---|---|
| 1 | [A signed run.completed as the trigger, sent by the CI job until Cypress Cloud sends it](0001-a-signed-run-completed-as-the-trigger.md) | accepted |
| 2 | [A door in front of Cloud MCP, scoped to the run the agent was started for](0002-a-door-in-front-of-cloud-mcp.md) | accepted |
| 3 | [The notifier writes the facts; the agent's words are quoted and defused](0003-the-notifier-writes-the-facts.md) | accepted |
| 4 | [A deterministic stand-in agent in CI, not a model](0004-a-deterministic-agent-in-ci.md) | accepted, amended by 6 |
| 5 | [Admission holds every triage Job to the receiver's template](0005-admission-holds-the-triage-job.md) | accepted |
| 6 | [Open models on CPU beside the stand-in, behind the same walls](0006-open-models-beside-the-control.md) | accepted |
| 7 | [The specs run as Jobs in the cluster, walled like the agent](0007-spec-jobs-walled-like-the-agent.md) | proposed, layer 2 (December 2026) |
| 8 | [A live leg: a recorded run, read back through the real Cloud MCP](0008-the-live-leg.md) | accepted, amended by 9 |
| 9 | [A live delivery: Cypress Cloud's own run.completed, through a tunnel open only while the job runs](0009-a-live-delivery-through-a-tunnel.md) | proposed, until the first live delivery |
