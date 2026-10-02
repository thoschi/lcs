#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
SERVER_URL="${1:-}"
OUTPUT="${2:-$ROOT/build/lcs-linbo-agent}"
CA_FILE="${LCS_CA_FILE:-}"

if [ -z "$SERVER_URL" ]; then
   echo "Aufruf: $0 SERVER-URL [ZIELDATEI]" >&2
   exit 2
fi

if [ -z "$CA_FILE" ]; then
   for candidate in /etc/ssl/certs/ca-certificates.crt /etc/pki/tls/certs/ca-bundle.crt /etc/ssl/ca-bundle.pem; do
      if [ -r "$candidate" ]; then
         CA_FILE="$candidate"
         break
      fi
   done
fi
if [ -z "$CA_FILE" ] || [ ! -r "$CA_FILE" ]; then
   echo "Kein CA-Bündel gefunden. LCS_CA_FILE muss auf eine lesbare PEM-Datei zeigen." >&2
   exit 2
fi

CA_BASE64="$(base64 < "$CA_FILE" | tr -d '\n')"

mkdir -p "$(dirname "$OUTPUT")"
OUTPUT="$(cd "$(dirname "$OUTPUT")" && pwd)/$(basename "$OUTPUT")"

BUILD_DIR="$(mktemp -d)"
trap 'rm -rf "$BUILD_DIR"' EXIT
cp "$ROOT/linbo/main.go" "$ROOT/linbo/go.mod" "$BUILD_DIR/"
printf 'package main\n\nfunc init() { defaultCAPEMBase64 = `%s` }\n' "$CA_BASE64" > "$BUILD_DIR/ca_bundle.go"

cd "$BUILD_DIR"
CGO_ENABLED=0 GOOS="${GOOS:-linux}" GOARCH="${GOARCH:-amd64}" \
   go build -trimpath -ldflags="-s -w -X main.defaultServer=$SERVER_URL" -o "$OUTPUT" .

echo "LINBO-Binärdatei erstellt: $OUTPUT"
