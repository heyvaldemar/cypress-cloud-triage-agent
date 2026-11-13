#!/usr/bin/env bash
# scripts/live.sh record | up | signing | tunnel - the live leg's steps
# (ADR 8; signing and tunnel are its webhook mode, ADR 9).
#
# record: runs the same spec in the same pinned image as tests/e2e/run.sh, with
# --record, into the project CYPRESS_PROJECT_ID names, with the record key in
# CYPRESS_RECORD_KEY. Both are handed to the container by name, never on a
# command line. GitHub's own variables go with them, so Cloud files the run
# under this workflow run. Cypress's output, which prints the run's URL, goes
# to out/cypress.log and not to the job's log.
#
# Two of the three tests fail on purpose, so Cypress exits 2; this exits 0
# when a run was recorded and 1 when none was. The run's number goes to
# out/recorded-run.
#
# up < token: after scripts/install.sh, points the Cloud MCP door at Cypress
# Cloud (scripts/live-up.py). The token on stdin becomes a Secret in
# cloud-mcp only; the door's egress is mcp.cypress.io's addresses on 443,
# and its pod is given those addresses for the name; the receiver serves the
# project in CYPRESS_PROJECT_ID instead of kind01.
#
# signing < secret: before up, the secret set on the webhook in Cypress Cloud
# becomes the receiver's signing secret (scripts/live-up.py signing), in place
# of the one scripts/install.sh generated. up restarts the receiver, which
# then reads it.
#
# tunnel < token: after up, the receiver at a public HTTPS URL for as long as
# the job runs (ADR 9). The token of the named Cloudflare tunnel goes to a
# file only this user reads, which cloudflared reads (--token-file), never a
# command line. cloudflared, pinned in tunnel.lock, hands what reaches
# WEBHOOK_HOST to tests/e2e/tap.py on 127.0.0.1:18091, which passes it to the
# receiver through a port-forward on 18090 and keeps each delivery in
# $RUNNER_TEMP/tunnel/deliveries.jsonl. This exits 0 once the receiver answers
# its health check at https://WEBHOOK_HOST/healthz, and 1 otherwise, saying
# what answered instead: a redirect there is the zone's own forwarding rule
# catching the hostname.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
ROOT="$(pwd)"
CYPRESS="$(sed -n 's/^CYPRESS=//p' tests/e2e/run.sh)"

case "${1:-}" in
  record)
    : "${CYPRESS_PROJECT_ID:?the Cypress Cloud project ID}" "${CYPRESS_RECORD_KEY:?the record key}"
    mkdir -p out
    rm -f out/cypress-results.json
    envs=(-e CYPRESS_PROJECT_ID -e CYPRESS_RECORD_KEY -e NO_COLOR=1)
    for v in GITHUB_ACTIONS GITHUB_WORKFLOW GITHUB_RUN_ID GITHUB_RUN_ATTEMPT GITHUB_RUN_NUMBER \
      GITHUB_REPOSITORY GITHUB_SERVER_URL GITHUB_SHA GITHUB_REF GITHUB_REF_NAME GITHUB_HEAD_REF \
      GITHUB_BASE_REF GITHUB_JOB GITHUB_ACTOR GITHUB_EVENT_NAME; do
      envs+=(-e "$v")
    done
    docker run --rm -v "$ROOT:/e2e" -w /e2e -u "$(id -u):$(id -g)" -e HOME=/tmp "${envs[@]}" \
      "$CYPRESS" --record >out/cypress.log 2>&1
    rc=$?
    # Cypress colours its output unless told not to, and a colour code sits
    # between "Recorded Run:" and the URL; the URL alone is matched.
    if [ ! -s out/cypress-results.json ] || ! grep -Eq 'https://cloud\.cypress\.io/projects/[a-z0-9]{6}/runs/[0-9]+' out/cypress.log; then
      echo "no run was recorded (cypress exited $rc); the last lines of its log, the project masked:"
      tail -n 25 out/cypress.log | sed "s/$CYPRESS_PROJECT_ID/<project>/g"
      exit 1
    fi
    grep -Eo 'https://cloud\.cypress\.io/projects/[a-z0-9]{6}/runs/[0-9]+' out/cypress.log | head -n 1 \
      | sed -E 's#.*/runs/##' >out/recorded-run
    failed="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["totalFailed"])' out/cypress-results.json)"
    echo "recorded run #$(cat out/recorded-run): $failed of the tests failed (cypress exited $rc)"
    ;;
  up)
    : "${CYPRESS_PROJECT_ID:?the Cypress Cloud project ID}"
    # shellcheck disable=SC2207  # addresses have no spaces
    ips=($(python3 -c 'import socket; print(" ".join(sorted({a[4][0] for a in socket.getaddrinfo("mcp.cypress.io", 443, socket.AF_INET, socket.SOCK_STREAM)})))'))
    [ "${#ips[@]}" -gt 0 ] || { echo "mcp.cypress.io did not resolve"; exit 1; }
    mkdir -p out
    python3 scripts/live-up.py secret | kubectl apply -f - >/dev/null || exit 1
    python3 scripts/live-up.py egress "${ips[@]}" | kubectl apply -f - >/dev/null || exit 1
    python3 scripts/live-up.py door "${ips[@]}" >out/door-patch.json || exit 1
    python3 scripts/live-up.py receiver >out/receiver-patch.json || exit 1
    kubectl -n cloud-mcp patch deployment cloud-mcp --type strategic --patch-file out/door-patch.json >/dev/null || exit 1
    kubectl -n triage patch deployment receiver --type strategic --patch-file out/receiver-patch.json >/dev/null || exit 1
    rm -f out/receiver-patch.json
    kubectl -n cloud-mcp rollout status deployment/cloud-mcp --timeout=120s >/dev/null || exit 1
    kubectl -n triage rollout status deployment/receiver --timeout=120s >/dev/null || exit 1
    echo "the door forwards to Cloud MCP, reaching ${ips[*]} on 443 and nothing else outside; the receiver serves the recorded project"
    ;;
  signing)
    python3 scripts/live-up.py signing | kubectl apply -f - >/dev/null || exit 1
    echo "the receiver's signing secret is the webhook's, in triage only"
    ;;
  tunnel)
    : "${WEBHOOK_HOST:?the hostname of the tunnel}" "${RUNNER_TEMP:?a directory the job owns}"
    dir="$RUNNER_TEMP/tunnel"
    mkdir -p "$dir" && chmod 700 "$dir" || exit 1
    (umask 077 && head -n 1 >"$dir/token")
    [ -s "$dir/token" ] || { echo "no tunnel token on stdin"; exit 1; }
    bin="$(scripts/install-tools.sh "$ROOT/.cache/tunnel-bin" "$ROOT/tunnel.lock")" || exit 1
    [ -x "$bin/cloudflared" ] || { echo "tunnel.lock has no cloudflared for this platform"; exit 1; }
    # These run on for the rest of the job, so none keeps this step's output
    # or its stdin, which held the token. kubectl port-forward exits when a
    # forwarded connection breaks, so it is brought back each time.
    (while :; do kubectl -n triage port-forward service/receiver 18090:8080; sleep 1; done) </dev/null >/dev/null 2>&1 &
    python3 -I tests/e2e/tap.py 18091 18090 "$dir/deliveries.jsonl" </dev/null >"$dir/tap.log" 2>&1 &
    "$bin/cloudflared" tunnel --no-autoupdate --metrics 127.0.0.1:18092 --logfile "$dir/cloudflared.log" \
      run --token-file "$dir/token" </dev/null >/dev/null 2>&1 &
    ready=""
    for _ in $(seq 1 60); do
      curl -fs -o /dev/null http://127.0.0.1:18092/ready && { ready=1; break; }
      sleep 1
    done
    [ -n "$ready" ] || { echo "cloudflared did not connect the tunnel in 60 seconds"; exit 1; }
    got=""
    for _ in $(seq 1 30); do
      got="$(curl -s -o "$dir/healthz" -w '%{http_code}' --max-time 10 "https://$WEBHOOK_HOST/healthz")"
      [ "$got" = 200 ] && [ "$(cat "$dir/healthz")" = '{"ok": true}' ] && break
      sleep 2
    done
    case "$got" in
      200) [ "$(cat "$dir/healthz")" = '{"ok": true}' ] \
             || { echo "the public URL answered 200, but not with the receiver's health check"; exit 1; } ;;
      30?) echo "the public URL answered $got: a redirect rule of the zone catches the tunnel's hostname"; exit 1 ;;
      *) echo "the public URL answered ${got:-nothing} instead of the receiver's health check"; exit 1 ;;
    esac
    echo "the receiver answers at the tunnel's public URL, over HTTPS, for as long as this job runs"
    ;;
  *)
    echo "usage: scripts/live.sh record | up < token | signing < secret | tunnel < token" >&2
    exit 2
    ;;
esac
