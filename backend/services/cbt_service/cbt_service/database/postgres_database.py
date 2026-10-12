# from fastapi import FastAPI
# from sqlalchemy import Engine
# from .database_interface import IDatabase
# from sqlmodel import SQLModel, create_engine, Session
# from cbt_service.core.settings import settings

# DATABASE_HOST = settings.POSTGRES_SERVER
# DATABASE_PORT = settings.POSTGRES_PORT
# DATABASE_NAME = settings.POSTGRES_DB
# DATABASE_USER = settings.POSTGRES_USER
# DATABASE_PASSWORD = settings.POSTGRES_PASSWORD
# DB_URL = settings.DATABASE_URL

# connect_args = {} #{"check_same_thread": False}

# DB_URL_PATH = (
#     f"postgresql://{DATABASE_USER}:{DATABASE_PASSWORD}"
#     f"@{DATABASE_HOST}:{DATABASE_PORT}/{DATABASE_NAME}"
# )

# DATABASE_URL = DB_URL if DB_URL is not None else DB_URL_PATH


# class PostgresDatabase(IDatabase):

#     def engine(self) -> Engine:
#         return create_engine(DATABASE_URL, echo=True, connect_args=connect_args)

#     def connect(self):
#          pass
#         #  SQLModel.metadata.create_all(self.engine())
    
#     def disconnect(self):
#         pass
    
#     def get_session(self):
#         with Session(self.engine()) as session:
#             yield session


from collections.abc import Iterator

from sqlalchemy import Engine
from sqlmodel import Session, create_engine

from cbt_service.core.settings import settings
from .database_interface import IDatabase

DATABASE_URL: str = settings.DATABASE_URL or (
    f"postgresql://{settings.POSTGRES_USER}:{settings.POSTGRES_PASSWORD}"
    f"@{settings.POSTGRES_SERVER}:{settings.POSTGRES_PORT}/{settings.POSTGRES_DB}"
)


class PostgresDatabase(IDatabase):
    def __init__(self) -> None:
        self._engine: Engine = create_engine(
            DATABASE_URL,
            echo=False,               # was True: logs every answer payload
            pool_size=10,
            max_overflow=5,           # max 15 per process
            pool_timeout=10,
            pool_pre_ping=True,
            pool_recycle=900,
            use_native_hstore=False,  # skips the hstore OID query on connect
            connect_args={
                "connect_timeout": 10,
                "keepalives": 1,
                "keepalives_idle": 30,
                "keepalives_interval": 10,
                "keepalives_count": 5,
            },
        )

    def engine(self) -> Engine:
        return self._engine

    def connect(self) -> None:
        pass

    def disconnect(self) -> None:
        self._engine.dispose()

    def get_session(self) -> Iterator[Session]:
        with Session(self._engine) as session:
            yield session