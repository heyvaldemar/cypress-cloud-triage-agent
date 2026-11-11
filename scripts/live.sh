#!/usr/bin/env bash
# scripts/live.sh record - record the spec to Cypress Cloud (ADR 8).
#
# Runs the same spec in the same pinned image as tests/e2e/run.sh, with
# --record, into the project CYPRESS_PROJECT_ID names, with the record key in
# CYPRESS_RECORD_KEY. Both are handed to the container by name, never on a
# command line. GitHub's own variables go with them, so Cloud files the run
# under this workflow run. Cypress's output, which prints the run's URL, goes
# to out/cypress.log and not to the job's log.
#
# Two of the three tests fail on purpose, so Cypress exits 2; this exits 0
# when a run was recorded and 1 when none was.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
ROOT="$(pwd)"
CYPRESS="$(sed -n 's/^CYPRESS=//p' tests/e2e/run.sh)"

case "${1:-}" in
  record)
    : "${CYPRESS_PROJECT_ID:?the Cypress Cloud project ID}" "${CYPRESS_RECORD_KEY:?the record key}"
    mkdir -p out
    rm -f out/cypress-results.json
    envs=(-e CYPRESS_PROJECT_ID -e CYPRESS_RECORD_KEY)
    for v in GITHUB_ACTIONS GITHUB_WORKFLOW GITHUB_RUN_ID GITHUB_RUN_ATTEMPT GITHUB_RUN_NUMBER \
      GITHUB_REPOSITORY GITHUB_SERVER_URL GITHUB_SHA GITHUB_REF GITHUB_REF_NAME GITHUB_HEAD_REF \
      GITHUB_BASE_REF GITHUB_JOB GITHUB_ACTOR GITHUB_EVENT_NAME; do
      envs+=(-e "$v")
    done
    docker run --rm -v "$ROOT:/e2e" -w /e2e -u "$(id -u):$(id -g)" -e HOME=/tmp "${envs[@]}" \
      "$CYPRESS" --record >out/cypress.log 2>&1
    rc=$?
    if [ ! -s out/cypress-results.json ] || ! grep -q 'Recorded Run: https://cloud.cypress.io/projects/' out/cypress.log; then
      echo "no run was recorded (cypress exited $rc); the last lines of its log, the project masked:"
      tail -n 25 out/cypress.log | sed "s/$CYPRESS_PROJECT_ID/<project>/g"
      exit 1
    fi
    failed="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["totalFailed"])' out/cypress-results.json)"
    echo "recorded: $failed of the tests failed (cypress exited $rc)"
    ;;
  *)
    echo "usage: scripts/live.sh record" >&2
    exit 2
    ;;
esac
