from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI

load_dotenv()  # dev convenience; real deployments set env vars directly

from chat import router as chat_router  # noqa: E402
from db import create_pool  # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pool = await create_pool()
    yield
    await app.state.pool.close()


app = FastAPI(lifespan=lifespan)
app.include_router(chat_router)


@app.get("/health")
def health():
    return {"status": "ok"}
