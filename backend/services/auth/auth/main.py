from fastapi import FastAPI
from sqlmodel import SQLModel
from auth.api.v1.routes import auth, users, internal
from auth.api.v1.auth_routes import AuthRoutes
from auth.database.database import database_engine
from contextlib import asynccontextmanager
from redis.asyncio import Redis
from auth.utility.redis.redis_client import redis_client
from alembic.config import Config
from alembic import command
import logging
import time

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

# def run_migrations():
#     try:
#         alembic_cfg = Config("alembic.ini")
#         command.upgrade(alembic_cfg, "head")
#         logging.info("Database migrations completed successfully")
#     except Exception as e:
#         logging.exception("Migration failed")
#         raise RuntimeError(f"Migration failed: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Run migrations before accepting requests
    # run_migrations()

    # Startup
    try:
        await redis_client.ping()
        print("Redis connected successfully")
    except Exception as e:
        raise RuntimeError(f"Redis unavailable at startup: {e}")
    
    yield  # App runs here
    
    # Shutdown
    await redis_client.aclose()
    print("Redis connection closed")


app = FastAPI(title='Auth Service', lifespan=lifespan)

app.include_router(auth.router, prefix=AuthRoutes.API_VERSION.value, tags=['auth'])
app.include_router(users.router, prefix=AuthRoutes.API_VERSION.value)
app.include_router(internal.router, prefix=AuthRoutes.API_VERSION.value)