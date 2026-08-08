import os
import shutil
import subprocess
import json
import time
import asyncio
from .base import BaseOutput

# lark-cli 在 Windows 上是 .CMD 包装；用 shell=False + 解析出的完整路径调用，
# 可彻底避免 Windows cmd 把标题里的 & | < > 等当成元字符（曾导致含 & 的标题推送崩溃）。
_LARK_CLI_CACHE = None


def _lark_cli():
    global _LARK_CLI_CACHE
    if _LARK_CLI_CACHE is None:
        _LARK_CLI_CACHE = shutil.which("lark-cli") or "lark-cli"
    return _LARK_CLI_CACHE


# 飞书标题来自文件名，可能含 Windows cmd 元字符。lark-cli 是 .CMD 包装，内部 %* 会把
# & | < > ^ ( ) % 当成命令分隔符导致推送崩溃，且引号无法干净传递（要么被剥掉拆命令，
# 要么被原样存进标题）。统一把 ASCII 元字符映射为全角/安全字（仅作用于标题，正文走
# stdin 不受影响；Obsidian 侧文件名保留原字符，仅飞书展示标题做此归一）。
_SPECIAL_MAP = {
    '&': '和', '|': '｜', '<': '〈', '>': '〉',
    '^': '＾', '(': '（', ')': '）', '%': '％',
}


def _sanitize_title(t: str) -> str:
    if not isinstance(t, str):
        return t
    for k, v in _SPECIAL_MAP.items():
        t = t.replace(k, v)
    return t


class FeishuOutput(BaseOutput):
    # 进程内缓存已解析的系列容器 node_token，避免同进程重复建/重复查
    _series_node_cache: dict = {}

    def __init__(self, name: str = "feishu"):
        super().__init__(name)
        self.wiki_space = os.getenv("FEISHU_WIKI_SPACE", "")
        self.wiki_parent_node = os.getenv("FEISHU_WIKI_PARENT_NODE", "")
        self._cli_available = None

    @staticmethod
    def _rate_limit_text(text: str) -> bool:
        """检测飞书频限特征（CLI 可能以非 0 退出并打印错误，也可能返回 code 99991400）。"""
        t = (text or "").lower()
        return ("frequency limit" in t) or ("request trigger frequency" in t) or ("99991400" in t)

    def _is_rate_limit(self, result) -> bool:
        if not result:
            return False
        if result.get("code") == 99991400:
            return True
        err = result.get("error") or {}
        msg = (err.get("message") or "") if isinstance(err, dict) else str(err)
        return self._rate_limit_text(msg)

    def _cli_exec(self, args: list, timeout: int, input_text: str = None) -> dict:
        """单次执行 lark-cli；命中频限且 CLI 以非 0 退出时，包装成带 code 的 dict 以便外层重试。"""
        try:
            result = subprocess.run(
                [_lark_cli()] + args,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=False,
                timeout=timeout,
                input=input_text,
            )
            out = f"{result.stderr}\n{result.stdout}"
            if result.returncode != 0 and self._rate_limit_text(out):
                return {"code": 99991400, "ok": False, "error": {"message": out.strip()[:300]}}
            if result.returncode != 0:
                error_msg = (result.stderr or "").strip() or (result.stdout or "").strip()
                print(f"✗ CLI命令执行失败: {error_msg}")
                return None

            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                return {"stdout": result.stdout.strip()}

        except FileNotFoundError:
            print("✗ 未找到飞书CLI，请先安装: npx @larksuite/cli@latest install")
            return None
        except subprocess.TimeoutExpired:
            print("✗ CLI命令执行超时")
            return None
        except Exception as e:
            print(f"✗ CLI命令执行异常: {str(e)}")
            return None

    def _run_cli_command(self, args: list, timeout: int = 90, input_text: str = None,
                         max_retries: int = 4, retry_base: float = 3.0) -> dict:
        """执行 lark-cli，并对飞书频限（99991400 / frequency limit）做指数退避重试。

        频限是瞬时错误，重试即可恢复；其他错误不重试、原样返回。
        """
        last = None
        for attempt in range(max_retries + 1):
            last = self._cli_exec(args, timeout, input_text)
            if self._is_rate_limit(last) and attempt < max_retries:
                wait = retry_base * (2 ** attempt)
                print(f"   ⏳ 触发飞书频限，{wait:.0f}s 后重试 ({attempt + 1}/{max_retries})")
                time.sleep(wait)
                continue
            return last
        return last

    async def _cli_exec_async(self, args, timeout, input_text=None):
        try:
            process = await asyncio.create_subprocess_exec(
                _lark_cli(), *args,
                stdin=asyncio.subprocess.PIPE if input_text is not None else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                shell=False
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(input=input_text.encode("utf-8") if input_text is not None else None),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                try:
                    process.kill()
                except Exception:
                    pass
                print("✗ CLI命令执行超时")
                return None

            stdout_str = stdout.decode('utf-8', errors='replace') if stdout else ""
            stderr_str = stderr.decode('utf-8', errors='replace') if stderr else ""
            out = f"{stderr_str}\n{stdout_str}"
            if process.returncode != 0 and self._rate_limit_text(out):
                return {"code": 99991400, "ok": False, "error": {"message": out.strip()[:300]}}
            if process.returncode != 0:
                error_msg = stderr_str.strip() or stdout_str.strip()
                print(f"✗ CLI命令执行失败: {error_msg}")
                return None

            try:
                return json.loads(stdout_str)
            except json.JSONDecodeError:
                return {"stdout": stdout_str.strip()}

        except FileNotFoundError:
            print("✗ 未找到飞书CLI，请先安装: npx @larksuite/cli@latest install")
            return None
        except Exception as e:
            print(f"✗ CLI命令执行异常: {str(e)}")
            return None

    async def _run_cli_command_async(self, args: list, timeout: int = 90, input_text: str = None,
                                     max_retries: int = 4, retry_base: float = 3.0) -> dict:
        """异步版本：同样对飞书频限做指数退避重试。"""
        last = None
        for attempt in range(max_retries + 1):
            last = await self._cli_exec_async(args, timeout, input_text)
            if self._is_rate_limit(last) and attempt < max_retries:
                wait = retry_base * (2 ** attempt)
                print(f"   ⏳ 触发飞书频限，{wait:.0f}s 后重试 ({attempt + 1}/{max_retries})")
                await asyncio.sleep(wait)
                continue
            return last
        return last

    def _resolve_parent(self, parent_token: str = None) -> list:
        """返回 --parent-token / --wiki-space 参数列表（优先级：显式 parent_token > 配置父节点 > 空间）。"""
        if parent_token:
            return ["--parent-token", parent_token]
        if self.wiki_parent_node:
            return ["--parent-token", self.wiki_parent_node]
        return ["--wiki-space", self.wiki_space]

    _inbox_node_cache: dict = {}

    def ensure_inbox_node(self) -> str:
        """确保「【00_待归类】」收件箱节点存在于父节点下，返回其 node_token（已存在则复用）。

        与 ensure_series_node 同理：单篇总结默认落此节点，用户后续手动拖到分类节点。
        """
        if not self.is_available():
            return ""
        parent = self.wiki_parent_node
        space = self.wiki_space
        cache_key = f"{space}|{parent}|【00_待归类】"
        if cache_key in FeishuOutput._inbox_node_cache:
            return FeishuOutput._inbox_node_cache[cache_key]
        # 1) 查已有子节点
        try:
            listing = self._run_cli_command([
                "wiki", "+node-list",
                "--parent-node-token", parent,
                "--space-id", space,
                "--as", "user", "--json", "--page-all"
            ])
            if listing and listing.get("ok"):
                for item in listing.get("data", {}).get("nodes", []):
                    if item.get("title") == "【00_待归类】":
                        tok = item.get("node_token", "")
                        FeishuOutput._inbox_node_cache[cache_key] = tok
                        return tok
        except Exception:
            pass
        # 2) 不存在则创建（docx 节点作容器，与系列课容器一致）
        try:
            result = self._run_cli_command([
                "wiki", "+node-create",
                "--title", "【00_待归类】",
                "--node-type", "origin",
                "--obj-type", "docx",
                "--parent-node-token", parent,
                "--space-id", space,
                "--as", "user", "--json"
            ])
            if result and result.get("ok"):
                node = result.get("data", {})
                tok = node.get("node_token") or (node.get("node", {}) or {}).get("node_token")
                if tok:
                    print(f"   📥 已建飞书收件箱节点「【00_待归类】」：{tok}")
                    FeishuOutput._inbox_node_cache[cache_key] = tok
                    return tok
        except Exception as e:
            print(f"   ⚠️ 建飞书收件箱节点异常：{e}")
        return ""

    def list_children(self, parent_token: str) -> list:
        """列出 parent_token 下的子节点（容器/文档），返回 data.nodes 列表。"""
        if not parent_token or not self.is_available():
            return []
        try:
            listing = self._run_cli_command([
                "wiki", "+node-list",
                "--parent-node-token", parent_token,
                "--space-id", self.wiki_space,
                "--as", "user", "--json", "--page-all"
            ])
            if listing and listing.get("ok"):
                return listing.get("data", {}).get("nodes", [])
        except Exception:
            pass
        return []

    def delete_node(self, node_token: str, yes: bool = True, obj_type: str = "wiki") -> bool:
        """删除飞书 wiki 节点（容器/文档）。node_token 唯一标识；--yes 跳过二次确认；obj_type 默认 wiki。"""
        if not node_token or not self.is_available():
            return False
        try:
            args = ["wiki", "+node-delete", "--node-token", node_token,
                     "--obj-type", obj_type,
                     "--space-id", self.wiki_space, "--as", "user", "--json"]
            if yes:
                args += ["--yes"]
            result = self._run_cli_command(args)
            if result and result.get("ok"):
                print(f"   🗑️ 已删飞书节点：{node_token}")
                return True
            print(f"   ⚠️ 删节点返回非 ok：{result}")
            return False
        except Exception as e:
            print(f"   ⚠️ 删节点异常：{e}")
            return False


    def _verify_node_present(self, parent_token: str, title: str) -> bool:
        """保存后最佳努力校验：在 parent 下查找刚写入的 title 节点。

        目的：把「飞书落盘失败被静默吞掉」变成「显式告警」。本会话曾因标题含 & 触发
        Windows cmd 元字符 bug，save 返回 False 但编排层没校验返回值，导致漏节点
        靠人工清点才发现。自此每篇 save 成功后都核对一次。

        非阻塞：飞书有最终一致性，查不到也可能是时序延迟，故只告警、不让 save 失败
        （避免监控层据此无限重试/造成重复节点）。真正兜底由 audit_sync.py 结构性 diff 负责。
        """
        if not parent_token:
            return True
        try:
            children = self.list_children(parent_token)
            for node in children:
                if node.get("title") == title:
                    return True
            print(f"   ⚠️ 校验告警：父节点下未找到刚写入的「{title}」"
                  f"（可能最终一致延迟，或推送实际未生效，请运行 audit_sync.py 复核）")
            return False
        except Exception:
            return True

    def ensure_series_node(self, series_title: str) -> str:
        """确保「系列名」容器节点存在于父节点下，返回其 node_token（已存在则复用）。

        飞书 wiki 没有独立 folder 类型，容器即一个有子节点的 docx 节点，
        与用户需求「先生成一个文件叫<系列名>，下面才是课程内容」一致。
        """
        if not self.is_available():
            return ""
        parent = self.wiki_parent_node
        space = self.wiki_space
        cache_key = f"{space}|{parent}|{series_title}"
        if cache_key in FeishuOutput._series_node_cache:
            return FeishuOutput._series_node_cache[cache_key]
        # 1) 查已有子节点，避免重复建
        try:
            listing = self._run_cli_command([
                "wiki", "+node-list",
                "--parent-node-token", parent,
                "--space-id", space,
                "--as", "user", "--json", "--page-all"
            ])
            if listing and listing.get("ok"):
                # 注意：+node-list 返回结构是 data.nodes（非 items）
                for item in listing.get("data", {}).get("nodes", []):
                    if item.get("title") == series_title:
                        return item.get("node_token", "")
        except Exception:
            pass
        # 2) 不存在则创建（docx 节点作为容器）
        try:
            result = self._run_cli_command([
                "wiki", "+node-create",
                "--title", series_title,
                "--node-type", "origin",
                "--obj-type", "docx",
                "--parent-node-token", parent,
                "--space-id", space,
                "--as", "user", "--json"
            ])
            if result and result.get("ok"):
                node = result.get("data", {})
                token = node.get("node_token") or (node.get("node", {}) or {}).get("node_token")
                if token:
                    print(f"   📁 已建飞书容器节点「{series_title}」：{token}")
                    FeishuOutput._series_node_cache[cache_key] = token
                    return token
            else:
                err = result.get("error", {}).get("message", "未知错误") if result else "命令失败"
                print(f"   ⚠️ 建飞书容器节点失败：{err}")
        except Exception as e:
            print(f"   ⚠️ 建飞书容器节点异常：{e}")
        return ""

    def save_series(self, content: str, filename: str, series_title: str) -> bool:
        """系列课保存：先确保父节点下有「系列名」容器，再在其下建文档。"""
        if not self.is_available():
            return False
        parent = self.ensure_series_node(series_title)
        if not parent:
            return False
        return self.save(content, filename, parent_token=parent)

    # 多级目录节点缓存：{space|parent|a/b/c: node_token}
    _folder_path_cache: dict = {}

    def _ensure_child_node(self, parent_token: str, title: str) -> str:
        """确保 parent_token 下存在名为 title 的容器节点，返回其 node_token。"""
        space = self.wiki_space
        # 1) 查已有子节点
        try:
            listing = self._run_cli_command([
                "wiki", "+node-list",
                "--parent-node-token", parent_token,
                "--space-id", space,
                "--as", "user", "--json", "--page-all"
            ])
            if listing and listing.get("ok"):
                for item in listing.get("data", {}).get("nodes", []):
                    if item.get("title") == title:
                        return item.get("node_token", "")
        except Exception:
            pass
        # 2) 不存在则创建（docx 节点作容器）
        try:
            result = self._run_cli_command([
                "wiki", "+node-create",
                "--title", title,
                "--node-type", "origin",
                "--obj-type", "docx",
                "--parent-node-token", parent_token,
                "--space-id", space,
                "--as", "user", "--json"
            ])
            if result and result.get("ok"):
                node = result.get("data", {})
                tok = node.get("node_token") or (node.get("node", {}) or {}).get("node_token")
                if tok:
                    print(f"   📁 已建飞书容器节点「{title}」：{tok}")
                    return tok
        except Exception as e:
            print(f"   ⚠️ 建飞书容器节点「{title}」异常：{e}")
        return ""

    def ensure_folder_path(self, dirs: list) -> str:
        """确保多级目录节点链存在（如 ['投资交易','舟亦横']），返回最深一级 node_token。

        与 Obsidian 子目录对称：filename 含 "/" 时按路径逐级建 wiki 容器节点。
        任一级失败返回 ""（调用方回落收件箱）。
        """
        if not self.is_available() or not dirs:
            return ""
        parent = self.wiki_parent_node
        space = self.wiki_space
        walked = []
        for d in dirs:
            walked.append(d)
            cache_key = f"{space}|{self.wiki_parent_node}|{'/'.join(walked)}"
            if cache_key in FeishuOutput._folder_path_cache:
                parent = FeishuOutput._folder_path_cache[cache_key]
                continue
            tok = self._ensure_child_node(parent, d)
            if not tok:
                return ""
            FeishuOutput._folder_path_cache[cache_key] = tok
            parent = tok
        return parent

    def _split_subdir(self, filename: str):
        """拆分 filename 的子目录与文件名：'a/b/x.md' -> (['a','b'], 'x.md')。"""
        norm = filename.replace("\\", "/")
        parts = [p for p in norm.split("/") if p.strip()]
        if len(parts) <= 1:
            return [], filename
        return parts[:-1], parts[-1]

    def save(self, content: str, filename: str, parent_token: str = None) -> bool:
        if not self.is_available():
            return False

        # filename 含子目录（如「投资交易/舟亦横/xxx.md」）→ 逐级建容器节点，与 Obsidian 对称
        dirs, base_name = self._split_subdir(filename)
        if dirs and not parent_token:
            parent_token = self.ensure_folder_path(dirs)
        filename = base_name

        # 单篇总结默认进「【00_待归类】」收件箱（用户手动归类）；系列课走 save_series 显式传 parent_token
        if not parent_token:
            parent_token = self.ensure_inbox_node()

        print("正在上传到飞书知识库...")

        title = _sanitize_title(os.path.splitext(filename)[0])

        try:
            args = [
                "docs", "+create",
                "--title", title,
                "--content", "-",
                "--doc-format", "markdown",
                "--as", "user"
            ]

            args.extend(self._resolve_parent(parent_token))

            # 内容经 stdin 传入（CLI 的 --content - 读取标准输入），避免临时文件与路径限制
            result = self._run_cli_command(args, input_text=content)

            if result and (result.get("ok") or result.get("code") == 0):
                doc_url = result.get("data", {}).get("doc_url")
                node_token = result.get("data", {}).get("node_token")

                print(f"✓ 文档创建成功")
                if doc_url:
                    print(f"✓ 文档链接: {doc_url}")
                elif node_token:
                    print(f"✓ 节点Token: {node_token}")
                self._verify_node_present(parent_token, title)  # 飞书落盘自检（非阻塞）
                return True
            else:
                error_msg = result.get("error", {}).get("message", "未知错误") if result else "命令执行失败"
                print(f"✗ 创建文档失败: {error_msg}")
                return False
        except Exception as e:
            print(f"✗ 创建文档失败: {str(e)}")
            return False

    async def save_async(self, content: str, filename: str, parent_token: str = None) -> bool:
        if not self.is_available():
            return False

        # filename 含子目录 → 逐级建容器节点（同步 ensure，量小可接受）
        dirs, base_name = self._split_subdir(filename)
        if dirs and not parent_token:
            parent_token = self.ensure_folder_path(dirs)
        filename = base_name

        # 单篇总结默认进「【00_待归类】」收件箱；系列课走 save_series 显式传 parent_token
        if not parent_token:
            parent_token = self.ensure_inbox_node()

        print("正在上传到飞书知识库（异步）...")

        title = _sanitize_title(os.path.splitext(filename)[0])

        try:
            args = [
                "docs", "+create",
                "--title", title,
                "--content", "-",
                "--doc-format", "markdown",
                "--as", "user"
            ]

            args.extend(self._resolve_parent(parent_token))

            result = await self._run_cli_command_async(args, input_text=content)

            if result and (result.get("ok") or result.get("code") == 0):
                doc_url = result.get("data", {}).get("doc_url")
                node_token = result.get("data", {}).get("node_token")

                print(f"✓ 文档创建成功（异步）")
                if doc_url:
                    print(f"✓ 文档链接: {doc_url}")
                elif node_token:
                    print(f"✓ 节点Token: {node_token}")
                self._verify_node_present(parent_token, title)  # 飞书落盘自检（非阻塞）
                return True
            else:
                error_msg = result.get("error", {}).get("message", "未知错误") if result else "命令执行失败"
                print(f"✗ 创建文档失败: {error_msg}")
                return False
        except Exception as e:
            print(f"✗ 创建文档失败: {str(e)}")
            return False

    def get_output_path(self, filename: str) -> str:
        return "飞书知识库"

    def is_available(self) -> bool:
        if not self.wiki_space:
            return False
        if self._cli_available is None:
            result = self._run_cli_command(["--version"])
            self._cli_available = result is not None
        return self._cli_available