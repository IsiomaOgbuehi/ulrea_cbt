from abc import ABC, abstractmethod

from sqlalchemy import Engine

class IDatabase(ABC):
    @abstractmethod
    def engine(self) -> Engine:
        pass

    @abstractmethod
    def connect(self):
        pass

    @abstractmethod
    def disconnect(self):
        pass

    @abstractmethod
    def get_session(self):
        pass