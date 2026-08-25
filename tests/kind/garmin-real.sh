#!/usr/bin/env bash
set -Eeuo pipefail

# Run one real Garmin Connect sync in a disposable/private Kind namespace.
# Credentials are read from the terminal and are never written to this file.

usage() {
  cat <<'EOF'
Usage: tests/kind/garmin-real.sh [options]

Options:
  --enable-schedule   Enable the CronJob after the one-shot sync succeeds.
  -h, --help          Show this help.

Environment overrides:
  KIND_CLUSTER_NAME   Kind cluster name (default: wanderer-garmin-real)
  KIND_NAMESPACE      Namespace prefix for a fresh run (default: wanderer-garmin-real)
  HELM_RELEASE        Helm release name (default: wanderer)
  KIND_NODE_IMAGE     Kind node image (default: kindest/node:v1.31.4)
  SYNC_IMAGE          Local synchronizer image (default: wanderer-garmin-sync:kind)
  GARMIN_STORAGE_SIZE Synchronizer PVC size (default: 10Gi)

A new namespace always gets a new local Wanderer database. The script creates
one local Wanderer user and one raw wanderer_key_ API token in that database;
it never accepts or reuses a token from another Wanderer installation.
EOF
}

enable_schedule=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --enable-schedule)
      enable_schedule=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'unknown option: %s\n\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cluster_name=${KIND_CLUSTER_NAME:-wanderer-garmin-real}
namespace_prefix=${KIND_NAMESPACE:-wanderer-garmin-real}
release=${HELM_RELEASE:-wanderer}
node_image=${KIND_NODE_IMAGE:-kindest/node:v1.31.4}
sync_image=${SYNC_IMAGE:-wanderer-garmin-sync:kind}
storage_size=${GARMIN_STORAGE_SIZE:-10Gi}
namespace="${namespace_prefix}-$(date +%s)"
fullname="$release"
if [[ "$release" != *wanderer* ]]; then
  fullname="${release}-wanderer"
fi
web_name="${fullname}-web"
database_name="${fullname}-database"
search_name="${fullname}-search"
cronjob_name="${fullname}-garmin-sync"
pvc_name="$cronjob_name"
bootstrap_pod="${fullname}-garmin-bootstrap"

out=$(mktemp -d)
kubeconfig="$out/kubeconfig"
port_forward_pid=""
local_web_port=""

cleanup() {
  rc=$?
  set +e
  if [[ -n "$port_forward_pid" ]]; then
    kill "$port_forward_pid" >/dev/null 2>&1 || true
    wait "$port_forward_pid" >/dev/null 2>&1 || true
  fi
  if [[ -s "$kubeconfig" ]]; then
    KUBECONFIG="$kubeconfig" kubectl delete pod "$bootstrap_pod" \
      --namespace "$namespace" --ignore-not-found >/dev/null 2>&1
  fi
  rm -rf "$out"
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

create_secret_from_env_file() {
  local secret_name="$1"
  local env_file="$out/${secret_name}.env"
  shift
  umask 077
  printf '%s\n' "$@" > "$env_file"
  chmod 600 "$env_file"
  kubectl create secret generic "$secret_name" \
    --namespace "$namespace" \
    --from-env-file="$env_file" \
    --dry-run=client -o yaml |
    kubectl apply -f - >/dev/null
  rm -f "$env_file"
}

validate_wanderer_api_token_secret() {
  local valid
  if ! valid="$(kubectl get secret wanderer-garmin-sync \
    --namespace "$namespace" \
    -o jsonpath='{.data.api-token}' |
    python3 -c 'import base64, sys; print(str(base64.b64decode(sys.stdin.buffer.read()).startswith(b"wanderer_key_")).lower())')"; then
    printf 'Could not inspect the Wanderer API token Secret.\n' >&2
    exit 2
  fi
  if [[ "$valid" != true ]]; then
    printf 'The local Wanderer API token Secret is not a raw wanderer_key_ token.\n' >&2
    printf 'This script creates that token inside the fresh Kind database; do not copy a token from another installation.\n' >&2
    exit 2
  fi
}

for command_name in curl docker helm kind kubectl python3; do
  command -v "$command_name" >/dev/null || {
    printf 'missing required command: %s\n' "$command_name" >&2
    exit 2
  }
done

[[ -f "$root/charts/wanderer/Chart.yaml" ]] || {
  printf 'chart not found below %s\n' "$root" >&2
  exit 2
}

if ! kind get clusters 2>/dev/null | grep -Fxq "$cluster_name"; then
  printf 'Creating Kind cluster %s\n' "$cluster_name"
  kind create cluster \
    --name "$cluster_name" \
    --image "$node_image" \
    --wait 180s
fi

kind export kubeconfig --name "$cluster_name" --kubeconfig "$kubeconfig"
export KUBECONFIG="$kubeconfig"
kubectl wait --for=condition=Ready node --all --timeout=180s

printf 'Building synchronizer image %s\n' "$sync_image"
docker build --tag "$sync_image" "$root/sync"
kind load docker-image "$sync_image" --name "$cluster_name"

kubectl create namespace "$namespace" --dry-run=client -o yaml | kubectl apply -f - >/dev/null

if kubectl get secret wanderer-garmin-sync --namespace "$namespace" >/dev/null 2>&1 || \
   kubectl get secret garmin-connect --namespace "$namespace" >/dev/null 2>&1; then
  printf 'One or more test Secrets already exist in namespace %s.\n' "$namespace" >&2
  printf 'Refusing to overwrite them. Run again to use a new timestamped namespace.\n' >&2
  exit 2
fi

read -r -p 'Garmin email: ' garmin_email
read -r -s -p 'Garmin password: ' garmin_password
printf '\n'
[[ -n "$garmin_email" && -n "$garmin_password" ]] || {
  printf 'Garmin email and password cannot be empty.\n' >&2
  exit 2
}
create_secret_from_env_file garmin-connect \
  "garmin-email=${garmin_email}" \
  "garmin-password=${garmin_password}"
unset garmin_email garmin_password

printf 'Installing the suspended Garmin CronJob in %s\n' "$namespace"
helm upgrade --install "$release" "$root/charts/wanderer" \
  --namespace "$namespace" \
  --create-namespace \
  --set-string web.origin="http://${web_name}:3000" \
  --set-string web.config.publicPocketbaseURL="http://${database_name}:8090" \
  --set web.config.disableSignup=false \
  --set web.persistence.enabled=false \
  --set-string database.persistence.data.size=2Gi \
  --set-string database.persistence.plugins.size=1Gi \
  --set-string search.persistence.size=2Gi \
  --set garminSync.enabled=true \
  --set garminSync.suspend=true \
  --set garminSync.sources.garminConnect.enabled=true \
  --set garminSync.sources.officialExport.enabled=false \
  --set-string garminSync.sources.garminConnect.secret.existingSecret=garmin-connect \
  --set-string garminSync.wanderer.existingSecret=wanderer-garmin-sync \
  --set-string garminSync.image.repository="${sync_image%:*}" \
  --set-string garminSync.image.tag="${sync_image##*:}" \
  --set garminSync.image.pullPolicy=IfNotPresent \
  --set-string garminSync.persistence.size="$storage_size"

kubectl rollout status "statefulset/$database_name" --namespace "$namespace" --timeout=10m
kubectl rollout status "statefulset/$search_name" --namespace "$namespace" --timeout=10m
kubectl rollout status "deployment/$web_name" --namespace "$namespace" --timeout=10m

start_web_port_forward() {
  local i
  local_web_port="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"
  kubectl port-forward --address 127.0.0.1 \
    "service/$web_name" "$local_web_port:3000" \
    --namespace "$namespace" >"$out/web-port-forward.log" 2>&1 &
  port_forward_pid=$!

  for ((i = 0; i < 90; i++)); do
    if ! kill -0 "$port_forward_pid" >/dev/null 2>&1; then
      printf 'The local Wanderer port-forward exited before becoming ready.\n' >&2
      return 1
    fi
    if curl --silent --fail --max-time 2 \
      --output /dev/null "http://127.0.0.1:${local_web_port}/login"; then
      printf 'Wanderer is reachable locally on port %s.\n' "$local_web_port"
      return 0
    fi
    sleep 1
  done

  printf 'Timed out waiting for the local Wanderer port-forward.\n' >&2
  return 1
}

bootstrap_wanderer_account() {
  local username email password
  local user_payload="$out/wanderer-user.json"
  local user_response="$out/wanderer-user-response.json"
  local login_payload="$out/wanderer-login.json"
  local login_response="$out/wanderer-login-response.json"
  local token_payload="$out/wanderer-api-token.json"
  local token_response="$out/wanderer-api-token-response.json"
  local cookie_jar="$out/wanderer-cookie.jar"
  local user_id_file="$out/wanderer-user-id"
  local api_token_file="$out/wanderer-api-token"
  local status

  printf '\nThis Kind run has a fresh Wanderer database.\n'
  printf 'The following account is created only in that local test database.\n'
  read -r -p 'Fresh Wanderer username [garmin_test]: ' username
  username=${username:-garmin_test}
  if [[ ${#username} -lt 3 || ! "$username" =~ ^[[:alnum:]_][[:alnum:]_.]*$ ]]; then
    printf 'Wanderer username must be at least 3 characters and contain only letters, numbers, underscores, or dots.\n' >&2
    return 2
  fi
  read -r -p 'Fresh Wanderer email: ' email
  read -r -s -p 'Fresh Wanderer password: ' password
  printf '\n'
  [[ -n "$email" && -n "$password" ]] || {
    printf 'Wanderer email and password cannot be empty.\n' >&2
    return 2
  }
  if (( ${#password} < 8 || ${#password} > 72 )); then
    printf 'Wanderer password must contain 8 to 72 characters.\n' >&2
    return 2
  fi

  BOOTSTRAP_USERNAME="$username" \
  BOOTSTRAP_EMAIL="$email" \
  BOOTSTRAP_PASSWORD="$password" \
  python3 - "$user_payload" <<'PY'
import json
import os
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
path.write_text(json.dumps({
    "username": os.environ["BOOTSTRAP_USERNAME"],
    "email": os.environ["BOOTSTRAP_EMAIL"],
    "password": os.environ["BOOTSTRAP_PASSWORD"],
    "passwordConfirm": os.environ["BOOTSTRAP_PASSWORD"],
}), encoding="utf-8")
path.chmod(0o600)
PY

  if ! status="$(curl --silent --show-error --max-time 30 \
    --request PUT \
    -H 'Accept: application/json' \
    -H 'Content-Type: application/json' \
    --data-binary "@$user_payload" \
    --output "$user_response" \
    --write-out '%{http_code}' \
    "http://127.0.0.1:${local_web_port}/api/v1/user")"; then
    status=000
  fi
  if [[ "$status" != 200 && "$status" != 201 ]]; then
    printf 'Fresh Wanderer account creation failed (HTTP %s).\n' "$status" >&2
    return 1
  fi

  BOOTSTRAP_EMAIL="$email" \
  BOOTSTRAP_PASSWORD="$password" \
  python3 - "$login_payload" <<'PY'
import json
import os
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
path.write_text(json.dumps({
    "email": os.environ["BOOTSTRAP_EMAIL"],
    "password": os.environ["BOOTSTRAP_PASSWORD"],
}), encoding="utf-8")
path.chmod(0o600)
PY

  if ! status="$(curl --silent --show-error --max-time 30 \
    --request POST \
    -H 'Accept: application/json' \
    -H 'Content-Type: application/json' \
    --cookie-jar "$cookie_jar" \
    --data-binary "@$login_payload" \
    --output "$login_response" \
    --write-out '%{http_code}' \
    "http://127.0.0.1:${local_web_port}/api/v1/auth/login")"; then
    status=000
  fi
  if [[ "$status" != 200 ]]; then
    printf 'Fresh Wanderer login failed (HTTP %s).\n' "$status" >&2
    return 1
  fi

  if ! python3 - "$login_response" "$user_id_file" <<'PY'
import json
import pathlib
import sys

response = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
record = response.get("record") or {}
user_id = record.get("id")
if not isinstance(user_id, str) or len(user_id) != 15:
    raise SystemExit(1)
pathlib.Path(sys.argv[2]).write_text(user_id, encoding="utf-8")
pathlib.Path(sys.argv[2]).chmod(0o600)
PY
  then
    printf 'Fresh Wanderer login returned no usable local session.\n' >&2
    return 1
  fi

  BOOTSTRAP_USER_ID="$(<"$user_id_file")" \
  python3 - "$token_payload" <<'PY'
import json
import os
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
path.write_text(json.dumps({
    "name": "garmin-kind-sync",
    "user": os.environ["BOOTSTRAP_USER_ID"],
}), encoding="utf-8")
path.chmod(0o600)
PY

  if ! status="$(curl --silent --show-error --max-time 30 \
    --request PUT \
    -H 'Accept: application/json' \
    -H 'Content-Type: application/json' \
    --cookie "$cookie_jar" \
    --cookie-jar "$cookie_jar" \
    --data-binary "@$token_payload" \
    --output "$token_response" \
    --write-out '%{http_code}' \
    "http://127.0.0.1:${local_web_port}/api/v1/api-token")"; then
    status=000
  fi
  if [[ "$status" != 200 && "$status" != 201 ]]; then
    printf 'Fresh Wanderer API-token creation failed (HTTP %s).\n' "$status" >&2
    return 1
  fi

  if ! python3 - "$token_response" "$api_token_file" <<'PY'
import json
import pathlib
import sys

data = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
token = data.get("rawToken")
if not isinstance(token, str) or not token.startswith("wanderer_key_"):
    raise SystemExit(1)
path = pathlib.Path(sys.argv[2])
path.write_text(token, encoding="utf-8")
path.chmod(0o600)
PY
  then
    printf 'Wanderer did not return a raw wanderer_key_ token.\n' >&2
    return 1
  fi

  kubectl create secret generic wanderer-garmin-sync \
    --namespace "$namespace" \
    --from-file=api-token="$api_token_file" \
    --dry-run=client -o yaml |
    kubectl apply -f - >/dev/null
  validate_wanderer_api_token_secret
  unset username email password
  rm -f "$user_payload" "$user_response" "$login_payload" "$login_response" \
    "$token_payload" "$token_response" "$cookie_jar" "$user_id_file" \
    "$api_token_file"
  printf 'Created a raw Wanderer API token in this fresh Kind database.\n'
}

start_web_port_forward
bootstrap_wanderer_account
validate_wanderer_api_token_secret
helm upgrade "$release" "$root/charts/wanderer" \
  --namespace "$namespace" \
  --reuse-values \
  --set web.config.disableSignup=true >/dev/null
printf 'Wanderer signup is disabled after local account bootstrap.\n'

kubectl delete pod "$bootstrap_pod" --namespace "$namespace" --ignore-not-found >/dev/null
cat <<YAML | kubectl apply -f - >/dev/null
apiVersion: v1
kind: Pod
metadata:
  name: ${bootstrap_pod}
  namespace: ${namespace}
  labels:
    app.kubernetes.io/name: wanderer
    app.kubernetes.io/instance: ${release}
    app.kubernetes.io/component: garmin-sync
spec:
  automountServiceAccountToken: false
  restartPolicy: Never
  securityContext:
    runAsNonRoot: true
    runAsUser: 65532
    runAsGroup: 65532
    fsGroup: 65532
    seccompProfile:
      type: RuntimeDefault
  containers:
    - name: bootstrap
      image: ${sync_image}
      imagePullPolicy: IfNotPresent
      command: ["sleep", "3600"]
      env:
        - name: GARMIN_EMAIL
          valueFrom:
            secretKeyRef:
              name: garmin-connect
              key: garmin-email
        - name: GARMIN_PASSWORD
          valueFrom:
            secretKeyRef:
              name: garmin-connect
              key: garmin-password
      volumeMounts:
        - name: sync-data
          mountPath: /data
  volumes:
    - name: sync-data
      persistentVolumeClaim:
        claimName: ${pvc_name}
YAML

kubectl wait --for=condition=Ready "pod/$bootstrap_pod" \
  --namespace "$namespace" --timeout=5m

kubectl exec -it "pod/$bootstrap_pod" --namespace "$namespace" -- \
  python -c '
import getpass
import os
from pathlib import Path
from garminconnect import Garmin

token_path = Path("/data/state/garmin_tokens.json")
token_path.parent.mkdir(parents=True, exist_ok=True)
client = Garmin(
    os.environ["GARMIN_EMAIL"],
    os.environ["GARMIN_PASSWORD"],
    prompt_mfa=lambda: getpass.getpass("Garmin MFA code: "),
)
client.login(str(token_path))
print(f"Garmin login succeeded; activities={client.count_activities()}")
'

kubectl exec "pod/$bootstrap_pod" --namespace "$namespace" -- \
  python -c '
import os
print(oct(os.stat("/data/state/garmin_tokens.json").st_mode & 0o777))
'
printf 'Token file permissions above should be 0o600.\n'
kubectl delete pod "$bootstrap_pod" --namespace "$namespace" --ignore-not-found >/dev/null

job_name="${cronjob_name}-manual-$(date +%s)"
printf 'Starting one real Garmin sync Job: %s\n' "$job_name"
kubectl create job --from=cronjob/"$cronjob_name" "$job_name" \
  --namespace "$namespace"

if ! kubectl wait --for=condition=complete "job/$job_name" \
  --namespace "$namespace" --timeout=30m; then
  printf 'The sync Job did not complete successfully. Logs follow.\n' >&2
  kubectl get job "$job_name" --namespace "$namespace" -o wide || true
  kubectl logs "job/$job_name" --namespace "$namespace" --all-containers=true || true
  exit 1
fi

kubectl logs "job/$job_name" --namespace "$namespace" --all-containers=true

printf '\nCronJob state:\n'
kubectl get cronjob "$cronjob_name" --namespace "$namespace" \
  -o custom-columns='NAME:.metadata.name,SCHEDULE:.spec.schedule,SUSPEND:.spec.suspend,LAST:.status.lastScheduleTime'

printf '\nSynchronizer resources:\n'
kubectl get jobs,pods,pvc --namespace "$namespace" \
  -l app.kubernetes.io/component=garmin-sync

if (( enable_schedule == 0 )); then
  read -r -p 'Enable the six-hour Garmin CronJob now? [y/N] ' enable_answer
  if [[ "$enable_answer" =~ ^[Yy]$ ]]; then
    enable_schedule=1
  fi
fi

if (( enable_schedule == 1 )); then
  helm upgrade "$release" "$root/charts/wanderer" \
    --namespace "$namespace" \
    --reuse-values \
    --set garminSync.suspend=false >/dev/null
  printf 'CronJob enabled.\n'
else
  printf 'CronJob remains suspended. Enable it later with:\n'
  printf '  helm upgrade %s %s --namespace %s --reuse-values --set garminSync.suspend=false\n' \
    "$release" "$root/charts/wanderer" "$namespace"
fi

printf '\nThe namespace and PVC were left in place for inspection.\n'
printf 'The Garmin Secret and token store remain in the cluster by design.\n'
printf 'Revoke the test API token and remove the namespace when finished.\n'
