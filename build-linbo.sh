#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
SERVER_URL="${1:-}"
OUTPUT="${2:-$ROOT/build/lcs-linbo-agent}"

if [ -z "$SERVER_URL" ]; then
   echo "Aufruf: $0 SERVER-URL [ZIELDATEI]" >&2
   exit 2
fi

mkdir -p "$(dirname "$OUTPUT")"
OUTPUT="$(cd "$(dirname "$OUTPUT")" && pwd)/$(basename "$OUTPUT")"

cd "$ROOT/linbo"
CGO_ENABLED=0 GOOS="${GOOS:-linux}" GOARCH="${GOARCH:-amd64}" \
   go build -trimpath -ldflags="-s -w -X main.defaultServer=$SERVER_URL" -o "$OUTPUT" .

echo "LINBO-Binärdatei erstellt: $OUTPUT"
