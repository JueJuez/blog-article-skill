"""
blog-article-skill 命令行入口脚本

提供稳定的命令行接口，所有外部调用都通过此脚本执行。
避免 PowerShell 多行字符串传递问题。

使用方法：
    python assets/run.py "https://example.com/article"
    python assets/run.py --content "文章内容..."
    python assets/run.py --url "https://example.com" --author "作者" --tags "AI,技术"
"""

import sys
import os
import argparse

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

_MODULE_CACHE = {}
def _import_module(module_path: str):
    if module_path in _MODULE_CACHE:
        return _MODULE_CACHE[module_path]
    module = __import__(module_path, fromlist=[''])
    _MODULE_CACHE[module_path] = module
    return module


def main():
    parser = argparse.ArgumentParser(description='blog-article-skill 文章总结工具')
    parser.add_argument('url', nargs='?', help='文章链接或内容')
    parser.add_argument('--content', type=str, help='直接传入文章内容')
    parser.add_argument('--url', '-u', type=str, dest='url_arg', help='文章链接')
    parser.add_argument('--author', '-a', type=str, default='', help='作者信息')
    parser.add_argument('--tags', '-t', type=str, default='', help='标签，逗号分隔')
    parser.add_argument('--summarized', '-s', type=str, help='已总结好的内容（跳过AI总结，直接保存）')

    args = parser.parse_args()

    content = args.content or args.url_arg or args.url or ''

    if not content and not args.summarized:
        print("用法:")
        print("  python assets/run.py \"https://example.com/article\"")
        print("  python assets/run.py --content \"文章内容...\"")
        print("  python assets/run.py --url \"https://example.com\" --author \"作者\" --tags \"AI,技术\"")
        print("  python assets/run.py --summarized \"总结内容...\" --url \"...\" --author \"...\" --tags \"...\"")
        return 1

    tags = [t.strip() for t in args.tags.split(',')] if args.tags else []

    assets = _import_module('assets')
    skill_main = getattr(assets, 'skill_main')

    print("=" * 60)
    print("blog-article-skill 执行中...")
    print("=" * 60)

    # 场景1：直接传入已总结好的内容（跳过AI总结步骤）
    if args.summarized:
        print("\n📝 直接保存已总结好的内容...")
        input_data = {
            'summarized_content': args.summarized,
            'author': args.author,
            'tags': tags
        }
        if args.url_arg or args.url:
            input_data['original_url'] = args.url_arg or args.url
        result = skill_main(input_data)
        if result.get('success'):
            print("\n" + "=" * 60)
            print("✅ 文章总结保存完成")
            print(f"文件名: {result.get('filename', '未知')}")
            return 0
        print(f"\n❌ 保存失败: {result.get('message', '未知错误')}")
        return 1

    # 场景2：从文件读取已总结内容
    if os.path.isfile(content):
        print(f"\n📂 从文件读取已总结内容: {content}")
        save_func = _import_module('assets.main').save_summarized_from_file
        try:
            save_func(
                filepath=content,
                author=args.author,
                tags=tags
            )
            print("\n✅ 文章总结保存完成！")
            return 0
        except Exception as e:
            print(f"\n❌ 保存失败: {e}")
            return 1

    print("\n📥 步骤1：获取文章内容并处理")

    input_data = {
        'content': content,
        'author': args.author,
        'tags': tags
    }

    if content.startswith('http://') or content.startswith('https://'):
        input_data['url'] = content

    result = skill_main(input_data)

    if not result.get('success'):
        print(f"\n❌ 执行失败: {result.get('message', '未知错误')}")
        return 1

    if 'filename' in result:
        print("\n" + "=" * 60)
        print("✅ 文章总结与保存完成")
        print(f"标题: {result.get('original_title', '未知')}")
        print(f"文件名: {result.get('filename')}")
        return 0

    if result.get('need_continue_summary'):
        print("\n⚠️ 未配置外部 AI Provider，无法自动总结")
        print("\n📋 已成功抓取文章内容，原始内容已暂存至 notes/ 目录：")
        print("=" * 60)
        print(f"标题: {result.get('original_title', '未知')}")
        print(f"链接: {result.get('original_url', '未知')}")
        print(f"作者: {result.get('author', '未知')}")
        if result.get('tags'):
            print(f"标签: {', '.join(result['tags'])}")
        print("=" * 60)
        print("\n💡 外层对话可直接使用 Read 工具读取 notes/_raw_*.md 文件获取完整原文")
        print("\n💡 通用流程说明（适用于任何 AI 平台）：")
        print("""
    当前对话 AI 收到 need_continue_summary=True 后，请按以下步骤处理：

    1. 使用 Read 工具读取 notes/_raw_*.md 文件获取完整原文（避免终端截断）
    2. 从 result['prompt'] 获取 CONTENT_SUMMARY_PROMPT（结构化总结模板）
    3. 将 prompt 和原文拼接后，调用当前 AI 进行结构化总结
    4. 将总结内容写入临时文件，然后调用专用保存脚本：

       方式A - 写入文件 + _save_summary.py（推荐，无引号问题）：
          # 先将总结内容写入 notes/_summary.md（用 Write 工具）
          # 然后运行：
          python _save_summary.py notes/_summary.md --url "原文链接" --author "作者" --tags "AI,技术"

       方式B - 直接传入总结内容（简短内容可用）：
          python _save_summary.py --direct "总结内容..." --url "原文链接" --author "作者" --tags "AI,技术"

    注意：避免使用 assets/run.py --summarized 传长文本，PowerShell 内联字符串有引号嵌套问题。
    使用 _save_summary.py 配合文件路径是最稳的方式。
        """)
        return 0

    print("\n❌ 未知错误")
    return 1


if __name__ == "__main__":
    sys.exit(main())
