#!/usr/bin/env bash
# install.sh - apply the triage stack to the cluster in the current kubeconfig
# (kind/cluster.yaml), and wait for it.
#
# Three things cannot live in manifests/ and are made here:
#   - the webhook signing secret, generated on this machine and handed to
#     kubectl on stdin, so it exists only as a Secret in triage. Against
#     Cypress Cloud it is the secret set on the webhook; the CI trigger
#     signs with the same one (scripts/send-run.py).
#   - the Slack webhook URL, a Secret in notify only. On kind it points at
#     the Slack stand-in, with a random path, as a real one has.
#   - each door's egress rule to the API server. A NetworkPolicy names
#     addresses, and the API server's is the cluster's.
set -euo pipefail
cd "$(dirname "$0")/.."

kubectl apply -k manifests/ >/dev/null
echo "applied manifests/"

if ! kubectl -n triage get secret webhook-signing >/dev/null 2>&1; then
  python3 - <<'PY' | kubectl apply -f - >/dev/null
import json, secrets
print(json.dumps({"apiVersion": "v1", "kind": "Secret", "type": "Opaque",
                  "metadata": {"name": "webhook-signing", "namespace": "triage"},
                  "stringData": {"secret": secrets.token_urlsafe(32)}}))
PY
  echo "signing secret generated, in triage only"
fi
if ! kubectl -n notify get secret slack-webhook >/dev/null 2>&1; then
  python3 - <<'PY' | kubectl apply -f - >/dev/null
import json, secrets
url = "http://slack.slack.svc.cluster.local:8080/services/T0KIND/B0KIND/" + secrets.token_urlsafe(18)
print(json.dumps({"apiVersion": "v1", "kind": "Secret", "type": "Opaque",
                  "metadata": {"name": "slack-webhook", "namespace": "notify"},
                  "stringData": {"url": url}}))
PY
  echo "Slack webhook URL made, in notify only"
fi

# The API server as a pod sees it: the Service address, and the endpoint it
# is translated to. Network plugins differ in which of the two a policy is
# matched against, so each rule names both and nothing else.
svc_ip="$(kubectl -n default get service kubernetes -o jsonpath='{.spec.clusterIP}')"
svc_port="$(kubectl -n default get service kubernetes -o jsonpath='{.spec.ports[0].port}')"
ep_ip="$(kubectl -n default get endpointslices -l kubernetes.io/service-name=kubernetes -o jsonpath='{.items[0].endpoints[0].addresses[0]}')"
ep_port="$(kubectl -n default get endpointslices -l kubernetes.io/service-name=kubernetes -o jsonpath='{.items[0].ports[0].port}')"
python3 - "$svc_ip" "$svc_port" "$ep_ip" "$ep_port" <<'PY' | kubectl apply -f - >/dev/null
import json, sys
svc_ip, svc_port, ep_ip, ep_port = sys.argv[1:]
egress = [{"to": [{"ipBlock": {"cidr": svc_ip + "/32"}}], "ports": [{"protocol": "TCP", "port": int(svc_port)}]},
          {"to": [{"ipBlock": {"cidr": ep_ip + "/32"}}], "ports": [{"protocol": "TCP", "port": int(ep_port)}]}]
# Who needs the API server, and for what: the receiver creates Jobs; the
# two doors ask TokenReview who is calling; the agents' resolver watches
# Services and EndpointSlices.
needs = [("triage", "receiver"), ("cloud-mcp", "cloud-mcp"), ("notify", "notifier"), ("agent-dns", None)]
print(json.dumps({"apiVersion": "v1", "kind": "List", "items": [
    {"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy",
     "metadata": {"name": "to-api-server", "namespace": ns},
     "spec": {"podSelector": {"matchLabels": {"app.kubernetes.io/name": app}} if app else {},
              "policyTypes": ["Egress"], "egress": egress}} for ns, app in needs]}))
PY
echo "the receiver, both doors and the agents' resolver may reach the API server at $svc_ip:$svc_port and $ep_ip:$ep_port"

for d in agent-dns/agent-dns triage/receiver cloud-mcp/cloud-mcp notify/notifier slack/slack outside/collector; do
  kubectl -n "${d%/*}" rollout status "deployment/${d#*/}" --timeout=180s >/dev/null
  echo "ready: $d"
done
