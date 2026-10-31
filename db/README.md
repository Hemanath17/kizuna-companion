# Database (schema v2)

Apply migrations in order to a Supabase Postgres project (pgvector is a Supabase-provided extension; `auth.users` and `auth.uid()` already exist there).

```bash
psql "$DATABASE_URL" -f db/0001_schema_v2.sql
psql "$DATABASE_URL" -f db/0002_safety.sql
```

Or via the Supabase SQL editor: paste each file's contents in order and run.

## Notes

- `profiles.is_owner` and `profiles.plan` are server-only: the migration revokes `insert`/`update` on those two columns from `authenticated` (RLS gates rows, not columns, and Supabase grants `authenticated` full column access by default — the row-level policy alone would let a user `PATCH` their own `is_owner`/`plan`). A server/service-role connection is unaffected by this revoke. The client-facing profile-update endpoint (built in a later phase) will also whitelist editable fields in application code.
- Known gap, deferred on purpose: the per-user policies on `messages` and `outbox` let any authenticated client (even one talking to Supabase directly, bypassing .NET/Python) insert/update `role`, `status`, `safety_flag`, and outbox job rows on their own `user_id`. This step has no auth/trust boundary yet, so it isn't tightened here. Phase 1's trust-boundary work (or Phase 5 hardening) should either move those writes to a service/worker role or add column-level restrictions (e.g. clients insert `role='user'` only; outbox gets no client policy at all), the same pattern used above for `is_owner`/`plan`.
- Richer tables (`memory`, `memory_tombstones`, `journal_entries`, `open_threads`) are intentionally out of scope for these migrations; they land in their own later phases per PLAN.md section 13. `crisis_events` landed in `0002_safety.sql` (Phase 2 Step 2.2).
- `0002_safety.sql`'s session-risk mechanism is computed on hydrate over the message window, not a persisted `conversations.session_risk` column — see the migration file's own comment for why, and `agent-python/safety.py`'s `compute_session_risk`.
