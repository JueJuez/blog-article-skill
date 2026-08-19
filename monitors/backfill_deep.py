"""monitors/backfill_deep.py — 哥飞/生财 历史深挖驱动（针对 weread 乱序代理）。

根因
----
weread 免费代理是**乱序分片**：单次 discover 只返回**一个随机分片**，并非从新到老的
连续历史。旧 backfill 逻辑在「本批 0 新 / 未见数≤batch」时就标记 backfill_done，
导致只抓到一个分片（通常是近期、已被每日监控 seen 标记过的）就**假完成**，
深层历史（哥飞→2025-04、生财→2024-12，探针已实证可达）从未落盘。
wechat.WechatSource.discover 已改为：完成判定仅保留 reached_since（见其内注释），
不再因「0 新 / 小分片」假完成。真正的「穷尽」由本驱动负责。

本驱动策略
----------
- 每轮对每个号**先重置 backfill_done**（双保险，即使 discover 误标也清零），再调一次
  `run.py --backfill --apply` 抓该号当前分片并落盘；
- 解析 apply_summaries 健康度「文章 N」判断本轮实际落盘数；
- 连续 EMPTY_THRESHOLD 轮某号 0 落盘 → 判定该号穷尽，写 done(exhausted_consecutive_empty)；
- token 失效时 run.py 自动弹码并阻塞等扫码（用户扫后自动续），无需手动重跑；
- seen 在 state 持久化，跨轮/跨次运行去重，可随时中断重跑、进度不丢。

用法
----
    python monitors/backfill_deep.py
（建议后台运行；token 过期会自动弹码等扫码）
"""
import os
import sys
import time
import json
import subprocess
import re

sys.path.insert(0, os.getcwd())

from monitors.run import (  # noqa: E402
    load_weread_auth, trigger_relogin, _wait_for_token_refresh, WECHAT_RELOGIN_WAIT,
)
from monitors import backfill as bf  # noqa: E402

ROOT = os.getcwd()
PY = r"C:\Users\O1830\.workbuddy\binaries\python\versions\3.13.12\python.exe"
STATE_PATH = os.path.join(ROOT, "monitors", "state.json")
LOG_PATH = os.path.join(ROOT, "monitors", "backfill_deep.log")

ACCOUNTS = [
    {"name": "哥飞", "since": "2025-01-01"},
    {"name": "生财有术", "since": "2024-12-01"},
]
MAX_ROUNDS = 80
EMPTY_THRESHOLD = 12   # 连续 N 轮 0 落盘 → 判定穷尽（乱序代理需较高阈值防误判）
BATCH = 50
ROUND_GAP = 5          # 轮间冷却秒数，降频控


def load_state() -> dict:
    with open(STATE_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_state(st: dict) -> None:
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)


def log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def parse_saved(out: str) -> int:
    """从 apply_summaries 健康度行解析本轮实际落盘文章数：... / 文章 N ..."""
    m = re.search(r"文章\s+(\d+)", out)
    return int(m.group(1)) if m else 0


def reset_account(name: str) -> None:
    st = load_state()
    bf.reset_backfill(st, [name])
    save_state(st)


def mark_done(st: dict, name: str, reason: str) -> None:
    sd = st.setdefault("backfill", {}).setdefault(name, {})
    sd["backfill_done"] = True
    sd["backfill_done_reason"] = reason
    sd["backfill_done_at"] = int(time.time())
    save_state(st)


def run_one(name: str, since: str) -> int:
    """抓单号一个分片并落盘；返回本轮实际落盘文章数（token 失效会阻塞等扫码）。"""
    reset_account(name)  # 双保险：清零可能存在的 done 标记
    p = subprocess.run(
        [PY, "monitors/run.py", "--backfill", "--names", name,
         "--since", since, "--batch", str(BATCH), "--apply"],
        capture_output=True, text=True, timeout=2400,
    )
    out = (p.stdout or "") + "\n" + (p.stderr or "")
    saved = parse_saved(out)
    for ln in out.splitlines():
        s = ln.strip()
        if any(k in s for k in ("文章", "RELOGIN_QR", "健康度", "backfill", "warn", "error", "traceback")):
            log("    > " + s[:200])
    return saved


def main() -> None:
    log("=== 启动深层回溯驱动（乱序代理多轮回溯）===")
    done = {a["name"]: False for a in ACCOUNTS}
    empty = {a["name"]: 0 for a in ACCOUNTS}
    total = {a["name"]: 0 for a in ACCOUNTS}
    st = load_state()
    for a in ACCOUNTS:
        if st.get("backfill", {}).get(a["name"], {}).get("backfill_done"):
            log(f"  {a['name']} 启动时发现旧 done 标记（多为假完成），已重置，由本驱动控完成")
            reset_account(a["name"])

    for rnd in range(1, MAX_ROUNDS + 1):
        if all(done.values()):
            log("全部账号已穷尽，结束。")
            break
        log(f"--- 第 {rnd}/{MAX_ROUNDS} 轮 ---")
        for a in ACCOUNTS:
            if done[a["name"]]:
                continue
            try:
                saved = run_one(a["name"], a["since"])
            except subprocess.TimeoutExpired:
                log(f"  {a['name']} 单轮超时（可能遇代理空窗），跳过本轮，下轮续")
                saved = 0
            except Exception as e:
                log(f"  {a['name']} 单轮异常: {type(e).__name__} {e}")
                saved = 0
            total[a["name"]] += saved
            if saved > 0:
                empty[a["name"]] = 0
                log(f"  {a['name']} 本轮落盘 {saved} 篇（累计 {total[a['name']]}）")
            else:
                empty[a["name"]] += 1
                log(f"  {a['name']} 本轮 0 落盘（连续空 {empty[a['name']]}/{EMPTY_THRESHOLD}）")
                if empty[a["name"]] >= EMPTY_THRESHOLD:
                    st = load_state()
                    mark_done(st, a["name"], "exhausted_consecutive_empty")
                    done[a["name"]] = True
                    log(f"  ✅ {a['name']} 判定穷尽（连续 {EMPTY_THRESHOLD} 轮 0 落盘），标记完成")
        time.sleep(ROUND_GAP)

    log(f"=== 驱动结束 === 累计落盘：{total}")
    for a in ACCOUNTS:
        log(f"  {a['name']}: {total[a['name']]} 篇")


if __name__ == "__main__":
    main()
