import os
import subprocess
import json
import asyncio
from .base import BaseOutput


class FeishuOutput(BaseOutput):
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

    def save(self, content: str, filename: str) -> bool:
        if not self.is_available():
            return False

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

            if self.wiki_parent_node:
                args.extend(["--parent-token", self.wiki_parent_node])
            else:
                args.extend(["--wiki-space", self.wiki_space])

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

    async def save_async(self, content: str, filename: str) -> bool:
        if not self.is_available():
            return False

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

            if self.wiki_parent_node:
                args.extend(["--parent-token", self.wiki_parent_node])
            else:
                args.extend(["--wiki-space", self.wiki_space])

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