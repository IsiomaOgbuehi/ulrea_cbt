from fastapi import FastAPI
from cbt_service.cbt_service.main import app as inner_app

app = FastAPI(title="CBT Service Wrapper")

# mount inner app
app.mount("/", inner_app)