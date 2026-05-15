import os
import subprocess
import json
from dotenv import load_dotenv
from .base import BaseOutput


class FeishuOutput(BaseOutput):
    def __init__(self, name: str = "feishu"):
        super().__init__(name)
        load_dotenv()
        self.wiki_space = os.getenv("FEISHU_WIKI_SPACE", "")
        self.wiki_parent_node = os.getenv("FEISHU_WIKI_PARENT_NODE", "")

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

    def save(self, content: str, filename: str) -> bool:
        if not self.is_available():
            return False

        print("正在上传到飞书知识库...")

        title = os.path.splitext(filename)[0]
        temp_file = f"{title}.md"

        try:
            with open(temp_file, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception as e:
            print(f"✗ 创建临时文件失败: {str(e)}")
            return False

        try:
            if self.wiki_parent_node:
                args = [
                    "wiki", "+node-create",
                    "--title", title,
                    "--space-id", self.wiki_space,
                    "--parent-node-token", self.wiki_parent_node,
                    "--as", "user"
                ]
            else:
                args = [
                    "docs", "+create",
                    "--title", title,
                    "--markdown", f"@{temp_file}",
                    "--wiki-space", self.wiki_space,
                    "--as", "user"
                ]

            result = self._run_cli_command(args)

            if result and (result.get("ok") or result.get("code") == 0):
                doc_url = result.get("data", {}).get("doc_url")
                node_token = result.get("data", {}).get("node_token")

                print(f"✓ 文档创建成功")
                if doc_url:
                    print(f"✓ 文档链接: {doc_url}")
                elif node_token:
                    print(f"✓ 节点Token: {node_token}")

                os.remove(temp_file)
                return True
            else:
                error_msg = result.get("error", {}).get("message", "未知错误") if result else "命令执行失败"
                print(f"✗ 创建文档失败: {error_msg}")
                os.remove(temp_file)
                return False
        except Exception as e:
            print(f"✗ 创建文档失败: {str(e)}")
            if os.path.exists(temp_file):
                os.remove(temp_file)
            return False

    def get_output_path(self, filename: str) -> str:
        return "飞书知识库"

    def is_available(self) -> bool:
        if not self.wiki_space or self.wiki_space == "my_library":
            return False
        result = self._run_cli_command(["--version"])
        return result is not None