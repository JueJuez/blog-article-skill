import subprocess
import json
import time
import os
import sys
import re
import shutil
import urllib.parse
from typing import Optional, Tuple

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 逻辑字段 -> 飞书字段键（field id）的默认映射。
# 已按「开源项目库」(base ImIVbBnc0aDDWhsSHFiccJninFc / tblMpX9hguggiWc5)
# 的真实列 id 全部填好；feishu_fields.json 与环境变量 FEISHU_FIELD_MAP 可覆盖。
DEFAULT_FIELD_MAP = {
    "project_name": "fldWipEsqn",     # 项目名称
    "summary": "fldHKPkz7h",          # 项目描述
    "tags": "fldusiGjuY",             # 能力标签
    "git_url": "fldvVvQNRR",          # Git 地址
    "project_type": "fld3urADAF",     # 项目类型
    "run_form": "fldtoRnPG9",         # 运行形式
    "target_user": "fldNXvEbeG",      # 给谁用
    "domain": "fldS6Xnn6h",           # 功能领域
    "highlights": "fldfVAd0PX",       # 核心亮点
    "community_score": "fldqHZ3KZt",  # 社区评分
    "doc_score": "fldOdZy7KC",        # 文档评分
    "func_score": "fldCbpLXal",       # 功能评分
    "total_score": "fldQIa5t33",      # 综合评分
    "eval_date": "fldztznzza",        # 评估日期
    "status": "fldz26W1X4",           # 状态
}


def load_field_map() -> dict:
    """加载字段映射，优先级：环境变量 FEISHU_FIELD_MAP > feishu_fields.json > feishu_fields.example.json > 默认值。

    未知 / 为空的字段键会被上游 to_feishu_fields 跳过，不会写入飞书。
    说明：feishu_fields.json 含作者个人表 id，已被 .gitignore 忽略（本地私有）；
    仓库提交的 feishu_fields.example.json 是占位模板，clone 后复制为 feishu_fields.json 再改。
    """
    m = dict(DEFAULT_FIELD_MAP)
    for cfg_name in ("feishu_fields.json", "feishu_fields.example.json"):
        cfg_path = os.path.join(BASE_DIR, cfg_name)
        if os.path.exists(cfg_path):
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    user_map = json.load(f)
                if isinstance(user_map, dict):
                    m.update({k: v for k, v in user_map.items() if v})
            except (json.JSONDecodeError, IOError):
                pass
            break  # 命中真实配置即停止，不叠加 example
    env = os.environ.get("FEISHU_FIELD_MAP")
    if env:
        try:
            user_map = json.loads(env)
            if isinstance(user_map, dict):
                m.update({k: v for k, v in user_map.items() if v})
        except (json.JSONDecodeError, ValueError):
            pass
    return m


def _get_default_base_token() -> str:
    return os.environ.get("FEISHU_BASE_TOKEN", "")


def _get_default_table_id() -> str:
    return os.environ.get("FEISHU_TABLE_ID", "")


# 飞书 Wiki / 文档链接（含 ?table= 参数），与裸 base token 区分
_WIKI_RE = re.compile(r"feishu\.(cn|com)/(wiki|base)")


def _is_wiki_url(spec: str) -> bool:
    return bool(spec) and bool(_WIKI_RE.search(spec or ""))


def resolve_feishu_target(
    spec: Optional[str] = None,
    table_id: Optional[str] = None,
) -> Tuple[str, str]:
    """辨别 FEISHU_BASE_TOKEN 是 wiki/doc 链接还是裸 base token，返回 (base_token, table_id)。

    - 若是 wiki/doc 链接：用 lark-cli base +url-resolve 解析成真实 base_token；
      并从链接的 ?table= 参数提取表 id（未显式提供 table_id 时）。
    - 否则当作裸 base token 原样返回。
    """
    spec = spec or _get_default_base_token()
    table_id = table_id or _get_default_table_id()
    if _is_wiki_url(spec):
        # 从 wiki URL 的 ?table= 提取表 id（若未显式提供）
        if not table_id:
            try:
                qs = urllib.parse.urlparse(spec).query
                tbl = urllib.parse.parse_qs(qs).get("table", [None])[0]
                if tbl:
                    table_id = tbl
            except Exception:
                pass
        out = _run_lark_cli(["base", "+url-resolve", "--url", spec, "--as", "user"])
        if out:
            data = out.get("data", {}) or {}
            token = (
                data.get("base_token")
                or data.get("node_token")
                or data.get("wiki_token")
            )
            if token:
                spec = token
    return spec or "", table_id or ""


def _resolve_lark_cli() -> Optional[str]:
    """定位 lark-cli 可执行文件。优先用 PATH，其次回退到 WorkBuddy 连接器默认安装目录。"""
    for name in ("lark-cli", "lark-cli.cmd", "lark-cli.ps1"):
        found = shutil.which(name)
        if found:
            return found
    cand = os.path.expanduser(
        os.path.join(
            "~", ".workbuddy", "binaries", "node",
            "cli-connector-packages", "lark-cli",
        )
    )
    for ext in ("", ".cmd", ".ps1"):
        p = cand + ext
        if os.path.exists(p):
            return p
    return None


def _run_lark_cli(args: list) -> Optional[dict]:
    """执行 lark-cli 子命令。

    Windows 上 lark-cli 是 .cmd 包装脚本，无法被 CreateProcess 直接拉起，
    需经 cmd.exe /c 运行；其余平台直接执行。JSON 参数通过参数列表传递，
    由 subprocess 自动转义，避免手动拼接命令行导致的引号问题。
    """
    exe = _resolve_lark_cli()
    if not exe:
        print("  lark-cli 未安装或未找到（请确保已安装并登录，且在 PATH 中）")
        return None
    cmd = [exe] + list(args)
    if sys.platform.startswith("win") and exe.lower().endswith((".cmd", ".bat")):
        cmd = ["cmd.exe", "/c", exe] + list(args)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    except FileNotFoundError:
        print("  lark-cli 未安装或未找到（请确保已安装并登录，且在 PATH 中）")
        return None
    except Exception as e:
        print(f"  lark-cli 执行异常: {e}")
        return None

    if result.returncode != 0:
        error_msg = (result.stderr or result.stdout).strip()
        print(f"  lark-cli 执行失败: {error_msg}")
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"stdout": result.stdout.strip()}


def write_record(
    fields: dict,
    base_token: Optional[str] = None,
    table_id: Optional[str] = None,
) -> bool:
    base_token, table_id = resolve_feishu_target(base_token, table_id)
    if not base_token or not table_id:
        print("  ❌ 未配置飞书 Base Token 或 Table ID")
        print("     方式 A：设置环境变量 FEISHU_BASE_TOKEN(裸 token) + FEISHU_TABLE_ID")
        print("     方式 B：设置 FEISHU_BASE_TOKEN 为飞书 Wiki/文档链接（自动解析，")
        print("             表 id 从链接 ?table= 读取）")
        print("     调用时也可显式传入 base_token / table_id 参数")
        return False
    args = [
        "base",
        "+record-upsert",
        "--as", "user",
        "--base-token", base_token,
        "--table-id", table_id,
        "--json", json.dumps(fields, ensure_ascii=False),
    ]
    result = _run_lark_cli(args)
    if result and (result.get("ok") or result.get("code") == 0):
        return True
    if result and result.get("error"):
        print(f"  飞书写入失败: {result['error']}")
    return False


def write_record_with_retry(
    fields: dict,
    base_token: Optional[str] = None,
    table_id: Optional[str] = None,
    max_retries: int = 3,
    retry_delay: int = 1,
) -> bool:
    for attempt in range(max_retries):
        if write_record(fields, base_token, table_id):
            return True
        if attempt < max_retries - 1:
            print(f"  重试第 {attempt + 1} 次...")
            time.sleep(retry_delay)
    return False


def is_feishu_configured() -> bool:
    bt = os.environ.get("FEISHU_BASE_TOKEN", "")
    if not bt:
        return False
    # wiki/doc 链接会在写入时从 ?table= 解析出表 id，故只需 base 端已配置
    if _is_wiki_url(bt):
        return True
    return bool(os.environ.get("FEISHU_TABLE_ID"))


def check_lark_cli_available() -> bool:
    result = _run_lark_cli(["--version"])
    return result is not None
