from contextlib import asynccontextmanager
from fastapi import FastAPI
from alembic.config import Config
from alembic import command
import logging
import time
from item_bank_service.core.redis.redis_client import redis_client

from item_bank_service.database.database import database_engine as engine
from item_bank_service.api.v1.routes import subjects, items

def run_migrations():
    alembic_cfg = Config("alembic.ini")

    for i in range(5):
        try:
            command.upgrade(alembic_cfg, "head")
            logging.info("Migration successful")
            return
        except Exception as e:
            logging.warning(f"Retry {i+1} failed: {e}")
            time.sleep(3)

    raise RuntimeError("Migration failed after retries")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Run migrations before accepting requests
    # run_migrations()

    # start up
    try:
        await redis_client.ping()
        print("Redis connected successfully")
    except Exception as e:
        # raise RuntimeError(f"Redis unavailable at startup: {e}")
        print(f"Redis unavailable at startup: {e}")
    
    yield  # App runs here
    
    # -------------------------
    # SHUTDOWN
    # -------------------------
    try:
        await redis_client.aclose()
        logging.info("Redis connection closed")

    except Exception as e:
        logging.warning(f"Redis shutdown failed: {e}")

    # SQLModel.metadata.create_all(bind=engine)
    # yield


app = FastAPI(
    title="Item Bank Service",
    description="Manage subjects, questions, and bulk uploads for CBT platform.",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(subjects.router, prefix="/api/v1")
app.include_router(items.router, prefix="/api/v1")
