#!/usr/bin/env bash
# load-run.sh PROJECT_ID RUN_NUMBER - give the Cloud MCP stand-in a run.
#
# Reads out/cypress-results.json (written by `cypress run` through
# cypress.config.js), writes out/run.json and out/run-completed.json
# (scripts/results-to-run.py), and puts the run into the ConfigMap the
# stand-in serves, then restarts it so it reads the run at once: a mounted
# ConfigMap otherwise changes only at the kubelet's next sync, up to a
# minute later.
set -euo pipefail
cd "$(dirname "$0")/.."
project="${1:?project ID}"
run="${2:?run number}"

python3 scripts/results-to-run.py out/cypress-results.json "$project" "$run" out
kubectl -n cloud-mcp create configmap cloud-runs --from-file="$project-$run.json=out/run.json" \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl -n cloud-mcp rollout restart deployment/cloud-mcp >/dev/null
kubectl -n cloud-mcp rollout status deployment/cloud-mcp --timeout=120s >/dev/null
echo "the Cloud MCP stand-in serves $project #$run"
