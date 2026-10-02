---
default: minor
---

### Upgrade warning: Wanderer v0.21.0 requires a shared proxy secret

**Before upgrading with `secret.existingSecret`, add `pocketbase-proxy-secret`
to that Secret** (or the key configured by `secret.keys.pocketbaseProxy`).
Generate a random value with `openssl rand -hex 32` and store it in your
secret manager, not in Git. Web and database must use the same value for
`POCKETBASE_PROXY_SECRET`.

A missing Secret key prevents the pods from starting. An empty or mismatched
value causes incoming federation to fail. Chart-managed Secrets generate the
new proxy value on upgrade and preserve existing encryption and search keys.

If you rotate an externally managed proxy secret, restart both web and
database together. External Secret content changes do not automatically
restart pods. Back up PocketBase before upgrading; a Helm rollback does not
undo application data migrations.
