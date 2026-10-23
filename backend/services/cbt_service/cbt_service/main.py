from contextlib import asynccontextmanager
from fastapi import FastAPI
from alembic.config import Config
from alembic import command
import logging
import time
from collections.abc import AsyncGenerator

from cbt_service.core.http_client import close_http_client, get_http_client
from cbt_service.core.redis.redis_client import redis_client

from cbt_service.database.database import database_engine as engine
from cbt_service.api.v1.routes import subjects, items, exams, attempts, internal,violations, exam_violations

logger: logging.Logger = logging.getLogger(__name__)

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


# @asynccontextmanager
# async def lifespan(app: FastAPI):
#     # Run migrations before accepting requests
#     # run_migrations()

#     get_http_client()                 # create at startup
#     # yield
#     await close_http_client()         # close on shutdown
#     await redis_client.aclose()       # redis-py >= 5.0.1; use .close() on older versions

#     # start up
#     try:
#         await redis_client.ping()
#         print("Redis connected successfully")
#     except Exception as e:
#         # raise RuntimeError(f"Redis unavailable at startup: {e}")
#         print(f"Redis unavailable at startup: {e}")
    
#     yield  # App runs here
    
#     # -------------------------
#     # SHUTDOWN
#     # -------------------------
#     try:
#         await redis_client.aclose()
#         logging.info("Redis connection closed")

#     except Exception as e:
#         logging.warning(f"Redis shutdown failed: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # ---- STARTUP ----
    # Run migrations before accepting requests
    # run_migrations()

    get_http_client()  # create the shared httpx client

    try:
        await redis_client.ping()
        logger.info("Redis connected successfully")
    except Exception as exc:
        # raise RuntimeError(f"Redis unavailable at startup: {exc}")
        logger.warning("Redis unavailable at startup: %s", exc)

    yield  # app runs here

    # ---- SHUTDOWN ----
    try:
        await close_http_client()
    except Exception as exc:
        logger.warning("HTTP client shutdown failed: %s", exc)

    try:
        await redis_client.aclose()
        logger.info("Redis connection closed")
    except Exception as exc:
        logger.warning("Redis shutdown failed: %s", exc)


app = FastAPI(
    title="CBT Services",
    description="Manage subjects, questions, and bulk uploads for CBT platform. Create exams, Attempt exams.",
    version="1.0.0",
    lifespan=lifespan,
)


# ITEM AND SUBJECT SERVICES
app.include_router(subjects.router, prefix="/api/v1")
app.include_router(items.router, prefix="/api/v1")


# Exam AND ATTEMPT SERVICES
app.include_router(exams.router, prefix="/api/v1")
app.include_router(attempts.router, prefix="/api/v1")

# Internal
app.include_router(internal.router)

# Violations
app.include_router(violations.router, prefix="/api/v1")
app.include_router(exam_violations.router, prefix="/api/v1")