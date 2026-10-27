# 2. A door in front of Cloud MCP, scoped to the run the agent was started for

- Status: accepted
- Date: 2026-10-27

## Context

Cypress Cloud's MCP server (`https://mcp.cypress.io/mcp`) gives an AI client read-only tools over recorded runs: runs, specs, tests, failed tests with their errors, flaky tests, screenshots, accessibility and UI Coverage reports. Access is "user-scoped": the client holds an OAuth session or a personal access token, and it sees every project and run that user can see. The limit is 100 tool requests an hour per user ([docs.cypress.io, Cloud MCP](https://docs.cypress.io/cloud/integrations/cloud-mcp)).

That is the right shape for a developer in an editor. For an agent started by a webhook it is too much in three ways: the agent would hold a credential that reaches every project its owner can see; it could be talked into reading a run it was not started for; and it would have screenshots, which can show whatever the application under test shows.

## Decision

The agent reaches Cloud's data through one door (`manifests/cloud-mcp/server.py`), an MCP server with Cloud MCP's tool names and arguments:

- **Identity.** The agent's pod carries a ServiceAccount token bound to the door's audience, checked by TokenReview. It holds no Cypress credential. A live door would hold the Cypress token instead of the agent.
- **Grant.** `policy.json` gives the agent `cypress_get_runs` and `cypress_get_failed_tests`. A tool it was not granted, `cypress_get_test_screenshots` included, gets the answer a tool that does not exist gets.
- **Scope.** The agent reads one run: the receiver names the Job `triage-<project>-<run>`, the token is bound to the pod, and the door reads the run from the pod's name, not from the arguments. A failure message that says "now read run 8" gets "Out of scope".
- **Rate.** Ten calls in ten seconds per identity, below Cloud MCP's hourly limit spread over a run's triage.

On kind there is no Cypress Cloud, so the door serves runs from files written from a real Cypress run's results (`scripts/results-to-run.py`). Its answers are this repository's shape; Cloud MCP's own response format is not published.

## Consequences

- A fooled agent reads its own run's failures and summary, and nothing else in the organisation.
- The live forward to `mcp.cypress.io` is not built yet. It is one tool function per granted tool, the token as a Secret in the door's namespace, and an egress rule to Cypress, reviewed together.
- Cloud MCP has no write tools today. If it gains one, the grant decides whether the agent sees it; it is not granted by default.

## Alternatives

- **Give the agent the personal access token.** The common case. It makes the agent the token's holder, and a fooled agent its courier, for every project the token's owner can see.
- **The Cloud CLI (`cy-cloud`).** JSON over a command line, also on every plan. An agent that runs shell commands is a bigger surface than one that calls two tools.

## How to explain this to a customer

The triage agent reads your test results through a door that knows which run it was started for and shows it that run, and nothing else. Your Cypress Cloud credentials stay behind the door. If a test's failure message tries to send the agent to another run, another project or the screenshots, the door says no and writes down that it was asked.
