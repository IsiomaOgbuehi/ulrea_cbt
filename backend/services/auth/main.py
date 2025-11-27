from fastapi import FastAPI
from backend.services.auth.api.v1.routes import auth
from .api.v1.auth_routes import AuthRoutes

app = FastAPI(title='Auth Service')

app.include_router(auth.router, prefix=AuthRoutes.api_version.value, tags=['auth'])
