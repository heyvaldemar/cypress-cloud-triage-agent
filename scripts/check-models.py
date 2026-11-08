#!/usr/bin/env python3
"""check-models.py - the model leg's pieces agree with what ADR 6 promises.

    scripts/check-models.py      (scripts/validate.sh models runs it)

- the model reaches nothing: the models namespace denies ingress and egress
  by default, and no policy there allows egress;
- it answers the agents' namespace only, on its one port;
- the agents' one more way out in this leg is that port, and nothing else;
- the harness reads only the environment admission allows a triage Job, routes
  every tool it offers to a door or the network, and opens only http(s): a
  web request tool that took file:// would read the pod's tokens;
- models/models.lock pins exactly the models the e2e matrix runs, each by the
  file's sha256 at a fixed revision and by its image's digest.
"""
import ast
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALLOWED_ENV = {"PYTHONDONTWRITEBYTECODE", "CLOUD_MCP_URL", "NOTIFY_URL", "RUN_PROJECT", "RUN_NUMBER"}


def read(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as f:
        return f.read()


def documents(text):
    out = []
    for d in re.split(r"(?m)^---\n", text):
        if not re.search(r"(?m)^kind: ", d):
            continue
        kind = re.search(r"(?m)^kind: (\S+)", d).group(1)
        name = re.search(r"(?m)^  name: (\S+)", d).group(1)
        ns = re.search(r"(?m)^  namespace: (\S+)", d)
        out.append((kind, ns.group(1) if ns else name, name, d))
    return out


def main():
    bad = []
    pols = [(ns, name, d) for kind, ns, name, d in documents(read("manifests/models/models.yaml"))
            if kind == "NetworkPolicy"]
    if not any(ns == "models" and "podSelector: {}" in d and "policyTypes: [Ingress, Egress]" in d
               for ns, name, d in pols):
        bad.append("the models namespace has no default-deny for both ingress and egress")
    for ns, name, d in pols:
        if ns == "models" and "egress:" in d:
            bad.append("%s allows egress from the models namespace: the model must reach nothing" % name)
        if ns == "models" and "ingress:" in d:
            sources = re.findall(r"kubernetes.io/metadata.name: (\S+)", d)
            if sources != ["triage-agents"] or d.count("port:") != 1 or "port: 8080" not in d:
                bad.append("%s lets more than the agents' namespace reach the model, or on more than its port" % name)
    agents = [(name, d) for ns, name, d in pols if ns == "triage-agents"]
    if len(agents) != 1:
        bad.append("the model leg adds %d policies to the agents' namespace, not one" % len(agents))
    for name, d in agents:
        if re.findall(r"kubernetes.io/metadata.name: (\S+)", d) != ["models"] or d.count("port:") != 1 \
                or "port: 8080" not in d or "ingress:" in d:
            bad.append("%s opens more for the agents than the model's one port" % name)

    src = read("manifests/model-agent/agent.py")
    envs = set(re.findall(r'os\.environ\.get\("([A-Z_]+)"', src)) | set(re.findall(r'os\.environ\["([A-Z_]+)"\]', src))
    if envs - ALLOWED_ENV:
        bad.append("the harness reads environment admission does not allow: %s" % sorted(envs - ALLOWED_ENV))
    tree = ast.parse(src)
    offered = {n.args[0].value for n in ast.walk(tree)
               if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "fn" and n.args}
    cypress = re.search(r"CYPRESS_TOOLS = \{[^}]*\}", src)
    routed = (set(re.findall(r'"(cypress_[a-z_]+)"', cypress.group(0))) if cypress else set()) | {"run_shell", "http_request"}
    if not offered or offered - routed:
        bad.append("the harness offers tools it does not route to a door or the network: %s" % sorted(offered - routed))
    if 'not in ("http", "https")' not in src:
        bad.append("the harness's web request is not held to http and https: file:// would read the pod's tokens")

    lock = [l.split() for l in read("models/models.lock").splitlines() if l.strip() and not l.startswith("#")]
    matrix = re.search(r"model: \[([^\]]+)\]", read(".github/workflows/e2e.yml"))
    matrix = sorted(m.strip() for m in matrix.group(1).split(",")) if matrix else []
    if sorted(l[0] for l in lock) != matrix:
        bad.append("models.lock pins %s; the e2e runs %s" % (sorted(l[0] for l in lock), matrix))
    for l in lock:
        if len(l) < 5 or not re.fullmatch(r"[0-9a-f]{64}", l[2]) or not re.fullmatch(r"sha256:[0-9a-f]{64}", l[-1]):
            bad.append("%s is not pinned by its file's sha256 and its image's digest" % l[0])
        elif not re.search(r"/resolve/[0-9a-f]{40}/", l[1]):
            bad.append("%s is not fetched at a fixed revision" % l[0])

    print("\n".join(bad) or "the model reaches nothing and answers the agents on one port; the harness reads the "
          "allowed environment, routes all %d tools and opens only http(s); %d models pinned, the e2e's"
          % (len(offered), len(lock)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
