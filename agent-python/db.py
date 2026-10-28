import os
from contextlib import asynccontextmanager

import asyncpg


async def create_pool() -> asyncpg.Pool:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("Missing required environment variable: DATABASE_URL")
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
