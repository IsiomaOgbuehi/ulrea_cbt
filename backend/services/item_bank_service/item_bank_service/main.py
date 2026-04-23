from contextlib import asynccontextmanager
from fastapi import FastAPI
from sqlmodel import SQLModel

from item_bank_service.database.database import database_engine as engine
from item_bank_service.database.models import subject, item  # noqa: F401 — registers models
from item_bank_service.api.v1.routes import subjects, items


@asynccontextmanager
async def lifespan(app: FastAPI):
    SQLModel.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="Item Bank Service",
    description="Manage subjects, questions, and bulk uploads for CBT platform.",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(subjects.router, prefix="/api/v1")
app.include_router(items.router, prefix="/api/v1")
