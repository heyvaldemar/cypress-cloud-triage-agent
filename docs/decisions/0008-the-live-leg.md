# 8. A live leg: a recorded run, read back through the real Cloud MCP

- Status: proposed (the probe is built; the forward is written from what it reads, and nothing here is claimed until a run proves it)
- Date: 2026-11-11

## Context

On kind the door in front of Cloud MCP (ADR 2) serves runs from files written from a real Cypress run. ADR 2 left the live forward to `mcp.cypress.io` for later: one tool function per granted tool, the token as a Secret in the door's namespace, and an egress rule to Cypress, reviewed together.

Cypress's page for Cloud MCP ([docs.cypress.io, Cloud MCP](https://docs.cypress.io/cloud/integrations/cloud-mcp), read 2026-11-11) names the fourteen tools, the URL, the transport (streamable HTTP) and the header a personal access token goes in. It does not publish the tools' arguments or their answers, or which protocol version the server speaks. A forward written from the page would be a guess at all three, and the door's scope check depends on two of them: which argument names the run, and whether the answer is about that run only.

Reaching Cypress Cloud also breaks a rule the rest of this repository keeps: nothing outside the repository's own CI is touched to test a change (AGENTS.md). One workflow has to be the exception, and it has to be one that cannot leak what it reads.

## Decision

`.github/workflows/live.yml` is the one workflow that reaches Cypress Cloud. It runs on main only: the record key, the Cloud MCP token and the project ID are secrets of the `cypress-cloud` environment, whose branch policy is `main`. The project ID is a secret too, so GitHub masks it, and with it every run URL, wherever a log would print it.

1. **The probe (built).** The spec runs in the same pinned image as the e2e, with `--record` (`scripts/live.sh record`); two of its three tests fail, one of them with the customer's note in its message. `scripts/live-probe.py` then asks the real Cloud MCP about that run with the door's own client (`manifests/cloud-mcp/upstream.py`): the protocol version, the tool list with each input schema, and an answer from each of `cypress_get_runs`, `cypress_get_failed_tests`, `cypress_get_tests` and `cypress_get_specs`, with arguments built from each tool's schema. The job's log and summary show the protocol, the schemas, the argument names used and each answer's shape with every value replaced by its type, and whether the note's address came back. The answers themselves can name the project and the people on it, so they go only into an artifact encrypted to a certificate passed when the run is dispatched, kept for one day; without a certificate they are deleted.
2. **The client (built).** The token is sent to one URL, over TLS, in the Authorization header. The client follows no redirect: Python's `urllib` follows a 302 for a POST as a GET to wherever it points and keeps the Authorization header on it, which a unit test showed by catching the token at the redirect's target. It uses no proxy from the environment, never reads the body of an error answer, which a server may use to quote the request, and never puts the token in an exception or its repr. It keeps the cookies its one URL sets, since `mcp.cypress.io` sits behind a load balancer that pins a client to a backend with one. It speaks 2026-07-28 where the server does and falls back to the initialize handshake and its session otherwise. `tests/unit/test_upstream.py`, with two plants.
3. **The forward (next, from the probe's answers).** The door forwards the agent's two granted tools and nothing else. The token is a Secret in `cloud-mcp` only, mounted as a file into the door. The door builds the upstream arguments from the run it scoped the caller to, never from the caller's arguments, and refuses an answer that is not about that run. The same caps and control-character stripping apply. The door's egress is DNS and Cypress's addresses on 443, resolved at install. The stand-in agent, the control (ADR 4), triages the recorded run; every refusal the kind e2e checks is checked again, and Slack's facts line carries Cloud's counts.

Each live run records three test results. Cloud MCP allows 100 tool requests an hour per user; a live run uses about ten.

## Consequences

- The door's forward is written against Cloud MCP's real arguments and answers, and a change on Cypress's side shows up as a red probe, not as a door that quietly reads the wrong run.
- A public log of the live leg shows no project, no run URL and no answer. Reading the answers needs the private key of the certificate the run was dispatched with.
- The live claims stay unmade until step 3's run on main: the README and the threat model keep saying the live Cloud MCP is not exercised.
- The record key and the token are personal to the maintainer's account, and the token reaches every project that account can see. Only main can use them, and only this workflow names them.

## Alternatives

- **Probe from a laptop with the token.** The token would leave GitHub's secrets for a shell, and the probe would not be repeatable by anyone reading the repository.
- **Print the answers in the job's log.** The repository is public, and so are its logs and artifacts.
- **Write the forward from the docs.** They publish no arguments or answers, and the scope check rests on both.

## How to explain this to a customer

Before the triage door talks to your real Cypress Cloud, we ask Cloud what it actually answers, once, with your own token, inside your CI. Nothing it says is printed where others can read it. The door is then built from those answers, so it reads exactly the run it was started for, and the token never reaches the agent.
