# Threat model

What the triage stack defends against when a failing test talks its agent into misbehaving, the control for each threat, and the check that proves it. A control without a check is listed as a residual risk, not as a control.

The checks are `tests/e2e/run.sh` on a fresh kind cluster (every change), and `scripts/validate.sh` with its planted violations (every change).

## The assumption

The agent will be fooled. A Cypress failure message carries whatever the application under test showed: a customer's note, a product name, a translated string, the text of a pull request's commit. An agent that reads it can be talked into acting on instructions it finds there, and no prompt is a reliable defence against that. This stack does not try to stop the agent from being fooled. It decides what a fooled agent can reach and say, and the stand-in agent (`manifests/agent/agent.py`) is fooled on purpose in every run.

## What is protected

| Asset | Why it matters |
|---|---|
| The failure context | test names, errors and application text are what an attacker wants sent out |
| The channel's trust | a summary that hides a failure, pings everyone or carries a link is harm without a network packet |
| The Cypress Cloud credential | it reaches every project its owner can see |
| The webhook signing secret | whoever holds it can start triage |
| The Slack webhook URL | whoever holds it can post as the bot |
| The cluster API and other workloads | a token with the right verbs is the cluster |
| Capacity | a door that starts an agent per request can be made to start thousands |

## Threats, controls and evidence

| Threat | Control | Evidence |
|---|---|---|
| A forged or altered delivery starts a triage | HMAC-SHA256 over the timestamp and body, constant-time, before anything is read (ADR 1) | e2e step 3 and `test_receiver.py`: unsigned, forged, another secret, body or timestamp changed after signing, each refused; none starts a Job |
| A captured delivery is replayed | five-minute window both ways; the signature is the replay key, not the unsigned `X-Cypress-Event-Id` | the same delivery again, and again with a new event ID: 409, no second Job |
| Cypress's retries start the same triage twice | once per run, keyed on the signed project ID and run number; the Job's name is the same key | a retry signed with a new timestamp: 200, no second Job |
| An unsigned header changes what the receiver does | the body decides the event; `X-Cypress-Event` is not read | an accessibility body sent as `run.completed`: ignored; a plant that reads the header is caught |
| A timestamp that is not a number passes the window check | digits only, refused otherwise | a signed `NaN` timestamp: 400 |
| A tricked or replaced receiver starts something other than the agent | admission holds every Job and Pod in `triage-agents` to the template: image, command, script, tokens, environment, resolver, lifetime (ADR 5) | e2e step 2: twenty Jobs made from the real template with one change each, refused for the reason each names; the receiver's own Job admitted |
| The agent sends the failure context to an outside address | nothing leaves a triage pod but DNS to its own resolver and its two doors (`network-policies.yaml`, `agent-dns.yaml`) | the agent follows the planted instruction and is blocked; a pod outside the agents' namespace reaches the same address, so the block is the agents' |
| The agent carries data out in the names it looks up | the agents' resolver answers cluster names only and relays nothing; admission refuses a triage pod that names another resolver | the agent's lookup of an outside name gets no answer; a plant naming the cluster's resolver is refused |
| The agent reads runs or projects it was not started for | the Cloud MCP door reads the run from the agent's pod, not from its arguments (ADR 2) | another run and another project: out of scope, logged with the agent's pod |
| The agent uses tools it was not given | the door's grant: two read-only tools; anything else looks like no tool | `cypress_get_test_screenshots` and `run_shell`, as the planted text asks: refused, logged |
| The agent holds a Cypress, Slack or Kubernetes credential | the agent mounts two short tokens, one per door; the signing secret is in `triage` only and the Slack URL in `notify` only; admission refuses a Secret in a triage pod | the agent lists what it holds; the e2e lists where each Secret exists; plants with a Secret volume or a Secret in the environment are refused |
| The agent hides a failure in its summary | the notifier writes the first line from Cypress's counts, under its own identity (ADR 3) | the agent leaves the planted failure out; the Slack message still says 2 of 3 failed |
| The agent pings the channel or leaks data through a link preview | Slack's control characters escaped, outside addresses broken, previews off | the Slack stand-in's copy has no live outside link, no mention, previews off |
| The agent writes to Slack around the notifier, or starts more triage | the agents' egress has neither; Slack takes the notifier only, and the receiver refuses the agents' namespace | the agent is blocked from both; with an agents' namespace's egress wide open, both still refuse it while the outside answers |
| The agent posts many messages, or loops on the door | one message per run; ten door calls in ten seconds | the second message is refused; the loop is rate limited |
| The agent reaches the Kubernetes API or cloud credentials | no API token, and the egress allowlist | both blocked |

## Residual risks

- **A plain-words lie.** The agent can still write "this failure is a flake" under the facts line. The line above it and the label say otherwise; a reader can still believe the agent.
- **Bare domains.** Slack may link a domain written without a scheme. Previews are off, so nothing is fetched unless a person clicks.
- **No sandboxed runtime.** Triage pods run on the node's kernel. [kubernetes-ai-agent-sandbox](https://github.com/heyvaldemar/kubernetes-ai-agent-sandbox) shows the same agents on gVisor.
- **One replica, memory only.** The replay cache and the started set live in one receiver process. A restart inside the five-minute window forgets which signatures it saw; the Job name still stops a second triage of the same run.
- **The live webhook and the live Cloud MCP are not exercised.** On kind the trigger is the CI job's signed delivery and the door serves a real Cypress run from files. A delivery from Cypress Cloud and a forward to `mcp.cypress.io` need a paid plan and a token, and are not claimed.
- **Slack by name.** NetworkPolicy allows addresses, not names; against the real Slack the notifier's egress is an egress proxy that allows `hooks.slack.com` only. On kind it is the stand-in's Service.
- **The audit trail stays on the node.** Every door writes a JSON line per decision; nothing ships them elsewhere.
- **TLS.** On kind the receiver is reached through a port-forward. A public receiver sits behind an ingress that terminates TLS; the signature does not encrypt the body.
