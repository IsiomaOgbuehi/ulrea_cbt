from fastapi import FastAPI
from item_bank_service.item_bank_service.main import app as inner_app

app = FastAPI(title="Item Bank Service Wrapper")

# mount inner app
app.mount("/", inner_app)