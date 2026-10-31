import getpass
import uuid
from dataclasses import dataclass
from pathlib import Path

import asyncpg


def _repo_root() -> Path:
    directory = Path(__file__).resolve().parent
    while directory != directory.parent and not (directory / "PLAN.md").exists():
        directory = directory.parent
    if not (directory / "PLAN.md").exists():
        raise RuntimeError("could not locate repo root (PLAN.md) from tests/testdb.py")
    return directory


@dataclass
class TestDatabase:
    name: str
    admin_user: str
    # DSN for the test's own setup/verification queries (superuser, bypasses RLS).
    superuser_dsn: str
    # DSN the app pool connects with: a login role that does NOT bypass RLS,
    # the same shape of connection a real deployment would use.
    agent_dsn: str

    async def seed_auth_user(self, user_id: uuid.UUID) -> None:
        conn = await asyncpg.connect(self.superuser_dsn)
        try:
            await conn.execute("insert into auth.users (id) values ($1)", user_id)
        finally:
            await conn.close()

    async def seed_profile_and_conversation(self, user_id: uuid.UUID, conversation_id: uuid.UUID) -> None:
        conn = await asyncpg.connect(self.superuser_dsn)
        try:
            await conn.execute("insert into profiles (id, email) values ($1, $2)", user_id, "test@example.com")
            await conn.execute(
                "insert into conversations (id, user_id) values ($1, $2)", conversation_id, user_id
            )
        finally:
            await conn.close()

    # Simulates .NET's acceptance step (Phase 1 Step 4): Python now trusts
    # that the user message already exists for a given turn_id, so tests
    # must seed it themselves instead of asserting Python creates it.
    async def seed_user_message(
        self,
        user_id: uuid.UUID,
        conversation_id: uuid.UUID,
        turn_id: uuid.UUID,
        text: str,
        sequence_number: int = 0,
    ) -> None:
        conn = await asyncpg.connect(self.superuser_dsn)
        try:
            await conn.execute(
                """
                insert into messages
                    (id, user_id, conversation_id, turn_id, idempotency_key, sequence_number, role, content, status)
                values (gen_random_uuid(), $1, $2, $3, $4, $5, 'user', $6, 'accepted')
                """,
                user_id, conversation_id, turn_id, f"{turn_id}:user", sequence_number, text,
            )
        finally:
            await conn.close()

    async def fetch_messages(self, conversation_id: uuid.UUID):
        conn = await asyncpg.connect(self.superuser_dsn)
        try:
            return await conn.fetch(
                "select role, status, content, sequence_number from messages"
                " where conversation_id = $1 order by sequence_number",
                conversation_id,
            )
        finally:
            await conn.close()

    async def fetch_outbox(self, turn_id: uuid.UUID):
        conn = await asyncpg.connect(self.superuser_dsn)
        try:
            return await conn.fetch(
                "select job_type, status from outbox where turn_id = $1", turn_id
            )
        finally:
            await conn.close()

    async def superuser_fetch(self, query: str, *args):
        conn = await asyncpg.connect(self.superuser_dsn)
        try:
            return await conn.fetch(query, *args)
        finally:
            await conn.close()

    async def drop(self) -> None:
        admin = await asyncpg.connect(f"postgresql://{self.admin_user}@127.0.0.1:5432/postgres")
        try:
            await admin.execute(
                "select pg_terminate_backend(pid) from pg_stat_activity"
                " where datname = $1 and pid <> pg_backend_pid()",
                self.name,
            )
            await admin.execute(f'drop database if exists "{self.name}"')
        finally:
            await admin.close()


async def create_test_database() -> TestDatabase:
    name = f"kizuna_agent_test_{uuid.uuid4().hex}"
    admin_user = getpass.getuser()

    admin = await asyncpg.connect(f"postgresql://{admin_user}@127.0.0.1:5432/postgres")
    try:
        await admin.execute(f'create database "{name}"')
    finally:
        await admin.close()

    superuser_dsn = f"postgresql://{admin_user}@127.0.0.1:5432/{name}"
    conn = await asyncpg.connect(superuser_dsn)
    try:
        # Roles are cluster-wide, not per-database, so earlier test runs may
        # have already created these.
        await conn.execute(
            """
            do $$
            begin
                if not exists (select from pg_roles where rolname = 'authenticated') then
                    create role authenticated;
                end if;
                if not exists (select from pg_roles where rolname = 'anon') then
                    create role anon;
                end if;
                if not exists (select from pg_roles where rolname = 'kizuna_agent') then
                    -- noinherit: must explicitly `set role authenticated` to use
                    -- its grants, matching Supabase's authenticator/PostgREST
                    -- pattern. Login + no BYPASSRLS so RLS is enforced for real.
                    create role kizuna_agent with login noinherit;
                end if;
                grant authenticated to kizuna_agent;
            end
            $$
            """
        )
        await conn.execute("create schema auth")
        await conn.execute("create table auth.users (id uuid primary key default gen_random_uuid())")
        # Mirrors Supabase's real auth.uid(): reads the request.jwt.claim.sub GUC
        # PostgREST sets per-request, instead of a hardcoded null.
        await conn.execute(
            """
            create function auth.uid() returns uuid language sql stable as $$
                select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid
            $$
            """
        )
        # Supabase provisions every real project with default privileges granting
        # anon/authenticated ALL on tables created afterward; the migration's
        # revoke/grant only narrows that baseline, so replicate it here.
        await conn.execute(
            "alter default privileges in schema public grant all on tables to authenticated, anon"
        )

        for migration_file in ("0001_schema_v2.sql", "0002_safety.sql"):
            migration_sql = (_repo_root() / "db" / migration_file).read_text()
            await conn.execute(migration_sql)
    finally:
        await conn.close()

    agent_dsn = f"postgresql://kizuna_agent@127.0.0.1:5432/{name}"
    return TestDatabase(name=name, admin_user=admin_user, superuser_dsn=superuser_dsn, agent_dsn=agent_dsn)
