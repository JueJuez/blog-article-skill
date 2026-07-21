import os
from .base import BaseOutput

# 单篇总结的「收件箱」：新总结默认落此文件夹，用户后续手动归类。
# 系列课自带子目录（filename 含 "/"，如「千刀千法/...」），不进收件箱。
OBSIDIAN_INBOX = "待归类"


class ObsidianOutput(BaseOutput):
    def __init__(self, name: str = "obsidian"):
        super().__init__(name)
        self.vault_path = os.getenv("OBSIDIAN_VAULT_PATH", "")

    def _resolve_rel(self, filename: str) -> str:
        """单篇总结（filename 不含子目录）统一进收件箱；系列课自带子目录保持原样。"""
        if "/" in filename or "\\" in filename:
            return filename
        return f"{OBSIDIAN_INBOX}/{filename}"

    def save(self, content: str, filename: str) -> bool:
        if not self.is_available():
            return False

        try:
            rel = self._resolve_rel(filename)
            file_path = os.path.join(self.vault_path, rel)
            # 关键：建「系列名/」或「待归类/」这类子目录（此前只建 vault 根，导致系列课保存 FileNotFoundError）
            os.makedirs(os.path.dirname(file_path), exist_ok=True)

            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)

            print(f"✓ 已保存到 Obsidian: {file_path}")
            return True
        except Exception as e:
            print(f"✗ 保存失败: {str(e)}")
            return False

    def get_output_path(self, filename: str) -> str:
        return os.path.join(self.vault_path, self._resolve_rel(filename))

    def is_available(self) -> bool:
        return bool(self.vault_path) and os.path.isdir(self.vault_path)