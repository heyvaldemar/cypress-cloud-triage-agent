#!/usr/bin/env bash
# models.sh <registry|build|serve|agent> [MODEL] - the model leg's model (ADR 6).
#
#   registry  start a registry beside the kind cluster, on kind's network, and
#             point every node's containerd at it as localhost:5001. A pod can
#             then name a model image by digest, which `kind load` cannot give
#             it: containerd files an imported archive under its index's
#             digest and a made-up name, so a pod naming the manifest digest
#             finds nothing
#   build     fetch the file models/models.lock pins for MODEL (by the
#             publisher's revision, checked against its sha256), build it into
#             an image reproducibly, check the digest is the one the lock (the
#             flagship's) pins, and push it to the registry by that digest
#   serve     run llama-server with that image as a read-only volume, in the
#             models namespace, walled (manifests/models/models.yaml)
#   agent     swap the triage-agent ConfigMap's stand-in for the model harness
#             (manifests/model-agent/agent.py) and its model.json. The Job the
#             receiver starts, and what admission holds it to, do not change
#
# MODEL_PLATFORM=linux/arm64 builds for an arm64 laptop; the digest then
# differs from the lock's, which pins the amd64 build CI makes, and build says
# so instead of failing. MODEL_THREADS is llama-server's thread count.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CACHE="${MODEL_CACHE:-$ROOT/.cache/models}"
CLUSTER="${KIND_CLUSTER:-cypress-triage}"
REGISTRY_IMAGE=docker.io/library/registry:3.0.0@sha256:6c5666b861f3505b116bb9aa9b25175e71210414bd010d92035ff64018f9457e
# The flagship's BuildKit (scripts/model.sh there): the reproducible digest
# depends on it.
BUILDKIT=moby/buildkit:v0.32.2@sha256:28a898719c18a33f4e8000685287fa36fd0dd9560c6440227d3a732d79bb41d8
PLATFORM="${MODEL_PLATFORM:-linux/amd64}"
THREADS="${MODEL_THREADS:-4}"
say() { printf '== %s\n' "$*"; }
sha256() { if command -v sha256sum >/dev/null; then sha256sum "$1"; else shasum -a 256 "$1"; fi | cut -d' ' -f1; }

lock_line() {
  grep -vE '^\s*(#|$)' "$ROOT/models/models.lock" | awk -v m="$1" '$1 == m' | head -n 1
}

cmd="${1:-}"
MODEL="${2:-}"
case "$cmd" in
  registry)
    if ! docker inspect kind-registry >/dev/null 2>&1; then
      docker run -d --restart=no --name kind-registry --network kind -p 127.0.0.1:5001:5000 "$REGISTRY_IMAGE" >/dev/null
    fi
    for node in $(kind get nodes --name "$CLUSTER"); do
      docker exec "$node" sh -c 'mkdir -p /etc/containerd/certs.d/localhost:5001 &&
        printf "[host.\"http://kind-registry:5000\"]\n" >/etc/containerd/certs.d/localhost:5001/hosts.toml'
    done
    for _ in $(seq 1 30); do curl -fs -o /dev/null http://127.0.0.1:5001/v2/ && break; sleep 1; done
    say "registry kind-registry on kind's network, localhost:5001 on every node"
    ;;
  build)
    [ -n "$MODEL" ] || { echo "usage: models.sh build MODEL" >&2; exit 2; }
    read -r _name url sha _rest < <(lock_line "$MODEL") || { echo "$MODEL is not in models/models.lock" >&2; exit 1; }
    pinned="${_rest##* }"
    dir="$CACHE/$MODEL"
    mkdir -p "$dir"
    if [ ! -f "$dir/model.gguf" ] || [ "$(sha256 "$dir/model.gguf")" != "$sha" ]; then
      curl -fL --retry 3 --retry-all-errors -o "$dir/model.gguf" "$url"
    fi
    [ "$(sha256 "$dir/model.gguf")" = "$sha" ] || { echo "$MODEL: the file's sha256 is not the pinned $sha" >&2; exit 1; }
    cp "$ROOT/models/Dockerfile" "$dir/Dockerfile"
    docker buildx inspect models >/dev/null 2>&1 \
      || docker buildx create --name models --driver docker-container --driver-opt "image=$BUILDKIT" >/dev/null
    layout="$CACHE/$MODEL.layout"
    rm -rf "$layout" "$layout.tar"
    SOURCE_DATE_EPOCH=0 docker buildx build --builder models --platform "$PLATFORM" --provenance=false --sbom=false \
      --output "type=oci,dest=$layout.tar,rewrite-timestamp=true,compression=uncompressed" "$dir" >&2
    mkdir -p "$layout" && tar -xf "$layout.tar" -C "$layout" && rm -f "$layout.tar"
    digest="$(python3 -I "$ROOT/scripts/push-layout.py" "$layout" 127.0.0.1:5001 "models/$MODEL")"
    # The registry holds it now; the layout would hold it a second time on
    # the runner's disk. The file stays, for the cache.
    rm -rf "$layout"
    docker buildx rm models >/dev/null 2>&1 || true
    printf '%s\n' "$digest" >"$CACHE/$MODEL.digest"
    if [ "$digest" != "$pinned" ]; then
      if [ "$PLATFORM" = linux/amd64 ]; then
        echo "$MODEL built as $digest; models/models.lock pins $pinned. The build is not reproducible, or the pin is stale." >&2
        exit 1
      fi
      say "$MODEL built for $PLATFORM as $digest (the lock pins the amd64 build, $pinned)"
    else
      say "$MODEL built as $digest, the digest models/models.lock pins, and pushed by it"
    fi
    ;;
  serve)
    [ -n "$MODEL" ] || { echo "usage: models.sh serve MODEL" >&2; exit 2; }
    digest="$(cat "$CACHE/$MODEL.digest")"
    sed -e "s|@MODEL@|localhost:5001/models/$MODEL@$digest|" -e "s|@ALIAS@|$MODEL|" -e "s|@THREADS@|$THREADS|" \
      "$ROOT/manifests/models/models.yaml" | kubectl apply -f - >/dev/null
    kubectl -n models rollout status deployment/llama --timeout=600s >/dev/null
    say "llama-server serves $MODEL from localhost:5001/models/$MODEL@${digest:0:19}, $THREADS threads"
    ;;
  agent)
    [ -n "$MODEL" ] || { echo "usage: models.sh agent MODEL" >&2; exit 2; }
    cfg="$(mktemp -d)"
    python3 - "$MODEL" "$THREADS" "$ROOT/models/pricing.json" >"$cfg/model.json" <<'PY'
import json, sys
model, threads, pricing = sys.argv[1], int(sys.argv[2]), json.load(open(sys.argv[3]))
print(json.dumps({"url": "http://llama.models.svc.cluster.local:8080", "model": model, "threads": threads,
                  "max_turns": 8, "max_tokens": 400, "temperature": 0.6, "deadline_seconds": 240,
                  "usd_per_vcpu_hour": pricing["usd_per_vcpu_hour"]}))
PY
    kubectl -n triage-agents create configmap triage-agent \
      --from-file=agent.py="$ROOT/manifests/model-agent/agent.py" --from-file=model.json="$cfg/model.json" \
      --dry-run=client -o yaml | kubectl apply -f - >/dev/null
    rm -rf "$cfg"
    say "the triage agent is the model harness, asking $MODEL"
    ;;
  *)
    sed -n '2,24p' "$0" >&2
    exit 2
    ;;
esac
