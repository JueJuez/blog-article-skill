from dataclasses import dataclass, field, asdict
from typing import List, Optional
import json
import re

from assets.collector import stars_to_score

PROJECT_TYPES = ["MCP", "Skill", "Agent工具", "项目"]
RUN_FORMS = ["MCP-stdio", "MCP-SSE", "Skill", "不适用"]
USER_TYPES = ["Agent调用", "本地运行", "两者皆可"]
DOMAINS = [
    "PPT/演示文稿生成", "学习资料/课程制作", "音频生成/处理",
    "AI 视频生成/编辑", "AI 写作辅助", "代码安全防御",
    "代码测试/调试", "项目管理/CI", "架构/设计",
    "数据分析与可视化", "AI 模型交互/编排", "通用工具", "其他",
]

# 逻辑字段 -> 飞书字段键（field id 或列名，取决于你的 lark-cli 版本）
# 具体映射在 assets/feishu_writer.py 的 load_field_map() 中加载，可被
# 项目根目录的 feishu_fields.json 或环境变量 FEISHU_FIELD_MAP 覆盖。
LOGICAL_FIELDS = [
    "project_name",   # 项目名称
    "summary",        # 项目描述
    "tags",           # 能力标签
    "git_url",        # Git 地址
    "project_type",   # 项目类型
    "run_form",       # 运行形式
    "target_user",    # 给谁用
    "domain",         # 功能领域
    "highlights",     # 核心亮点
    "community_score",# 社区评分
    "doc_score",      # 文档评分
    "func_score",     # 功能评分
    "total_score",    # 综合评分
    "eval_date",      # 评估日期
    "status",         # 状态
]


@dataclass
class AnalysisResult:
    summary: str = ""
    project_type: str = ""
    run_form: str = ""
    target_user: str = ""
    domain: str = ""
    tags: List[str] = field(default_factory=list)
    highlights: str = ""
    doc_score: int = 0
    func_score: int = 0

    def to_feishu_fields(
        self, repo_name: str, git_url: str, stars: int, field_map: dict = None
    ) -> dict:
        """把分析结果转成飞书字段字典。

        field_map: 逻辑字段名 -> 飞书字段键（id 或列名）。
        映射为 None / 空字符串的字段会被跳过（不会写入飞书），
        这样尚未在你表格里建好对应列时也不会报错。
        """
        from assets.feishu_writer import load_field_map

        m = field_map or load_field_map()
        community_score = stars_to_score(stars)
        total_score = community_score + self.doc_score + self.func_score
        date_str = __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        values = {
            "project_name": repo_name,
            "summary": self.summary,
            "tags": ", ".join(self.tags) if self.tags else "",
            "git_url": git_url,
            "project_type": self.project_type,
            "run_form": self.run_form,
            "target_user": self.target_user,
            "domain": self.domain,
            "highlights": self.highlights,
            "community_score": community_score,
            "doc_score": self.doc_score,
            "func_score": self.func_score,
            "total_score": total_score,
            "eval_date": date_str,
            "status": "已入库",
        }

        out = {}
        for logical, value in values.items():
            key = m.get(logical)
            if key:  # 仅写入已配置的字段
                out[key] = value
        return out


ANALYSIS_PROMPT = """你是一个专业的开源项目评估分析师。请根据以下开源项目（可能来自 GitHub 或 Gitee）的 README 内容和 Stars 数量，完成一次全面的项目评估分析。

## 项目信息

项目名称: {repo_name}
Stars 数量: {stars}

## README 内容

{readme_content}

## 分析要求

请以 JSON 格式输出以下字段，不要输出任何其他内容：

1. "summary": 一句话简介（20字以内），说明项目是干什么的
2. "project_type": 项目类型，从以下四选一：
   - "MCP" - 实现了 MCP 协议标准的服务器
   - "Skill" - Lark CLI 可加载的 skill
   - "Agent工具" - 供 AI Agent 使用但非标准 MCP 的工具
   - "项目" - 普通开源项目（库、CLI 工具等）
3. "run_form": 运行形式，从以下四选一：
   - "MCP-stdio" - 本地命令行启动的 MCP 服务器
   - "MCP-SSE" - 远程 HTTP 服务的 MCP 服务器
   - "Skill" - Lark CLI 可加载的 skill
   - "不适用" - 非 MCP/Skill 项目
4. "target_user": 给谁用，从以下三选一：
   - "Agent调用" - 供 AI Agent 直接调用的工具
   - "本地运行" - 需用户本地安装运行的 CLI 工具
   - "两者皆可"
5. "domain": 功能领域，单选主分类，从以下选择：
   PPT/演示文稿生成 | 学习资料/课程制作 | 音频生成/处理 | AI 视频生成/编辑 | AI 写作辅助 | 代码安全防御 | 代码测试/调试 | 项目管理/CI | 架构/设计 | 数据分析与可视化 | AI 模型交互/编排 | 通用工具 | 其他
6. "tags": 能力标签，多选自由标签数组，描述具体能力（如 ["AI生成PPT", "支持自定义模板", "导出为PDF"]）
7. "highlights": 核心亮点文本，区别于同类项目的独特优势
8. "doc_score": 文档评分（1-10分），评估 README 完善度，考虑以下维度：
   - 有项目简介（20%）
   - 有安装/配置说明（20%）
   - 有使用示例（25%）
   - 有 API/配置参数说明（20%）
   - 有贡献指南/FAQ/常见问题（15%）
9. "func_score": 功能评分（1-10分），评估功能完整度，考虑以下维度：
   - 有明确的工具/命令定义（30%）
   - 有完整的代码结构（25%）
   - 有测试代码（15%）
   - 有错误处理机制（15%）
   - 有 CI/CD 配置（15%）

## 输出格式

仅输出一个 JSON 对象，不要包含 markdown 代码块标记或其他文字。
"""


def coerce_choice(value, allowed, default: str = "") -> str:
    """把 LLM 返回的选项值纠偏到允许集合内。

    先精确匹配；再大小写不敏感匹配；再包含匹配（容错 "MCP Server" / "mcp" 等）；
    都不中则返回 default，避免写出飞书单选列不存在的选项导致写入失败。
    """
    if value is None:
        return default
    v = str(value).strip()
    if v in allowed:
        return v
    vl = v.lower()
    for a in allowed:
        if a.lower() == vl:
            return a
        if vl and (vl in a.lower() or a.lower() in vl):
            return a
    return default


def parse_llm_response(response: str) -> Optional[AnalysisResult]:
    if not response:
        return None
    cleaned = response.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    try:
        result = AnalysisResult(
            summary=str(data.get("summary", "")),
            project_type=coerce_choice(
                data.get("project_type", ""), PROJECT_TYPES, default="项目"),
            run_form=coerce_choice(
                data.get("run_form", ""), RUN_FORMS, default="不适用"),
            target_user=coerce_choice(
                data.get("target_user", ""), USER_TYPES, default="两者皆可"),
            domain=coerce_choice(
                data.get("domain", ""), DOMAINS, default="其他"),
            tags=list(data.get("tags", [])),
            highlights=str(data.get("highlights", "")),
            doc_score=int(data.get("doc_score", 0)),
            func_score=int(data.get("func_score", 0)),
        )
        return result
    except (ValueError, TypeError):
        return None


def build_analysis_prompt(repo_name: str, stars: int, readme_content: str) -> str:
    return ANALYSIS_PROMPT.format(
        repo_name=repo_name, stars=stars, readme_content=readme_content
    )
