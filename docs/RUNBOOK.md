# yral-rishi-analytics — operator runbook

Privileged / gated steps for the read-only analytics service. Nothing here runs
automatically — each item is a per-action op Rishi performs (CLAUDE.md Rule 9,
Part C). Read-only, replica-only, isolated on rishi-6; the chat service is never
touched.

## 1. Create the DB roles + schema (once, gated)

`db/setup_analytics_ro.sql` creates `analytics_ro` (replica, read-only) +
`analytics_rw` (leader, writes the `analytics` schema only) + the `analytics`
schema. Run **once**, **pg_dump first**, against the Patroni **leader**,
connected to the `yral_agent_db` database.

### Password substitution — DO NOT put real passwords in the file

The script ships with placeholder passwords (`REPLACE_ME_VIA_SWARM_SECRET`) so a
real secret never lands in a file or in shell history. Procedure:

1. Run the script **as-is** (placeholder passwords create the roles).
2. Immediately set the real passwords in the same `psql` session:
   ```sql
   ALTER ROLE analytics_ro PASSWORD '<real-ro-password>';
   ALTER ROLE analytics_rw PASSWORD '<real-rw-password>';
   ```
   Prefer this `ALTER ROLE`-after approach over `sed`-substituting the file —
   the password never touches disk or history.
3. Store the full DSNs (with those passwords) in Swarm secrets:
   - `analytics_db_dsn` → `analytics_ro` @ a **replica** endpoint.
   - `analytics_db_dsn_rw` → `analytics_rw` @ the **leader** endpoint.

### Both roles must survive the merge to `main`

The PR stack splits the script: **Phase 0** PR has only `analytics_ro`; **Phase
A** PR adds `analytics_rw`. When the stack collapses to `main`, `main`'s
`setup_analytics_ro.sql` **must contain BOTH roles** — sequential merges must not
drop `analytics_rw`. Verify the merged file has both before running it.

### One-time fix — `login_audit` ownership (gated)

`analytics.analytics_login_audit` was hand-created as `postgres`, so `analytics_rw`
can't write it (OAuth callback failed). Run **once** on the leader with Rishi's go:

```sql
-- db/fix_login_audit_owner.sql
ALTER TABLE analytics.analytics_login_audit OWNER TO analytics_rw;
```

**Invariant going forward:** every `analytics`-schema object is created AND owned
by `analytics_rw` (the service's `ensure_table` path). Never hand-create an
analytics object as `postgres` again — that's what caused this.

## 2. Swarm secrets

| Secret | Role / endpoint | Used by |
|---|---|---|
| `analytics_db_dsn` | `analytics_ro` @ replica | all dashboard + heavy reads |
| `analytics_db_dsn_rw` | `analytics_rw` @ leader | hourly refresh write only |
| `analytics_google_oauth_client_secret` | Google OAuth (Phase B) | login |
| `HEADLINE_TOKEN` (env or secret) | temp shared secret | `/headline` until login lands |

The hourly refresher stays **dormant** until `analytics_db_dsn_rw` exists — no
leader contact at all until then.

## 3. Deploy to rishi-6

`docker stack deploy` with `deploy/stack.yml` (rishi-6 placement, `:stable`
rollback tag). The service must have **zero** effect on the chat path if it
falls over — own image, role, secrets, resource caps, `/healthz`.

## 4. Caddy (Phase B)

`analytics.rishi.yral.com` is a **separate** stanza on rishi-1/2 — it must not
modify the `agent.rishi.yral.com` or chat-ai routes. Gated on Rishi.
