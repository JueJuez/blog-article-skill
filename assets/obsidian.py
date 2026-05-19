import os
from .base import BaseOutput


class ObsidianOutput(BaseOutput):
    def __init__(self, name: str = "obsidian"):
        super().__init__(name)
        self.vault_path = os.getenv("OBSIDIAN_VAULT_PATH", "")

    def save(self, content: str, filename: str) -> bool:
        if not self.is_available():
            return False

        try:
            os.makedirs(self.vault_path, exist_ok=True)
            file_path = os.path.join(self.vault_path, filename)

            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)

            print(f"✓ 已保存到 Obsidian: {file_path}")
            return True
        except Exception as e:
            print(f"✗ 保存失败: {str(e)}")
            return False

    def get_output_path(self, filename: str) -> str:
        return os.path.join(self.vault_path, filename)

    def is_available(self) -> bool:
        return bool(self.vault_path) and os.path.isdir(self.vault_path)