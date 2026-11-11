#!/usr/bin/env python3
"""What the live leg adds to the triage stack, as Kubernetes objects (ADR 8).

    scripts/live-up.py secret < token      the Cloud MCP token, a Secret in cloud-mcp only
    scripts/live-up.py egress IP...        the door's one way out: those addresses, port 443
    scripts/live-up.py door IP...          the door's patch: forward, read the token, pin the name
    scripts/live-up.py receiver            the receiver's patch: the project in CYPRESS_PROJECT_ID

scripts/live.sh up hands each to kubectl, the token on stdin, as
scripts/install.sh does with the signing secret. A NetworkPolicy names
addresses, not names, so the door's egress is the addresses mcp.cypress.io
had when the stack came up, and the door's pod is given the same addresses
for the name (hostAliases), so what it connects to is what the rule allows.
"""
import ipaddress
import json
import os
import re
import sys

HOST = "mcp.cypress.io"
URL = "https://%s/mcp" % HOST
TOKEN_DIR = "/var/run/cloud-mcp-upstream"


def addresses(argv):
    ips = sorted({str(ipaddress.IPv4Address(a)) for a in argv})
    if not ips:
        sys.exit("no address for %s" % HOST)
    return ips


def secret(_):
    token = sys.stdin.readline().strip()
    if not token or any(c.isspace() for c in token):
        sys.exit("no token on stdin, or one with spaces in it")
    return {"apiVersion": "v1", "kind": "Secret", "type": "Opaque",
            "metadata": {"name": "cloud-mcp-upstream", "namespace": "cloud-mcp"},
            "stringData": {"token": token}}


def egress(argv):
    return {"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy",
            "metadata": {"name": "to-cloud-mcp-upstream", "namespace": "cloud-mcp"},
            "spec": {"podSelector": {"matchLabels": {"app.kubernetes.io/name": "cloud-mcp"}},
                     "policyTypes": ["Egress"],
                     "egress": [{"to": [{"ipBlock": {"cidr": ip + "/32"}} for ip in addresses(argv)],
                                 "ports": [{"protocol": "TCP", "port": 443}]}]}}


def door(argv):
    return {"spec": {"template": {"spec": {
        "hostAliases": [{"ip": ip, "hostnames": [HOST]} for ip in addresses(argv)],
        "containers": [{"name": "server",
                        "env": [{"name": "UPSTREAM_URL", "value": URL},
                                {"name": "UPSTREAM_TOKEN_FILE", "value": TOKEN_DIR + "/token"}],
                        "volumeMounts": [{"name": "upstream", "mountPath": TOKEN_DIR, "readOnly": True}]}],
        # 0440: the file's group is the pod's fsGroup, the door's own.
        "volumes": [{"name": "upstream", "secret": {"secretName": "cloud-mcp-upstream", "defaultMode": 0o440}}],
    }}}}


def receiver(_):
    project = os.environ.get("CYPRESS_PROJECT_ID", "")
    if not re.fullmatch(r"[a-z0-9]{6}", project):
        sys.exit("CYPRESS_PROJECT_ID is not a Cypress project ID")
    return {"spec": {"template": {"spec": {"containers": [
        {"name": "receiver", "env": [{"name": "PROJECTS", "value": project}]}]}}}}


if __name__ == "__main__":
    modes = {"secret": secret, "egress": egress, "door": door, "receiver": receiver}
    if len(sys.argv) < 2 or sys.argv[1] not in modes:
        sys.exit(__doc__)
    json.dump(modes[sys.argv[1]](sys.argv[2:]), sys.stdout)
