-- Kizuna schema v2, Phase 3 Step 3.1 follow-up. See PLAN.md section 6:
-- every memory must record whether the user stated it or the model
-- inferred it, never blending the two. The memory agent (Phase 3 Step 3.3)
-- is the first writer that sets 'inferred'; everything before it only
-- ever writes stated facts, hence the default.

alter table memory
  add column source_kind text not null default 'stated' check (source_kind in ('stated', 'inferred'));
