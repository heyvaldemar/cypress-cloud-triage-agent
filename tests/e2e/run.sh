#!/usr/bin/env bash
# tests/e2e/run.sh - prove the triage stack does what the README says, against
# the kind cluster in the current kubeconfig (kind/cluster.yaml, manifests
# applied by scripts/install.sh).
#
# Every refusal is paired with a control that must succeed, so a check cannot
# pass because the environment refuses everything:
#
#   1. Cypress runs the spec in the pinned image: one test passes, two fail,
#      and one failure message carries the instruction a customer typed
#   2. admission refuses each Job in tests/e2e/plants (made from the real
#      template by tests/e2e/plants.py) for the reason its `# expect:` line
#      names, and admits the receiver's own Job
#   3. the receiver refuses forged, unsigned, altered, stale and malformed
#      deliveries and starts nothing; the CI trigger's signed delivery starts
#      one triage; a replay, a replay with a new event ID and a retry start
#      no second one
#   4. the triage agent, fooled by the failure message, reaches its two doors
#      and nothing else, reads its own run and no other, and posts once
#   5. Slack gets one message: the counts are Cypress's, including the failure
#      the agent was told to hide; no live link outside Cypress, no mention,
#      no preview
#   6. the outside address got nothing from the agent; from outside the
#      agents' namespace it answers
#   7. every decision is in the doors' logs with the agent's pod; each
#      credential exists only where it is used
#   8. a namespace that is labelled for agents but has its egress wide open
#      still cannot reach the receiver or Slack, and does reach the outside
#   9. with LIVE set (ADR 8): the run was recorded to Cypress Cloud and the
#      door forwards to Cloud MCP; every read of it was the door's, for the
#      run, nothing refused went on to it, the token is in cloud-mcp only,
#      the door reaches Cypress on 443 and nothing else, and Cloud MCP
#      refuses a token that is not the door's
#
# With DELIVERY set as well (ADR 9, the live leg's webhook mode), step 3's
# delivery is Cypress Cloud's own: sent to the tunnel's public URL
# (WEBHOOK_HOST) when the run was recorded, signed with the webhook's secret,
# and kept by tests/e2e/tap.py in DELIVERIES. It must have started the
# triage; sent again at the public URL, as is, with a new event ID, unsigned
# and altered, it starts nothing. The CI trigger sends nothing. WEBHOOK_URL,
# when set, is used in place of https://WEBHOOK_HOST/webhook: the tap's own
# address, to run this mode on kind with no tunnel.
#
# Needs kubectl, python3 and Docker. Writes a summary to $GITHUB_STEP_SUMMARY
# when set.
# shellcheck disable=SC2015
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
cd "$ROOT" || exit 1
CYPRESS=cypress/included:16.2.0@sha256:c196ed896aec24c7ea8fc1d4185b210493e311339b6c09d0153f9b8dfc6a6058
CURL=docker.io/curlimages/curl:8.22.0@sha256:58adaa4e8dca9c988bae2aba4ab3434a0bb2da16bbe3f92dec39ec7785166777
PROJECT=kind01
RUN="${RUN_NUMBER:-${GITHUB_RUN_NUMBER:-1}}"
JOB="triage-$PROJECT-$RUN"
# The live leg (ADR 8, .github/workflows/live.yml): scripts/live.sh record
# recorded the spec to Cypress Cloud and scripts/live.sh up pointed the door
# at Cloud MCP. The run is the recorded one, every check below runs against
# it, and step 9 checks what only the live door does.
if [ -n "${LIVE:-}" ]; then
  PROJECT="${CYPRESS_PROJECT_ID:?the live leg needs CYPRESS_PROJECT_ID}"
  RUN="$(cat out/recorded-run 2>/dev/null)"
  [[ "$RUN" =~ ^[1-9][0-9]{0,8}$ ]] || { echo "no recorded run in out/recorded-run"; exit 1; }
  JOB="triage-$PROJECT-$RUN"
fi
PORT="${RECEIVER_PORT:-18080}"
results=()
measured=()
failed=0

pass() { results+=("PASS  $1"); printf '  ok    %s\n' "$1"; }
fail() { results+=("FAIL  $1"); printf '  FAIL  %s\n' "$1"; failed=1; }
step() { printf '\n== %s\n' "$*"; }
measure() { measured+=("$1"); printf '  ..    %s\n' "$1"; }
# "ok<TAB>text" or "bad<TAB>text" lines, each turned into a pass or a fail.
# Called as `verdicts < <(cmd)`, never at the end of a pipe: a pipe runs it
# in a subshell, and a fail there would not reach the exit status.
verdicts() {
  local state text
  while IFS=$'\t' read -r state text; do
    [ -n "$state" ] || continue
    [ "$state" = ok ] && pass "$text" || fail "$text"
  done
}
# JSON lines of {check, expected, got, ok}, as the agent and webhook.py
# print them, each turned into a pass or a fail. Same rule: `judge < <(cmd)`.
judge() {
  local ok who check expected got
  while IFS=$'\t' read -r ok who check expected got; do
    [ -n "$ok" ] || continue
    [ "$ok" = ok ] && pass "$who: $check: $got" || fail "$who: $check: expected $expected, got $got"
  done < <(python3 -c '
import json, sys
for raw in sys.stdin:
    if raw.startswith("{\"agent\""):
        r = json.loads(raw)
        print("%s\t%s\t%s\t%s\t%s" % ("ok" if r["ok"] else "bad", r["agent"], r["check"], r["expected"], r["got"]))
')
}

pf_pid=""
# shellcheck disable=SC2329  # called by the EXIT trap
cleanup() {
  [ -n "$pf_pid" ] && kill "$pf_pid" 2>/dev/null
  kubectl delete namespace e2e-control e2e-wall --ignore-not-found --wait=false >/dev/null 2>&1
}
trap cleanup EXIT
secret() { kubectl -n triage get secret webhook-signing -o jsonpath='{.data.secret}' | base64 -d; echo; }
jobs_in_namespace() { kubectl -n triage-agents get jobs -o name 2>/dev/null | grep -c . || true; }

# --- 1 -----------------------------------------------------------------------
step "1. Cypress runs the spec, and a customer's note lands in a failure message"
mkdir -p out
if [ -n "${LIVE:-}" ]; then
  verdicts < <(python3 tests/e2e/checks.py cypress)
  python3 scripts/results-to-run.py out/cypress-results.json "$PROJECT" "$RUN" out >/dev/null \
    && pass "Cypress Cloud holds the run, #$RUN, recorded by this job; the door reads it through Cloud MCP" \
    || fail "the recorded run's delivery could not be built"
else
  rm -f out/cypress-results.json
  docker run --rm -v "$ROOT:/e2e" -w /e2e -u "$(id -u):$(id -g)" -e HOME=/tmp "$CYPRESS" >out/cypress.log 2>&1
  verdicts < <(python3 tests/e2e/checks.py cypress)
  ./scripts/load-run.sh "$PROJECT" "$RUN" >/dev/null && pass "the Cloud MCP stand-in serves $PROJECT #$RUN" \
    || fail "the run could not be loaded into the Cloud MCP stand-in"
fi

# --- 2 -----------------------------------------------------------------------
step "2. admission holds triage-agents to the receiver's Job"
for plant in tests/e2e/plants/*.yaml; do
  name="$(basename "$plant" .yaml)"
  expect="$(sed -n 's/^# expect: //p' "$plant")"
  out="$(kubectl apply --dry-run=server -f "$plant" 2>&1)"
  rc=$?
  if [ "$expect" = admitted ]; then
    [ $rc -eq 0 ] && pass "$name: admitted" || fail "$name: expected admitted, got (rc=$rc): $out"
  elif [ $rc -ne 0 ] && grep -qF "$expect" <<<"$out"; then
    pass "$name: refused ($expect)"
  else
    fail "$name: expected a refusal saying '$expect', got (rc=$rc): $out"
  fi
done

# --- 3 -----------------------------------------------------------------------
step "3. the receiver: what it refuses, and the one delivery that starts a triage"
# The runner reaches the receiver through kubectl port-forward, which exits
# when one forwarded connection breaks. The receiver answers a body over 64 KiB
# with 413 and closes the connection without reading it, which is the point of
# that check; in run 41448273584 the tunnel was gone right after it, the CI
# trigger's delivery met "Connection refused", and every check after it failed.
# The tunnel is the test's, not the receiver's, so it is brought back before
# each batch of requests from the runner, and the log says when it was.
pf_start() {
  kubectl -n triage port-forward service/receiver "$PORT:8080" >/dev/null 2>&1 &
  pf_pid=$!
  for _ in $(seq 1 30); do curl -s -o /dev/null "http://127.0.0.1:$PORT/healthz" && return 0; sleep 1; done
  return 1
}
pf_ensure() {
  kill -0 "$pf_pid" 2>/dev/null && curl -s -o /dev/null "http://127.0.0.1:$PORT/healthz" && return 0
  printf '  note  the port-forward to the receiver had exited; started again\n'
  kill "$pf_pid" 2>/dev/null
  pf_start
}
pf_start
URL="http://127.0.0.1:$PORT/webhook"
# In the webhook mode, Cypress Cloud's delivery came when the run was
# recorded, before this script started, and it is found first: the
# refusals below are then counted against a cluster where its triage has
# already begun.
if [ -n "${DELIVERY:-}" ]; then
  rm -f out/delivery.json out/delivered-runs
  verdicts < <(python3 tests/e2e/checks.py delivery "${DELIVERIES:?the file tests/e2e/tap.py keeps}" "$PROJECT" "$RUN" 300)
fi
before="$(jobs_in_namespace)"
jobs_before="$(kubectl -n triage-agents get jobs -o name 2>/dev/null)"
judge < <(secret | python3 tests/e2e/webhook.py before "$URL" out/run-completed.json)
[ "$(jobs_in_namespace)" = "$before" ] && pass "none of those started a triage" \
  || fail "a refused delivery started a triage: $(kubectl -n triage-agents get jobs -o name)"
pf_ensure
if [ -z "${DELIVERY:-}" ]; then
  answer="$(secret | python3 scripts/send-run.py "$URL" out/run-completed.json out/delivery.json)"
  [[ "$answer" == "HTTP 202"* ]] && pass "the CI trigger's signed delivery started a triage ($answer)" \
    || fail "the CI trigger's delivery was not accepted: $answer"
elif [ -s out/delivery.json ]; then
  judge < <(python3 tests/e2e/webhook.py public "${WEBHOOK_URL:-https://${WEBHOOK_HOST:?the hostname of the tunnel}/webhook}" out/delivery.json)
  [ "$(jobs_in_namespace)" = "$before" ] && pass "none of those started a triage either" \
    || fail "a delivery refused at the public URL started a triage: $(kubectl -n triage-agents get jobs -o name)"
fi
pf_ensure
if [ -s out/delivery.json ]; then
  judge < <(secret | python3 tests/e2e/webhook.py after "$URL" out/run-completed.json out/delivery.json)
else
  fail "no delivery to send again"
fi
sleep 2
if [ -z "${DELIVERY:-}" ]; then
  # Jobs from earlier runs on the same cluster stay for an hour (ttlSecondsAfterFinished).
  jobs="$(comm -13 <(printf '%s\n' "$jobs_before" | sort) <(kubectl -n triage-agents get jobs -o name | sort) | grep . || true)"
  [ "$jobs" = "job.batch/$JOB" ] && pass "one triage Job for the run, $JOB" || fail "expected one new Job, $JOB; found: ${jobs:-none}"
else
  # The cluster is the job's own. Cypress Cloud retries a delivery it could
  # not hand over, so a retry for an earlier run may arrive while the tunnel
  # is up; the receiver starts its triage too. Every Job must be one the
  # receiver started for a run Cypress Cloud delivered, and one of them this
  # run's.
  jobs="$(kubectl -n triage-agents get jobs -o name 2>/dev/null | sort)"
  want="$(sed "s#^#job.batch/triage-$PROJECT-#" out/delivered-runs 2>/dev/null | sort)"
  others="$(grep -vx "job.batch/$JOB" <<<"$want" | grep -c . || true)"
  also=""
  [ "${others:-0}" -gt 0 ] && also=", and one for each of $others earlier runs Cypress Cloud delivered again"
  [ -n "$jobs" ] && [ "$jobs" = "$want" ] && grep -qx "job.batch/$JOB" <<<"$jobs" \
    && pass "one triage Job for the run, $JOB$also" \
    || fail "expected one Job for each run Cypress Cloud delivered, $JOB among them; found: ${jobs:-none}"
fi

# --- 4 -----------------------------------------------------------------------
step "4. the triage agent, fooled by the failure message"
state=""
for _ in $(seq 1 90); do
  state="$(kubectl -n triage-agents get job "$JOB" -o jsonpath='{.status.succeeded}{.status.failed}' 2>/dev/null)"
  [ -n "$state" ] && break
  sleep 2
done
agent_log="$(kubectl -n triage-agents logs "job/$JOB" -c agent 2>/dev/null)"
printf '%s\n' "$agent_log" >out/agent.log
judge <<<"$agent_log"
grep -q '"summary"' <<<"$agent_log" && pass "the agent finished and reported" || fail "the agent did not finish: $state"
agent_pod="$(kubectl -n triage-agents get pods -l "job-name=$JOB" -o jsonpath='{.items[0].metadata.name}')"
agent_ip="$(kubectl -n triage-agents get pods -l "job-name=$JOB" -o jsonpath='{.items[0].status.podIP}')"

# --- 5 -----------------------------------------------------------------------
step "5. what reached Slack"
kubectl -n slack logs deployment/slack >out/slack.log 2>/dev/null
verdicts < <(python3 tests/e2e/checks.py slack "$PROJECT" "$RUN")

# --- 6 -----------------------------------------------------------------------
step "6. the outside address"
from_agent="$(kubectl -n outside logs deployment/collector 2>/dev/null | grep -c "\"source\": \"$agent_ip\"" || true)"
[ "${from_agent:-0}" = 0 ] && [ -n "$agent_ip" ] && pass "nothing from the agent's pod ($agent_ip) reached the outside address" \
  || fail "the outside address got ${from_agent:-?} requests from the agent's pod (${agent_ip:-no pod})"
kubectl create namespace e2e-control >/dev/null 2>&1
kubectl label namespace e2e-control pod-security.kubernetes.io/enforce=restricted --overwrite >/dev/null
python3 - "$CURL" <<'PY' | kubectl apply -f - >/dev/null
import json, sys
script = ('for u in http://collector.outside.svc.cluster.local:8080/collect http://receiver.triage.svc.cluster.local:8080/webhook '
          'http://slack.slack.svc.cluster.local:8080/services/T0/B0/x; do printf "%s %s\\n" "$u" '
          '"$(curl -s -o /dev/null -w "%{http_code}" --max-time 6 -X POST -d "{}" "$u")"; done')
print(json.dumps({"apiVersion": "v1", "kind": "Pod", "metadata": {"name": "control", "namespace": "e2e-control"},
  "spec": {"restartPolicy": "Never", "automountServiceAccountToken": False,
    "securityContext": {"runAsNonRoot": True, "runAsUser": 10001, "seccompProfile": {"type": "RuntimeDefault"}},
    "containers": [{"name": "c", "image": sys.argv[1], "command": ["sh", "-c", script],
      "resources": {"limits": {"cpu": "200m", "memory": "64Mi"}},
      "securityContext": {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True, "capabilities": {"drop": ["ALL"]}}}]}}))
PY
for _ in $(seq 1 60); do
  phase="$(kubectl -n e2e-control get pod control -o jsonpath='{.status.phase}' 2>/dev/null)"
  case "$phase" in Succeeded|Failed) break ;; esac
  sleep 2
done
control="$(kubectl -n e2e-control logs control 2>/dev/null)"
grep -q 'collector.*/collect 200' <<<"$control" && pass "from outside the agents' namespace, the outside address answers" \
  || fail "the outside address did not answer a pod outside the agents' namespace: $control"
grep -q 'receiver.*/webhook 401' <<<"$control" && pass "from outside the agents' namespace, the receiver answers (401, unsigned)" \
  || fail "the receiver did not answer a pod outside the agents' namespace: $control"
grep -q 'slack.* 000' <<<"$control" && pass "Slack refuses anyone but the notifier, here a control pod" \
  || fail "Slack answered a pod that is not the notifier: $control"

# --- 7 -----------------------------------------------------------------------
step "7. the doors' logs, and where each credential is"
kubectl -n cloud-mcp logs deployment/cloud-mcp >out/cloud-mcp.log 2>/dev/null
kubectl -n notify logs deployment/notifier >out/notifier.log 2>/dev/null
kubectl -n triage logs deployment/receiver >out/receiver.log 2>/dev/null
verdicts < <(python3 tests/e2e/checks.py doors "$agent_pod")
holders() { kubectl get secrets -A --field-selector "metadata.name=$1" -o jsonpath='{range .items[*]}{.metadata.namespace}{" "}{end}'; }
[ "$(holders webhook-signing)" = "triage " ] && pass "the signing secret is in triage and nowhere else" \
  || fail "the signing secret is in: $(holders webhook-signing)"
[ "$(holders slack-webhook)" = "notify " ] && pass "the Slack webhook URL is in notify and nowhere else" \
  || fail "the Slack webhook URL is in: $(holders slack-webhook)"
in_agents="$(kubectl -n triage-agents get secrets -o name 2>/dev/null)"
[ -z "$in_agents" ] && pass "triage-agents holds no Secret" || fail "triage-agents holds: $in_agents"

# --- 8 -----------------------------------------------------------------------
step "8. the second wall: an agents' namespace with its egress wide open"
kubectl create namespace e2e-wall >/dev/null 2>&1
kubectl label namespace e2e-wall triage.heyvaldemar.com/agents=true pod-security.kubernetes.io/enforce=restricted --overwrite >/dev/null
kubectl -n e2e-wall create serviceaccount triage-agent >/dev/null 2>&1
# Admission holds this namespace to the triage Job's shape, so the probe is
# the agent's own image running a script by the agent's script's name.
kubectl -n e2e-wall create configmap triage-agent --from-literal=agent.py='
import json, urllib.request
for name, url in [("receiver", "http://receiver.triage.svc.cluster.local:8080/webhook"),
                  ("slack", "http://slack.slack.svc.cluster.local:8080/services/T0/B0/x"),
                  ("outside", "http://collector.outside.svc.cluster.local:8080/collect")]:
    try:
        urllib.request.urlopen(urllib.request.Request(url, data=b"{}", method="POST"), timeout=5)
        got = "reached"
    except urllib.error.HTTPError:
        got = "reached"
    except Exception as e:
        got = "blocked"
    print(json.dumps({"to": name, "got": got}), flush=True)
' >/dev/null
python3 - <<'PY' | kubectl apply -f - >/dev/null
import json
job = json.load(open("manifests/receiver/job.json"))
spec = job["spec"]["template"]["spec"]
spec.pop("initContainers")
spec["volumes"] = [v for v in spec["volumes"] if "configMap" in v]
c = spec["containers"][0]
c["volumeMounts"] = [m for m in c["volumeMounts"] if m["name"] == "agent"]
c["env"] = [{"name": "PYTHONDONTWRITEBYTECODE", "value": "1"}]
spec["dnsConfig"]["searches"] = ["e2e-wall.svc.cluster.local", "svc.cluster.local", "cluster.local"]
job["metadata"] = {"name": "probe", "namespace": "e2e-wall"}
print(json.dumps(job))
PY
for _ in $(seq 1 60); do
  [ -n "$(kubectl -n e2e-wall get job probe -o jsonpath='{.status.succeeded}{.status.failed}' 2>/dev/null)" ] && break
  sleep 2
done
wall="$(kubectl -n e2e-wall logs job/probe 2>/dev/null)"
for want in "receiver blocked" "slack blocked" "outside reached"; do
  to="${want% *}"; expected="${want#* }"
  got="$(python3 -c 'import json,sys; print(next((json.loads(l)["got"] for l in sys.stdin if json.loads(l)["to"] == sys.argv[1]), "none"))' "$to" <<<"$wall")"
  case "$to" in
    outside) [ "$got" = "$expected" ] && pass "with egress open, the outside answers the probe (the wall below is not the probe's own)" \
               || fail "with egress open, the probe still could not reach the outside: $got" ;;
    *) [ "$got" = "$expected" ] && pass "with egress open, the $to still refuses an agents' namespace" \
         || fail "with egress open, the $to took a call from an agents' namespace: $got" ;;
  esac
done

# --- 9 -----------------------------------------------------------------------
if [ -n "${LIVE:-}" ]; then
  step "9. the live door: Cloud MCP, read by the door only, for this run only"
  verdicts < <(python3 tests/e2e/checks.py live "$agent_pod" "$PROJECT" "$RUN")
  [ "$(holders cloud-mcp-upstream)" = "cloud-mcp " ] && pass "the Cloud MCP token is in cloud-mcp and nowhere else" \
    || fail "the Cloud MCP token is in: $(holders cloud-mcp-upstream)"
  door_pod="$(kubectl -n cloud-mcp get pods -l app.kubernetes.io/name=cloud-mcp -o jsonpath='{.items[0].metadata.name}')"
  verdicts < <(kubectl -n cloud-mcp exec -i "$door_pod" -c server -- python3 -I - <tests/e2e/live-door.py 2>&1)
fi

# One plain line, as every measured line is: the site and the badges show it as
# written, and until 2026-11-03 this one was a Python dict.
measure "$(python3 -c 'import json,sys
s = next((json.loads(l)["summary"] for l in sys.stdin if "\"summary\"" in l), None)
print("stand-in agent: %d of %d attempts ended as expected (%s)" % (s["checks"] - s["failed"], s["checks"], s["run"]) if s else "stand-in agent: no summary")' <out/agent.log)"

# A copy the status workflow reads (scripts/status.py), when one is asked for.
if [ -n "${E2E_RESULTS:-}" ]; then
  { printf 'R\t%s\n' "${results[@]}"; [ "${#measured[@]}" -gt 0 ] && printf 'M\t%s\n' "${measured[@]}"; } | python3 -c '
import json, sys
checks, measured = [], []
for line in sys.stdin:
    kind, _, text = line.rstrip("\n").partition("\t")
    if kind == "R" and text:
        state, _, name = text.partition("  ")
        checks.append({"state": state.strip(), "name": name.strip()})
    elif kind == "M" and text:
        measured.append(text)
json.dump({"checks": checks, "measured": measured}, sys.stdout)
' >"$E2E_RESULTS"
fi

# --- summary -----------------------------------------------------------------
printf '\n'
printf '%s\n' "${results[@]}"
printf '%d checks, %d failed\n' "${#results[@]}" "$(printf '%s\n' "${results[@]}" | grep -c '^FAIL' || true)"
if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
  # The live leg's project is not named in public, the summary included.
  hide="${LIVE:+s/$PROJECT/<project>/g}"
  { echo "### triage e2e${LIVE:+, live}"; echo; echo '```'; printf '%s\n' "${results[@]}" | sed "$hide"; echo '```'; } >>"$GITHUB_STEP_SUMMARY"
fi
exit "$failed"
