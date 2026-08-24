# Wanderer Helm chart

A production-oriented Helm chart for [Wanderer](https://github.com/open-wanderer/wanderer), the self-hosted trail catalogue.

This chart deploys the three components used by Wanderer `v0.20.0`:

- `flomp/wanderer-web:v0.20.0`
- `flomp/wanderer-db:v0.20.0` (PocketBase)
- `getmeili/meilisearch:v1.36.0`

The chart is versioned independently. The first chart release is `0.1.0`.

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

## Upstream references

- [Wanderer repository](https://github.com/open-wanderer/wanderer)
- [Wanderer Docker Compose](https://github.com/open-wanderer/wanderer/blob/v0.20.0/docker-compose.yml)
- [Environment configuration](https://wanderer.to/run/environment-configuration)
- [Backup guidance](https://wanderer.to/run/backend-configuration/backup-server)
- [Plugin installation](https://wanderer.to/run/installation/plugins)

## License

This chart is distributed under the Apache License 2.0. Wanderer itself remains licensed under AGPLv3; see the upstream project for its application license.
