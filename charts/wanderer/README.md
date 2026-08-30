# Wanderer Helm chart

This chart deploys [Wanderer](https://github.com/open-wanderer/wanderer), a self-hosted trail catalogue, with PocketBase and Meilisearch.

- Wanderer web
- Wanderer database (PocketBase)
- Meilisearch
- Optional Garmin synchronizer CronJob

The chart version and the upstream Wanderer version are released separately.

## Install

The chart is published as an OCI artifact in GHCR:

```bash
helm install wanderer \
  oci://ghcr.io/nabsku/charts/wanderer \
  --namespace wanderer \
  --create-namespace
```

To select a specific chart release, add `--version <chart-version>`.

For the full configuration, examples, upgrade notes, and local tests, see the [repository README](https://github.com/Nabsku/wanderer-helm#readme).

## Values to set

Set these values for the public Wanderer URL and PocketBase access:

- `web.origin`: the public Wanderer URL. It controls CORS, cookies, and federation URLs.
- `web.config.publicPocketbaseURL`: a URL reachable by both the browser and the web pod.

The database Ingress is disabled by default. If you enable it, expose PocketBase on a separate hostname and protect its admin endpoint (`/_/`) with your ingress or access-control layer.

## Defaults

The chart defaults to:

- signup disabled;
- private-instance mode enabled;
- one web replica with `Recreate` and RWO upload storage;
- one PocketBase workload and one Meilisearch workload;
- read-only root filesystems, dropped Linux capabilities, `RuntimeDefault` seccomp, and no ServiceAccount token mount;
- generated keys when no existing Secret is supplied;
- Garmin synchronization disabled.

The upstream images do not declare a non-root user. The chart therefore does not set `runAsNonRoot` by default. Check image UIDs and volume permissions before enabling it.

## Secrets and storage

For GitOps, use an External Secrets Operator, SOPS, or another secret manager:

```yaml
secret:
  existingSecret: wanderer-secrets
```

The Secret must contain the keys configured by `secret.keys`. If no existing Secret is supplied, the chart generates values at install time. Back up PocketBase before upgrades. Its PVC contains users, trails, images, and uploaded files. Meilisearch is a rebuildable search index and is not a backup of trail data.

PVCs are retained by default after `helm uninstall`. Delete retained PVCs only after a verified backup and an explicit operator decision.

## Garmin synchronizer

The optional `garminSync` CronJob is suspended by default. It can process Garmin Connect downloads or official Garmin account-export ZIP files and uploads routes through the Wanderer API.

The synchronizer PVC contains raw Garmin exports, route files, the idempotency manifest, and Garmin token state. Treat it as sensitive data. Use encrypted storage, restrict access, and include it in backup and retention policies. Do not put Garmin credentials in Git, chart values, container images, or logs.

The synchronizer preserves original FIT files. Set `garminSync.fitMode: gpx` only when a GPX-only consumer needs conversion; the original FIT file remains in the archive.

Garmin imports keep Garmin activity names and map activity types to Wanderer's Hiking, Walking, Running (including trail running), Biking, Climbing, Skiing, Canoeing, or Other categories. Existing Garmin imports are reconciled by their legacy activity filename on the first upgraded sync. Garmin activity photos are imported when available; the best-effort per-activity limit is controlled by `garminSync.limits.maxPhotosPerActivity` and defaults to 20.

See [`examples/values-garmin-sync.yaml`](https://github.com/Nabsku/wanderer-helm/blob/main/examples/values-garmin-sync.yaml) before enabling the CronJob.

## Links

- [Repository](https://github.com/Nabsku/wanderer-helm)
- [Wanderer](https://github.com/open-wanderer/wanderer)
- [Issues](https://github.com/Nabsku/wanderer-helm/issues)
- [License](https://github.com/Nabsku/wanderer-helm/blob/main/LICENSE)
