# Wanderer Helm chart

A production-oriented Helm chart for [Wanderer](https://github.com/open-wanderer/wanderer), the self-hosted trail catalogue.

This chart deploys the three components used by Wanderer `v0.20.0`:

- `flomp/wanderer-web:v0.20.0`
- `flomp/wanderer-db:v0.20.0` (PocketBase)
- `getmeili/meilisearch:v1.36.0`

The chart is versioned independently. The initial chart release is `0.1.0`; the optional Garmin synchronizer is part of the next `0.2.0` release.

## Important production contracts

Set both URLs explicitly:

- `web.origin`: the public Wanderer URL. It controls CORS, cookies, and federation URLs.
- `web.config.publicPocketbaseURL`: a URL reachable by the browser **and** by the web pod. The frontend constructs a PocketBase client in browser code; a Kubernetes-only Service name such as `http://wanderer-database:8090` is not a valid public value.

The database Ingress is disabled by default. If you enable it, expose PocketBase on a separate hostname and protect the PocketBase admin endpoint (`/_/`) with your ingress or access-control layer. The chart does not add authentication in front of that endpoint.

The chart defaults to:

- signup disabled;
- private-instance mode enabled;
- a 100 MiB SvelteKit request-body limit;
- read-only root filesystems, dropped Linux capabilities, `RuntimeDefault` seccomp, and no ServiceAccount token mount;
- one web replica with `Recreate`, because the upload watcher uses a local directory and the default volume is RWO;
- singleton PocketBase and Meilisearch workloads;
- generated stable keys when no external Secret is supplied.

The upstream images do not declare a non-root `USER`. The chart therefore does not set `runAsNonRoot` by default. Verify image UID and volume permissions before enabling it.

## Install from GHCR

The package is published as an OCI Helm chart:

```bash
helm install wanderer \
  oci://ghcr.io/nabsku/charts/wanderer \
  --version 0.1.0 \
  --namespace wanderer \
  --create-namespace \
  --values examples/values-production.yaml
```

The production example is a starting point only. Replace the example hosts, storage class, TLS Secret names, resource budgets, and external Secret reference before use.

The `0.2.0` synchronizer changes are currently on the feature branch. Do not use `--version 0.2.0` until that chart and its companion image have been published.

For a local smoke installation:

```bash
helm install wanderer ./charts/wanderer \
  --namespace wanderer \
  --create-namespace \
  --values examples/values-minimal.yaml
```

The local example uses `localhost` values for linting and local port-forward scenarios. It is not a public deployment configuration.

## Secrets

For GitOps, prefer an External Secrets Operator, SOPS, or another secret manager:

```yaml
secret:
  existingSecret: wanderer-secrets
```

The referenced Secret must contain the keys configured by `secret.keys`:

- `pocketbase-encryption-key`: exactly 32 characters;
- `meili-master-key`: at least 32 characters.

If `secret.existingSecret` is empty, the chart creates a Secret and generates high-entropy values at install time. Helm `lookup` preserves chart-generated values across upgrades. Do not rotate either key by changing a normal Helm value after data exists:

- changing the PocketBase key can make encrypted data unreadable;
- changing the Meilisearch key requires coordinated restart and index handling.

## Storage and recovery

| Workload | Mount | Role | Backup authority |
|---|---|---|---|
| PocketBase | `/pb_data` | SQLite, users, trails, images, uploaded files | **Primary source of truth** |
| PocketBase | `/data/plugins` | Installed plugin bundles | Durable plugin state |
| Meilisearch | `/meili_data` | Search index | Rebuildable cache |
| Web | `/app/uploads` | Optional file-watch/drop folder | Only pending watcher input |

PVCs are retained by default with `helm.sh/resource-policy: keep`. This protects data during `helm uninstall`, but it also means uninstall does not remove storage. Delete retained PVCs only after a verified backup and explicit operator decision.

Back up PocketBase before application upgrades. Wanderer documents PocketBase dashboard backups and direct `pb_data` backups. Restores are supported only within the same upstream minor version. Do not treat a Helm rollback as a data-migration rollback.

Meilisearch can be recreated and rebuilt from PocketBase. Do not use its PVC as the only backup of trail data.

## First-user bootstrap

The secure default is `web.config.disableSignup: true`. For a new empty instance, temporarily set it to `false`, create the first user, then set it back to `true` in the next upgrade.

## Networking

The chart keeps PocketBase and Meilisearch as internal `ClusterIP` Services. The web Ingress is optional.

If `networkPolicy.enabled` is true, provide:

- `networkPolicy.webIngress` for your ingress controller or trusted clients;
- `networkPolicy.webEgress` for external geocoding/routing APIs and the public PocketBase URL;
- `networkPolicy.databaseEgress` for any external plugin or SMTP traffic;
- `networkPolicy.searchEgress` if Meilisearch needs non-DNS external traffic.

The chart always adds same-namespace service traffic required by the three workloads and DNS egress. An empty `webIngress` intentionally denies external access.

## Optional Garmin synchronizer

The chart includes an opt-in `garminSync` CronJob. It uses one shared pipeline for:

- automated Garmin Connect activity downloads through the community `garminconnect` wrapper;
- official Garmin account-export ZIP files placed in the synchronizer PVC inbox.

A CronJob is used instead of a sidecar. It gives each sync a bounded run, prevents overlapping runs with `concurrencyPolicy: Forbid`, and keeps the RWO synchronizer PVC separate from Wanderer's web upload volume.

Wanderer `v0.20.0` accepts FIT files directly. The default `garminSync.fitMode: preserve` therefore keeps the original FIT file and uploads it without conversion. Set `fitMode: gpx` only when a GPX-only consumer needs the conversion; the raw FIT remains in the archive PVC. The synchronizer uses `fitdecode` and `gpxpy` for this conversion.

The Garmin Connect path is not an official Garmin personal API. It may break when Garmin changes authentication. The synchronizer fails with an explicit MFA-required error rather than prompting inside a CronJob. Bootstrap the token store in a trusted interactive workflow, then place it on the synchronizer PVC; otherwise use the official export path. The official account-wide export is more stable but is requested manually through Garmin and delivered as a ZIP.

Create two Secrets before enabling both sources:

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: wanderer-garmin-sync
stringData:
  api-token: "<Wanderer API token>"
---
apiVersion: v1
kind: Secret
metadata:
  name: garmin-connect
stringData:
  garmin-email: "<Garmin email>"
  garmin-password: "<Garmin password>"
```

Then use `examples/values-garmin-sync.yaml` as a starting point. Keep the feature disabled until the Secrets and storage are ready. The CronJob stores the idempotency manifest, Garmin token state, raw downloads, and official export files on a separate PVC. It does **not** mount the web upload PVC, so it does not depend on `ReadWriteMany` storage. Uploads go through Wanderer’s internal API with duplicate suppression.

For the official path, request Garmin's account export manually, place the resulting ZIP in `/data/inbox` on the synchronizer PVC, and trigger a one-shot run when needed:

```bash
kubectl create job --from=cronjob/wanderer-garmin-sync wanderer-garmin-sync-manual \
  --namespace wanderer
```

Use your cluster's approved PVC file-transfer method to place the ZIP in the inbox before starting that Job. The synchronizer archives the accepted route files and records their digests so a repeated run does not upload them again.

The synchronizer PVC contains raw Garmin exports, route files, and the Garmin token store. Treat it as sensitive data: use encrypted storage, restrict PVC access, and include it in backup and retention policies.

The CronJob is resilient to transient Wanderer failures. Each route gets up to four upload attempts by default, with exponential delays of 5, 10, and 20 seconds capped at 60 seconds. Network timeouts and HTTP 408, 425, 429, and 5xx responses are retried; permanent 4xx responses fail immediately. The manifest is saved after every successful route, so a Kubernetes Job retry resumes the unfinished set instead of re-uploading completed routes. Tune `garminSync.uploadRetries`, `garminSync.retryBackoffSeconds`, `garminSync.retryMaxBackoffSeconds`, and `garminSync.requestTimeoutSeconds` for the deployment.

The default Job deadline is six hours. This is based on a measured local run that projected roughly four hours for 829 activities and leaves room for bounded retries. If the deadline is exceeded, Kubernetes fails the Job and applies `backoffLimit`; completed routes remain recorded in the manifest.

If `networkPolicy.enabled` is true and Garmin Connect sync is enabled, add a narrow `networkPolicy.garminSyncEgress` rule for HTTPS access. Kubernetes NetworkPolicy cannot express a DNS hostname by itself; use the narrowest IP or CNI-specific FQDN policy available in your cluster.

## Resource and upload budgets

The chart starts with requests of 100m CPU and 256 MiB for web/PocketBase, and 100m CPU plus 512 MiB for Meilisearch. These are bootstrap budgets, not performance guarantees. Measure CPU, memory, search indexing time, and upload failures, then adjust `*.resources`.

`BODY_SIZE_LIMIT` is set to `104857600` bytes (100 MiB). If an upload exceeds that limit, raise the chart value and the corresponding ingress-proxy limit together. Do not silently switch to `Infinity` without an explicit upstream proxy and resource policy.

## Upgrade guidance

Before changing `web.image.tag`, `database.image.tag`, or the database image digest:

1. Take an application-consistent PocketBase backup or volume snapshot.
2. Read the upstream Wanderer changelog for the target release.
3. Confirm the restore-version requirement.
4. Upgrade in a maintenance window and verify all three readiness probes.
5. Exercise login, trail upload, map display, search, and plugin loading.

PocketBase and Meilisearch remain singleton workloads. Do not add HPA or replicas to them. Web replicas greater than one require shared RWX upload storage and a tested watcher strategy; the default `Recreate` strategy deliberately avoids RWO attach races.

## Verification

The repository CI runs Helm linting, schema negative tests, multi-configuration rendering, YAML parsing, and Kubernetes manifest validation. The chart also includes a Helm test Pod for web connectivity.

Local checks:

```bash
helm lint charts/wanderer -f examples/values-minimal.yaml
helm template wanderer charts/wanderer \
  --namespace wanderer \
  --values examples/values-production.yaml
helm package charts/wanderer
```

## Kind integration test

The repository includes a real Kubernetes integration test for the optional synchronizer. It builds the synchronizer image, loads it into Kind, installs Wanderer with the Garmin CronJob enabled, seeds a synthetic Garmin account-export ZIP into the synchronizer PVC, and runs the CronJob twice against a small in-cluster Wanderer API test double. The second run must skip the already-uploaded route, which verifies the persistent manifest and duplicate protection.

The test uses synthetic GPX data and a test token. It does not contact Garmin and does not need Garmin credentials.

With `kind`, `kubectl`, Helm, Docker, and Python 3 installed locally:

```bash
tests/kind/garmin-e2e.sh
```

Run the same GitHub Actions job with [`act`](https://github.com/nektos/act):

```bash
act workflow_dispatch \
  --workflows .github/workflows/kind.yml \
  --job kind-garmin \
  --container-architecture linux/amd64 \
  --platform ubuntu-latest=catthehacker/ubuntu:act-latest
```

The current `act` runner mounts the Docker socket automatically. If an older `act` build does not, configure its Docker socket mount once in the runner configuration instead of adding a duplicate mount to this command.

### Real Garmin account smoke test

Do not put real Garmin credentials into `act` or GitHub Actions. Use the separate interactive Kind script instead:

```bash
tests/kind/garmin-real.sh
```

The script:

1. Prompts for Garmin credentials in the terminal. It never accepts a Wanderer token from another installation.
2. Creates Kubernetes Secrets without putting values in the command line or repository.
3. Builds and loads the synchronizer image into Kind.
4. Installs a fresh local Wanderer database with signup temporarily enabled.
5. Prompts for a local Wanderer test account and creates it through the local API.
6. Logs into that local account and creates a raw `wanderer_key_...` API token in the same fresh database.
7. Disables signup again and installs the Garmin CronJob suspended.
8. Starts a temporary restricted pod on the synchronizer PVC.
9. Performs the Garmin login and prompts for MFA when required.
10. Stores the Garmin token state on the PVC with owner-only permissions.
11. Runs one real synchronization Job and prints the CronJob state and logs.
12. Asks whether to enable the six-hour schedule.

The local Wanderer username must contain only letters, numbers, underscores, and dots; the default `garmin_test` is valid. The namespace and PVC are intentionally kept after the run so the archive, manifest, and CronJob can be inspected. Remove the local test account/token and delete the namespace when finished. The Garmin Connect source is unofficial and the current implementation does not provide a date or activity-count filter; a live run walks the configured activity pages.

Use `--enable-schedule` to enable the schedule automatically after a successful one-shot run:

```bash
tests/kind/garmin-real.sh --enable-schedule
```

Every invocation appends a timestamp to `KIND_NAMESPACE`, so it creates a new Wanderer database instead of reusing a previous test. The generated API token is stored only in that namespace's Wanderer database and Kubernetes Secret; a PocketBase login JWT or token from another Wanderer deployment will return HTTP 401.

## Upstream references

- [Wanderer repository](https://github.com/open-wanderer/wanderer)
- [Wanderer Docker Compose](https://github.com/open-wanderer/wanderer/blob/v0.20.0/docker-compose.yml)
- [Environment configuration](https://wanderer.to/run/environment-configuration)
- [Backup guidance](https://wanderer.to/run/backend-configuration/backup-server)
- [Plugin installation](https://wanderer.to/run/installation/plugins)
- [python-garminconnect](https://github.com/cyberjunky/python-garminconnect)
- [fitdecode](https://github.com/polyvertex/fitdecode)
- [gpxpy](https://github.com/tkrajina/gpxpy)

## License

This chart is distributed under the Apache License 2.0. Wanderer itself remains licensed under AGPLv3; see the upstream project for its application license.
