"""验证并行竞态修复：多 worker 各自写 staging 文件，父进程合并无丢失、无覆盖、正确去重。

直接 import 真实模块函数（monitors.run_parallel._merge_stage_to_json），
模拟 wechat/bili 两 worker 并发写入后的合并行为。
"""
import os
import sys
import json
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)                       # 让 monitors.* 作为包可导入
sys.path.insert(0, os.path.join(_ROOT, "monitors"))  # 让 run.py 的扁平 `from status_store import` 可解析

import monitors.run_parallel as rp


def write(path, arr):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(arr, f, ensure_ascii=False)


def test_pending_summary_merge_keep_existing():
    """pending_summaries：保留 canonical 旧项 + 追加 staging 新项，按 url 去重（无丢失）。"""
    d = tempfile.mkdtemp()
    canonical = os.path.join(d, "pending_summaries.json")
    w = os.path.join(d, "wechat.stage.json")
    b = os.path.join(d, "bili.stage.json")
    # 旧 canonical 含 D（外部 Agent 尚未消费）
    write(canonical, [{"url": "D", "title": "old-D"}])
    # wechat worker 写入 A、B
    write(w, [{"url": "A", "title": "wechat-A"}, {"url": "B", "title": "wechat-B"}])
    # bili worker 写入 C，且与 wechat 重叠 A（模拟双源同 url 极端情况）
    write(b, [{"url": "C", "title": "bili-C"}, {"url": "A", "title": "bili-A-dup"}])

    rp._merge_stage_to_json(canonical, [w, b], key="url", keep_existing=True)

    merged = json.load(open(canonical, encoding="utf-8"))
    urls = [m["url"] for m in merged]
    assert sorted(urls) == ["A", "B", "C", "D"], f"丢失/异常: {urls}"
    # A 只应出现一次（去重）
    assert urls.count("A") == 1, f"A 未去重: {urls}"
    # 旧项 D 保留
    assert any(m["url"] == "D" for m in merged)
    print("✅ pending_summaries 合并：无丢失、无覆盖、已去重")


def test_pending_refetch_merge_replace():
    """pending_refetch：旧队列已被父进程路由给 worker 重抓，canonical 应由 staging 重建（旧项丢弃）。"""
    d = tempfile.mkdtemp()
    canonical = os.path.join(d, "pending_refetch.json")
    w = os.path.join(d, "wechat.stage.json")
    b = os.path.join(d, "bili.stage.json")
    # 旧 canonical 含 X（上一轮失败项，已由父进程路由给 worker）
    write(canonical, [{"url": "X", "route": "article", "refetch_count": 1}])
    # worker 重抓后：X 成功（不进 staging）；新失败 Y 进 staging
    write(w, [{"url": "Y", "route": "article", "refetch_count": 2}])
    write(b, [])

    rp._merge_stage_to_json(canonical, [w, b], key="url", keep_existing=False)

    merged = json.load(open(canonical, encoding="utf-8"))
    urls = [m["url"] for m in merged]
    # 旧 X 应被丢弃（已重抓），仅保留新失败 Y
    assert urls == ["Y"], f"refetch 语义错误: {urls}"
    print("✅ pending_refetch 合并：旧项已重抓丢弃、仅留新失败项（与串行 _save_json 语义一致）")


def test_empty_stage_noop():
    """某 worker 未运行（无 staging 文件）→ 不报错、不影响 canonical。"""
    d = tempfile.mkdtemp()
    canonical = os.path.join(d, "pending_summaries.json")
    write(canonical, [{"url": "Z", "title": "z"}])
    w = os.path.join(d, "wechat.stage.json")  # 不存在

    rp._merge_stage_to_json(canonical, [w], key="url", keep_existing=True)
    merged = json.load(open(canonical, encoding="utf-8"))
    assert [m["url"] for m in merged] == ["Z"]
    print("✅ 缺失 staging 文件：安全跳过、canonical 不受影响")


if __name__ == "__main__":
    test_pending_summary_merge_keep_existing()
    test_pending_refetch_merge_replace()
    test_empty_stage_noop()
    print("\n🎉 全部通过：并行竞态修复（staging + 父合并）验证无误")
