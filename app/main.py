from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings

from app.db import engine
from app.envelope import RbacError, rbac_error_handler
from app.models import Base
from app.routers import auth, catalogue, sync, users


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Dev convenience until Alembic migrations are added.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield


app = FastAPI(title="Central RBAC", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=settings.allowed_origins, allow_credentials=True,
    allow_methods=["*"], allow_headers=["content-type", "x-requested-with"],
)
app.add_exception_handler(RbacError, rbac_error_handler)
app.include_router(auth.router)
app.include_router(catalogue.router)
app.include_router(users.router)
app.include_router(sync.router)


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}
