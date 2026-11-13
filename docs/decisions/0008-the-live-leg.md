# 8. A live leg: a recorded run, read back through the real Cloud MCP

- Status: accepted (first live run [43038534753](https://github.com/heyvaldemar/cypress-cloud-triage-agent/actions/runs/43038534753), 2026-11-11)
- Date: 2026-11-11

## Context

On kind the door in front of Cloud MCP (ADR 2) serves runs from files written from a real Cypress run. ADR 2 left the live forward to `mcp.cypress.io` for later: one tool function per granted tool, the token as a Secret in the door's namespace, and an egress rule to Cypress, reviewed together.

Cypress's page for Cloud MCP ([docs.cypress.io, Cloud MCP](https://docs.cypress.io/cloud/integrations/cloud-mcp), read 2026-11-11) names the fourteen tools, the URL, the transport (streamable HTTP) and the header a personal access token goes in. It does not publish the tools' arguments or their answers, or which protocol version the server speaks. A forward written from the page would be a guess at all three, and the door's scope check depends on two of them: which argument names the run, and whether the answer is about that run only.

Reaching Cypress Cloud also breaks a rule the rest of this repository keeps: nothing outside the repository's own CI is touched to test a change (AGENTS.md). One workflow has to be the exception, and it has to be one that cannot leak what it reads.

## Decision

`.github/workflows/live.yml` is the one workflow that reaches Cypress Cloud. It runs on main only: the record key, the Cloud MCP token and the project ID are secrets of the `cypress-cloud` environment, whose branch policy is `main`. The project ID is a secret too, so GitHub masks it, and with it every run URL, wherever a log would print it.

1. **The probe (built).** The spec runs in the same pinned image as the e2e, with `--record` (`scripts/live.sh record`); two of its three tests fail, one of them with the customer's note in its message. `scripts/live-probe.py` then asks the real Cloud MCP about that run with the door's own client (`manifests/cloud-mcp/upstream.py`): the protocol version, the tool list with each input schema, and an answer from each of `cypress_get_runs`, `cypress_get_failed_tests`, `cypress_get_tests` and `cypress_get_specs`, with arguments built from each tool's schema. The job's log and summary show the protocol, the schemas, the argument names used and each answer's shape with every value replaced by its type, and whether the note's address came back. The answers themselves can name the project and the people on it, so they go only into an artifact encrypted to a certificate passed when the run is dispatched, kept for one day; without a certificate they are deleted.
2. **The client (built).** The token is sent to one URL, over TLS, in the Authorization header. The client follows no redirect: Python's `urllib` follows a 302 for a POST as a GET to wherever it points and keeps the Authorization header on it, which a unit test showed by catching the token at the redirect's target. It uses no proxy from the environment, never reads the body of an error answer, which a server may use to quote the request, and never puts the token in an exception or its repr. It names itself in the User-Agent, since Cloudflare in front of `mcp.cypress.io` answers Python's own with 403. It keeps the cookies its one URL sets, since `mcp.cypress.io` sits behind a load balancer that pins a client to a backend with one. It speaks 2026-07-28 where the server does and falls back to the initialize handshake and its session otherwise. `tests/unit/test_upstream.py`, with two plants.
3. **The forward (built, from the probe's answers).** Probe run 43035368622 read that Cloud MCP speaks 2026-07-28 with no session, takes `project_id` and `run_number`, answers JSON rows, each naming its project and run, with its hourly limit in `_meta`, and that the failed test's `error_message` carries the whole note. The door forwards the agent's two granted tools and nothing else. The token is a Secret in `cloud-mcp` only, mounted as a file into the door. The door builds the upstream arguments from the run it scoped the caller to, never from the caller's arguments, asks for the fields it serves (not the commit's author, not the Test Replay links), refuses an answer with a row about any other run, keeps an answer 30 seconds per tool and run, and answers in the files' shape, so the agent and the notifier do not change. The same caps and control-character stripping apply. The door's egress is DNS and Cypress's addresses on 443, resolved at install and given to the pod for the name. The stand-in agent, the control (ADR 4), triages the recorded run; `tests/e2e/run.sh` with `LIVE` set checks everything the kind e2e checks, and Slack's facts line carries Cloud's counts. Step 9 checks what only the live door does: every read of Cloud MCP was the door's and for the run, nothing refused went on, the token is in `cloud-mcp` only, the door reaches Cypress on 443 and nothing else, and Cloud MCP refuses another token. The first live run, [43038534753](https://github.com/heyvaldemar/cypress-cloud-triage-agent/actions/runs/43038534753) on main, passed all 104 checks; the triage cost Cloud MCP two requests, and seven more reads were answered from the door's copy.

The live job runs daily and on dispatch; the probe runs by hand, with a certificate. Each records three test results. Cloud MCP allows 100 tool requests an hour per user; a live run uses two.

## Consequences

- The door's forward is written against Cloud MCP's real arguments and answers, and a change on Cypress's side shows up as a red probe, not as a door that quietly reads the wrong run.
- A public log of the live leg shows no project, no run URL and no answer. Reading the answers needs the private key of the certificate the run was dispatched with.
- The live Cloud MCP is claimed with the run that proves it. The live webhook is not: it needs a public HTTPS receiver, which ADR 9 adds to this job as its webhook mode.
- The door's egress is the addresses `mcp.cypress.io` had when the stack came up. They are Cloudflare's; if they move during a run, the door's reads fail and the run is red, which is the right way for it to fail.
- The record key and the token are personal to the maintainer's account, and the token reaches every project that account can see. Only main can use them, and only this workflow names them.

## Alternatives

- **Probe from a laptop with the token.** The token would leave GitHub's secrets for a shell, and the probe would not be repeatable by anyone reading the repository.
- **Print the answers in the job's log.** The repository is public, and so are its logs and artifacts.
- **Write the forward from the docs.** They publish no arguments or answers, and the scope check rests on both.

## How to explain this to a customer

Before the triage door talks to your real Cypress Cloud, we ask Cloud what it actually answers, once, with your own token, inside your CI. Nothing it says is printed where others can read it. The door is then built from those answers, so it reads exactly the run it was started for, and the token never reaches the agent.
