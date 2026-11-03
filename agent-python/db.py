import os
from contextlib import asynccontextmanager

import asyncpg


async def create_pool() -> asyncpg.Pool:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("Missing required environment variable: DATABASE_URL")
    return await asyncpg.create_pool(database_url)


async def create_worker_pool() -> asyncpg.Pool:
    # A separate DSN/role (kizuna_worker, BYPASSRLS) from the hot path's
    # DATABASE_URL: PLAN.md section 16 contract 7 requires the worker to run
    # under its own identity, not a user token or the per-user RLS role.
    database_url = os.environ.get("WORKER_DATABASE_URL")
    if not database_url:
        raise RuntimeError("Missing required environment variable: WORKER_DATABASE_URL")
    return await asyncpg.create_pool(database_url)


@asynccontextmanager
async def user_scoped_transaction(conn: asyncpg.Connection, user_id: str):
    # SET LOCAL ROLE and set_config(..., is_local=true) both revert automatically
    # at COMMIT/ROLLBACK, so there is nothing to reset before the connection goes
    # back to the pool. Switching to 'authenticated' (not BYPASSRLS) means RLS is
    # enforced for real inside this block, the same as any Supabase client call.
    async with conn.transaction():
        await conn.execute("set local role authenticated")
        await conn.execute("select set_config('request.jwt.claim.sub', $1, true)", user_id)
        yield conn
