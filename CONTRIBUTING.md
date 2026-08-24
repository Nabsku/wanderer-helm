# Contributing

1. Read the chart README and upstream Wanderer documentation.
2. Keep the upstream image versions, persistent mount paths, and secret contracts explicit.
3. Add or update a render assertion for every behavior change.
4. Run `tests/render.sh` before opening a pull request.
5. Do not commit credentials, GPS data, rendered Secrets, or cluster-specific kubeconfig files.

Changes that affect data migration, PocketBase backups, public PocketBase exposure, or default security settings require explicit documentation and a focused review.
