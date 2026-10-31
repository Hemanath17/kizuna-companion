import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI

load_dotenv()  # dev convenience; real deployments set env vars directly

from chat import router as chat_router  # noqa: E402
from db import create_pool  # noqa: E402
from providers import EchoProvider, OpenAIProvider  # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pool = await create_pool()
    # AGENT_PROVIDER=echo is an escape hatch for the .NET integration suite
    # (PythonAgentProcess.cs), which exercises the trust boundary / transport
    # layer, not generation quality -- it doesn't need a real OpenAI key or
    # network access, same as Phase 1 before this step existed.
    app.state.provider = EchoProvider() if os.environ.get("AGENT_PROVIDER") == "echo" else OpenAIProvider()
    yield
    await app.state.pool.close()


app = FastAPI(lifespan=lifespan)
app.include_router(chat_router)


@app.get("/health")
def health():
    return {"status": "ok"}
