import os
from dotenv import load_dotenv
from .base import BaseOutput


class LocalOutput(BaseOutput):
    def __init__(self, name: str = "local"):
        super().__init__(name)
        load_dotenv()
        self.base_path = self._get_base_path()

    def _get_base_path(self) -> str:
        default_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "notes")
        return os.path.abspath(default_path)

    def save(self, content: str, filename: str) -> bool:
        try:
            os.makedirs(self.base_path, exist_ok=True)
            file_path = os.path.join(self.base_path, filename)

            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)

            print(f"✓ 已保存到本地: {file_path}")
            return True
        except Exception as e:
            print(f"✗ 保存失败: {str(e)}")
            return False

    def get_output_path(self, filename: str) -> str:
        return os.path.join(self.base_path, filename)

    def is_available(self) -> bool:
        return True