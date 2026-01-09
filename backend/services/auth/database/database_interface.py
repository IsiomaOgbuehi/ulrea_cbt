from abc import ABC, abstractmethod

class IDatabase(ABC):
    @abstractmethod
    def connect(self):
        pass

    @abstractmethod
    def disconnect(self):
        pass

    @abstractmethod
    def get_session(self):
        pass