import asyncio
import json
from collections.abc import AsyncIterator
from uuid import uuid4

import anyio
import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from auth import InternalClaims, require_chat_generate
from db import user_scoped_transaction
from providers import EchoProvider

router = APIRouter()


class ChatStreamRequest(BaseModel):
    conversation_id: str
    turn_id: str
    text: str
    sequence_number: int


def _sse(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()


async def _mark_user_message(conn: asyncpg.Connection, user_id: str, turn_id: str, status: str) -> None:
    # .NET inserted this row at acceptance (Phase 1 Step 4 correction); Python
    # never has its own id for it, so it's addressed by turn_id + role here.
    async with user_scoped_transaction(conn, user_id):
        await conn.execute(
            "update messages set status = $1 where turn_id = $2 and role = 'user'", status, turn_id
        )


async def _stream_chat(pool: asyncpg.Pool, claims: InternalClaims, body: ChatStreamRequest) -> AsyncIterator[bytes]:
    # turn_id comes from the verified token, not the body: the route already
    # rejected a body/token mismatch, but this keeps the token as the single
    # source of truth for what gets persisted.
    turn_id = claims.turn_id

    async with pool.acquire() as conn:
        try:
            tokens: list[str] = []
            async for token in EchoProvider().generate(body.text):
                tokens.append(token)
                yield _sse("token", {"text": token})

            async with user_scoped_transaction(conn, claims.user_id):
                await conn.execute(
                    """
                    insert into messages
                        (id, user_id, conversation_id, turn_id, idempotency_key, sequence_number, role, content, status)
                    values ($1, $2, $3, $4, $5, $6, 'assistant', $7, 'complete')
                    """,
                    str(uuid4()), claims.user_id, body.conversation_id, turn_id,
                    f"{turn_id}:assistant", body.sequence_number + 1, "".join(tokens),
                )
                await conn.execute(
                    """
                    insert into outbox (turn_id, user_id, job_type, status)
                    values ($1, $2, 'process_memory', 'pending')
                    """,
                    turn_id, claims.user_id,
                )
            # 'done' is only ever emitted after that transaction commits.
            yield _sse("done", {})

        except (asyncio.CancelledError, GeneratorExit):
            # The disconnect that triggers this cancels the ambient anyio scope,
            # so an unshielded await here would be re-cancelled immediately
            # instead of running; shield it so the write can actually commit.
            with anyio.CancelScope(shield=True):
                await _mark_user_message(conn, claims.user_id, turn_id, "interrupted")
            raise
        except Exception as exc:
            yield _sse("error", {"message": str(exc)})
            await _mark_user_message(conn, claims.user_id, turn_id, "failed")


@router.post("/chat/stream")
async def chat_stream(
    body: ChatStreamRequest,
    request: Request,
    claims: InternalClaims = Depends(require_chat_generate),
):
    if body.turn_id != claims.turn_id:
        raise HTTPException(status_code=401, detail="turn_id does not match token")

    pool: asyncpg.Pool = request.app.state.pool
    return StreamingResponse(_stream_chat(pool, claims, body), media_type="text/event-stream")
