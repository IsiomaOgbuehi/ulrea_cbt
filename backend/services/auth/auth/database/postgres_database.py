from fastapi import FastAPI
from sqlalchemy import Engine
from .database_interface import IDatabase
from sqlmodel import create_engine, Session
from auth.core.settings import settings

from collections.abc import Iterator

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