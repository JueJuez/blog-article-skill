"""scripts/persist_summary.py — 降级闭环的「接单」持久化助手。

由外层模型（主会话的子 AGENT，避免污染主上下文）调用：
1. 子 AGENT 读 raw 文件 → 按模板总结 → 把 {summarized_content, folder, ...} 写成一个 JSON 临时文件；
2. 调本脚本 `python scripts/persist_summary.py <json_path>`，脚本加载 .env、调
   `articles.main.skill_main({'summarized_content': ...})` → `save_summary_only` →
   双写 Obsidian + 飞书（与正常流程同一出口，契约一致）。

为何不直接在子 AGENT 里 import articles？因为 .env 必须在 import 前就位
（feishu.py 在 __init__ 读环境变量），本脚本统一处理 .env 加载 + sys.path，
子 AGENT 只需写好 JSON 再跑这一行，零转义风险。
"""
import sys
import os
import json

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_env():
    env_path = os.path.join(BASE_DIR, ".env")
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except FileNotFoundError:
        pass


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"success": False, "message": "用法: persisist_summary.py <json_path>"}))
        return
    _load_env()
    if BASE_DIR not in sys.path:
        sys.path.insert(0, BASE_DIR)
    json_path = sys.argv[1]
    try:
        d = json.load(open(json_path, "r", encoding="utf-8"))
    except Exception as e:
        print(json.dumps({"success": False, "message": f"读 JSON 失败: {e}"}))
        return
    from articles.main import skill_main
    res = skill_main({
        "summarized_content": d.get("summarized_content", ""),
        "folder": d.get("folder", ""),
        "original_url": d.get("original_url", ""),
        "author": d.get("author", ""),
        "tags": d.get("tags", []),
        "original_title": d.get("original_title", ""),
        "publish_time": d.get("publish_time", 0),
    })
    # 自清理：保存成功后从降级队列移除对应条目（按 original_url 匹配），
    # 这样即使中途停止子 AGENT，重跑也不会重复生成笔记。
    if res.get("success"):
        _clear_queue(d.get("original_url", ""))
    print(json.dumps(res, ensure_ascii=False))


def _clear_queue(url: str):
    if not url:
        return
    qp = os.path.join(BASE_DIR, "monitors", "pending_summaries.json")
    try:
        q = json.load(open(qp, "r", encoding="utf-8"))
    except Exception:
        return
    new = [x for x in q if x.get("url", "") != url]
    if len(new) != len(q):
        json.dump(new, open(qp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
