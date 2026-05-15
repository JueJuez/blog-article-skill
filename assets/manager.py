from typing import List
from .base import BaseOutput
from .local import LocalOutput
from .obsidian import ObsidianOutput
from .feishu import FeishuOutput


class OutputManager:
    def __init__(self):
        self.outputs: List[BaseOutput] = []
        self._init_outputs()

    def _init_outputs(self):
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

    def get_available_outputs(self) -> List[BaseOutput]:
        return [output for output in self.outputs if output.is_available()]

    def save_all(self, content: str, filename: str) -> None:
        available_outputs = self.get_available_outputs()

        if not available_outputs:
            print("✗ 没有可用的输出模块")
            return

        print(f"\n=== 正在保存到 {len(available_outputs)} 个目标 ===")

        has_failure = False
        for output in available_outputs:
            print(f"\n[{output.name}]")
            if not output.save(content, filename):
                has_failure = True

        if has_failure:
            print("\n⚠️ 外部目标保存失败，降级到本地 notes/")
            local = LocalOutput()
            local.save(content, filename)

    def save_to(self, content: str, filename: str, target: str) -> bool:
        for output in self.outputs:
            if output.name.lower() == target.lower():
                return output.save(content, filename)

        print(f"✗ 未找到目标输出: {target}")
        return False