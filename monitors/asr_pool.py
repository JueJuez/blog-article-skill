"""B站无字幕视频的有界 ASR 转写池（串行 / 并行路径共用，单一事实源）。

背景：
- 视频无 CC 字幕时需下载音频 + 本地 faster-whisper 转写，单条耗时分钟级，
  若同步串行调用会阻塞整轮抓取（公众号 / 重试 / 其他源都被拖住）。
- 解决：发现阶段先探测 CC，无 CC 的视频「收集」起来，待本轮主循环结束后，
  统一投递到 ProcessPoolExecutor（并发 = asr_max）批量转写，返回 {url: transcript}。
  与公众号「撞墙篇收集 → 末尾一次 CDP 批量」是同一个"先收集、后批量"的设计范式。

ASR 不需要 CDP/Chrome，故与公众号 CDP 批次互不阻塞，可并行执行（调用方决定）。
"""
import os
import re
import tempfile
import concurrent.futures as cf
import multiprocessing as mp
from typing import Dict, List, Tuple

from status_store import detect_asr_max_concurrency


def _asr_worker(url: str, out_path: str) -> Tuple[str, bool, str]:
    """子进程转写单条 url → 写 transcript 文件。返回 (url, ok, detail)。"""
    from videos import asr as asr_mod  # 延迟导入：子进程内解析 videos 路径
    try:
        r = asr_mod.transcribe_to_file(url, out_path)
        return (url, bool(r), str(r) if r else "empty")
    except Exception as e:  # 单条失败不影响池
        return (url, False, str(e)[:200])


def run_asr_batch(items: List[dict], asr_max: int = 0, run_id: str = "") -> Tuple[Dict[str, str], List[str]]:
    """批量转写无字幕视频。

    Args:
        items: [{url, title, ...}]（已确认无 CC 字幕的视频条目）
        asr_max: 并发数；<=0 自动探测（有 CUDA=2 / 无=1）
        run_id: 仅用于状态记录（可选，当前未强制记录到 status_store）
    Returns:
        (results, failures)
          results: {url: transcript_text}
          failures: [url, ...]（下载/转写失败，交由调用方进重试状态机）
    """
    if not items:
        return {}, []
    asr_max = asr_max or detect_asr_max_concurrency()
    out_dir = tempfile.mkdtemp(prefix="asr_batch_")
    results: Dict[str, str] = {}
    failures: List[str] = []

    def _collect(futs):
        for fut in cf.as_completed(futs):
            it, out = futs[fut]
            try:
                url, ok, _detail = fut.result()
            except Exception:
                failures.append(it["url"])
                continue
            if ok and os.path.exists(out):
                try:
                    results[it["url"]] = open(out, encoding="utf-8").read()
                except Exception:
                    failures.append(it["url"])
            else:
                failures.append(it["url"])

    try:
        with cf.ProcessPoolExecutor(max_workers=max(1, asr_max),
                                    mp_context=mp.get_context("spawn")) as ex:
            futs = {}
            for it in items:
                safe = re.sub(r"\W+", "_", it["url"])[-48:]
                out = os.path.join(out_dir, f"{safe}.txt")
                futs[ex.submit(_asr_worker, it["url"], out)] = (it, out)
            _collect(futs)
    except Exception as e:
        print(f"[warn] ASR 池初始化/执行失败，回退串行 ASR: {e}", file=sys.stderr)
        # 回退：逐条同步转写（此时主循环已结束，不会阻塞其他源）
        for it in items:
            if it["url"] in results or it["url"] in failures:
                continue
            try:
                from videos import asr as asr_mod
                safe = re.sub(r"\W+", "_", it["url"])[-48:]
                out = os.path.join(out_dir, f"{safe}.txt")
                r = asr_mod.transcribe_to_file(it["url"], out)
                if r and os.path.exists(out):
                    results[it["url"]] = open(out, encoding="utf-8").read()
                else:
                    failures.append(it["url"])
            except Exception:
                failures.append(it["url"])
    return results, failures
