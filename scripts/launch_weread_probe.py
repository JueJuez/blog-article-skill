#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/launch_weread_probe.py — weread 抓包探针的 DETACHED 启动器。

为什么需要它：用户扫码 + 摸索页面通常要好几分钟，跨越多轮会话。用 Bash 后台任务
（`run_in_background`）跑会在轮次结束时被回收；这里改成**非 DETACHED 父 spawn 一个
DETACHED 子进程**，父立刻返回，子进程脱离会话树继续跑（与 launch_scys_backfill.py
同款模式，但**不 wait**——本任务需要长期驻留而非串行跑完）。

用法：
    python scripts/launch_weread_probe.py
    python scripts/launch_weread_probe.py --duration 1800 --nav https://weread.qq.com/web/xxx

停止：在 `_tmp/weread_probe/` 下建一个 STOP 文件（或等 --duration 到期）。

⚠️ 不要在本脚本外再套一层 DETACHED launcher（DETACHED 父 spawn 会失能，见
   launch_scys_backfill.py 顶部说明）。
"""
import os
import subprocess
import sys
from pathlib import Path

BASE_DIR = str(Path(__file__).resolve().parent.parent)
SCRIPT = os.path.join(BASE_DIR, "scripts", "weread_probe.py")
OUT_DIR = os.path.join(BASE_DIR, "_tmp", "weread_probe")
LOG = os.path.join(OUT_DIR, "probe.log")

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    argv = [a for a in sys.argv[1:] if a != "--detached"]
    logf = open(LOG, "a", encoding="utf-8", buffering=1)
    # DETACHED 子不继承控制台句柄，必须显式传 stdout 文件对象，否则输出全丢。
    p = subprocess.Popen(
        [sys.executable, "-u", SCRIPT] + argv,
        cwd=BASE_DIR,
        stdout=logf,
        stderr=subprocess.STDOUT,
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )
    print(f"✓ weread 抓包探针已在后台启动（DETACHED pid={p.pid}）")
    print(f"  日志:   {LOG}")
    print(f"  网络记录: {OUT_DIR}/net-*.jsonl")
    print(f"  二维码截图: {OUT_DIR}/latest.png")
    print(f"  停止:   在 {OUT_DIR} 下建 STOP 文件，或等 --duration 到期（默认 1800s）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
