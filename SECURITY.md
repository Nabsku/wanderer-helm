# Security policy

## Supported versions

Only the latest chart release is supported. The chart pins the upstream Wanderer application version in `Chart.yaml`; review the upstream Wanderer release notes before upgrading.

## Reporting a vulnerability

Please use a private GitHub Security Advisory for this repository. Do not disclose credentials, GPS tracks, or other private data in a public issue.

The chart does not collect telemetry. It does create Kubernetes resources and can expose PocketBase through an optional Ingress; review those values before deployment.
