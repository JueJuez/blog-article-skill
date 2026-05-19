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


class OutputManager:
    def __init__(self):
        _ensure_env_loaded()
        self.outputs: List[BaseOutput] = []
        self._outputs_initialized = False

    def _ensure_outputs(self):
        if self._outputs_initialized:
            return

        feishu_output = FeishuOutput()
        obsidian_output = ObsidianOutput()

        has_external_config = False

        if feishu_output.is_available():
            self.outputs.append(feishu_output)
            has_external_config = True

        if obsidian_output.is_available():
            self.outputs.append(obsidian_output)
            has_external_config = True

        if not has_external_config:
            local_output = LocalOutput()
            self.outputs.append(local_output)

        self._outputs_initialized = True

    def get_available_outputs(self) -> List[BaseOutput]:
        self._ensure_outputs()
        return [output for output in self.outputs if output.is_available()]

    def save_all(self, content: str, filename: str) -> None:
        self._ensure_outputs()
        available_outputs = self.get_available_outputs()

        if not available_outputs:
            print("✗ 没有可用的输出模块")
            return

        print(f"\n=== 正在保存到 {len(available_outputs)} 个目标 ===")

        success_count = 0
        failure_count = 0
        for output in available_outputs:
            print(f"\n[{output.name}]")
            if output.save(content, filename):
                success_count += 1
            else:
                failure_count += 1

        if failure_count > 0:
            print(f"\n⚠️ 保存完成，{success_count} 个成功，{failure_count} 个失败")
            print("✗ 已配置外部输出目标，不自动降级到本地")

    def save_to(self, content: str, filename: str, target: str) -> bool:
        self._ensure_outputs()
        for output in self.outputs:
            if output.name.lower() == target.lower():
                return output.save(content, filename)

        print(f"✗ 未找到目标输出: {target}")
        return False