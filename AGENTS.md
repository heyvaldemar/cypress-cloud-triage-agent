# Working on this repository as an agent

Most of the code here is written by an AI agent, and the decisions and the verification are the maintainer's. This file is what the agent is held to. A reviewer can hold it to the same rules.

## What this repository claims

Every row of the README's table "What is proven, and how" is a check that runs in CI: `tests/e2e/run.sh` on a fresh kind cluster (`.github/workflows/e2e.yml`), or `scripts/validate.sh` with its planted violations (`.github/workflows/verify.yml`). Each refusal is paired with a control that must succeed, so a check cannot pass because the environment refuses everything. A claim with no check is not written, and in `docs/threat-model.md` a control with no check is listed as a residual risk. It is a reference architecture on kind, CPU only, at no cloud cost, and nothing says otherwise: no wording about running anywhere in production or holding a certification (the fleet's conformance check refuses it).

Two claims wait for a paid Cypress plan and a token, and are not made until a run proves them: a delivery from a live Cypress Cloud webhook, and the Cloud MCP door forwarding to `mcp.cypress.io`. Until then the README says the receiver is verified against the documented payload and signature, built from a real Cypress run, and that the end of the CI job sends it.

## Before changing anything

- Read the decision record that covers it, under `docs/decisions/`. A change that alters a decision amends that record or adds one, in the same format, ending with how the choice is explained to a customer. A change to a threat or a control changes its row in `docs/threat-model.md`.
- `make check` runs `scripts/validate.sh` (kubeconform, the admission plants against the template, images, python, the three doors' unit tests, and agreement between the pieces), then `tests/static/plant_violations.py`, which shows each check its planted violation from `tests/static/plants.tsv`. It must end all caught, none missed, none stale. Verify also runs actionlint and shellcheck; `make check` does not.
- `make test` runs Cypress in its pinned image and then the e2e against the cluster `make up` creates. It runs on Docker Desktop too.

## A check is trusted only after it has failed

A new or changed check ships with the violation it exists for: a plant in `tests/e2e/plants.py`, written to `tests/e2e/plants/` (a Job made from the real template with one thing changed, under a `# expect:` line naming the refusal it must get), a case in `tests/e2e/webhook.py` or `tests/unit/`, a row in `tests/static/plants.tsv`, or a deliberate sabotage run whose failing output is recorded in the commit message. A plant whose text is no longer in its file is reported stale and fails the run, so a change to a planted file updates its row in the same commit. A check that has never been seen to fail proves nothing.

Verdicts in `tests/e2e/run.sh` are read with `verdicts < <(cmd)` or `judge < <(cmd)`, never at the end of a pipe: a pipe runs the loop in a subshell, and a failure there does not reach the exit status. Python that reads files for the e2e goes in `tests/e2e/checks.py`, not in a heredoc: bash 3.2, which macOS ships, cannot parse a heredoc inside a process substitution.

## Proving a risky change

A change to admission, a network policy, a door, the Job template, a pin or a workflow runs the full e2e and Verify on a branch before it reaches main:

```bash
gh workflow run e2e.yml --ref <branch>
gh workflow run verify.yml --ref <branch>
```

`scripts/status.py` reads e2e runs on main only, so branch runs never touch the badges. A dispatched Verify also runs the freshness job, which fails when upstream has moved past a pin; that failure is about upstream, not the branch.

## Pins

Everything is pinned:

- kind, kubectl and kubeconform: version and sha256 per platform in `tools.lock`. `scripts/install-tools.sh` refuses a file that does not match.
- CI images (actionlint, shellcheck): by digest in `ci-images.pins`.
- Container images: by digest where they are used, in `manifests/`, `manifests/receiver/job.json`, `tests/e2e/run.sh` and `kind/cluster.yaml`. The agent's image and the gate's are also named in `manifests/admission.yaml`, and `scripts/validate.sh` fails when the two disagree, so a bump moves both in one commit. Cypress (`cypress/included`) is pinned in `tests/e2e/run.sh`.
- The Kubernetes schemas: a fixed commit of yannh/kubernetes-json-schema in `scripts/validate.sh`.
- Actions: by commit SHA, bumped weekly by Dependabot in one group and merged by `dependabot-automerge.yml` only after the e2e and Verify pass. The SLSA generator in `release-assets.yml` is the exception: it refuses a SHA, so it is referenced by tag.

Dependabot reads only the actions. Everything else is moved by hand, with the new hash checked against the publisher's, and not before a release is three days old. `scripts/check-freshness.py` reports each pin upstream has moved past and changes nothing.

## Numbers

The second row of README badges is written by `.github/workflows/status.yml` (`scripts/status.py`) to the `status` branch, from the e2e runs on main and their `e2e-results` artifacts. It is never edited by hand. The counts the README states (twenty refused Jobs and one admitted, nineteen planted violations, the 620-character note) are the counts in `tests/e2e/plants/`, `tests/static/plants.tsv` and `app/orders.json`, and change with them in the same commit. The Slack message in the README is pasted from a run on kind, not written, and says which run. When a check fails on one run and passes on the next, find what changed before calling it noise.

## Releases

- Fill `CHANGELOG.md` `[Unreleased]` in the same commit as the change. The compare links at the bottom move when a version is cut.
- Tag only a commit whose e2e and Verify are green, or a docs-only commit right after one, and say which in the release notes: what is new, what was found while building it, how it was checked.
- Publishing a release runs `release-assets.yml`: an archive of the tag, a keyless cosign signature and SLSA provenance. Then verify them the way a stranger would, with the `cosign verify-blob` and `slsa-verifier verify-artifact --source-tag <tag>` commands at the top of that workflow.

## Writing

Plain and specific. No em dashes, no marketing words, no claim the evidence does not carry. Name runs, commits and files. Commit subjects follow the log: `type(scope): what changed`. Commits are authored `Vladimir Mikhalev <10498744+heyvaldemar@users.noreply.github.com>` and carry no AI attribution lines.

## Never

- Print a secret or a credential, pass one as a command-line argument, or put one in a file or a log. The signing secret and the Slack URL are generated at install and handed to kubectl on stdin; the e2e run and the CI trigger read the signing secret on stdin.
- Turn off or weaken a check, a policy, a test or a gate to get a run green.
- Touch anything outside this repository's own CI to test a change: no cloud resources, no outside services, no real Slack, no Cypress Cloud project.
- Put a model or a model API key into the e2e run. The stand-in agent is a script that is fooled the same way every run (ADR 4).
- Let the receiver key replays on `X-Cypress-Event-Id`, read the event type from `X-Cypress-Event`, or coerce a timestamp that is not digits (ADR 1).
- Put anything from the webhook payload into the triage Job but the project ID and the run number.
- Loosen what `manifests/admission.yaml` holds triage Jobs to, the namespace labels it binds to, or Pod Security `restricted`.
- Open the agents' egress past their own resolver and their two doors, give that resolver a `forward` block, or let the receiver or Slack take calls from the agents' namespace.
- Grant the agent a tool beyond `cypress_get_runs` and `cypress_get_failed_tests`, or a scope beyond its own run, or let any door take a token for another audience.
- Let the agent's words into Slack without the notifier's escaping, broken addresses and previews off, or let the agent write the facts line.
