-- Kizuna schema v2, Phase 4 Step 4.3 (Kizuna's Reflections). See PLAN.md
-- section 2b (wellbeing spec) / section 9 / Phase 4 decisions and
-- ARCHITECTURE.md 5.11.
--
-- Numbering note (flagged, not silently resolved): the Step 4.3 prompt asked
-- for db/0006_reflections.sql, but 0006 was already taken (Step 3.1's
-- worker-memory-access migration), and 0007 was taken moments earlier by
-- this same session's Step 4.2 journal migration. This file is 0008,
-- continuing the real sequence.

create table reflections (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references profiles (id) on delete cascade,
  period_start timestamptz not null,
  period_end timestamptz not null,
  body text,
  status text not null default 'pending' check (status in ('pending', 'ready', 'rejected', 'failed')),
  prompt_version text,
  validator_version text,
  model text,
  rejection_reason text,
  created_at timestamptz not null default now(),
  generated_at timestamptz,
  -- Idempotent on the window: a repeat request for the same day/week
  -- returns the existing row instead of generating a duplicate.
  unique (user_id, period_start, period_end)
);

create table reflection_notes (
  id uuid primary key default gen_random_uuid(),
  reflection_id uuid not null references reflections (id) on delete cascade,
  user_id uuid not null references profiles (id) on delete cascade,
  body text not null,
  created_at timestamptz not null default now()
);

create index reflections_user_id_created_at_idx on reflections (user_id, created_at desc);
create index reflection_notes_reflection_id_idx on reflection_notes (reflection_id);

alter table reflections enable row level security;
alter table reflection_notes enable row level security;

create policy reflections_owner_access on reflections
  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);

create policy reflection_notes_owner_access on reflection_notes
  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);
