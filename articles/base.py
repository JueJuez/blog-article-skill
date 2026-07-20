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

    def save_series(self, content: str, filename: str, series_title: str) -> bool:
        """系列课保存默认实现：按「系列名/文件名」落到各自路径。

        飞书 Output 会重写此方法以先建容器节点；Obsidian/Local 直接复用本默认。
        """
        return self.save(content, f"{series_title}/{filename}")

    def is_available(self) -> bool:
        return True