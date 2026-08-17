"""tests/test_backfill_feature.py — 公众号历史回溯（续批）逻辑 mock 单测。

不依赖网络/微信 token：用 FakeClient 伪造 iter_articles 返回，验证 discover 的 backfill 分支：
  1) 非目标号（不在 WECHAT_BACKFILL_NAMES）直接跳过；
  2) 只 mark 本批 new 为 seen（保留续批能力，不一次性标记全历史）；
  3) 分批续批：每批 batch 篇，多跑几次往前翻；
  4) 完成判定：0 新 / 剩余≤batch → 标记 backfill_done，reason 区分
     reached_since（代理最老日期已越过 since） vs proxy_depth:<最老ts>（代理深度未到 since）。
"""
import os
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from monitors.wechat import WereadClient, WechatSource  # noqa: E402
from monitors.state import load_state, save_state  # noqa: E402

STATE_PATH = os.path.join(REPO, "monitors", "state_test_backfill.json")


def make_articles(n, start_ts, step_days):
    """生成 n 篇，最新->最老，publishTime 每次减 step_days 天。"""
    return [
        {"id": f"a{i}", "title": f"文章{i}", "publishTime": start_ts - i * step_days * 86400}
        for i in range(n)
    ]


class FakeClient(WereadClient):
    def __init__(self, items):
        self._items = items
        self.session = None  # 跳过大父类网络初始化

    def iter_articles(self, mp_id, max_pages=50):
        return list(self._items)

    def resolve_mp(self, *a, **k):
        return {"id": "MP_TEST", "name": "TEST"}


def make_src(name, items):
    src = WechatSource(FakeClient(items), mp_id="MP_TEST", name=name)
    src._resolve = lambda: "MP_TEST"  # 跳过网络解析
    return src


def setup_env(names, since_ts):
    os.environ["WECHAT_BACKFILL"] = "1"
    os.environ["WECHAT_BACKFILL_NAMES"] = ",".join(names)
    os.environ["WECHAT_BACKFILL_SINCE"] = str(since_ts)
    os.environ["WECHAT_BACKFILL_PAGES"] = "200"


def fresh_state():
    if os.path.exists(STATE_PATH):
        os.remove(STATE_PATH)
    return load_state(STATE_PATH)


def run_until_done(name, items, since_ts, batch):
    """反复调用 discover 直到该号 backfill_done，返回 (runs, total_seen, reason, oldest_ts)。"""
    setup_env([name], since_ts)
    src = make_src(name, items)
    state = fresh_state()
    runs = 0
    while True:
        res = src.discover(state, first_run_limit=batch, mode="auto")
        save_state(state, STATE_PATH)
        runs += 1
        if state.get("backfill", {}).get(name, {}).get("backfill_done"):
            sd = state["backfill"][name]
            return runs, len(state["sources"]["wechat:MP_TEST"]["seen"]), \
                   sd["backfill_done_reason"], sd["backfill_oldest_ts"]
        if runs > 50:
            raise AssertionError("未在正常步数内完成回溯（续批死循环？）")
    # 防御：理论上不会到这


def main():
    since = int(time.mktime(time.strptime("2026-01-01", "%Y-%m-%d")))
    start = int(time.mktime(time.strptime("2026-08-01", "%Y-%m-%d")))

    # ---- 场景 A：代理深度未到 since（proxy_depth） ----
    # 23 篇，每篇间隔 3 天，最老 ≈ 2026-02，仍 >= since 2026-01 → 全量都 >= since。
    items_a = make_articles(23, start, 3)
    runs_a, seen_a, reason_a, oldest_a = run_until_done("哥飞", items_a, since, 5)
    assert seen_a == 23, f"A: seen 应为 23，实际 {seen_a}"
    assert reason_a.startswith("proxy_depth:"), f"A: 应为 proxy_depth，实际 {reason_a}"
    # 验证只 mark new（非全量一次标记）：最后一步 seen 增量==本批
    print(f"[A proxy_depth] runs={runs_a} seen={seen_a} reason={reason_a} "
          f"oldest={time.strftime('%Y-%m-%d', time.localtime(oldest_a))}")

    # 完成后再次 discover 应直接跳过（返回 []）
    setup_env(["哥飞"], since)
    src = make_src("哥飞", items_a)
    state = load_state(STATE_PATH)
    assert src.discover(state, first_run_limit=5, mode="auto") == [], "A: 完成后应跳过"
    print("[A skip] 完成后 discover 返回 [] ✅")

    # ---- 场景 B：已越过 since 边界（reached_since） ----
    # 30 篇，每篇间隔 30 天，最老 ≈ 2024 年，明显 < since；since 过滤后约 7 篇 >= since。
    items_b = make_articles(30, start, 30)
    runs_b, seen_b, reason_b, oldest_b = run_until_done("生财有术", items_b, since, 5)
    # >= since 的篇数：从 2026-08-01 往回每 30 天，到 2026-01-01 约 7 篇
    ge_since = sum(1 for it in items_b if it["publishTime"] >= since)
    assert seen_b == ge_since, f"B: seen 应等于 >=since 篇数 {ge_since}，实际 {seen_b}"
    assert reason_b == "reached_since", f"B: 应为 reached_since，实际 {reason_b}"
    print(f"[B reached_since] runs={runs_b} seen(>=since)={seen_b} reason={reason_b} "
          f"oldest_raw={time.strftime('%Y-%m-%d', time.localtime(oldest_b))}")

    # ---- 场景 C：非目标号被跳过 ----
    setup_env(["哥飞"], since)  # 目标只有哥飞
    src_c = make_src("生财有术", items_b)  # 但源是生财
    state_c = fresh_state()
    assert src_c.discover(state_c, first_run_limit=5, mode="auto") == [], "C: 非目标号应跳过"
    print("[C scope] 非 WECHAT_BACKFILL_NAMES 的号 discover 返回 [] ✅")

    # 清理测试 state
    if os.path.exists(STATE_PATH):
        os.remove(STATE_PATH)
    print("\n✅ 全部回溯逻辑单测通过")


if __name__ == "__main__":
    main()
