# PLAN-20260911 · CDP 统一方案：实例层上移技能 + 项目侧薄适配

> 状态：**✅ 设计全部拍板（2026-09-11）**——D1-D5 + 方案 b + 双轨 + D4 + §3.2 文件锁 + §3.4 方案 A（死回退已清理）+ D6 退出机制（持有者注册表）+ §3.1 get_html + §6 观察期 7 天，全部确认。无剩余待批项；**新会话从 §9 S1（技能纯增量）开始执行**，S1-S6 每步独立 commit、独立验证。
> 设计对象：`shared/cdp_session.py`（550 行）→ 用户级技能 `cdp-automation-profile`；`videos/cdp_launch.py`（230 行）退役；YouTube 两消费方迁移共享端点。
> 讨论结论存档：`C:\Users\O1830\AppData\Local\Temp\handoff-20260911-133406.md`

## 1 已拍板决策与本文档范围

| # | 决策 | 内容 | 状态 |
|---|------|------|------|
| D1 | 分层原则 | 跟 Chrome 怎么被多进程安全共享有关的进技能，跟抓哪个网站有关的留项目 | 已确认 |
| D2 | B 组归属 | 方案 b：连接语义（会话封装 / from_endpoint / restart_fresh）随实例生命周期整类上移，项目侧瘦身为 re-export，11 处消费方零改动 | 用户拍板（本轮） |
| D3 | 引用形态 | 模块 + CLI 双轨：Python 项目 sys.path import 技能模块；非 Python 项目走 CLI 拿端点 JSON；两轨调同一份代码 | 用户拍板（本轮） |
| D4 | 代理扩展 | 撤销「共享启动加 `--load-extension` 参数位」例外设计；iGuge 降级为技能文档背景注记；全量复制已保留扩展与代理设置（2026-09-04 实测），仅留 YouTube 迁移后首跑的运行时验证点 | 用户拍板（本轮） |
| D5 | 共用语义 | 全机唯一实例；随机端口 + 端口文件集合点；user-data-dir 单实例锁；连接级隔离；实例常驻、没人日常关闭、仅 marker 过期精准清理；多身份 = 多克隆目录参数化 | 已确认（上一轮） |

本文档范围：给出两侧文件改动清单、API 签名、YouTube 迁移 diff 级改法、cdp_launch 退役步骤、测试计划、文档对齐条款。**全部为设计，未动手。**

## 2 目标架构与分层边界

```
用户级技能 cdp-automation-profile（跨项目单一真源）
│  实例生命周期：profile 全量复制/3 天 marker / 端口探测 / stale 自愈 / 僵尸清理 / 克隆启动
│  连接语义：    内核会话类（connect/close 只断开、from_endpoint、restart_fresh）
│  对外入口：    ensure_endpoint() 函数 + ensure_endpoint.py CLI（双轨同源）
│  依赖：        仅标准库（playwright 延迟导入，CLI 零 playwright）
│
├── 调用（Python 轨：sys.path import / CLI 轨：--json 拿端点）
│
本项目 blog-article-skill（及未来其他项目）
│  薄适配：      SharedCdpSession(技能内核类) —— 业务方法子类 + re-export
│  业务消费：    scys 抓取 / 公众号抓取（token、扫码续期）/ B站抓取（BILI_COOKIE）
│                / YouTube 字幕（迁移：改读共享端点）/ 监控编排
```

边界判据（D1）：技能内核不知道「微信 / scys / B站 / YouTube」任何一个词；项目侧不出现端口探测、进程清理、profile 复制中的任何一行。

## 3 技能侧改动（实施步骤 S1，纯增量）

技能目录现状：`SKILL.md`、`ensure_cdp_profile.py`、`cdp_open.py`、`find-url.mjs`、`selftest_kill.py`。新增三件，不改既有文件行为。

### 3.1 内核模块 `cdp_session.py`（新增）

上移来源 = `shared/cdp_session.py`，按 D1/D2 拆分：

| 原位置（shared/cdp_session.py） | 去向 | 说明 |
|---|---|---|
| `_free_port` L53 / `_read_devtools_port` L84 / `_probe_reuse_endpoint` L93 | 上移 | 原样 |
| `_probe_endpoint` L62 | 上移并解耦 | 现依赖项目 `scripts/login_cdp_fetch.probe_chrome_devtools`（L70）→ 改技能自带 HTTP 探测（GET `/json/version`，同 cdp_launch.py `is_port_up` L48 的轻量做法） |
| `_kill_chrome_on_port` L98 / `_get_chrome_cmdlines` L120 / `_find_clone_chrome_port` L151 / `_rewrite_devtools_port_file` L167 / `_kill_clone_chrome` L185 | 上移 | 进程清理族原样（Get-CimInstance / taskkill /F /T 坑已固化在 docstring） |
| `_find_system_chrome` L209 / `_chrome_running` L223 / `_ensure_chrome_closed` L233 | 上移 | 原样 |
| `_launch_cloned_logged_in_browser` L258 | 上移并解耦 | 现依赖项目 `profile_clone_fetch`（ensure_profile_clone / clone_is_fresh / CLONE_DIR，L276）→ 改调技能自身 `ensure_cdp_profile.py`（同目录 import，复制与 marker 判定单一真源） |
| `SharedCdpSession.__init__` L301 三段式 + `from_endpoint` L369 + `restart_fresh` L398 + `new_page` L511 + `context` L514 + `close` L518 + `__enter__/__exit__` L546 | 上移 | 类内核，改名 `CdpSession`（技能命名空间） |
| `get_html` L427 | 留项目 | 仅 scys 使用，遵守 YAGNI，技能最小切口 |
| `get_title` L418（依赖 `shared/fetch_title._clean`）、`_WECHAT_LOGIN_MARKERS` L437、`_extract_body` L440、`fetch_wechat` L461、`wechat_batch` L482（依赖 `login_cdp_fetch.write_output`、`profile_clone_fetch.slugify`、`notes/` 目录） | 留项目 | 业务方法，进项目侧薄子类 |

模块级 `sys.path` 注入（原 L48-50）与全部项目内 import 不随之上移；内核对外零项目依赖。

### 3.2 `ensure_endpoint()` API（新增，最小切口核心）

```python
class EnsureResult(NamedTuple):
    endpoint: str          # ws://127.0.0.1:<port>/devtools/browser/<uuid>（fallback 时 ws://127.0.0.1:<port>）
    port: int
    profile_dir: Path
    action: str            # "reused" | "healed" | "rebuilt"
    proc: object | None    # 仅 action="rebuilt" 时为 Popen 句柄；可不持有（生命周期见 §7/D6）

def ensure_endpoint(profile_dir: Path | None = None) -> EnsureResult:
    """三段式编排（逻辑 = 现 __init__ L315-357 的抽离）：
    ① 探测复用：读克隆目录 DevToolsActivePort → /json/version 探活 → 直接返回（reused）
    ② 端口自愈：Get-CimInstance 扫活进程命令行找真实端口 → 命中回写端口文件直接返回（healed）
    ③ 清理重建：旧端口 + 克隆目录双重清僵尸 → marker 陈旧/缺失才关 Chrome + 全量复制
       （clone_is_fresh 为真时复制是 no-op，不碰日常 Chrome）→ 随机端口启动（rebuilt）
    纯环境操作，不 connect、不 import playwright。"""
```

内核类 `CdpSession.__init__` 收敛为：`r = ensure_endpoint()` → `connect_over_cdp(r.endpoint)` → 接住 `r.proc` 供诊断，close 不杀（§7）。

### 3.3 CLI 外壳 `ensure_endpoint.py`（新增）

```
python ensure_endpoint.py            # 三段式保证就绪，stdout 输出 ws 端点（最后一行，同 ensure_cdp_profile 风格）
python ensure_endpoint.py --json     # 输出 {"endpoint": "...", "port": N, "profile_dir": "...", "action": "reused"}
python ensure_endpoint.py --probe-only   # 只探测/自愈，不清理不启动（等价 --status 语义）
```

CLI 走同一份 `cdp_session.ensure_endpoint`，无第二实现。非 Python 项目（Node 等）用 `CDP_SKILL_PY` / `CDP_PYTHON` 子进程调用，与现有 `ensure_cdp_profile.py` 调用方式同构。

### 3.4 技能路径解析（双轨契约）

- CLI 轨：沿用 **`CDP_SKILL_PY`**（现有跨平台唯一契约，平台安装时注入）。
- 模块轨：新增 **`CDP_SKILL_DIR`**（可选），回退链：`$CDP_SKILL_DIR` → `dirname($CDP_SKILL_PY)` → `~/.trae-cn/skills/cdp-automation-profile` → `~/.workbuddy/skills/cdp-automation-profile`，取第一个存在 `cdp_session.py` 的目录。项目侧封装于 `shared/cdp_session.py` 顶部 `_import_skill_module()`，找不到时抛带安装指引的清晰错误。

### 3.5 SKILL.md 更新

新增「内核会话与 ensure_endpoint」一节（API、CLI 用法、双轨接入片段）；新增「共用契约」一节（固化 D5 表）；关键坑区新增 iGuge 一句话背景注记（D4：全量副本已含扩展与代理设置，无需 `--load-extension`）。

## 4 项目侧改动（实施步骤 S2）

### 4.1 `shared/cdp_session.py` 瘦身（550 行 → 约 120 行）

```python
# 结构示意（非最终代码）
_skill = _import_skill_module()                       # §3.4 回退链
from <skill>.cdp_session import CdpSession as _CdpSession
from <skill>.cdp_session import ensure_endpoint, EnsureResult   # re-export 供新代码/测试用

class SharedCdpSession(_CdpSession):
    """业务薄子类：原 get_title / get_html / fetch_wechat / wechat_batch / _extract_body
    原样搬入；生命周期与连接语义全部继承技能内核。"""
    def get_title(self, url, wait_min=6000, wait_max=14000): ...
    def get_html(self, url, wait=8000): ...
    def fetch_wechat(self, url, wait_ms=8000): ...
    def wechat_batch(self, urls, wait_ms=8000): ...
```

**消费方零改动验证**（`from shared.cdp_session import SharedCdpSession` 全部保持原路径）：

| 消费方 | 位置 | 用法 | 影响 |
|---|---|---|---|
| monitors/run.py | L847、L1310 | `SharedCdpSession()` | 无 |
| monitors/run_parallel.py | L109-110、L193-194 | `from_endpoint(endpoint)` / `SharedCdpSession()` | 无 |
| monitors/run_source.py | L196、L230 | `with SharedCdpSession()` | 无 |
| articles/fetch.py | L261-262 | 进程内单例 | 无 |
| shared/fetch_title.py | L142 `WeChatCdpSession(SharedCdpSession)` | 继承 | 无（继承薄子类，原方法全在） |
| scripts/scys_batch_fetch.py | L40、L659 | 会话注入 | 无 |
| scripts/rename_list.py | L43、L234 | `with SharedCdpSession()` | 无 |
| scripts/audit_fidelity.py | L201-202 | `with SharedCdpSession()` | 无 |
| videos/fetch.py | L630-636 | B站 cookie 提取 | 无 |

### 4.2 `scripts/profile_clone_fetch.py` 角色变化

- `ensure_profile_clone`：已是「委托技能 + 本地回退」（L120-135），不动。
- `clone_is_fresh`（L78-106）：改为优先 import 技能模块同判定，本地实现降级为回退（与 ensure_profile_clone 同模式）。`CLONE_DIR`（L53）与 `CDP_PROFILE_DIR` 约定两侧一致，不动。

### 4.3 现依赖反向解耦确认

`shared/cdp_session.py` 对 `scripts/login_cdp_fetch`（probe_chrome_devtools L70、write_output L491）与 `scripts/profile_clone_fetch`（L276/L326/L492）的依赖随上移消解：probe 内置技能、克隆判定走技能、write_output/slugify 随业务方法留在项目侧且调用方就在项目侧。

## 5 YouTube 迁移（实施步骤 S3）

迁移目的（讨论已确认）：两函数硬编码 9222（老 cdp_launch 端点约定），cdp_launch 退役后无人起 9222，YouTube 字幕抓取直接断；迁移后与 scys / 公众号 / B站共用同一共享实例与同一份 Google 登录态，端点来源统一。

### 5.1 `videos/cdp_capture.py`

```python
# L72  def capture_transcript(url, port=9222, wait=40, out=None):
def capture_transcript(url, port=None, wait=40, out=None):
    if port is None:
        port = _ensure_endpoint().port        # 技能 ensure_endpoint，模块级缓存避免重复编排
    # L78 base = f"http://127.0.0.1:{port}" 起的逻辑不变
```

main（L211 `--port` default 9222 → default None；L217 `from videos.cdp_launch import ensure_chrome_running` 整段删除，由 `_ensure_endpoint()` 取代）。docstring 前置说明（L10-13）改指共享克隆目录与随机端口 + 端口文件。

### 5.2 `videos/fetch.py`

```python
# L247 def fetch_youtube_transcript_cdp(url: str, port: int = 9222, wait: int = 45):
def fetch_youtube_transcript_cdp(url: str, port: int | None = None, wait: int = 45):
    # L253-261 删除 cdp_launch 的 ensure_chrome_running 段，改为：
    #   ep = ensure_endpoint()   →   capture_transcript(url, port=ep.port, wait=wait)
```

`fetch_youtube_transcript`（L274）主入口 L313 `fetch_youtube_transcript_cdp(url)` 不变（默认 None → 自动 ensure_endpoint）。顺手修正 L622 注释漂移：「代价：会杀掉当前 Chrome 进程」→ 实际为「仅 clone 陈旧需复制时才关 Chrome（2026-09-10 条件化），会话结束仅注销持有者、最后持有者才关灯（见 §7/D6）」。

### 5.3 迁移验证（D4 运行时验证点）

实抓一条 YouTube 视频，确认：① 共享克隆实例下登录态可用；② 网络可达（代理经全量副本生效，iGuge 无需 --load-extension——若此验证失败再回到 D4 重议，不做事先设计）。验证通过前不进入 S4。

## 6 cdp_launch 退役（三步，跨 S3-S5）

| 步骤 | 动作 | 审批点 |
|---|---|---|
| 第一步（随 S3） | `videos/cdp_launch.py` 文件头加 DeprecationWarning 指向 `ensure_endpoint`；消费方全部改离（fetch.py L254、cdp_capture.py L217） | 本文档批准后随 S3 执行 |
| 第二步 | 观察期（**已确认 7 天**，2026-09-11）：YouTube / 全项目正常跑，无回退诉求 | 期间不动 |
| 第三步 | 删除 `cdp_launch.py`；清理引用：`references/youtube-cdp-workflow.md` 整篇按新架构重写（Chrome-CDP 副本 → 共享克隆目录、9222 → 端口文件、iGuge → 背景注记）、`RULES.md` L149 自修复指引改 `ensure_endpoint`、`README.md` L373、`AGENTS.md` 对应表述 | 第二次审批 |

退役理由存档（讨论已确认）：隔离式小副本（11 项扩展文件 + 固定 9222）与共享全量克隆（16GB 全量 + 随机端口 + 健康复用）方法不互补，AB 双方案只会双份维护；可取点（服务制「起一次常驻」外壳、`is_port_up` 轻量探测）已嫁接进 `ensure_endpoint`。

## 7 语义变更明示：close 从「自启动即关即清」到「实例常驻」

现行为（cdp_session.py L526-540）：自启动会话 close 时 taskkill 杀掉自己拉起的进程树。多 Agent 场景下，复用方（`_own_browser=False`）若还在连接，自启动方 close 会把整个实例带走——与健康复用「多 Agent 不互杀」的初衷相悖。

本方案执行已确认的 D5 契约：**close 永远只断开连接；实例常驻为全机共享资产；生命周期仅由 marker 过期精准清理（`--kill` / 三段式第③段）管理**。内核 close() 删除杀进程分支，`_own_browser` 仅保留给 `restart_fresh` 判断能否重建。影响：会话结束后 Chrome 留存（内存占用），由下次调用探测复用（省 30s+）或 marker 过期统一清理。此项为行为变更，随 S2 生效，测试 §8 覆盖。

## 8 测试计划（TDD，先测后码）

| 层 | 文件 | 内容 |
|---|---|---|
| 技能 | `selftest_endpoint.py`（新增，同 selftest_kill 模式） | 真拉 headless 实例 → ensure_endpoint 三段式各分支自判 PASS/FAIL：探测复用命中 / stale 文件自愈回写 / 清理后 rebuilt / rebuilt 后再调 reused |
| 项目 | `tests/test_cdp_session_reuse.py`（改造，现 669 行） | import 侧：`shared.cdp_session` 路径不变；monkeypatch 目标从项目模块命名空间改到技能模块命名空间（re-export 后 patch 项目侧名字不再作用于内核，这是本次测试改造的关键点）；行为断言全部保留 |
| 项目 | `tests/test_ensure_endpoint_orchestration.py`（新增） | mock 三段式分支编排：endpoint 命中不再清理；自愈命中回写；rebuilt 返回 EnsureResult 结构；CLI `--json` 输出 schema |
| 回归 | 既有 | `python -m pytest tests/ -x`（含 test_scys_routing 等全量）；`python selftest_kill.py`（技能 kill 逻辑未动，确认无回归） |

用户测试规范对照：每函数覆盖正常 / 边界（空端口文件、目录缺失、None 端口）/ 异常（探测失败、技能未安装给出清晰错误）三类；每个测试只测一个行为。

## 9 实施步骤（每步独立 commit、独立验证）

| 步骤 | 内容 | 验证 |
|---|---|---|
| S1 技能纯增量 | 新增 cdp_session.py + ensure_endpoint.py + selftest_endpoint.py + SKILL.md 更新；项目侧零改动 | 技能 selftest 全 PASS；项目全量测试仍绿（未接线） |
| S2 项目侧瘦身 | §4.1 薄子化 + §4.2 clone_is_fresh 委托 + 测试改造 + §7 close 语义 | 项目全量测试绿；`python monitors/run.py --mode auto --apply` 手动一轮（或用户指令触发） |
| S3 YouTube 迁移 | §5 两函数 + cdp_launch 标记 deprecated + fetch.py L622 注释 | §5.3 实抓验证通过 |
| S4 文档对齐 | §10 全部条款一次 commit | 逐条核对表 |
| S5 退役收尾 | 观察期后删 cdp_launch.py + 清引用 | 全仓 grep `cdp_launch|9222` 零业务命中 |
| S6 brain 同步 | 技能双副本（平台工作目录 ↔ brain `universal/cdp-automation-profile`）按 skill-installer 流程推送 | 用户指令触发（与 t26 分开） |

## 10 文档改动点（P2 对齐条款，随 S4）

| 文件 | 改什么 |
|---|---|
| `AGENTS.md` L18 | 「健康复用优先」段补：实例层（会话 + ensure_endpoint）已上移用户级技能，项目侧 `SharedCdpSession` 为业务薄子类；表述统一为「实例常驻、连接只断开、marker 过期才清理」，消除与 `--kill` / 定期全量复用的表述冲突 |
| `RULES.md` L149 | 「重跑 `python videos/cdp_launch.py` 自修复」改为 ensure_endpoint 语义（或随 S5 直接删） |
| `references/youtube-cdp-workflow.md` | 整篇重写：Chrome-CDP 小副本 → 共享克隆目录；固定 9222 → DevToolsActivePort 端口文件；iGuge / `--load-extension` → 背景注记（D4） |
| 技能 `SKILL.md` | §3.5 两节 + iGuge 注记 |
| `README.md` L373 | cdp_launch 行改为技能内核指针（随 S5 删除该行） |
| `docs/decisions/DECISION-20260910-cdp-sandbox-bypass.md` | 顺手更正「沙箱会回收子进程」过时结论（此前已识别待更正） |

## 11 风险与回滚

| 风险 | 缓解 |
|---|---|
| 技能双副本漂移（平台工作目录 vs brain） | `CDP_SKILL_PY`/`CDP_SKILL_DIR` 契约定位；S6 同步；技能改动永远先落工作副本 |
| 模块轨 import 生成 `__pycache__` 污染技能目录 | 技能 `.gitignore` 已有先例（_archive）；接受或忽略，不阻塞 |
| close 语义变更引发实例堆积 | D6 最后持有者关灯默认开：无持有者实例不滞留；marker 过期 + `--kill` 兜底；仅 `CDP_IDLE_SHUTDOWN=0`（纯常驻）会堆积，观察期按需使用 |
| D6 最后持有者误判（判定与新 connect 竞态） | 判定+关闭锁内原子；撞上关灯窗口的后来者走 ensure_endpoint 重建兜底，多等几秒不失败 |
| YouTube 迁移后网络不可达（代理意外失效） | D4 已定：运行时验证点，失败回到 §5.3 重议，不阻塞其余步骤 |
| 回滚 | S1 纯增量直接删除即回滚；S2/S3 各自独立 commit 可 revert；re-export 保证旧 import 路径兼容，消费方无需回改 |

## 12 决策状态（2026-09-11 全部拍板）

**已拍板**（无剩余待批项）：

| # | 决策 | 状态 |
|---|---|---|
| D1-D5 | 分层原则 / 方案 b 整类上移 / 模块+CLI 双轨 / 代理扩展撤销 / 实例常驻语义 | 已确认 |
| §3.2 文件锁 | 并发冷启动第③段全程持克隆目录文件锁 + double-check 二次探测 | 已拍板（2026-09-11） |
| §3.4 方案 A | 技能路径回退链去平台化：仅 `$CDP_SKILL_DIR` → `dirname($CDP_SKILL_PY)` 两条，落空报错指引 `.env` 配置，不做平台目录枚举 | 已拍板（2026-09-11） |
| §3.4 存量清理 | `profile_clone_fetch._resolve_skill_py` 的 `~/.workbuddy` 死回退已删除（含模块 docstring 两处平台示例表述更正） | 已执行（2026-09-11） |
| §3.1 get_html | 留项目（YAGNI），技能内核不含业务方法 | 用户拍板（2026-09-11） |
| D6 退出机制 | close = 断开 + 自注销持有者；最后持有者关灯（`.cdp_holders` 注册表 + PID 存活探测 + 锁内原子判定，`CDP_IDLE_SHUTDOWN` 默认开）；配 0 = 纯常驻 fallback | 用户拍板（2026-09-11） |
| §6 观察期 | cdp_launch 标弃用后观察 **7 天** | 用户拍板（2026-09-11） |

**下一步**：S1-S6 全部完成（见 §13），计划收官；无剩余执行项。

## 13 执行进度（2026-09-12 更新）

### 已完成

| 步骤 | 产出 | 验证 |
|---|---|---|
| S1 ✅ | 技能内核 `cdp_session.py`（687 行：连接语义 / §3.2 文件锁 / D6 holder 关灯 / `ensure_endpoint` 三段式）+ CLI `ensure_endpoint.py`（含 `--probe-only` / `--json`）+ SKILL.md「内核会话」「共用契约」两节 + iGuge 注记（D4） | py_compile；`ensure_endpoint.py --probe-only` 冒烟；项目全量 pytest 绿 |
| S2 ✅ | `shared/cdp_session.py` 瘦身为 205 行薄子类（`_resolve_skill_dir` 回退链加载内核 + 尾部 re-export `CdpSession`/`EnsureResult`/`ensure_endpoint`/`probe_endpoint`，11 处消费方零改动）；`profile_clone_fetch.clone_is_fresh` 无参本地化 + `ensure_profile_clone` subprocess 委托；`.env` 配 `CDP_SKILL_DIR`（`.env.example` 已补说明）；重写 `tests/test_cdp_session_reuse.py`（49 例）+ 新建 `tests/test_ensure_endpoint_orchestration.py`（24 例） | 两文件 73 例绿；项目全量 867 例绿；commit `5ae7b65` |
| S3 ✅ | §5 YouTube 迁移：`cdp_capture.py`（模块级 `_ENDPOINT_CACHE` + `_ensure_endpoint()` helper、`capture_transcript` 与 CLI `--port` 均改 default None、docstring 改指共享克隆目录/D5-D6 契约、直跑 CLI 补 sys.path 引导）+ `fetch.py`（`fetch_youtube_transcript_cdp` 改 `ensure_endpoint()` 编排、L622 注释改条件化关 Chrome + 持有者关灯语义）+ `cdp_launch.py` 文件头 DeprecationWarning；顺带修内核 `__init__` 内层恢复隐患（`_pw_stop()` 后重建 `_p` 对齐 `restart_fresh`）；新增 `tests/test_youtube_cdp_endpoint.py`（8 例，含防真实 Chrome 安全网）+ 内核重建用例 1 例 | 目标 9 例绿；项目全量 876 例绿；§5.3 实抓 `aircAruvnKk` 成功（冷启动实例端口 12139 复用直连，字幕 18430 字落盘 `.cache/yt_transcript_aircAruvnKk.txt`） |
| S4 ✅ | §10 六项文档对齐（2026-09-12，一次 commit）：① AGENTS.md L18 实例层上移注记 + 统一「实例常驻、连接只断开、marker 过期才清理」表述；② RULES.md §4.4 两处（9222→CDP 端点、cdp_launch 自修复→ensure_endpoint 三段式 + 技能 CLI `--probe-only`；最短路径①同步改写）；③ `references/youtube-cdp-workflow.md` 整篇重写（共享克隆目录/动态端口/EnsureResult 契约/D4 iGuge 背景注记/D6 关灯/TRAE 沙箱排查行；降级保存 API 更正 `skill_continue_summary`→`save_summary_only`）；④ 技能 SKILL.md §3.5 两节 + iGuge 注记核对通过（S1 产物，`.trae-cn` 工作副本）；⑤ README.md L373 cdp_launch 行改弃用注记；⑥ DECISION-20260910「回收子进程」表述核对通过（现文已是「按路径拦截启动期写盘」，全仓无残留，零改动）；另顺带对齐项目侧 SKILL.md 两处旧表述（该文件实际被 git 跟踪，AGENTS「已 gitignore」说法与事实不符，本次仍不提交、改动留工作区）；顺带修 `references/login-required-cdp-workflow.md` §7 三处过时交叉引用（重写连带漂移：共享克隆实例/同一实例/结论句） | 逐条核对 + 全量 grep 复核（旧关键词仅剩计划描述/弃用对照语境，零业务命中）；commit 含 AGENTS/RULES/README/两份工作流文档/PLAN 六文件 |
| S5 ✅ | 删除 `videos/cdp_launch.py`（235 行弃用启动器）；清引用：README 目录树删该行、workflow 头注/模块表/§7 旧方案表三处改「已删除」措辞、tests 删 `_guard_no_real_chrome` 绊线 helper（含四处调用）与 `TestCdpLaunchDeprecation` 弃用测试类（连带清理未使用 importlib/sys/MagicMock） | 全量 pytest **923 passed** 全绿（弃用测试随类移除）；grep `cdp_launch\|9222` 零业务命中（剩余命中均为 PLAN 历史记录 / workflow「已删除」措辞 / `login_cdp_fetch.py` 通用端口探测常量 / tests 显式端口输入值）；用户 2026-09-12 二次批准提前于观察期（原定至 09-18）执行 |
| S6 ✅ | 技能副本同步 brain（skill-installer 流程）：brain `skills/universal/cdp-automation-profile` 补 S1 两文件 + SKILL.md 两节（commit `2b773a2`，+827/-15，`cdp-proxy.mjs` 被 git 识别为 R100 重命名归档 `_archive/`，pre-commit 门禁全过）并单独 push Gitee 成功；推后回拷两平台副本（`.trae-cn` 已一致零操作、`.workbuddy` 补齐 S1 两文件 + SKILL.md），三端 diff（忽略行尾）零差异；顺带解除项目 `.workbuddy/skills/blog-article-skill/SKILL.md` 的 git 跟踪（`.gitignore` L35 已有 `.workbuddy/` 规则，历史跟踪项走 `git rm --cached`，工作区文件保留） | brain→`.workbuddy` 忽略行尾 diff exit 0；`skill_reconcile.py` 两平台均「通过（无阻断项）」（`.trae-cn` 需 `--ignore` 豁免 8 个 TRAE 平台内置技能 docx/pdf/pptx/xlsx/git-commit/design-taste-frontend/skill-map-traework/batch_link_import——孤儿阻断为既有状态，与本次 S6 无关）；⚠️ 执行偏差：robocopy /MIR 被 TRAE 沙箱拦截写盘（ERROR 5 无限重试，同 DECISION-20260910 先例），改用 PowerShell 原生 `Copy-Item` 逐文件镜像成功 |

### 偏差与已知隐患（记录，不阻塞 S3）

- **`selftest_endpoint.py` 未创建**（用户取消该写入）：S1 验证以 `--probe-only` 冒烟 + 项目 pytest 替代；§8 测试计划表该行作废。
- **S2 的「monitors 实跑一轮」未执行**（需登录态与网络写盘，TRAE 沙箱受限同下条）：随用户日常触发覆盖（说「跑一次」即跑，见项目规则）。
- **内核 `__init__` 内层恢复隐患 ✅ 已随 S3 修复（2026-09-12）**：`_pw_stop()` 后先重建 `self._p` 再 `_attach`（对齐 `restart_fresh` 写法），新增用例 `test_connect_failure_rebuilds_playwright_driver` 锁行为。
- **TRAE 沙箱下 `ensure_endpoint()` 自动编排无法闭环（2026-09-12 实测，环境限制非代码缺陷）**：Chrome 冷启动写自身 profile 文件（Crashpad/BrowserMetrics/lockfile）被沙箱拦截 → 30s 内端口未就绪；端口文件状态机读写受扰，每轮重新冷启动不复用。规避：实抓用 `--port <显式端口>` 直连活实例（已验证）；端口编排逻辑由 8 例单测覆盖；沙箱外真实场景（用户日常跑 monitors / `videos/run.py`）不受影响。
- **技能 SKILL.md 双副本漂移（S4 核对发现）✅ 已随 S6 收敛（2026-09-12）**：`.trae-cn` 工作副本含 S1 产物（「内核会话」「共用契约」两节 + iGuge 注记）✅；`.workbuddy` 副本原缺这些节，S6 经 brain 推送 + 推后回拷对齐（三端忽略行尾 diff 零差异）；项目 `.workbuddy/skills/blog-article-skill/SKILL.md` 已 `git rm --cached` 解除跟踪（`.gitignore` 规则自此真正生效）。

### 下一步（新会话执行）

| 步骤 | 内容 | 前置 |
|---|---|---|
| S5 | §6 观察期 7 天后删 `cdp_launch.py`（第二次审批点） | ✅ 已完成（2026-09-12，用户二次批准提前执行） |
| S6 | 技能副本同步 brain（skill-installer 流程；一并对齐 .workbuddy 副本漂移） | ✅ 已完成（2026-09-12：brain commit `2b773a2` 已 push；两平台副本回拷对齐零差异；reconcile 两平台无阻断；项目 SKILL.md 解除跟踪） |
