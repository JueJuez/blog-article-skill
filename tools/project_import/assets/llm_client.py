"""LLM 分析客户端。

分析来源的优先级：
1. --analysis-file / 环境变量 BATCH_LLM_ANALYSIS_FILE 指向一个分析 JSON 文件
   → 直接读取并解析（适合「子代理产出结果」或离线/测试场景）
2. 配置了 LLM API Key（BATCH_LLM_API_KEY / OPENAI_API_KEY，OpenAI 兼容接口）
   → 自动调用外部模型完成分析（HEADLESS，不进主会话）

设计原则：本工具**默认不让「执行模型主会话」直接读 README 做分析**（README 往往很长，
塞进主会话会污染上下文、挤占窗口）。默认做法是由**子代理**（执行模型隔离出的独立上下文）
读 README 并产出分析 JSON，再以 --analysis-file 喂回本工具；或者用户显式提供 BATCH_LLM_*
改为调用外部 LLM。两者都不会把 README 文本带进主会话。

环境变量：
  BATCH_LLM_API_KEY / OPENAI_API_KEY       API Key
  BATCH_LLM_BASE_URL / OPENAI_BASE_URL     接口地址（默认 https://api.openai.com/v1）
  BATCH_LLM_MODEL   / OPENAI_MODEL         模型名（默认 gpt-4o-mini）
"""
import os
import json

from assets.analyzer import build_analysis_prompt, parse_llm_response


def _load_analysis_file(path: str):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return parse_llm_response(f.read())
    except (IOError, OSError, TypeError) as e:
        print(f"  ⚠️ 读取分析文件失败: {e}")
        return None


def _call_openai(prompt: str) -> "object":
    import requests

    api_key = os.environ.get("BATCH_LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
    base_url = (
        os.environ.get("BATCH_LLM_BASE_URL")
        or os.environ.get("OPENAI_BASE_URL")
        or "https://api.openai.com/v1"
    )
    model = (
        os.environ.get("BATCH_LLM_MODEL")
        or os.environ.get("OPENAI_MODEL")
        or "gpt-4o-mini"
    )
    url = base_url.rstrip("/") + "/chat/completions"
    try:
        resp = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.2,
            },
            timeout=90,
        )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"]
        return parse_llm_response(text)
    except Exception as e:
        print(f"  ⚠️ LLM 调用失败: {e}")
        return None


def llm_analyze(
    repo_name: str,
    stars: int,
    readme: str,
    analysis_file: str = None,
) -> "object":
    """返回 AnalysisResult 或 None。

    分析来源优先级见模块 docstring。两者皆无时返回 None 并提示——
    不再走「交互粘贴」模式（那会把 README 带进主会话，污染上下文）。
    """
    analysis_file = analysis_file or os.environ.get("BATCH_LLM_ANALYSIS_FILE")
    if analysis_file and os.path.exists(analysis_file):
        return _load_analysis_file(analysis_file)

    api_key = os.environ.get("BATCH_LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if api_key:
        return _call_openai(build_analysis_prompt(repo_name, stars, readme))

    print(
        "  ⚠️ 未配置 LLM 分析来源：既无 --analysis-file，也无 BATCH_LLM_API_KEY。\n"
        "     默认请由「子代理」读 README 产出分析 JSON 并以 --analysis-file 喂回；\n"
        "     或设置 BATCH_LLM_API_KEY / BATCH_LLM_BASE_URL / BATCH_LLM_MODEL 走外部 LLM。\n"
        "     （不要让执行模型主会话直接读 README，会污染上下文。）"
    )
    return None
