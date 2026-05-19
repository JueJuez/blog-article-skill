"""
外层对话保存总结内容的专用入口脚本

解决 PowerShell 内联字符串嵌套引号问题。
标准流程：外层对话先将总结内容写入临时文件，
然后调用本脚本读取并保存到所有配置目标。

使用方法：
    python _save_summary.py <临时文件路径> --url "原文链接" --author "作者" --tags "AI,技术"

示例：
    # 方式1：从文件读取并保存（推荐，无引号问题）
    python _save_summary.py notes/_summary.md --url "https://..." --author "哥飞" --tags "独立开发者,SEO"

    # 方式2：直接传入字符串（简短内容可用）
    python _save_summary.py --direct "总结内容..." --url "https://..." --author "哥飞" --tags "独立开发者,SEO"

注意事项：
    - 文件路径必须存在且内容非空
    - 标签用逗号分隔
    - 所有参数均为可选，但建议尽量提供完整的元数据以获得更好的文件名
"""
import sys
import os
import argparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)


def main():
    parser = argparse.ArgumentParser(description='保存已总结的文章内容到所有配置目标')
    parser.add_argument('filepath', nargs='?', help='包含总结内容的文件路径')
    parser.add_argument('--direct', '-d', type=str, default='', help='直接传入总结内容字符串（简短内容使用）')
    parser.add_argument('--url', '-u', type=str, default='', help='原文链接')
    parser.add_argument('--author', '-a', type=str, default='', help='作者信息')
    parser.add_argument('--tags', '-t', type=str, default='', help='标签，逗号分隔')
    parser.add_argument('--title', type=str, default='', help='原始文章标题（可选，用于文件名）')

    args = parser.parse_args()

    # 获取总结内容
    summarized_content = ""
    if args.direct:
        summarized_content = args.direct
    elif args.filepath:
        if not os.path.isfile(args.filepath):
            print(f"❌ 文件不存在: {args.filepath}")
            return 1
        with open(args.filepath, 'r', encoding='utf-8') as f:
            summarized_content = f.read()
    else:
        print("❌ 请提供总结内容（通过文件路径或 --direct 参数）")
        print("用法:")
        print(f"  python {os.path.basename(__file__)} <文件路径> --url ... --author ... --tags ...")
        print(f"  python {os.path.basename(__file__)} --direct \"总结内容...\" --url ... --author ... --tags ...")
        return 1

    if not summarized_content.strip():
        print("❌ 总结内容为空")
        return 1

    tags = [t.strip() for t in args.tags.split(',')] if args.tags else []

    from assets.main import save_summarized_article

    try:
        formatted_note, filename = save_summarized_article(
            summarized_content=summarized_content,
            original_url=args.url,
            author=args.author,
            tags=tags,
            original_title=args.title
        )
        print(f"\n✅ 文章总结保存完成！")
        print(f"   文件名: {filename}")
        return 0
    except Exception as e:
        print(f"\n❌ 保存失败: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())