"""scripts/launch_fetch_up_detached.py — UP 主字幕批量抓取 DETACHED 启动器。

背景：fetch_up_range.py 是长任务（数百视频 × 限速 15-30s，常 1-2 小时），必须用
DETACHED 子进程脱离 agent 会话树，否则 run_in_background 在轮次结束被环境回收。

安全模式：**本脚本自身非 DETACHED**，仅 spawn 一个 DETACHED 子进程就返回。
（参考 launch_scys_backfill.py 的「DETACHED 父 spawn 失能」结论：非 DETACHED 父
spawn 单 DETACHED 子最稳。）

子进程内部：`_run_with_env.py` 注入 .env（BILI_COOKIE / OBSIDIAN_WRITE / DISABLE_FEISHU_SYNC）
→ 再执行 `fetch_up_range.py`（抓字幕 + 入 pending_summaries 队列，不总结）。

用法:
    python scripts/launch_fetch_up_detached.py --uid <数字UID> --author <UP名> --start 1 --end 207
"""
import argparse
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--uid", required=True)
    ap.add_argument("--author", required=True)
    ap.add_argument("--start", type=int, default=1)
    ap.add_argument("--end", type=int, required=True)
    ap.add_argument("--log", default="")
    ap.add_argument("--with-asr", action="store_true", help="批量内也做 ASR 转写（默认跳过）")
    ap.add_argument("--batch-size", type=int, default=1, help="每批子进程数（默认 1：每视频独立短命子进程，崩仅丢 1 条、熔断几乎不触发）")
    args = ap.parse_args()

    log_path = Path(args.log) if args.log else ROOT / "_tmp" / f"fetch_up_{args.uid}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable, str(ROOT / "scripts" / "_run_with_env.py"), "--",
        sys.executable, str(ROOT / "scripts" / "fetch_up_range.py"),
        str(args.start), str(args.end),
        "--uid", str(args.uid), "--author", args.author,
    ]
    if args.with_asr:
        cmd.append("--with-asr")
    # 始终透传 batch-size：fetch_up_range 自身默认是 8，不传会回退到 8 而失去「单条隔离」意义
    cmd += ["--batch-size", str(args.batch_size)]

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logf = open(log_path, "a", encoding="utf-8", buffering=1)
    logf.write(f"\n########## UP 补齐启动 {ts} uid={args.uid} author={args.author} "
               f"range={args.start}-{args.end} asr={args.with_asr} ##########\n")
    logf.flush()

    p = subprocess.Popen(
        cmd,
        cwd=str(ROOT),
        stdout=logf,
        stderr=subprocess.STDOUT,
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )
    logf.write(f"launched pid={p.pid}\n")
    logf.flush()
    # ⚠️ 关键：父进程必须阻塞在 p.wait()，保持进程树活跃，DETACHED 子进程才不会被
    # 会话回收（参考 launch_scys_backfill.py 同构：非 DETACHED 父 + 对每子 DETACHED + 串行 wait）。
    # 本 launcher 自身由调用方以 run_in_background 启动，故阻塞数小时不占会话。
    rc = p.wait()
    logf.write(f"child exit pid={p.pid} rc={rc} {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    logf.close()
    print(f"launched pid={p.pid} rc={rc} log={log_path}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
