from dataclasses import dataclass, field
from typing import List, Dict
from datetime import date


@dataclass
class ReportItem:
    url: str
    owner_repo: str
    status: str  # "success" | "skipped" | "failed"
    project_type: str = ""
    error_reason: str = ""
    not_uploaded: bool = False

    @property
    def emoji_status(self) -> str:
        return {"success": "✓", "skipped": "⏭", "failed": "✗"}.get(self.status, "?")


@dataclass
class Report:
    total_input: int = 0
    skipped_count: int = 0
    success_count: int = 0
    failed_count: int = 0
    local_count: int = 0
    type_distribution: Dict[str, int] = field(default_factory=lambda: {
        "MCP": 0, "Skill": 0, "Agent工具": 0, "项目": 0,
    })
    items: List[ReportItem] = field(default_factory=list)
    feishu_base_url: str = ""
    evaluation_date: str = field(default_factory=lambda: date.today().isoformat())

    def generate(self) -> str:
        lines = []
        lines.append("=" * 50)
        lines.append("  MCP/Skill 项目评估归档 \u2014 汇总报告")
        lines.append("=" * 50)
        lines.append("")
        lines.append("\U0001f4ca 统计概要")
        lines.append("\u2500" * 35)
        lines.append(f"  总输入条数：      {self.total_input}")
        if self.skipped_count > 0:
            lines.append(f"  已入库跳过：       {self.skipped_count}")
        if self.success_count > 0:
            lines.append(f"  成功入库数：       {self.success_count}")
        if self.local_count > 0:
            lines.append(f"  本地暂存数：       {self.local_count}")
        lines.append(f"  失败条数：         {self.failed_count}")
        lines.append("")
        lines.append("\U0001f4cb 按类型分布")
        lines.append("\u2500" * 35)
        for ptype, count in self.type_distribution.items():
            if count > 0:
                lines.append(f"  {ptype}：            {count} 个")
        lines.append("")
        if self.failed_count > 0:
            lines.append("\u274c 失败明细")
            lines.append("\u2500" * 35)
            for item in self.items:
                if item.status == "failed":
                    lines.append(f"  [{item.url}] \u2192 原因：{item.error_reason}")
            lines.append("")
        lines.append("\u2705 入库完成")
        lines.append("\u2500" * 35)
        if self.local_count > 0:
            lines.append("  评估结果已保存到 pending_results.json")
            lines.append(f"  共 {self.local_count} 条记录待上传到飞书")
        if self.feishu_base_url:
            lines.append(f"  飞书表格地址：{self.feishu_base_url}")
        lines.append(f"  评估日期：{self.evaluation_date}")
        return "\n".join(lines)


def build_report(
    items: List[ReportItem],
    feishu_base_url: str = "",
) -> Report:
    report = Report(feishu_base_url=feishu_base_url)
    report.total_input = len(items)
    for item in items:
        if item.status == "skipped":
            report.skipped_count += 1
        elif item.status == "success":
            if item.not_uploaded:
                report.local_count += 1
            else:
                report.success_count += 1
            if item.project_type in report.type_distribution:
                report.type_distribution[item.project_type] += 1
        elif item.status == "failed":
            report.failed_count += 1
    report.items = items
    return report