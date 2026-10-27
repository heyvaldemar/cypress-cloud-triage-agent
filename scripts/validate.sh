#!/usr/bin/env bash
# validate.sh [check...] - the static checks, without a cluster:
#
#   kubeconform   every manifest, the triage Job template and every plant are
#                 valid Kubernetes 1.37 objects
#   plants        tests/e2e/plants is what tests/e2e/plants.py makes from the
#                 template now
#   images        every image is referenced by digest
#   python        every Python file compiles
#   receiver      the webhook door's decisions (tests/unit/test_receiver.py)
#   notifier      what reaches Slack (tests/unit/test_notifier.py)
#   mcp           the Cloud MCP door's grants and scope (tests/unit/test_cloud_mcp.py)
#   agreement     the pieces agree: every grant names a read-only tool the
#                 door has, the agent reads its own run only, the two token
#                 audiences are the same in admission, the Job template, the
#                 doors and their manifests, and admission's two image
#                 digests are the template's
#
# With no arguments every check runs. tests/static/plants.tsv shows each
# check the violation it exists for (tests/static/plant_violations.py).
# The check_* functions are called by name from the loop at the bottom.
# shellcheck disable=SC2329
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

KUBE_VERSION=1.37.0
# yannh/kubernetes-json-schema at a fixed commit. K8S_SCHEMAS may point at a
# local checkout of the same commit instead. (The default is assigned on its
# own line: the template's braces would close a ${VAR:-default} early.)
default_schemas='https://raw.githubusercontent.com/yannh/kubernetes-json-schema/817113f21a2643311cbc3e992bb44ffe1c416c54/{{.NormalizedKubernetesVersion}}-standalone{{.StrictSuffix}}/{{.ResourceKind}}{{.KindSuffix}}.json'
K8S_SCHEMAS="${K8S_SCHEMAS:-$default_schemas}"

check_kubeconform() {
  mkdir -p .cache/kubeconform out
  kubectl kustomize manifests >out/manifests.yaml || return 1
  kubeconform -strict -summary -output text -kubernetes-version "$KUBE_VERSION" \
    -cache .cache/kubeconform -schema-location "$K8S_SCHEMAS" \
    out/manifests.yaml manifests/receiver/job.json tests/e2e/plants
}

check_plants() { python3 tests/e2e/plants.py --check; }

check_images() {
  # tests/e2e/plants is left out on purpose: one plant is a tag-only image.
  local bad
  bad="$(grep -rnE '(image:|"image":|^[A-Z]+=)[[:space:]]*"?[a-z0-9.-]+(\.[a-z]+)?/[^@[:space:]"]+:[^@[:space:]"]+("|$|[[:space:]])' \
    manifests kind tests/e2e/run.sh .github/workflows 2>/dev/null | grep -v '@sha256:' || true)"
  if [ -n "$bad" ]; then echo "images without a digest:"; echo "$bad"; return 1; fi
  echo "every image is referenced by digest"
}

check_python() {
  local f bad=0
  while IFS= read -r f; do
    python3 -m py_compile "$f" || bad=1
  done < <(git ls-files '*.py' 2>/dev/null || find . -name '*.py' -not -path './.cache/*')
  [ "$bad" -eq 0 ] && echo "every Python file compiles"
  return "$bad"
}

check_receiver() { python3 tests/unit/test_receiver.py 2>&1 | tail -3; return "${PIPESTATUS[0]}"; }
check_notifier() { python3 tests/unit/test_notifier.py 2>&1 | tail -3; return "${PIPESTATUS[0]}"; }
check_mcp() { python3 tests/unit/test_cloud_mcp.py 2>&1 | tail -3; return "${PIPESTATUS[0]}"; }

check_agreement() {
  python3 - <<'PY'
import importlib.util, json, re, sys
spec = importlib.util.spec_from_file_location("server", "manifests/cloud-mcp/server.py")
srv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(srv)
policy = json.load(open("manifests/cloud-mcp/policy.json"))
job = json.load(open("manifests/receiver/job.json"))
admission = open("manifests/admission.yaml").read()
notifier = open("manifests/notifier/notifier.py").read()
notifier_yaml = open("manifests/notifier/notifier.yaml").read()
bad = []
agent = "system:serviceaccount:triage-agents:triage-agent"
for who, grant in sorted(policy["identities"].items()):
    for t in grant["tools"]:
        if t not in srv.TOOLS:
            bad.append("%s is granted %s, a tool the door does not have" % (who, t))
        elif srv.TOOLS[t]["definition"]["annotations"].get("destructiveHint", True):
            bad.append("%s is granted %s, which is not read-only" % (who, t))
if policy["identities"].get(agent, {}).get("scope") != "own-run":
    bad.append("the triage agent may read runs other than its own")
mcp_aud = policy["audience"]
notify_aud = re.search(r'NOTIFY_AUDIENCE", "([^"]+)"', notifier).group(1)
job_auds = sorted(s["serviceAccountToken"]["audience"] for v in job["spec"]["template"]["spec"]["volumes"]
                  if "projected" in v for s in v["projected"]["sources"])
if job_auds != sorted([mcp_aud, notify_aud]):
    bad.append("the Job template's tokens %s are not for the two doors (%s, %s)" % (job_auds, mcp_aud, notify_aud))
allowed = re.search(r"audience in \[([^\]]+)\]", admission).group(1)
if sorted(re.findall(r"'([^']+)'", allowed)) != sorted([mcp_aud, notify_aud]):
    bad.append("admission allows token audiences %s, not the two doors'" % allowed)
if "audience: %s" % mcp_aud not in notifier_yaml:
    bad.append("the notifier's token is not for the Cloud MCP door's audience (%s)" % mcp_aud)
spec_ = job["spec"]["template"]["spec"]
for c, key in [(spec_["containers"][0], "containers"), (spec_["initContainers"][0], "inits")]:
    if "c.image ==\n        '%s'" % c["image"] not in admission and "c.image == '%s'" % c["image"] not in admission:
        bad.append("admission does not allow the template's %s image %s" % (key, c["image"]))
print("\n".join(bad) or "grants are read-only tools the door has; the agent reads its own run; "
      "one pair of audiences; admission allows the template's two images")
sys.exit(1 if bad else 0)
PY
}

checks=("$@")
[ ${#checks[@]} -eq 0 ] && checks=(kubeconform plants images python receiver notifier mcp agreement)
failed=0
for c in "${checks[@]}"; do
  if "check_$c"; then echo "-- $c: ok"; else echo "-- $c: FAILED"; failed=1; fi
done
exit "$failed"
