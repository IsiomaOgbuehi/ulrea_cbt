from abc import ABC, abstractmethod
from collections.abc import Iterator
from sqlmodel import Session

from sqlalchemy import Engine

class IDatabase(ABC):
    @abstractmethod
    def engine(self) -> Engine:
        pass

    @abstractmethod
    def connect(self) -> None: 
        pass

    @abstractmethod
    def disconnect(self)  -> None :
        pass

    @abstractmethod
    def get_session(self)-> Iterator[Session] :
        pass