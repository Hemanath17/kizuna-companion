-- Kizuna schema v2, Phase 2 Step 2.2 (safety). See PLAN.md sections 2b, 8, 16
-- and ARCHITECTURE.md sections 5.1, 5.2.

create table crisis_events (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references profiles (id) on delete cascade,
  message_id uuid not null references messages (id) on delete cascade,
  risk_category text not null,
  severity text not null,
  stage1_signal jsonb not null,
  stage2_reason text,
  policy_outcome text not null,
  created_at timestamptz not null default now()
);

create index crisis_events_user_id_created_at_idx on crisis_events (user_id, created_at desc);

alter table crisis_events enable row level security;

create policy crisis_events_owner_access on crisis_events
  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);

-- Session-risk mechanism: computed on hydrate over the window, NOT a
-- persisted `conversations.session_risk` column. A persisted running column
-- needs its own incremental-update write path on every turn -- another
-- place for the staleness/drift PLAN.md section 16's "a mechanism is not a
-- guarantee" principle warns about -- for a value cheap enough to recompute
-- from the same message window already hydrated for generation, so it can
-- never disagree with what Stage 2 actually sees. See agent-python/safety.py
-- (compute_session_risk). No schema change needed for this choice.
