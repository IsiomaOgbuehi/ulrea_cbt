from contextlib import asynccontextmanager
from fastapi import FastAPI
from sqlmodel import SQLModel

from attempt_service.database.database import database_engine as engine
from attempt_service.database.models import attempt  # noqa: F401 — registers models
from attempt_service.api.v1.routes import attempts


@asynccontextmanager
async def lifespan(app: FastAPI):
    SQLModel.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="Attempt Service",
    description="Manages exam sessions, autosave, and scoring.",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(attempts.router, prefix="/api/v1")
