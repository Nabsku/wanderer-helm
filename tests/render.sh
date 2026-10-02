#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
chart="$root/charts/wanderer"
out=$(mktemp -d)
trap 'rm -rf "$out"' EXIT

pb_key=$(printf '%032d' 0)
meili_key=$(printf '%048d' 0)

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

helm template wanderer "$chart" -n wanderer \
  -f "$root/examples/values-garmin-sync.yaml" > "$out/garmin.yaml"
python3 "$root/tests/assert_render.py" "$out/garmin.yaml" --mode garmin

if helm lint "$chart" >/dev/null 2>&1; then
  echo "expected default values to fail because web.origin and publicPocketbaseURL are required" >&2
  exit 1
fi

if helm lint "$chart" -f "$root/examples/values-minimal.yaml" --set secret.pocketbaseEncryptionKey=short >/dev/null 2>&1; then
  echo "expected a short PocketBase key to fail schema validation" >&2
  exit 1
fi

if helm template wanderer "$chart" -n wanderer \
  -f "$root/examples/values-garmin-sync.yaml" \
  --set garminSync.wanderer.existingSecret= >/dev/null 2>&1; then
  echo "expected Garmin sync to require a Wanderer API-token Secret" >&2
  exit 1
fi

if helm template wanderer "$chart" -n wanderer \
  -f "$root/examples/values-garmin-sync.yaml" \
  --set garminSync.sources.garminConnect.enabled=false \
  --set garminSync.sources.officialExport.enabled=false >/dev/null 2>&1; then
  echo "expected Garmin sync to require at least one source" >&2
  exit 1
fi

if helm template wanderer "$chart" -n wanderer \
  -f "$root/examples/values-garmin-sync.yaml" \
  --set networkPolicy.enabled=true >/dev/null 2>&1; then
  echo "expected Garmin NetworkPolicy egress to be explicit" >&2
  exit 1
fi

if helm lint "$chart" -f "$root/examples/values-garmin-sync.yaml" \
  --set garminSync.wanderer.url=not-a-url >/dev/null 2>&1; then
  echo "expected Garmin sync URL schema validation to reject malformed URLs" >&2
  exit 1
fi

if helm lint "$chart" -f "$root/examples/values-minimal.yaml" \
  --set secret.pocketbaseProxySecret=short >"$out/short-proxy.log" 2>&1; then
  echo "expected a short proxy secret to fail schema validation" >&2
  exit 1
fi
grep -q pocketbaseProxySecret "$out/short-proxy.log"

helm template wanderer "$chart" -n wanderer \
  -f "$root/examples/values-minimal.yaml" \
  --set secret.existingSecret=external-secrets \
  --set secret.keys.pocketbaseProxy=custom-proxy > "$out/external.yaml"
python3 - "$out/external.yaml" <<'PY'
import sys
import yaml

docs = list(yaml.safe_load_all(open(sys.argv[1])))
assert not any(doc and doc['kind'] == 'Secret' for doc in docs)
for doc in docs:
    if doc and doc['kind'] in ('Deployment', 'StatefulSet') and doc['metadata']['name'] in ('wanderer-web', 'wanderer-database'):
        env = doc['spec']['template']['spec']['containers'][0]['env']
        proxy = next(entry for entry in env if entry['name'] == 'POCKETBASE_PROXY_SECRET')
        assert proxy['valueFrom']['secretKeyRef'] == {'name': 'external-secrets', 'key': 'custom-proxy'}
PY

helm package "$chart" --destination "$out"
chart_version=$(python3 -c 'import sys, yaml; print(yaml.safe_load(open(sys.argv[1]))["version"])' "$chart/Chart.yaml")
package="$out/wanderer-${chart_version}.tgz"
test -f "$package"
printf 'chart package: %s\n' "$package"
