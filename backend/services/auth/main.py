from fastapi import FastAPI
from auth.auth.main import app as inner_app

app = FastAPI(title="Auth Service Wrapper")

# mount inner app
app.mount("/", inner_app)