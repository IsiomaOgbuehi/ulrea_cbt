from fastapi import FastAPI
from sqlalchemy import Engine
from .database_interface import IDatabase
from dotenv import load_dotenv
import os
from sqlmodel import SQLModel, create_engine, Session

load_dotenv()

DATABASE_HOST = os.getenv('POSTGRES_SERVER')
DATABASE_PORT = os.getenv('POSTGRES_PORT')
DATABASE_NAME = os.getenv('POSTGRES_DB')
DATABASE_USER = os.getenv('POSTGRES_USER')
DATABASE_PASSWORD = os.getenv('POSTGRES_PASSWORD')

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