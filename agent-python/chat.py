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
from generation import stream_reply_with_fallback
from hydrate import load_conversation_style, load_recent_messages, load_user_message_id
from persona import render_persona_prompt
from providers import ProviderInterface
from safety import CRISIS_RESPONSE_TEXT, HEIGHTENED_CARE_CONSTRAINTS, SAFETY_POLICY_VERSION, run_safety_pipeline
from token_trace import TokenTrace

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


async def _insert_assistant_message_and_outbox(
    conn: asyncpg.Connection, user_id: str, conversation_id: str, turn_id: str, sequence_number: int, content: str
) -> None:
    async with user_scoped_transaction(conn, user_id):
        await conn.execute(
            """
            insert into messages
                (id, user_id, conversation_id, turn_id, idempotency_key, sequence_number, role, content, status)
            values ($1, $2, $3, $4, $5, $6, 'assistant', $7, 'complete')
            """,
            str(uuid4()), user_id, conversation_id, turn_id, f"{turn_id}:assistant", sequence_number, content,
        )
        await conn.execute(
            """
            insert into outbox (turn_id, user_id, job_type, status)
            values ($1, $2, 'process_memory', 'pending')
            """,
            turn_id, user_id,
        )


async def _stream_chat(
    pool: asyncpg.Pool, claims: InternalClaims, body: ChatStreamRequest, provider: ProviderInterface
) -> AsyncIterator[bytes]:
    # turn_id comes from the verified token, not the body: the route already
    # rejected a body/token mismatch, but this keeps the token as the single
    # source of truth for what gets persisted.
    turn_id = claims.turn_id
    tracer = TokenTrace(turn_id=turn_id)

    async with pool.acquire() as conn:
        try:
            async with user_scoped_transaction(conn, claims.user_id):
                conversation_style = await load_conversation_style(conn, claims.user_id)
                window = await load_recent_messages(conn, body.conversation_id)

            # Safety gates generation (ARCHITECTURE.md 5.1): this runs BEFORE
            # any reply is generated, on the hot path.
            outcome = await run_safety_pipeline(provider, window, tracer)

            if outcome.policy_outcome == "crisis":
                for word in CRISIS_RESPONSE_TEXT.split(" "):
                    yield _sse("token", {"text": word + " "})

                async with user_scoped_transaction(conn, claims.user_id):
                    message_id = await load_user_message_id(conn, turn_id)
                    await conn.execute(
                        """
                        insert into crisis_events
                            (user_id, message_id, risk_category, severity, stage1_signal, stage2_reason, policy_outcome)
                        values ($1, $2, $3, $4, $5, $6, $7)
                        """,
                        claims.user_id,
                        message_id,
                        outcome.assessment.risk_category,
                        outcome.assessment.severity,
                        json.dumps(
                            {
                                "flagged": outcome.stage1.flagged,
                                "categories": outcome.stage1.categories,
                                "ambiguity": outcome.stage1.ambiguity,
                                "context_shift": outcome.stage1.context_shift,
                                "session_risk": outcome.session_risk,
                                "confidence": outcome.assessment.confidence,
                                "uncertainty": outcome.assessment.uncertainty,
                                "policy_version": SAFETY_POLICY_VERSION,
                            }
                        ),
                        outcome.assessment.reason,
                        "crisis",
                    )
                await _insert_assistant_message_and_outbox(
                    conn, claims.user_id, body.conversation_id, turn_id, body.sequence_number + 1, CRISIS_RESPONSE_TEXT
                )
                # 'done' is only ever emitted after that transaction commits.
                yield _sse("done", {})
                tracer.log()
                return

            constraints = HEIGHTENED_CARE_CONSTRAINTS if outcome.policy_outcome == "heightened_care" else ""
            system = render_persona_prompt(conversation_style, constraints)

            tokens: list[str] = []
            async for token in stream_reply_with_fallback(provider, system, window, tracer):
                tokens.append(token)
                yield _sse("token", {"text": token})

            await _insert_assistant_message_and_outbox(
                conn, claims.user_id, body.conversation_id, turn_id, body.sequence_number + 1, "".join(tokens)
            )
            # 'done' is only ever emitted after that transaction commits.
            yield _sse("done", {})
            tracer.log()

        except (asyncio.CancelledError, GeneratorExit):
            # The disconnect that triggers this cancels the ambient anyio scope,
            # so an unshielded await here would be re-cancelled immediately
            # instead of running; shield it so the write can actually commit.
            with anyio.CancelScope(shield=True):
                await _mark_user_message(conn, claims.user_id, turn_id, "interrupted")
            tracer.log()
            raise
        except Exception as exc:
            yield _sse("error", {"message": str(exc)})
            await _mark_user_message(conn, claims.user_id, turn_id, "failed")
            tracer.log()


@router.post("/chat/stream")
async def chat_stream(
    body: ChatStreamRequest,
    request: Request,
    claims: InternalClaims = Depends(require_chat_generate),
):
    if body.turn_id != claims.turn_id:
        raise HTTPException(status_code=401, detail="turn_id does not match token")

    pool: asyncpg.Pool = request.app.state.pool
    provider: ProviderInterface = request.app.state.provider
    return StreamingResponse(_stream_chat(pool, claims, body, provider), media_type="text/event-stream")
