from fastapi import FastAPI
from attempt_service.attempt_service.main import app as inner_app

app = FastAPI(title="Auth Service Wrapper")

# mount inner app
app.mount("/", inner_app)