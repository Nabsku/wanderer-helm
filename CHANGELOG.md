# Changelog

## 0.2.0 - Unreleased

- Added an opt-in Garmin synchronizer CronJob with official-export ZIP ingestion and Garmin Connect activity downloads.
- Added FIT preservation by default and optional FIT-to-GPX conversion, with durable archives and idempotent upload state.
- Added a pinned synchronizer image, PVC, security defaults, NetworkPolicy egress contract, schema validation, unit tests, and multi-architecture release publishing.

## 0.1.0 - 2026-08-24

- Initial production-oriented Helm chart for Wanderer 0.20.0.
- Added web Deployment, PocketBase and Meilisearch StatefulSets, Services, PVCs, optional Ingress, and optional NetworkPolicy.
- Added generated or externally managed secret support, startup dependency gates, schema validation, render tests, and GHCR release automation.
