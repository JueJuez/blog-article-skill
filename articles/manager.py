import os
from typing import List
from .base import BaseOutput
from .local import LocalOutput
from .obsidian import ObsidianOutput
from .feishu import FeishuOutput

_env_loaded = False
def _ensure_env_loaded():
    global _env_loaded
    if not _env_loaded:
        try:
            from dotenv import load_dotenv
            env_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                '.env'
            )
            if os.path.exists(env_path):
                load_dotenv(env_path)
        except ImportError:
            pass
        _env_loaded = True


def _obsidian_env_enabled() -> bool:
    """持久开关：.env 设 OBSIDIAN_WRITE=1 时，默认也写 Obsidian（双写）。

    默认不开启 —— 项目规则（2026-08-08）：默认只写飞书，Obsidian 按需开启，
    避免重复落盘浪费。显式传 obsidian=True 总是优先于本开关。
    """
    return os.getenv("OBSIDIAN_WRITE", "").lower() in ("1", "true", "yes", "on")


class OutputManager:
    """落盘闸门。

    默认行为由 `.env` 决定（不靠 AI 记性）：
    - 本项目当前（2026-09-04 起）：`OBSIDIAN_WRITE=1` + `DISABLE_FEISHU_SYNC=1`
      → 默认只写本地 Obsidian，不写飞书。
    - 旧规则（2026-08-08–2026-09-03）：`OBSIDIAN_WRITE` 未设 → 默认只写飞书，
      `obsidian=True` / `OBSIDIAN_WRITE=1` 时追加 Obsidian。

    两者皆不可用（Obsidian 未配 且 飞书关/不可用）时回退本地 notes/，避免丢数据。
    """

    def __init__(self, obsidian: bool = False):
        _ensure_env_loaded()
        # 显式 True 强制开启；否则看持久开关（默认关）
        self.obsidian_requested = bool(obsidian) or _obsidian_env_enabled()
        self._feishu = FeishuOutput()
        self._obsidian = ObsidianOutput()
        self._local = LocalOutput()
        self._resolved: List[BaseOutput] = None

    def _resolve(self) -> List[BaseOutput]:
        # 测试/优化期开关：DISABLE_FEISHU_SYNC=1 时仅跳过飞书写入（飞书代码保留，不删）
        disable_feishu = os.getenv("DISABLE_FEISHU_SYNC", "").lower() in ("1", "true", "yes", "on")
        targets: List[BaseOutput] = []
        if self._feishu.is_available() and not disable_feishu:
            targets.append(self._feishu)
        if self.obsidian_requested and self._obsidian.is_available():
            targets.append(self._obsidian)
        if not targets:
            # 飞书不可用（或主动关闭且未请求 Obsidian）→ 本地兜底，避免丢数据
            if self._local.is_available():
                targets.append(self._local)
        return targets

    def get_available_outputs(self) -> List[BaseOutput]:
        if self._resolved is None:
            self._resolved = self._resolve()
        return self._resolved

    def save_all(self, content: str, filename: str, title: str = "") -> None:
        available_outputs = self.get_available_outputs()

        if not available_outputs:
            print("✗ 没有可用的输出模块")
            return

        print(f"\n=== 正在保存到 {len(available_outputs)} 个目标 ===")

        success_count = 0
        failure_count = 0
        for output in available_outputs:
            print(f"\n[{output.name}]")
            try:
                ok = output.save(content, filename, title=title)
            except Exception as e:
                ok = False
                print(f"✗ 输出模块 {output.name} 异常: {str(e)}")
            if ok:
                success_count += 1
            else:
                failure_count += 1

        if failure_count > 0:
            has_external = any(o.name != "local" for o in available_outputs)
            print(f"\n⚠️ 保存完成，{success_count} 个成功，{failure_count} 个失败")
            if has_external:
                print("✗ 已配置外部输出目标，不自动降级到本地")

    def save_to(self, content: str, filename: str, target: str) -> bool:
        mapping = {"feishu": self._feishu, "obsidian": self._obsidian, "local": self._local}
        out = mapping.get(target.lower())
        if out is None:
            print(f"✗ 未找到目标输出: {target}")
            return False
        if not out.is_available():
            print(f"✗ 目标输出 {target} 不可用")
            return False
        return out.save(content, filename)