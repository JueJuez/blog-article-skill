"""
blog-article-skill 命令行入口脚本

提供稳定的命令行接口，所有外部调用都通过此脚本执行。
避免 PowerShell 多行字符串传递问题。

使用方法：
    python articles/run.py "https://example.com/article"
    python articles/run.py --content "文章内容..."
    python articles/run.py --url "https://example.com" --author "作者" --tags "AI,技术"
    python articles/run.py --note-type key_points "https://example.com/lecture"
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


def run_batch(args):
    """A3：批量目录模式。对该目录下所有 .md/.txt 原文逐篇总结。"""
    import glob
    tags = [t.strip() for t in args.tags.split(',')] if args.tags else []
    skill_main = _import_module('articles').skill_main
    batch_dir = args.batch
    if not os.path.isdir(batch_dir):
        print(f"❌ 批量目录不存在: {batch_dir}")
        return 1

    files = sorted(glob.glob(os.path.join(batch_dir, "**", "*.md"), recursive=True) +
                   glob.glob(os.path.join(batch_dir, "**", "*.txt"), recursive=True))
    # 过滤掉 notes/ 下的成品与 _raw_ 暂存，避免自我循环
    files = [f for f in files if "_raw_" not in os.path.basename(f)]
    if not files:
        print(f"⚠️ 目录 {batch_dir} 下未找到 .md/.txt 原文")
        return 0

    print("=" * 60)
    print(f"📚 批量总结模式：扫描到 {len(files)} 个文件")
    print("=" * 60)

    ok, skip, fail = 0, 0, 0
    for f in files:
        print(f"\n{'─'*60}\n📄 处理: {os.path.basename(f)}")
        try:
            with open(f, "r", encoding="utf-8") as fh:
                content = fh.read()
        except Exception as e:
            print(f"   ❌ 读取失败: {e}")
            fail += 1
            continue
        if not content.strip():
            print("   ⏭️ 空文件，跳过")
            skip += 1
            continue

        result = skill_main({
            'content': content,
            'author': args.author,
            'tags': tags,
            'note_type': args.note_type,
            'force': args.force,
        })
        if result.get('skipped'):
            print(f"   ⏭️ 已存在，跳过: {result.get('filename')}")
            skip += 1
        elif result.get('success') and result.get('filename'):
            print(f"   ✅ 完成: {result.get('filename')}")
            ok += 1
        elif result.get('need_continue_summary'):
            print(f"   ⚠️ 已抓取原文，等待外层 AI 总结（降级的文件: notes/_raw_*）")
            ok += 1  # 抓取成功，总结交由外层
        else:
            print(f"   ❌ 失败: {result.get('message', '未知错误')}")
            fail += 1

    print("\n" + "=" * 60)
    print(f"🏁 批量总结完成：成功 {ok} / 跳过 {skip} / 失败 {fail}")
    print("=" * 60)
    return 0


def main():
    parser = argparse.ArgumentParser(description='blog-article-skill 文章总结工具')
    parser.add_argument('url', nargs='?', help='文章链接或内容')
    parser.add_argument('--content', type=str, help='直接传入文章内容')
    parser.add_argument('--url', '-u', type=str, dest='url_arg', help='文章链接')
    parser.add_argument('--author', '-a', type=str, default='', help='作者信息')
    parser.add_argument('--tags', '-t', type=str, default='', help='标签，逗号分隔')
    parser.add_argument('--summarized', '-s', type=str, help='已总结好的内容（跳过AI总结，直接保存）')
    parser.add_argument('--note-type', '-n', type=str, default='', help='笔记类型：structured / key_points（留空自动分类）')
    parser.add_argument('--batch', '-b', type=str, default='', help='批量目录模式：对该目录下所有 .md/.txt 原文逐篇总结')
    parser.add_argument('--force', action='store_true', help='A2 去重：强制重新总结已处理过的内容')

    args = parser.parse_args()

    # A3：批量目录模式
    if args.batch:
        return run_batch(args)

    content = args.content or args.url_arg or args.url or ''

    if not content and not args.summarized:
        print("用法:")
        print("  python articles/run.py \"https://example.com/article\"")
        print("  python articles/run.py --content \"文章内容...\"")
        print("  python articles/run.py --url \"https://example.com\" --author \"作者\" --tags \"AI,技术\"")
        print("  python articles/run.py --summarized \"总结内容...\" --url \"...\" --author \"...\" --tags \"...\"")
        return 1

    tags = [t.strip() for t in args.tags.split(',')] if args.tags else []

    articles = _import_module('articles')
    skill_main = getattr(articles, 'skill_main')

    print("=" * 60)
    print("blog-article-skill 执行中...")
    print("=" * 60)

    # 场景1：直接传入已总结好的内容（跳过AI总结步骤）
    if args.summarized:
        print("\n📝 直接保存已总结好的内容...")
        input_data = {
            'summarized_content': args.summarized,
            'author': args.author,
            'tags': tags,
            'note_type': args.note_type,
            'force': args.force,
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
        save_func = _import_module('articles.main').save_summarized_from_file
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
        'tags': tags,
        'note_type': args.note_type,
        'force': args.force,
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
    2. 从 result['prompt'] 获取对应的笔记模板（note_type 见 result['note_type']）
    3. 将 prompt 和原文拼接后，调用当前 AI 进行总结
    4. 将总结内容写入临时文件，然后调用专用保存脚本：

       方式A - 写入文件 + articles/_save_summary.py（推荐，无引号问题）：
           # 先将总结内容写入 notes/_summary.md（用 Write 工具）
           # 然后运行：
           python articles/_save_summary.py notes/_summary.md --url "原文链接" --author "作者" --tags "AI,技术"

       方式B - 直接传入总结内容（简短内容可用）：
           python articles/_save_summary.py --direct "总结内容..." --url "原文链接" --author "作者" --tags "AI,技术"

    注意：避免使用 articles/run.py --summarized 传长文本，PowerShell 内联字符串有引号嵌套问题。
    使用 articles/_save_summary.py 配合文件路径是最稳的方式。
        """)
        return 0

    print("\n❌ 未知错误")
    return 1


if __name__ == "__main__":
    sys.exit(main())
