# exam_service/main.py
from contextlib import asynccontextmanager
from fastapi import FastAPI
from sqlmodel import SQLModel
from exam_service.database.database import database_engine as engine
from exam_service.database.models import exam  # noqa: F401
from exam_service.api.v1.routes import exams


@asynccontextmanager
async def lifespan(app: FastAPI):
    SQLModel.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="Exam Service", lifespan=lifespan)
app.include_router(exams.router, prefix="/api/v1")