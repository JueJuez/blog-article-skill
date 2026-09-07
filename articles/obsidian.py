import os
from .base import BaseOutput

# 单篇总结的「收件箱」：新总结默认落此文件夹，用户后续手动归类。
# 与飞书收件箱同名（【待归类】），便于两边对照（2026-09-04 从【00_待归类】对齐）。
# 系列课自带子目录（filename 含 "/"，如「千刀千法/...」），不进收件箱。
OBSIDIAN_INBOX = "【待归类】"


class ObsidianOutput(BaseOutput):
    def __init__(self, name: str = "obsidian"):
        super().__init__(name)
        self.vault_path = os.getenv("OBSIDIAN_VAULT_PATH", "")

    def _resolve_rel(self, filename: str) -> str:
        """单篇总结（filename 不含子目录）统一进收件箱；系列课自带子目录保持原样。"""
        if "/" in filename or "\\" in filename:
            return filename
        return f"{OBSIDIAN_INBOX}/{filename}"

    def ensure_folder_path(self, dirs: list) -> str:
        """逐级建目录并返回最深一级的绝对路径（对齐 feishu.ensure_folder_path 语义）。

        修复范围外 bug（2026-09-07）：此前 _save_series_note 的 folder 路由分支
        对 ObsidianOutput 调 ensure_folder_path 直接 AttributeError，被外层
        except 吞掉 → 系列笔记静默跳过 vault 落盘。
        """
        if not dirs or not self.is_available():
            return ""
        try:
            cur = self.vault_path
            for d in dirs:
                if not d:
                    continue
                cur = os.path.join(cur, d)
            os.makedirs(cur, exist_ok=True)
            return cur
        except Exception as e:
            print(f"✗ 目录创建失败: {e}")
            return ""

    def ensure_series_node(self, series_title: str, parent_token: str = None) -> str:
        """在 parent（目录绝对路径或 vault 根）下建系列容器目录，返回其绝对路径。"""
        if not self.is_available():
            return ""
        parent = parent_token or self.vault_path
        try:
            ser = os.path.join(parent, series_title)
            os.makedirs(ser, exist_ok=True)
            return ser
        except Exception as e:
            print(f"✗ 系列容器创建失败: {e}")
            return ""

    def save(self, content: str, filename: str, parent_token: str = None, title: str = "") -> bool:
        """parent_token 给定时落到该目录下（系列容器场景）；否则进收件箱/子目录。"""
        if not self.is_available():
            return False

        try:
            if parent_token:
                file_path = os.path.join(parent_token, filename)
            else:
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