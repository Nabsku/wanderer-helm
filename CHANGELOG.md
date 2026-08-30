# Changelog

## [0.4.0](https://github.com/Nabsku/wanderer-helm/compare/v0.3.0...v0.4.0) (2026-08-30)


### Features

* **chart:** document Garmin trail metadata ([3427b7d](https://github.com/Nabsku/wanderer-helm/commit/3427b7de574af70a186a87ffc1ae81d649ca393e))

## [0.3.0](https://github.com/Nabsku/wanderer-helm/compare/v0.2.3...v0.3.0) (2026-08-30)


### Features

* **sync:** import Garmin activity metadata ([0cf9648](https://github.com/Nabsku/wanderer-helm/commit/0cf9648c01108093d4c1e55257f1167b331c4c54))

## [0.2.3](https://github.com/Nabsku/wanderer-helm/compare/v0.2.2...v0.2.3) (2026-08-26)


### Bug Fixes

* include chart README in package ([5522332](https://github.com/Nabsku/wanderer-helm/commit/5522332b621fdd39d110081bd05a061cb0c2c0c7))

## [0.2.2](https://github.com/Nabsku/wanderer-helm/compare/v0.2.1...v0.2.2) (2026-08-25)


### Bug Fixes

* keep Artifact Hub image metadata current ([5b41e5d](https://github.com/Nabsku/wanderer-helm/commit/5b41e5df933377985bde53ee905c5b63408b9493))
* manage Artifact Hub image version ([8f4a4aa](https://github.com/Nabsku/wanderer-helm/commit/8f4a4aaadcd03747e47de52e0c53827a1d7c0300))

## [0.2.1](https://github.com/Nabsku/wanderer-helm/compare/v0.2.0...v0.2.1) (2026-08-25)


### Bug Fixes

* improve Artifact Hub chart metadata ([821a697](https://github.com/Nabsku/wanderer-helm/commit/821a69767ff64214c834593c9973d7d68abc3d23))

## [0.2.0](https://github.com/Nabsku/wanderer-helm/compare/v0.1.0...v0.2.0) (2026-08-25)


### Features

* add optional Garmin activity synchronizer ([28c57c2](https://github.com/Nabsku/wanderer-helm/commit/28c57c2385046f909b4262ca659381869b6d4db2))
* harden Garmin sync delivery ([5a7898f](https://github.com/Nabsku/wanderer-helm/commit/5a7898f658b1233975c5135c578ffc3cd5bfe378))

## 0.1.0 - 2026-08-24

- Initial Helm chart for Wanderer 0.20.0.
- Added web Deployment, PocketBase and Meilisearch StatefulSets, Services, PVCs, optional Ingress, and optional NetworkPolicy.
- Added generated or externally managed secret support, startup dependency gates, schema validation, render tests, and GHCR release automation.
