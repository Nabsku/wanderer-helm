#!/usr/bin/env bash
set -Eeuo pipefail

# Run the optional Garmin synchronizer against a real Kind cluster and a
# synthetic Wanderer upload endpoint. No Garmin credentials are used.

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cluster_name=${KIND_CLUSTER_NAME:-wanderer-garmin-ci}
node_image=${KIND_NODE_IMAGE:-kindest/node:v1.31.4}
namespace=${KIND_NAMESPACE:-wanderer-garmin-ci-$RANDOM}
release=${HELM_RELEASE:-wanderer}
sync_image=${SYNC_IMAGE:-wanderer-garmin-sync:kind}
sync_repository=${sync_image%:*}
sync_tag=${sync_image##*:}
out=$(mktemp -d)
kubeconfig="$out/kubeconfig"
cluster_created=0

cleanup() {
  status=$?
  set +e
  if [[ -s "$kubeconfig" ]]; then
    KUBECONFIG="$kubeconfig" kubectl delete namespace "$namespace" --ignore-not-found --wait=false >/dev/null 2>&1
  fi
  if (( cluster_created == 1 )); then
    kind delete cluster --name "$cluster_name" >/dev/null 2>&1
  fi
  rm -rf "$out"
  exit "$status"
}
trap cleanup EXIT

for command_name in docker helm kind kubectl python3; do
  command -v "$command_name" >/dev/null || {
    printf 'missing required command: %s\n' "$command_name" >&2
    exit 2
  }
done

if ! kind get clusters | grep -Fxq "$cluster_name"; then
  kind create cluster \
    --name "$cluster_name" \
    --image "$node_image" \
    --wait 180s
  cluster_created=1
fi

kind export kubeconfig --name "$cluster_name" --kubeconfig "$kubeconfig"
export KUBECONFIG="$kubeconfig"
kubectl wait --for=condition=Ready node --all --timeout=180s

printf 'Building synchronizer image %s\n' "$sync_image"
docker build --tag "$sync_image" "$root/sync"
kind load docker-image "$sync_image" --name "$cluster_name"

kubectl create namespace "$namespace" --dry-run=client -o yaml | kubectl apply -f - >/dev/null

mock_configmap="${release}-garmin-mock-script"
kubectl create configmap "$mock_configmap" \
  --namespace "$namespace" \
  --from-file=server.py="$root/tests/kind/mock_wanderer.py" \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null

kubectl apply --namespace "$namespace" -f - <<YAML
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ${release}-garmin-mock
  labels:
    app.kubernetes.io/name: wanderer-garmin-mock
spec:
  replicas: 1
  selector:
    matchLabels:
      app.kubernetes.io/name: wanderer-garmin-mock
  template:
    metadata:
      labels:
        app.kubernetes.io/name: wanderer-garmin-mock
    spec:
      automountServiceAccountToken: false
      securityContext:
        runAsNonRoot: true
        runAsUser: 65532
        runAsGroup: 65532
        fsGroup: 65532
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: mock
          image: python:3.12-alpine
          imagePullPolicy: IfNotPresent
          command: ["python", "/mock/server.py"]
          env:
            - name: MOCK_TOKEN
              value: kind-test-token
          ports:
            - name: http
              containerPort: 8080
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities:
              drop: ["ALL"]
          readinessProbe:
            httpGet:
              path: /healthz
              port: http
            periodSeconds: 2
            timeoutSeconds: 2
            failureThreshold: 30
          volumeMounts:
            - name: script
              mountPath: /mock/server.py
              subPath: server.py
            - name: tmp
              mountPath: /tmp
      volumes:
        - name: script
          configMap:
            name: ${mock_configmap}
            defaultMode: 0555
        - name: tmp
          emptyDir: {}
---
apiVersion: v1
kind: Service
metadata:
  name: ${release}-garmin-mock
  labels:
    app.kubernetes.io/name: wanderer-garmin-mock
spec:
  selector:
    app.kubernetes.io/name: wanderer-garmin-mock
  ports:
    - name: http
      port: 8080
      targetPort: http
YAML

kubectl rollout status "deployment/${release}-garmin-mock" --namespace "$namespace" --timeout=180s

kubectl apply --namespace "$namespace" -f - <<YAML
apiVersion: v1
kind: Secret
metadata:
  name: ${release}-garmin-sync
stringData:
  api-token: kind-test-token
YAML

helm upgrade --install "$release" "$root/charts/wanderer" \
  --namespace "$namespace" \
  --create-namespace \
  --set-string web.origin="http://${release}-web:3000" \
  --set-string web.config.publicPocketbaseURL="http://${release}-database:8090" \
  --set web.persistence.enabled=false \
  --set-string database.persistence.data.size=1Gi \
  --set-string database.persistence.plugins.size=1Gi \
  --set-string search.persistence.size=1Gi \
  --set garminSync.enabled=true \
  --set garminSync.suspend=true \
  --set garminSync.sources.garminConnect.enabled=false \
  --set garminSync.sources.officialExport.enabled=true \
  --set-string garminSync.wanderer.url="http://${release}-garmin-mock:8080" \
  --set garminSync.wanderer.existingSecret="${release}-garmin-sync" \
  --set-string garminSync.persistence.size=1Gi \
  --set-string garminSync.image.repository="$sync_repository" \
  --set-string garminSync.image.tag="$sync_tag" \
  --set garminSync.image.pullPolicy=Never \
  --set garminSync.requestTimeoutSeconds=10 \
  --set garminSync.activeDeadlineSeconds=300 \
  --set garminSync.ttlSecondsAfterFinished=300

# The synchronizer PVC uses a WaitForFirstConsumer storage class in Kind. Do
# not ask Helm to wait for the suspended CronJob's PVC before creating a seed
# consumer below.
kubectl rollout status "statefulset/${release}-database" --namespace "$namespace" --timeout=300s
kubectl rollout status "statefulset/${release}-search" --namespace "$namespace" --timeout=300s
kubectl rollout status "deployment/${release}-web" --namespace "$namespace" --timeout=300s
if ! helm test "$release" --namespace "$namespace" --timeout=5m; then
  kubectl get pods --namespace "$namespace" -o wide || true
  exit 1
fi

sync_claim=$(kubectl get pvc --namespace "$namespace" \
  -l app.kubernetes.io/component=garmin-sync \
  -o jsonpath='{.items[0].metadata.name}')
if [[ -z "$sync_claim" ]]; then
  printf 'could not find the synchronizer PVC\n' >&2
  exit 1
fi

fixture="$out/garmin-export.zip"
python3 - "$fixture" <<'PY'
from pathlib import Path
import sys
import zipfile

output = Path(sys.argv[1])
gpx = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<gpx version=\"1.1\" creator=\"wanderer-kind-test\" xmlns=\"http://www.topografix.com/GPX/1/1\">
  <trk><name>Kind integration route</name><trkseg>
    <trkpt lat=\"52.5200\" lon=\"13.4050\"><ele>34</ele></trkpt>
    <trkpt lat=\"52.5205\" lon=\"13.4060\"><ele>35</ele></trkpt>
  </trkseg></trk>
</gpx>
"""
with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
    archive.writestr("DI_CONNECT/activities/kind-route.gpx", gpx)
PY

fixture_configmap="${release}-garmin-fixture"
kubectl create configmap "$fixture_configmap" \
  --namespace "$namespace" \
  --from-file=garmin-export.zip="$fixture" \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null

seed_job="${release}-garmin-seed"
kubectl delete job "$seed_job" --namespace "$namespace" --ignore-not-found >/dev/null
kubectl apply --namespace "$namespace" -f - <<YAML
apiVersion: batch/v1
kind: Job
metadata:
  name: ${seed_job}
spec:
  backoffLimit: 0
  ttlSecondsAfterFinished: 300
  template:
    spec:
      restartPolicy: Never
      automountServiceAccountToken: false
      securityContext:
        runAsNonRoot: true
        runAsUser: 65532
        runAsGroup: 65532
        fsGroup: 65532
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: seed
          image: ${sync_image}
          imagePullPolicy: Never
          command: ["python", "-c"]
          args:
            - >-
              from pathlib import Path; import shutil;
              target=Path('/data/inbox/garmin-export.zip');
              target.parent.mkdir(parents=True, exist_ok=True);
              shutil.copyfile('/fixture/garmin-export.zip', target);
              target.chmod(0o600)
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities:
              drop: ["ALL"]
          volumeMounts:
            - name: sync-data
              mountPath: /data
            - name: fixture
              mountPath: /fixture
      volumes:
        - name: sync-data
          persistentVolumeClaim:
            claimName: ${sync_claim}
        - name: fixture
          configMap:
            name: ${fixture_configmap}
YAML
kubectl wait --for=condition=complete "job/${seed_job}" --namespace "$namespace" --timeout=180s

first_job="${release}-garmin-first"
second_job="${release}-garmin-second"
for job_name in "$first_job" "$second_job"; do
  kubectl delete job "$job_name" --namespace "$namespace" --ignore-not-found >/dev/null
  kubectl create job "$job_name" \
    --namespace "$namespace" \
    --from="cronjob/${release}-garmin-sync" >/dev/null
  if ! kubectl wait --for=condition=complete "job/${job_name}" --namespace "$namespace" --timeout=300s; then
    kubectl logs --namespace "$namespace" "job/${job_name}" || true
    exit 1
  fi
  kubectl logs --namespace "$namespace" "job/${job_name}"
done

first_logs=$(kubectl logs --namespace "$namespace" "job/${first_job}")
second_logs=$(kubectl logs --namespace "$namespace" "job/${second_job}")
grep -F 'sync complete: uploaded=1 skipped=0' <<<"$first_logs"
grep -F 'sync complete: uploaded=0 skipped=1' <<<"$second_logs"

mock_pod=$(kubectl get pod --namespace "$namespace" \
  -l app.kubernetes.io/name=wanderer-garmin-mock \
  -o jsonpath='{.items[0].metadata.name}')
received=$(kubectl exec --namespace "$namespace" "$mock_pod" -- \
  python -c 'import urllib.request; print(urllib.request.urlopen("http://127.0.0.1:8080/received", timeout=5).read().decode())')
printf 'Mock Wanderer received: %s\n' "$received"
python3 - "$received" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
assert payload["count"] == 1, payload
assert len(payload["files"]) == 1, payload
assert payload["files"][0].endswith("kind-route.gpx"), payload
PY

printf 'Kind Garmin integration passed in namespace %s\n' "$namespace"
