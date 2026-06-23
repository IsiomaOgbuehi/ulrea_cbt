from fastapi import FastAPI
from exam_service.exam_service.main import app as inner_app

app = FastAPI(title="Exam Service Wrapper")

# mount inner app
app.mount("/", inner_app)