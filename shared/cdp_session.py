"""shared/cdp_session.py — 项目侧薄子类（实例机制单一真源在 cdp-automation-profile 技能内核）。

历史演进（会话语义，2026-08-26 → 2026-09-11）：
  2026-08-26 公众号与 scys 共用一套 CDP 逻辑，「只开关一次浏览器」。
  2026-09-02 塌缩为唯一路径：关 Chrome → 全量复制真实 profile 到非默认目录
    （CdpAutomationProfile\\Chrome，CDP_PROFILE_DIR 可覆盖）→ 非默认 dir 开调试端口
    启动系统 Chrome（Chrome 151+ 仅非默认 dir 放行）→ connect_over_cdp 接管。
    删除路径1（接管活 Chrome：Chrome 151+ 默认 profile 写端口不监听）与
    路径3（headless 克隆无登录兜底：静默撞登录墙）。
  2026-09-04 禁增量同步（破坏 Secure Preferences，丢扩展/登录态），改全量复制。
  2026-09-10 健康复用：先探测克隆调试 Chrome，健康直接接管（close 只断开不杀）；
    stale 端口文件自愈；不健康按旧端口 + 克隆目录双重清僵尸后重建。
  2026-09-11（PLAN-20260911 S2）瘦身：连接编排 / 进程清理 / 文件锁 / D6 持有者
    注册表等实例机制整体上移技能内核 cdp-automation-profile（跨项目单一真源，
    Python import 与 CLI 同源）。本文件只剩：
      1. 技能内核定位与加载（_resolve_skill_dir / _import_skill_module）；
      2. SharedCdpSession 薄子类：只保留本项目业务方法（微信/scys 抓取），
         实例机制全部继承内核 CdpSession；
      3. re-export 内核公开名（CdpSession/EnsureResult/ensure_endpoint/probe_endpoint），
         消费方继续 `from shared.cdp_session import SharedCdpSession`，零改动。

技能目录解析优先级（2026-09-12 架构修正：项目 .env 不再参与，技能自包含）：
  $CDP_SKILL_DIR → dirname($CDP_SKILL_PY，平台安装技能时注入) → 常见平台 skills 目录约定。
  技能自身的运行参数由内核在导入时从其「自身目录内的 .env」加载，项目完全不感知技能位置与参数。

用法：
  A. 单次取标题（公众号）：
        with SharedCdpSession() as s:
            t = s.get_title("https://mp.weixin.qq.com/s/xxx")
  B. 批量 / 复杂交互（scys）：
        with SharedCdpSession() as s:
            page = s.new_page()
            page.goto(url); ...  # scys 自己的 collect_list / fetch_article 逻辑
            html = s.get_html(url)
"""
from __future__ import annotations

import importlib.util
import os
import random
import sys
from pathlib import Path

# wechat_batch 懒导入 scripts/ 下的 login_cdp_fetch / profile_clone_fetch，需保留路径注入。
_SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

_KERNEL_MODULE_NAME = "cdp_automation_profile_kernel"


# 注：项目 .env 不再参与技能路径解析（2026-09-12 架构修正 · 彻底解耦）。
# 技能路径仅由 ①平台注入 CDP_SKILL_PY 或 ②内核自登记文件定位；消费项目不扫描
# 任何平台目录（.workbuddy/.trae-cn 等）。技能运行参数由内核从自身目录 .env 加载。


def _resolve_skill_dir() -> str:
    """定位技能内核目录（cdp_session.py / ensure_cdp_profile.py 所在目录）。

    解析优先级（技能自包含，消费项目不扫描任何平台目录）：
      1) 环境变量 CDP_SKILL_DIR
      2) dirname(环境变量 CDP_SKILL_PY)   ← 平台安装技能时注入（brain 跨平台契约，首选）
      3) 内核自登记文件 %LOCALAPPDATA%\\.cdp_automation_profile\\skill_dir.txt
         （各平台副本在运行 ensure_cdp_profile.py 时写入自身目录，平台未注入时兜底）
    技能自身的运行参数由内核在导入时从其「自身目录内的 .env」加载，与消费项目无关。
    """
    candidates = [
        os.environ.get("CDP_SKILL_DIR"),
        os.path.dirname(os.environ["CDP_SKILL_PY"]) if os.environ.get("CDP_SKILL_PY") else None,
    ]
    # 内核自登记位置（平台无关，由 ensure_cdp_profile.py 在运行时写入；
    # 消费项目不扫描任何平台目录，彻底解耦）。
    reg = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) \
        / ".cdp_automation_profile" / "skill_dir.txt"
    if reg.is_file():
        candidates.append(reg.read_text(encoding="utf-8").strip())
    for cand in candidates:
        if cand and os.path.isfile(os.path.join(cand, "cdp_session.py")):
            return cand
    raise RuntimeError(
        "找不到 cdp-automation-profile 技能内核（目录下需有 cdp_session.py）。\n"
        "彻底解耦模式：消费项目不再扫描平台目录。请二选一：\n"
        "  1) 平台在加载技能时注入 CDP_SKILL_PY（指向本平台技能副本的 cdp_session.py）；或\n"
        "  2) 先运行一次该技能内核的 ensure_cdp_profile.py（任意子命令），完成自登记。\n"
        "三份副本位于 .workbuddy / .trae-cn / brain 的 skills/cdp-automation-profile 下。"
    )


def _import_skill_module():
    """以稳定 sys.modules 名加载技能内核 cdp_session.py（单例缓存，防双重加载破坏 patch）。"""
    mod = sys.modules.get(_KERNEL_MODULE_NAME)
    if mod is not None:
        return mod
    kernel_py = Path(_resolve_skill_dir()) / "cdp_session.py"
    spec = importlib.util.spec_from_file_location(_KERNEL_MODULE_NAME, kernel_py)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法为技能内核创建 import spec：{kernel_py}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_KERNEL_MODULE_NAME] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(_KERNEL_MODULE_NAME, None)
        raise
    return module


_skill = _import_skill_module()


def extract_body(page) -> str:
    """从已渲染页面抽取正文（唯一实现，2026-09-18 收敛）。

    此前本项目有 3 份逐字相同的实现：`articles/fetch.py:_extract_body_scys`、
    `scripts/scys_batch_fetch.py:ScysClient._extract_body`、本类的 `_extract_body`。
    抽取规则必须单一：只改一处却漏了另外两处，会导致同一页面在不同入口抽出的正文不一样。

    策略：按选择器优先级取最长的 inner_text；全落空则退回 `document.body.innerText`。
    """
    body = ""
    for sel in [".article-content", ".article-detail", "#articleContent",
                ".topic-content", ".post-content", ".markdown-body",
                "article", "main", "body"]:
        try:
            el = page.query_selector(sel)
            if el:
                t = el.inner_text().strip()
                if len(t) > len(body):
                    body = t
        except Exception:
            continue
    if not body:
        try:
            body = page.evaluate("() => document.body.innerText")
        except Exception:
            body = ""
    return body or ""


class SharedCdpSession(_skill.CdpSession):
    """项目业务层薄子类：实例机制（健康复用/自愈/锁/D6 关灯）继承内核，此处只放抓取方法。"""

    def get_title(self, url: str, wait_min: int = 6000, wait_max: int = 14000) -> str:
        """每篇等待 wait_min~wait_max 毫秒的随机区间（默认 6~14s），
        打破机械节拍，比固定 8s 更不易被识别为脚本，且仍属人的阅读节奏。"""
        from shared.fetch_title import _clean
        self._page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        self._page.wait_for_timeout(random.uniform(wait_min, wait_max))
        return _clean(self._page.title())

    def get_html(self, url: str, wait: int = 8000) -> str:
        page = self.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(wait)
            return page.content()
        finally:
            page.close()

    _WECHAT_LOGIN_MARKERS = ["立即登录", "登录后查看", "请登录", "扫码登录",
                             "您还未登录", "成为会员", "开通会员", "订阅后"]

    def _extract_body(self, page) -> str:
        """从已渲染页面抽取正文。唯一实现在模块级 `extract_body()`（2026-09-18 收敛）。"""
        return extract_body(page)

    def fetch_wechat(self, url: str, wait_ms: int = 8000):
        """单篇微信正文 → (title, body, 0) 或 None（撞墙/过短/失败）。

        复用本会话已建立的 context（live 或 profile_clone），不另起浏览器。
        """
        page = self.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(wait_ms)
            title = page.title()
            body = self._extract_body(page)
            if any(m in body for m in self._WECHAT_LOGIN_MARKERS):
                return None
            if len(body.strip()) < 100:
                return None
            return (title, body, 0)
        except Exception:
            return None
        finally:
            page.close()

    def wechat_batch(self, urls: list, wait_ms: int = 8000) -> dict:
        """一次会话批量抓多篇微信文：复用本会话 context，避免重开/重 kill 浏览器。

        scys / 公众号 / 重试 三类抓取共用同一个 SharedCdpSession 时，
        微信撞墙篇收集后统一调本方法，与 scys 同一次会话破墙，Chrome 最多杀一次。

        Returns:
            {url: (title, body, 0) 抓取成功 | None 撞墙/失败/过短}
        """
        from login_cdp_fetch import write_output
        from profile_clone_fetch import slugify
        out_base = Path(__file__).resolve().parent.parent / "notes" / "_scraped" / "wechat_cdp_batch"
        out_base.mkdir(parents=True, exist_ok=True)
        results: dict = {u: None for u in urls}
        print(f"[session] 复用同一 CDP 会话批量抓 {len(urls)} 篇微信文（不重开浏览器）")
        for url in urls:
            try:
                r = self.fetch_wechat(url, wait_ms=wait_ms)
            except Exception as e:
                print(f"[session] {url} 失败: {e}")
                r = None
            if r:
                out = out_base / f"{slugify(url)}.md"
                write_output(out, url, r[0], r[1])
                results[url] = r
            else:
                results[url] = None
        return results


# 内核公开名 re-export：消费方与测试统一走 shared.cdp_session 命名空间，不直接 import 技能。
CdpSession = _skill.CdpSession
EnsureResult = _skill.EnsureResult
ensure_endpoint = _skill.ensure_endpoint
probe_endpoint = _skill.probe_endpoint
