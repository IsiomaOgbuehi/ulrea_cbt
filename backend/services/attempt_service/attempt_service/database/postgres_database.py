from fastapi import FastAPI
from sqlalchemy import Engine
from .database_interface import IDatabase
from sqlmodel import SQLModel, create_engine, Session
from attempt_service.core.settings import settings

DATABASE_HOST = settings.POSTGRES_SERVER
DATABASE_PORT = settings.POSTGRES_PORT
DATABASE_NAME = settings.POSTGRES_DB
DATABASE_USER = settings.POSTGRES_USER
DATABASE_PASSWORD = settings.POSTGRES_PASSWORD

connect_args = {} #{"check_same_thread": False}

DATABASE_URL = (
    f"postgresql://{DATABASE_USER}:{DATABASE_PASSWORD}"
    f"@{DATABASE_HOST}:{DATABASE_PORT}/{DATABASE_NAME}"
)


class PostgresDatabase(IDatabase):

    def engine(self) -> Engine:
        return create_engine(DATABASE_URL, echo=True, connect_args=connect_args)

    def connect(self):
         pass
        #  SQLModel.metadata.create_all(self.engine())
    
    def disconnect(self):
        pass
    
    def get_session(self):
        with Session(self.engine()) as session:
            yield session