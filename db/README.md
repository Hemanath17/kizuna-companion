# Database (schema v2)

Apply migrations in order to a Supabase Postgres project (pgvector is a Supabase-provided extension; `auth.users` and `auth.uid()` already exist there).

```bash
psql "$DATABASE_URL" -f db/0001_schema_v2.sql
psql "$DATABASE_URL" -f db/0002_safety.sql
psql "$DATABASE_URL" -f db/0003_memory.sql
psql "$DATABASE_URL" -f db/0004_memory_source_kind.sql
psql "$DATABASE_URL" -f db/0005_worker_lease.sql
psql "$DATABASE_URL" -f db/0006_worker_memory_access.sql
psql "$DATABASE_URL" -f db/0007_journal.sql
psql "$DATABASE_URL" -f db/0008_reflections.sql
```

`0005_worker_lease.sql` creates the `kizuna_worker` role (BYPASSRLS, since the worker is a trusted backend process that must see every user's pending jobs, not a client-facing one) but sets no password, the same way every other secret in this repo stays out of checked-in files. Before running the worker against a real project: `alter role kizuna_worker with password '...';` and put that value in `WORKER_DATABASE_URL`.

Or via the Supabase SQL editor: paste each file's contents in order and run.

## Notes

- `profiles.is_owner` and `profiles.plan` are server-only: the migration revokes `insert`/`update` on those two columns from `authenticated` (RLS gates rows, not columns, and Supabase grants `authenticated` full column access by default — the row-level policy alone would let a user `PATCH` their own `is_owner`/`plan`). A server/service-role connection is unaffected by this revoke. The client-facing profile-update endpoint (built in a later phase) will also whitelist editable fields in application code.
- Known gap, deferred on purpose: the per-user policies on `messages` and `outbox` let any authenticated client (even one talking to Supabase directly, bypassing .NET/Python) insert/update `role`, `status`, `safety_flag`, and outbox job rows on their own `user_id`. This step has no auth/trust boundary yet, so it isn't tightened here. Phase 1's trust-boundary work (or Phase 5 hardening) should either move those writes to a service/worker role or add column-level restrictions (e.g. clients insert `role='user'` only; outbox gets no client policy at all), the same pattern used above for `is_owner`/`plan`.
- `journal_entries` and `open_threads` are intentionally out of scope for these migrations; they land in their own later phases per PLAN.md section 13. `crisis_events` landed in `0002_safety.sql` (Phase 2 Step 2.2); `memory`/`memory_tombstones` landed in `0003_memory.sql` (Phase 3 Step 3.1).
- `0002_safety.sql`'s session-risk mechanism is computed on hydrate over the message window, not a persisted `conversations.session_risk` column — see the migration file's own comment for why, and `agent-python/safety.py`'s `compute_session_risk`.
- `0003_memory.sql` only has the `profile` and `episodic` memory types in its CHECK constraint; `reflective` is deferred until the reflection agent that produces it exists (PLAN.md section 6).
- `0004_memory_source_kind.sql` adds `memory.source_kind` (`'stated'` or `'inferred'`, default `'stated'`), closing the PLAN.md section 6 requirement to distinguish what the user said from what the model inferred. `memory_store.search_memory`/`list_memories` surface it; the memory agent (Phase 3 Step 3.3) is the first writer that will actually set `'inferred'`.
- Phase 4 Step 4.1 (PLAN.md Phase 4 decision 1) moved memory correction/deletion ownership entirely into Python, via a new internal-token scope `memory:manage` (`agent-python/auth.py`, `agent-python/memory_routes.py`). The .NET `/memory` endpoints are now thin proxies (`backend-dotnet/Program.cs`); the old `MemoryRepository`/`IMemoryRepository` that duplicated supersede/tombstone logic (and left corrections with a stale embedding, since .NET has no embedding model access) is deleted, not deprecated.
- `0007_journal.sql` (Phase 4 Step 4.2) adds `journal_entries` (.NET-owned writes, per frozen contract #1 -- this is raw user-authored content, not derived memory state) and `memory.source_journal_entry_id`, exclusive with `source_message_id` via a check constraint. **Known limitation, by design:** editing a journal entry (`PATCH /journal/{id}`) does NOT re-run memory extraction. Re-extraction would need supersede semantics across everything the original version of the entry produced, which is out of scope for Phase 4; the entry's memories reflect whatever version of the text existed when the `journal_memory_extraction` outbox job ran, not necessarily the current text.
- `0008_reflections.sql` (Phase 4 Step 4.3) adds `reflections` and `reflection_notes`. Generation is on-demand only (PLAN.md Phase 4 decision 3) -- no cron, no scheduler; `POST /reflections` enqueues a `reflection_generate` outbox job the existing Phase 3 worker processes. A rejected reflection (failed `agent-python/reflection_validator.py`) is excluded from every read endpoint in `backend-dotnet` by status, never surfaced even as an empty shell.
- `outbox.turn_id` is reused as a generic "subject id" column for non-chat job types (`journal_memory_extraction`, `reflection_generate`): it holds the journal entry's or reflection's own id, not a chat turn_id. The column has no foreign key, so this is a safe repurposing rather than a schema change -- see `backend-dotnet/Data/JournalRepository.cs` and `ReflectionRepository.cs`.
