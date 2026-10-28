-- Kizuna schema v2 (Phase 1). See PLAN.md section 12 and ARCHITECTURE.md section 7.
-- Scope: only what Phase 1 needs. memory, memory_tombstones, journal_entries,
-- open_threads, crisis_events arrive in their own later phases.

create extension if not exists vector;

create table profiles (
  id uuid primary key references auth.users (id) on delete cascade,
  email text,
  name text,
  is_owner boolean not null default false,
  plan text not null default 'free',
  voice_id text,
  avatar_id text,
  conversation_style text,
  last_framing text,
  last_summarized_at timestamptz,
  created_at timestamptz not null default now()
);

create table conversations (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references profiles (id) on delete cascade,
  created_at timestamptz not null default now()
);

create table messages (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references profiles (id) on delete cascade,
  conversation_id uuid not null references conversations (id) on delete cascade,
  turn_id uuid not null,
  idempotency_key text not null,
  sequence_number int not null,
  role text not null,
  content text not null,
  status text not null,
  safety_flag boolean not null default false,
  emotion_label text,
  emotion_score float,
  created_at timestamptz not null default now(),
  unique (user_id, idempotency_key)
);

create table outbox (
  id uuid primary key default gen_random_uuid(),
  turn_id uuid not null,
  user_id uuid not null references profiles (id) on delete cascade,
  job_type text not null,
  status text not null default 'pending',
  attempts int not null default 0,
  claimed_at timestamptz,
  created_at timestamptz not null default now()
);

create index messages_user_id_created_at_idx on messages (user_id, created_at desc);
create index outbox_status_idx on outbox (status);

alter table profiles enable row level security;
alter table conversations enable row level security;
alter table messages enable row level security;
alter table outbox enable row level security;

create policy profiles_owner_access on profiles
  for all using (auth.uid() = id) with check (auth.uid() = id);

-- RLS gates rows, not columns: without this, a client can PATCH its own
-- is_owner/plan to anything, since Supabase grants authenticated full
-- column access by default. PLAN.md section 12 requires both.
-- A column-level revoke alone does NOT work here: Postgres's column-level
-- ACL only applies when there's no satisfying table-level grant, and
-- Supabase's default privileges grant ALL at the table level. So revoke the
-- table-level privilege first, then grant back only the writable columns.
revoke insert, update on profiles from authenticated, anon;
grant insert (id, email, name, voice_id, avatar_id, conversation_style, last_framing) on profiles to authenticated;
grant update (email, name, voice_id, avatar_id, conversation_style, last_framing) on profiles to authenticated;

create policy conversations_owner_access on conversations
  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);

create policy messages_owner_access on messages
  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);

create policy outbox_owner_access on outbox
  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);
