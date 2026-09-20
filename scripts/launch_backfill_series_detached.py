"""scripts/launch_backfill_series_detached.py — 系列课补齐 DETACHED 启动器。

背景：backfill_series.py 列全集 + 逐条抓字幕(含 ASR 兜底) + 入 pending_summaries 队列，
203 集系列常跑数小时，必须用 DETACHED 子进程脱离 agent 会话树，否则 run_in_background
在轮次结束被环境回收（参考 launch_fetch_up_detached.py 同构结论）。

子进程内部：`run_with_env.py` 注入 .env（BILI_COOKIE 等）→ 执行 `backfill_series.py`。

用法:
    python scripts/launch_backfill_series_detached.py \
        --url <系列1任一集URL> [--url <系列2任一集URL> ...] [--gap 30]
"""
import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", action="append", required=True,
                    help="系列任一集 URL，可多次传入处理多个系列")
    ap.add_argument("--gap", type=float, default=None,
                    help="抓字幕间隔秒（默认读 BILI_GAP，缺省 30）")
    ap.add_argument("--log", default="")
    args = ap.parse_args()

    log_path = Path(args.log) if args.log else ROOT / "_tmp" / "backfill_series.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable, str(ROOT / "scripts" / "run_with_env.py"), "--",
        sys.executable, str(ROOT / "scripts" / "backfill_series.py"),
    ]
    for u in args.url:
        cmd += ["--url", u]
    if args.gap is not None:
        cmd += ["--gap", str(args.gap)]

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logf = open(log_path, "a", encoding="utf-8", buffering=1)
    logf.write(f"\n########## 系列课补齐启动 {ts} urls={args.url} gap={args.gap} ##########\n")
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
    # 父进程阻塞在 p.wait()：本 launcher 由调用方以 run_in_background 启动，
    # 故阻塞数小时不占会话；DETACHED 子进程脱离会话树、抗轮次回收。
    rc = p.wait()
    logf.write(f"child exit pid={p.pid} rc={rc} {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    logf.close()
    print(f"launched pid={p.pid} rc={rc} log={log_path}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
