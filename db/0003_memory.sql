-- Kizuna schema v2, Phase 3 Step 3.1 (memory storage). See PLAN.md sections
-- 6, 2b, 16, 17 and ARCHITECTURE.md sections 5.5, 7.
-- Scope: storage only -- profile and episodic memory types. 'reflective'
-- is deferred until the reflection agent (Phase 3 Step 3.3+) exists to
-- produce it; adding the type now with no writer would be dead schema.

create table memory (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references profiles (id) on delete cascade,
  memory_type text not null check (memory_type in ('profile', 'episodic')),
  content text not null,
  source_message_id uuid references messages (id) on delete set null,
  source_timestamp timestamptz,
  confidence float,
  status text not null default 'active' check (status in ('active', 'superseded', 'deleted')),
  valid_from timestamptz,
  valid_until timestamptz,
  superseded_by uuid references memory (id),
  user_verified boolean not null default false,
  is_sensitive boolean not null default false,
  pending_confirmation boolean not null default false,
  embedding vector(1536),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table memory_tombstones (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references profiles (id) on delete cascade,
  content_hash text not null,
  embedding vector(1536),
  created_at timestamptz not null default now(),
  unique (user_id, content_hash)
);

create index memory_user_id_status_idx on memory (user_id, status);

-- HNSW over ivfflat: ivfflat's list count is tuned from the data already in
-- the table at build time, which doesn't fit a migration run against an
-- empty table. HNSW builds incrementally with no such up-front tuning, at
-- the cost of a slower/larger build -- the right trade at this table's size.
-- Requires pgvector >= 0.5.0 (already assumed: extension created in 0001).
create index memory_embedding_hnsw_idx on memory using hnsw (embedding vector_cosine_ops);
create index memory_tombstones_embedding_hnsw_idx on memory_tombstones using hnsw (embedding vector_cosine_ops);

alter table memory enable row level security;
alter table memory_tombstones enable row level security;

create policy memory_owner_access on memory
  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);

create policy memory_tombstones_owner_access on memory_tombstones
  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);
