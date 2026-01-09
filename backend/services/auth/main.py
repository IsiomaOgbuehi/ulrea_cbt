from fastapi import FastAPI
from sqlmodel import SQLModel
from api.v1.routes import auth
from api.v1.auth_routes import AuthRoutes
from database.database import database_engine

app = FastAPI(title='Auth Service')

app.include_router(auth.router, prefix=AuthRoutes.api_version.value, tags=['auth'])

@app.on_event("startup")
def on_startup():
    SQLModel.metadata.create_all(database_engine)
