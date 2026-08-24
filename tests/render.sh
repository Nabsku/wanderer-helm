#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
chart="$root/charts/wanderer"
out=$(mktemp -d)
trap 'rm -rf "$out"' EXIT

pb_key=0123456789abcdef0123456789abcdef
meili_key=0123456789abcdef0123456789abcdef0123456789abcdef

helm lint --strict "$chart" -f "$root/examples/values-minimal.yaml"
helm lint --strict "$chart" -f "$root/examples/values-production.yaml"

helm template wanderer "$chart" -n wanderer \
  -f "$root/examples/values-minimal.yaml" \
  --set "secret.pocketbaseEncryptionKey=$pb_key" \
  --set "secret.meiliMasterKey=$meili_key" > "$out/minimal.yaml"
python3 "$root/tests/assert_render.py" "$out/minimal.yaml" --mode minimal

helm template wanderer "$chart" -n wanderer \
  -f "$root/examples/values-production.yaml" > "$out/production.yaml"
python3 "$root/tests/assert_render.py" "$out/production.yaml" --mode production

helm template wanderer "$chart" -n wanderer \
  -f "$root/examples/values-networkpolicy.yaml" \
  --set "secret.pocketbaseEncryptionKey=$pb_key" \
  --set "secret.meiliMasterKey=$meili_key" > "$out/network.yaml"
python3 "$root/tests/assert_render.py" "$out/network.yaml" --mode network

if helm lint "$chart" >/dev/null 2>&1; then
  echo "expected default values to fail because web.origin and publicPocketbaseURL are required" >&2
  exit 1
fi

if helm lint "$chart" -f "$root/examples/values-minimal.yaml" --set secret.pocketbaseEncryptionKey=short >/dev/null 2>&1; then
  echo "expected a short PocketBase key to fail schema validation" >&2
  exit 1
fi

helm package "$chart" --destination "$out"
test -f "$out/wanderer-0.1.0.tgz"
printf 'chart package: %s\n' "$out/wanderer-0.1.0.tgz"
