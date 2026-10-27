#!/usr/bin/env bash
# install-tools.sh [bin-dir] - install the tools in tools.lock for this
# platform, each checked against its pinned sha256. Prints the directory to
# put on PATH. Already-installed tools with the right hash are kept.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BIN="${1:-$ROOT/.cache/bin}"
case "$(uname -s)-$(uname -m)" in
  Linux-x86_64) platform=linux-amd64 ;;
  Darwin-arm64) platform=darwin-arm64 ;;
  *) echo "no pins for $(uname -s)-$(uname -m); add them to tools.lock" >&2; exit 1 ;;
esac
sha256() { if command -v sha256sum >/dev/null; then sha256sum "$1"; else shasum -a 256 "$1"; fi | cut -d' ' -f1; }

mkdir -p "$BIN/.downloads"
while read -r name version plat url member sum; do
  case "$name" in ''|'#'*) continue ;; esac
  [ "$plat" = "$platform" ] || continue
  marker="$BIN/.downloads/$name-$version-$plat.sha256"
  if [ -x "$BIN/$name" ] && [ "$(cat "$marker" 2>/dev/null)" = "$sum" ]; then
    continue
  fi
  file="$BIN/.downloads/$(basename "$url")"
  curl -fsSL --retry 3 -o "$file" "$url"
  got="$(sha256 "$file")"
  if [ "$got" != "$sum" ]; then
    echo "$name $version: sha256 $got, pinned $sum. Not installing." >&2
    exit 1
  fi
  case "$url" in
    *.tar.gz) tar -xzf "$file" -C "$BIN/.downloads" "$member" && mv -f "$BIN/.downloads/$member" "$BIN/$name" ;;
    *) cp -f "$file" "$BIN/$name" ;;
  esac
  chmod +x "$BIN/$name"
  echo "$sum" >"$marker"
  echo "installed $name $version" >&2
done <"$ROOT/tools.lock"
echo "$BIN"
