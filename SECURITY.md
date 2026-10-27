# Security policy

## Supported versions

| Version | Supported |
|---|---|
| The latest release | yes |
| `main` | yes |
| Older releases | no |

## Reporting a vulnerability

Report privately through GitHub: **Security → Report a vulnerability** on this repository. Email to v@valdemar.ai also works. The process is described at [heyvaldemar.com/security](https://heyvaldemar.com/security/).

You can expect an acknowledgment within 7 days. There is no bounty program; valid, responsibly disclosed reports are credited in the changelog. Please do not open public issues for security reports.

## What this repository is

A reference stack, not a hosted service. A vulnerability here is most likely a way for text in a failing test to reach something the README says it cannot: a delivery the receiver accepts that it should refuse, a Job admission lets through, a path past the egress policies, a run the Cloud MCP door serves out of scope, or text the notifier lets reach Slack as a link or a mention.

## Supply chain

- Container images are pinned by digest, and CI fails on one that is not.
- GitHub Actions are pinned by commit SHA; CLI tools by version and sha256 (`tools.lock`); CI images by digest (`ci-images.pins`).

## Credentials

None in this repository or its CI. The webhook signing secret and the Slack URL used on kind are generated at install, inside the cluster. The stand-in agent uses no model and no API key.
