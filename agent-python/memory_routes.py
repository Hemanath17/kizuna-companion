"""Phase 4 Step 4.1 Part A: Python owns the full memory lifecycle. The .NET
/memory endpoints become thin proxies to these (PLAN.md Phase 4 decision 1;
ARCHITECTURE.md 5.12). Authenticated by the internal service token, scoped
to the token's sub claim -- the same RLS-backed scoping memory_store.py
already uses everywhere else, not a second ownership check.
"""

import uuid
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

import memory_store
import policy
from auth import InternalClaims, require_memory_manage
from providers import ProviderInterface

router = APIRouter()


class MemoryOut(BaseModel):
    id: UUID
    memory_type: str
    content: str
    confidence: float | None
    status: str
    user_verified: bool
    is_sensitive: bool
    pending_confirmation: bool
    source_kind: str
    source_message_id: UUID | None
    source_journal_entry_id: UUID | None
    source_timestamp: datetime | None
    superseded_by: UUID | None
    created_at: datetime
    updated_at: datetime


class MemoryPatchRequest(BaseModel):
    content: str | None = None


def _parse_memory_id(memory_id: str) -> str:
    try:
        return str(uuid.UUID(memory_id))
    except ValueError:
        # Malformed id is indistinguishable from "not found" to the caller,
        # same convention as the cross-user case below.
        raise HTTPException(status_code=404, detail="memory not found")


@router.get("/memory")
async def list_memory(
    request: Request,
    claims: InternalClaims = Depends(require_memory_manage),
    memory_type: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[MemoryOut]:
    pool = request.app.state.pool
    # Default view is "current facts": corrected-away and forgotten rows are
    # excluded, reachable instead via GET /memory/{id}/history.
    records = await memory_store.list_memories(
        pool, claims.user_id, memory_type=memory_type, status=status or "active", limit=limit, offset=offset
    )
    return [MemoryOut(**record) for record in records]


@router.get("/memory/{memory_id}/history")
async def memory_history(
    memory_id: str, request: Request, claims: InternalClaims = Depends(require_memory_manage)
) -> list[MemoryOut]:
    pool = request.app.state.pool
    parsed_id = _parse_memory_id(memory_id)
    records = await memory_store.get_supersede_history(pool, claims.user_id, parsed_id)
    return [MemoryOut(**record) for record in records]


@router.patch("/memory/{memory_id}")
async def patch_memory(
    memory_id: str,
    body: MemoryPatchRequest,
    request: Request,
    claims: InternalClaims = Depends(require_memory_manage),
) -> MemoryOut:
    pool = request.app.state.pool
    provider: ProviderInterface = request.app.state.provider
    parsed_id = _parse_memory_id(memory_id)

    if body.content:
        record = await policy.correct_memory(pool, claims.user_id, provider, parsed_id, body.content)
    else:
        record = await policy.approve_memory(pool, claims.user_id, parsed_id)

    if record is None:
        raise HTTPException(status_code=404, detail="memory not found")
    return MemoryOut(**record)


@router.delete("/memory/{memory_id}", status_code=204)
async def delete_memory(
    memory_id: str, request: Request, claims: InternalClaims = Depends(require_memory_manage)
) -> None:
    pool = request.app.state.pool
    parsed_id = _parse_memory_id(memory_id)
    forgotten = await policy.forget_memory(pool, claims.user_id, parsed_id)
    if not forgotten:
        raise HTTPException(status_code=404, detail="memory not found")
