from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from auth.main import app
from auth.database.database import database
import pytest
from sqlmodel import SQLModel
from sqlalchemy.pool import StaticPool

# Setup the TestClient
# client = TestClient(app)

SQLALCHEMY_DATABASE_URL = 'sqlite:///:memory:'

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={'check_same_thread': False}, poolclass=StaticPool,
)

TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Define a dependency override for the test session
def override_get_db():
    try:
        db = TestingSessionLocal()
        yield db
    finally:
        db.close()

# app.dependency_overrides[SessionDep] = override_get_db

@pytest.fixture(scope="function")
def client():
    # Create tables before the test
    SQLModel.metadata.create_all(bind=engine)
    # Override the dependency
    app.dependency_overrides[database.get_session] = override_get_db
    # Get the test client
    with TestClient(app) as c:
        yield c  # Run the test
    # Drop tables after the test
    SQLModel.metadata.drop_all(bind=engine)
    # Clear the dependency override
    app.dependency_overrides.clear()

def test_create_item(client):
    response = client.post(
        '/api/v1/auth/signup', json={'organization': {'name': 'Test Org', 'verified': False, 'address': 'Test Address', 
                                                      'email': 'amycole@gmail.com', 'phone': '+2348039361659', 'organization_type': 'school'}}
    )
    assert response.status_code == 200, response.json()
    data = response.json()
    assert data["name"] == "Test Org"
    assert data["phone"] == "+2348039361659"
    assert "id" in data


# def setup() -> None:
#     print('SETTING UP-----')
#     # Create the tables in the test database
#     Base.metadata.create_all(bind=engine)


# def teardown() -> None:
#     print('TEARING DOWN-----')
#     # Drop the tables in the test database
#     Base.metadata.drop_all(bind=engine)