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

    def _run_cli_command(self, args: list) -> dict:
        try:
            result = subprocess.run(
                ["lark-cli"] + args,
                capture_output=True,
                text=True,
                encoding="utf-8",
                shell=True
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
        except Exception as e:
            print(f"✗ CLI命令执行异常: {str(e)}")
            return None

    async def _run_cli_command_async(self, args: list) -> dict:
        try:
            process = await asyncio.create_subprocess_exec(
                "lark-cli", *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                shell=True
            )
            stdout, stderr = await process.communicate()

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
            temp_file = f"{title}.md"
            with open(temp_file, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception as e:
            print(f"✗ 创建临时文件失败: {str(e)}")
            return False

        try:
            args = [
                "docs", "+create",
                "--title", title,
                "--markdown", f"@{temp_file}",
                "--as", "user"
            ]

            if self.wiki_parent_node:
                args.extend(["--wiki-node", self.wiki_parent_node])
            else:
                args.extend(["--wiki-space", self.wiki_space])

            result = self._run_cli_command(args)

            if result and (result.get("ok") or result.get("code") == 0):
                doc_url = result.get("data", {}).get("doc_url")
                node_token = result.get("data", {}).get("node_token")

                print(f"✓ 文档创建成功")
                if doc_url:
                    print(f"✓ 文档链接: {doc_url}")
                elif node_token:
                    print(f"✓ 节点Token: {node_token}")

                os.unlink(temp_file)
                return True
            else:
                error_msg = result.get("error", {}).get("message", "未知错误") if result else "命令执行失败"
                print(f"✗ 创建文档失败: {error_msg}")
                os.unlink(temp_file)
                return False
        except Exception as e:
            print(f"✗ 创建文档失败: {str(e)}")
            if os.path.exists(temp_file):
                os.unlink(temp_file)
            return False

    async def save_async(self, content: str, filename: str) -> bool:
        if not self.is_available():
            return False

        print("正在上传到飞书知识库（异步）...")

        title = os.path.splitext(filename)[0]

        try:
            temp_file = f"{title}.md"
            with open(temp_file, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception as e:
            print(f"✗ 创建临时文件失败: {str(e)}")
            return False

        try:
            args = [
                "docs", "+create",
                "--title", title,
                "--markdown", f"@{temp_file}",
                "--as", "user"
            ]

            if self.wiki_parent_node:
                args.extend(["--wiki-node", self.wiki_parent_node])
            else:
                args.extend(["--wiki-space", self.wiki_space])

            result = await self._run_cli_command_async(args)

            if result and (result.get("ok") or result.get("code") == 0):
                doc_url = result.get("data", {}).get("doc_url")
                node_token = result.get("data", {}).get("node_token")

                print(f"✓ 文档创建成功（异步）")
                if doc_url:
                    print(f"✓ 文档链接: {doc_url}")
                elif node_token:
                    print(f"✓ 节点Token: {node_token}")

                if os.path.exists(temp_file):
                    os.unlink(temp_file)
                return True
            else:
                error_msg = result.get("error", {}).get("message", "未知错误") if result else "命令执行失败"
                print(f"✗ 创建文档失败: {error_msg}")
                if os.path.exists(temp_file):
                    os.unlink(temp_file)
                return False
        except Exception as e:
            print(f"✗ 创建文档失败: {str(e)}")
            if os.path.exists(temp_file):
                os.unlink(temp_file)
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