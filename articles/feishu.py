import os
import subprocess
import json
import asyncio
from .base import BaseOutput


class FeishuOutput(BaseOutput):
    # 进程内缓存已解析的系列容器 node_token，避免同进程重复建/重复查
    _series_node_cache: dict = {}

    def __init__(self, name: str = "feishu"):
        super().__init__(name)
        self.wiki_space = os.getenv("FEISHU_WIKI_SPACE", "")
        self.wiki_parent_node = os.getenv("FEISHU_WIKI_PARENT_NODE", "")
        self._cli_available = None

    def _run_cli_command(self, args: list, timeout: int = 90, input_text: str = None) -> dict:
        try:
            result = subprocess.run(
                ["lark-cli"] + args,
                capture_output=True,
                text=True,
                encoding="utf-8",
                shell=True,
                timeout=timeout,
                input=input_text,
            )

            if result.returncode != 0:
                error_msg = result.stderr.strip() or result.stdout.strip()
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

    async def _run_cli_command_async(self, args: list, timeout: int = 90, input_text: str = None) -> dict:
        try:
            process = await asyncio.create_subprocess_exec(
                "lark-cli", *args,
                stdin=asyncio.subprocess.PIPE if input_text is not None else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                shell=True
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

            stdout_str = stdout.decode('utf-8') if stdout else ""
            stderr_str = stderr.decode('utf-8') if stderr else ""

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

    def _resolve_parent(self, parent_token: str = None) -> list:
        """返回 --parent-token / --wiki-space 参数列表（优先级：显式 parent_token > 配置父节点 > 空间）。"""
        if parent_token:
            return ["--parent-token", parent_token]
        if self.wiki_parent_node:
            return ["--parent-token", self.wiki_parent_node]
        return ["--wiki-space", self.wiki_space]

    _inbox_node_cache: dict = {}

    def ensure_inbox_node(self) -> str:
        """确保「待归类」收件箱节点存在于父节点下，返回其 node_token（已存在则复用）。

        与 ensure_series_node 同理：单篇总结默认落此节点，用户后续手动拖到分类节点。
        """
        if not self.is_available():
            return ""
        parent = self.wiki_parent_node
        space = self.wiki_space
        cache_key = f"{space}|{parent}|待归类"
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
                    if item.get("title") == "待归类":
                        tok = item.get("node_token", "")
                        FeishuOutput._inbox_node_cache[cache_key] = tok
                        return tok
        except Exception:
            pass
        # 2) 不存在则创建（docx 节点作容器，与系列课容器一致）
        try:
            result = self._run_cli_command([
                "wiki", "+node-create",
                "--title", "待归类",
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
                    print(f"   📥 已建飞书收件箱节点「待归类」：{tok}")
                    FeishuOutput._inbox_node_cache[cache_key] = tok
                    return tok
        except Exception as e:
            print(f"   ⚠️ 建飞书收件箱节点异常：{e}")
        return ""

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

    def save(self, content: str, filename: str, parent_token: str = None) -> bool:
        if not self.is_available():
            return False

        # 单篇总结默认进「待归类」收件箱（用户手动归类）；系列课走 save_series 显式传 parent_token
        if not parent_token:
            parent_token = self.ensure_inbox_node()

        print("正在上传到飞书知识库...")

        title = os.path.splitext(filename)[0]

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

        # 单篇总结默认进「待归类」收件箱；系列课走 save_series 显式传 parent_token
        if not parent_token:
            parent_token = self.ensure_inbox_node()

        print("正在上传到飞书知识库（异步）...")

        title = os.path.splitext(filename)[0]

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