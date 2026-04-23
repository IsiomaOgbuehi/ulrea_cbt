from fastapi import FastAPI
from sqlmodel import SQLModel
from auth.api.v1.routes import auth, users, internal
from auth.api.v1.auth_routes import AuthRoutes
from auth.database.database import database_engine
from contextlib import asynccontextmanager
from redis.asyncio import Redis
from auth.utility.redis.redis_client import redis_client

# @asynccontextmanager
# async def lifespan(app: FastAPI):
#     SQLModel.metadata.create_all(database_engine)
#     yield

# redis_client = Redis(
#     decode_responses=True,
#     socket_connect_timeout=2,
#     socket_timeout=2,
#     # retry_on_timeout=True,
# )

@asynccontextmanager
async def lifespan(app: FastAPI):
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