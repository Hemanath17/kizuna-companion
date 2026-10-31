import asyncpg


async def load_conversation_style(conn: asyncpg.Connection, user_id: str) -> str | None:
    return await conn.fetchval("select conversation_style from profiles where id = $1", user_id)


async def load_user_message_id(conn: asyncpg.Connection, turn_id: str) -> str:
    # .NET generates this row's id, not Python (Phase 1 Step 4 correction);
    # the crisis_events FK needs it, so look it up by turn_id + role.
    return await conn.fetchval("select id from messages where turn_id = $1 and role = 'user'", turn_id)


async def load_recent_messages(conn: asyncpg.Connection, conversation_id: str, limit: int = 20) -> list[dict[str, str]]:
    # .NET already persisted this turn's user message before calling Python
    # (Phase 1 Step 4), so it's naturally the last row here -- no need to
    # separately append the incoming text.
    rows = await conn.fetch(
        """
        select role, content from (
            select role, content, sequence_number
            from messages
            where conversation_id = $1 and role in ('user', 'assistant')
            order by sequence_number desc
            limit $2
        ) recent
        order by sequence_number asc
        """,
        conversation_id,
        limit,
    )
    return [{"role": row["role"], "content": row["content"]} for row in rows]
