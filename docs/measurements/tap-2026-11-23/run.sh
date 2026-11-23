#!/bin/bash
# One measurement run: a fresh container with no network, Cypress open with
# Electron, then a fresh claude -p in safe mode whose only tool is Bash,
# allowed only to docker exec into that container.
set -uo pipefail
M="${TAP_WORK:-$(cd "$(dirname "$0")" && pwd)/work}"  # holds src-a/, src-b/, prompt-a.txt, prompt-b.txt; runs/ is written here
IMG=cypress/included:16.3.0@sha256:3efa8d156a094a6c13ddf3fba616e97f7492706685064b75a39823e1147eca97
id=$1; variant=$2; name=$3
R=$M/runs/$id; mkdir -p "$R/project" "$R/cwd"
/bin/cp -R "$M/src-$variant/." "$R/project/"
docker rm -f "$name" >/dev/null 2>&1
date -u +%FT%TZ > "$R/started"
docker run -d --name "$name" --network none -v "$R/project:/e2e" -w /e2e --entrypoint bash "$IMG" -c 'sleep infinity' >/dev/null
docker inspect "$name" --format '{{.HostConfig.NetworkMode}} {{.Image}}' > "$R/container.txt"
docker exec -d "$name" bash -lc 'Xvfb :99 -screen 0 1280x1024x24'
sleep 2
docker exec -d "$name" bash -lc 'cd /e2e && DISPLAY=:99 CYPRESS_AGENT_MODE=1 cypress open --e2e --browser electron --project /e2e'
ok=0
for i in $(seq 90); do
  docker exec "$name" bash -lc 'cd /e2e && cypress tap status --json' > "$R/attach.json" 2>/dev/null
  if grep -q '"browserAttached": *true' "$R/attach.json"; then ok=1; break; fi
  sleep 2
done
[ "$ok" = 1 ] || { echo "$id: browser never attached" > "$R/FAILED"; docker rm -f "$name" >/dev/null; exit 1; }
prompt=$(sed "s/NAME/$name/g" "$M/prompt-$variant.txt")
cd "$R/cwd"
env -i HOME="$HOME" USER="$USER" PATH="$PATH" TERM=dumb LANG=en_US.UTF-8 \
  claude -p --safe-mode --model claude-opus-5-5 --tools Bash \
  --allowedTools "Bash(docker exec $name:*)" "Bash(docker exec -i $name:*)" \
  --output-format stream-json --verbose "$prompt" > "$R/stream.jsonl" 2> "$R/stderr.txt"
echo $? > "$R/claude-exit"
date -u +%FT%TZ > "$R/agent-done"
# The check after the agent: rerun the spec and read the result.
docker exec "$name" bash -lc 'cd /e2e && cypress tap run cypress/e2e/orders.cy.js' > "$R/check-run.txt" 2>&1
for i in $(seq 60); do
  docker exec "$name" bash -lc 'cd /e2e && cypress tap status --json' > "$R/check.json" 2>/dev/null
  grep -qE '"status": *"(passed|failed)"' "$R/check.json" && break
  sleep 2
done
docker exec "$name" bash -lc 'cd /e2e && cypress tap reporter' > "$R/check-reporter.txt" 2>&1
diff -u "$M/src-$variant/app/index.html" "$R/project/app/index.html" > "$R/index.diff"
diff -ru -x out "$M/src-$variant/cypress" "$R/project/cypress" > "$R/tests.diff"; echo $? > "$R/tests-diff-exit"
diff -u "$M/src-$variant/cypress.config.js" "$R/project/cypress.config.js" > "$R/config.diff"; echo $? > "$R/config-diff-exit"
diff -u "$M/src-$variant/app/orders.json" "$R/project/app/orders.json" > "$R/orders.diff"; echo $? > "$R/orders-diff-exit"
docker rm -f "$name" >/dev/null
echo "$id done"
