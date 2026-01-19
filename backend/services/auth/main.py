from fastapi import FastAPI
from sqlmodel import SQLModel
from api.v1.routes import auth
from api.v1.auth_routes import AuthRoutes
from database.database import database_engine
from contextlib import asynccontextmanager

# @asynccontextmanager
# async def lifespan(app: FastAPI):
#     SQLModel.metadata.create_all(database_engine)
#     yield

app = FastAPI(title='Auth Service') # lifespan=lifespan

app.include_router(auth.router, prefix=AuthRoutes.api_version.value, tags=['auth'])
