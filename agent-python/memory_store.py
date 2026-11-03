import hashlib
import uuid
from datetime import datetime

import asyncpg

from db import user_scoped_transaction

# Cosine similarity above which a candidate fact is treated as a reworded
# repeat of a tombstoned (forgotten) one. Calibrated against OpenAI
# embeddings' typical range for paraphrases of the same fact (~0.85-0.95);
# PLAN.md section 6 requires a reworded repeat to still be caught.
TOMBSTONE_SIMILARITY_THRESHOLD = 0.85


def _normalize(content: str) -> str:
    return " ".join(content.strip().lower().split())


def content_hash(content: str) -> str:
    return hashlib.sha256(_normalize(content).encode("utf-8")).hexdigest()


def _vector_literal(embedding: list[float]) -> str:
    # asyncpg has no pgvector codec registered; passing the standard pgvector
    # text input format and casting with ::vector in the query is the
    # dependency-free way to round-trip a vector(1536) column.
    return "[" + ",".join(f"{v:.8f}" for v in embedding) + "]"


async def insert_memory(
    pool: asyncpg.Pool,
    user_id: str,
    *,
    memory_type: str,
    content: str,
    embedding: list[float],
    source_message_id: str | None = None,
    source_timestamp: datetime | None = None,
    confidence: float | None = None,
    status: str = "active",
    valid_from: datetime | None = None,
    valid_until: datetime | None = None,
    is_sensitive: bool = False,
    pending_confirmation: bool = False,
    source_kind: str = "stated",
) -> str:
    memory_id = str(uuid.uuid4())
    async with pool.acquire() as conn, user_scoped_transaction(conn, user_id):
        await conn.execute(
            """
            insert into memory (
                id, user_id, memory_type, content, source_message_id, source_timestamp,
                confidence, status, valid_from, valid_until, is_sensitive, pending_confirmation,
                source_kind, embedding
            ) values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14::vector)
            """,
            memory_id,
            user_id,
            memory_type,
            content,
            source_message_id,
            source_timestamp,
            confidence,
            status,
            valid_from,
            valid_until,
            is_sensitive,
            pending_confirmation,
            source_kind,
            _vector_literal(embedding),
        )
    return memory_id


async def search_memory(
    pool: asyncpg.Pool,
    user_id: str,
    query_embedding: list[float],
    top_k: int = 5,
    *,
    filters: dict | None = None,
) -> list[dict]:
    filters = filters or {}
    args: list = [_vector_literal(query_embedding), filters.get("status", "active")]
    conditions = ["status = $2"]
    if "memory_type" in filters:
        args.append(filters["memory_type"])
        conditions.append(f"memory_type = ${len(args)}")
    if "pending_confirmation" in filters:
        args.append(filters["pending_confirmation"])
        conditions.append(f"pending_confirmation = ${len(args)}")
    if "source_kind" in filters:
        args.append(filters["source_kind"])
        conditions.append(f"source_kind = ${len(args)}")
    args.append(top_k)
    query = f"""
        select id, memory_type, content, confidence, status, user_verified, is_sensitive,
               pending_confirmation, source_kind, source_message_id, source_timestamp, superseded_by,
               created_at, updated_at, 1 - (embedding <=> $1::vector) as similarity
        from memory
        where {" and ".join(conditions)}
        order by embedding <=> $1::vector
        limit ${len(args)}
    """
    async with pool.acquire() as conn, user_scoped_transaction(conn, user_id):
        rows = await conn.fetch(query, *args)
    return [dict(row) for row in rows]


async def supersede_memory(pool: asyncpg.Pool, user_id: str, old_id: str, new_id: str) -> None:
    async with pool.acquire() as conn, user_scoped_transaction(conn, user_id):
        await conn.execute(
            "update memory set status = 'superseded', superseded_by = $1, updated_at = now() where id = $2",
            new_id,
            old_id,
        )


async def tombstone_memory(pool: asyncpg.Pool, user_id: str, memory_id: str) -> None:
    async with pool.acquire() as conn, user_scoped_transaction(conn, user_id):
        row = await conn.fetchrow(
            "select content, embedding::text as embedding from memory where id = $1", memory_id
        )
        if row is None:
            return
        await conn.execute(
            """
            insert into memory_tombstones (user_id, content_hash, embedding)
            values ($1, $2, $3::vector)
            on conflict (user_id, content_hash) do nothing
            """,
            user_id,
            content_hash(row["content"]),
            row["embedding"],
        )
        await conn.execute(
            "update memory set status = 'deleted', embedding = null, updated_at = now() where id = $1",
            memory_id,
        )


async def get_memory(pool: asyncpg.Pool, user_id: str, memory_id: str) -> dict | None:
    async with pool.acquire() as conn, user_scoped_transaction(conn, user_id):
        row = await conn.fetchrow(
            """
            select id, memory_type, content, confidence, status, user_verified, is_sensitive,
                   pending_confirmation, source_kind, source_message_id, source_timestamp, superseded_by,
                   created_at, updated_at
            from memory
            where id = $1
            """,
            memory_id,
        )
    return dict(row) if row is not None else None


async def list_memories(pool: asyncpg.Pool, user_id: str) -> list[dict]:
    async with pool.acquire() as conn, user_scoped_transaction(conn, user_id):
        rows = await conn.fetch(
            """
            select id, memory_type, content, confidence, status, user_verified, is_sensitive,
                   pending_confirmation, source_kind, source_message_id, source_timestamp, superseded_by,
                   created_at, updated_at
            from memory
            order by created_at desc
            """
        )
    return [dict(row) for row in rows]


async def set_user_verified(pool: asyncpg.Pool, user_id: str, memory_id: str, verified: bool = True) -> None:
    async with pool.acquire() as conn, user_scoped_transaction(conn, user_id):
        await conn.execute(
            "update memory set user_verified = $1, updated_at = now() where id = $2", verified, memory_id
        )


async def check_tombstone(
    pool: asyncpg.Pool,
    user_id: str,
    content: str,
    embedding: list[float],
    *,
    similarity_threshold: float = TOMBSTONE_SIMILARITY_THRESHOLD,
) -> bool:
    """True if content/embedding matches a tombstoned (forgotten) fact,
    either by exact content hash or by high embedding similarity -- the
    reworded-repeat case an exact hash alone can't catch."""
    async with pool.acquire() as conn, user_scoped_transaction(conn, user_id):
        exact = await conn.fetchval(
            "select exists(select 1 from memory_tombstones where content_hash = $1)",
            content_hash(content),
        )
        if exact:
            return True
        row = await conn.fetchrow(
            """
            select 1 - (embedding <=> $1::vector) as similarity
            from memory_tombstones
            order by embedding <=> $1::vector
            limit 1
            """,
            _vector_literal(embedding),
        )
    return row is not None and row["similarity"] >= similarity_threshold
