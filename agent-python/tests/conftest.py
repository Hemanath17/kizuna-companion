import sys
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio
from fastapi import FastAPI

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # make chat/auth/db/providers importable

from chat import router as chat_router  # noqa: E402

from tokens import AUDIENCE, ISSUER, SIGNING_KEY  # noqa: E402
from testdb import create_test_database  # noqa: E402


@pytest_asyncio.fixture
async def test_database():
    db = await create_test_database()
    yield db
    await db.drop()


@pytest_asyncio.fixture
async def pool(test_database):
    p = await asyncpg.create_pool(test_database.agent_dsn)
    yield p
    await p.close()


@pytest.fixture
def app(pool, monkeypatch):
    monkeypatch.setenv("INTERNAL_SIGNING_KEY", SIGNING_KEY)
    monkeypatch.setenv("INTERNAL_TOKEN_ISSUER", ISSUER)
    monkeypatch.setenv("INTERNAL_TOKEN_AUDIENCE", AUDIENCE)
    application = FastAPI()
    application.include_router(chat_router)
    application.state.pool = pool
    return application
