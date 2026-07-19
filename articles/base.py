from abc import ABC, abstractmethod


class BaseOutput(ABC):
    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    def save(self, content: str, filename: str) -> bool:
        pass

    @abstractmethod
    def get_output_path(self, filename: str) -> str:
        pass

    def is_available(self) -> bool:
        return True