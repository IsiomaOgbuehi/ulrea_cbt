from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from backend.services.auth.auth.main import app
# from database.database import SessionDep

class Base(DeclarativeBase):
    pass

# Setup the TestClient
client = TestClient(app)

SQLALCHEMY_DATABASE_URL = 'sqlite:///:memory:'

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={'check_same_thread': False}
)

TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Define a dependency override for the test session
def override_get_db():
    try:
        db = TestingSessionLocal()
        yield db
    finally:
        db.close()

app.dependency_overrides = override_get_db

def test_create_item():
    response = client.post(
        "/items/", json={"name": "Test Item", "description": "This is a test item"}
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["name"] == "Test Item"
    assert data["description"] == "This is a test item"
    assert "id" in data


def setup() -> None:
    print('SETTING UP-----')
    # Create the tables in the test database
    Base.metadata.create_all(bind=engine)


def teardown() -> None:
    print('TEARING DOWN-----')
    # Drop the tables in the test database
    Base.metadata.drop_all(bind=engine)