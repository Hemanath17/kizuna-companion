-- Kizuna schema v2, Phase 4 Step 4.2 (My Journal). See PLAN.md section 9 /
-- Phase 4 decisions and ARCHITECTURE.md 5.10.
--
-- Numbering note (flagged, not silently resolved): the Step 4.2 prompt asked
-- for db/0005_journal.sql, but 0005 and 0006 were already taken by Step
-- 3.2/3.1's worker-lease and worker-memory-access migrations, applied before
-- this step started. This file is 0007, continuing the real sequence.

create table journal_entries (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references profiles (id) on delete cascade,
  body text not null,
  -- Deliberately non-clinical vocabulary (PLAN.md Phase 4 decision 4); null
  -- is a first-class value, since mood is optional and self-reported only.
  mood text check (mood in ('great', 'good', 'okay', 'low', 'rough')),
  -- Same idempotency discipline as messages (PLAN.md section 16 item 3):
  -- POST /journal's dedup gate is this constraint, not check-then-insert.
  idempotency_key text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (user_id, idempotency_key)
);

create index journal_entries_user_id_created_at_idx on journal_entries (user_id, created_at desc);

alter table journal_entries enable row level security;

create policy journal_entries_owner_access on journal_entries
  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);

-- Explicit provenance channel (PLAN.md section 6): a memory came from a chat
-- message OR a journal entry, never implied by which column happens to be
-- set. `on delete set null` mirrors source_message_id's existing FK style --
-- deleting a journal entry does not retroactively delete the memories it
-- produced, only drops the now-stale pointer to its source.
alter table memory add column source_journal_entry_id uuid references journal_entries (id) on delete set null;
alter table memory add constraint memory_source_channel_exclusive
  check (not (source_message_id is not null and source_journal_entry_id is not null));
