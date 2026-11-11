#!/usr/bin/env bash
# scripts/live.sh record | up - the live leg's two steps (ADR 8).
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
  *)
    echo "usage: scripts/live.sh record | up < token" >&2
    exit 2
    ;;
esac
