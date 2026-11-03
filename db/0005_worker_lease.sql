-- Kizuna schema v2, Phase 3 Step 3.2 (durable outbox worker). See PLAN.md
-- section 16 contract 8 and ARCHITECTURE.md section 5.4.

alter table outbox add column lease_expires_at timestamptz;

-- The worker is a trusted backend process, not a client-facing one: per
-- turn_id it must see every user's pending jobs, which a per-user RLS
-- policy (the shape used everywhere else in this schema) can't express.
-- kizuna_worker is therefore a separate role with BYPASSRLS, used only by
-- worker.py's own connection pool via WORKER_DATABASE_URL -- never by a
-- client-reachable path. This mirrors how kizuna_agent/authenticated/anon
-- are provisioned outside the migration files (see db/README.md); run the
-- block below once against the real project the same way.
do $$
begin
    if not exists (select from pg_roles where rolname = 'kizuna_worker') then
        create role kizuna_worker with login bypassrls;
    end if;
end
$$;

grant select, update on outbox to kizuna_worker;
grant select, update on messages to kizuna_worker;
