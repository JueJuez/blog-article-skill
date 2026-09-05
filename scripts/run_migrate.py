"""run_migrate.py — schtasks 启动入口。

设计: schtasks 由系统服务拉起本进程(父=系统, 非 DETACHED 用户进程),
故不受 WorkBuddy 会话/DETACHED 父的进程回收影响。本进程仅设好 lark-cli
所需环境, 然后 os.execv 替换为真正的迁移脚本(继承系统父, 常驻跑到完)。

绝不在本文件里写任何会阻塞/退出的逻辑 —— 立即 execv 交给迁移脚本。
"""
import os
import sys

PY = r"C:\Users\O1830\.workbuddy\binaries\python\versions\3.13.12\python.exe"
MIG = r"D:\Code\Skills\blog-article-skill\scripts\feishu_to_obsidian.py"
HOME = r"C:\Users\O1830"
NODE_DIR = r"C:\Users\O1830\.workbuddy\binaries\node\versions\22.22.2"

os.environ["HOME"] = HOME
os.environ["USERPROFILE"] = HOME
if NODE_DIR not in os.environ.get("PATH", ""):
    os.environ["PATH"] = NODE_DIR + ";" + os.environ.get("PATH", "")
os.environ["PYTHONUTF8"] = "1"

# execv 替换当前进程为迁移脚本, 继承系统父 PID → 常驻
os.execv(PY, [PY, MIG] + sys.argv[1:])
