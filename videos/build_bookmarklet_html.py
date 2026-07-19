"""videos/build_bookmarklet_html.py — 由 yt_bookmarklet.js 生成安装页 yt_bookmark_install.html

安装页历史上多次踩到转义坑（\\n+ 被 HTML/JS 双重解析、json.dumps 二次转义等），
本脚本用两条原则一次性根治：
  1. 书签码从 yt_bookmarklet.js 的 //BM_START ~ //BM_END 之间【精确提取】，
     保证安装页嵌入与源码逐字一致（不再手抄行号）。
  2. 嵌入 HTML 时统一用 html.escape(code, quote=True)，同时处理 & < > " ——
     浏览器解析 href/textarea 时会自动还原，得到与源完全一致的 JS。
  3. 除可拖拽链接外，另提供只读文本框，用户可全选复制手动建书签（终极兜底）。

运行：python videos/build_bookmarklet_html.py
"""
import html
import os

HERE = os.path.dirname(os.path.abspath(__file__))
JS = os.path.join(HERE, "yt_bookmarklet.js")
OUT = os.path.join(HERE, "yt_bookmark_install.html")


def extract_bookmarklet() -> str:
    with open(JS, encoding="utf-8") as f:
        src = f.read()
    start = src.index("//BM_START") + len("//BM_START")
    end = src.index("//BM_END")
    code = src[start:end].strip()
    if not code.startswith("javascript:"):
        raise SystemExit("提取失败：未找到 javascript: 开头的一行版")
    if "\n" in code:
        raise SystemExit("提取失败：书签码必须是单行")
    return code


def build_html(code: str) -> str:
    esc = html.escape(code, quote=True)  # & < > " 全部转义
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>安装「抓YT字幕」书签</title>
<style>
  body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
       max-width:760px;margin:40px auto;padding:0 20px;color:#1a1a1a;line-height:1.7;background:#fff;}}
  h1{{font-size:22px;}}
  .bm{{display:inline-block;padding:10px 18px;background:#2563eb;color:#fff !important;
      border-radius:8px;text-decoration:none;font-weight:600;font-size:15px;
      box-shadow:0 2px 6px rgba(37,99,235,.3);}}
  .drag{{background:#eef2ff;border:1px dashed #93c5fd;border-radius:10px;padding:20px;text-align:center;margin:16px 0;}}
  ol{{padding-left:22px;}} li{{margin:8px 0;}}
  code{{background:#f3f4f6;padding:2px 6px;border-radius:4px;font-size:13px;}}
  textarea{{width:100%;height:120px;font-family:Consolas,Monaco,monospace;font-size:12px;
           border:1px solid #d1d5db;border-radius:8px;padding:10px;box-sizing:border-box;color:#111;background:#fafafa;}}
  .tip{{color:#6b7280;font-size:13px;}}
  .step{{background:#f9fafb;border-radius:10px;padding:16px 20px;margin:14px 0;border:1px solid #eee;}}
  .warn{{background:#fffbeb;border:1px solid #fde68a;border-radius:8px;padding:12px 16px;font-size:14px;}}
</style>
</head>
<body>
  <h1>「抓 YT 字幕」书签安装</h1>

  <div class="drag">
    <p>① 把下面这个按钮 <b>拖到浏览器书签栏</b>：</p>
    <a class="bm" href="{esc}">📥 抓YT字幕</a>
    <p class="tip">拖不动？用下面的文本框手动建书签。</p>
  </div>

  <div class="step">
    <b>手动安装（更稳）：</b>
    <ol>
      <li>浏览器书签栏右键 → 「添加书签 / 添加网页」</li>
      <li>名称随便填（如 <code>抓YT字幕</code>）</li>
      <li>网址 URL 粘贴下面文本框里的<b>整段内容</b>：</li>
    </ol>
    <textarea readonly onclick="this.select()">{esc}</textarea>
    <p class="tip">点一下文本框会自动全选，然后 Ctrl+C 复制。</p>
  </div>

  <div class="step">
    <b>使用：</b>
    <ol>
      <li>（可选）先启动本地桥，命令：<br>
        <code>C:/Users/O1830/.workbuddy/binaries/python/envs/default/Scripts/python.exe -m videos.yt_bridge</code><br>
        <span class="tip">桥没开也行——字幕会自动复制到剪贴板兜底。</span></li>
      <li>在你自己的浏览器打开任意 YouTube 视频页（能正常播放的那个）</li>
      <li>点一下书签栏的「抓YT字幕」</li>
      <li>成功后弹窗提示：字幕存入笔记 / 或已复制到剪贴板</li>
    </ol>
  </div>

  <div class="warn">
    <b>说明：</b>必须在你自己的浏览器里点（它带扩展代理能上 YouTube）。本助手所在环境因 Clash 代理只认浏览器扩展客户端，无法直连 YouTube，所以由浏览器抓、发回本机处理。
  </div>
</body>
</html>
"""


if __name__ == "__main__":
    code = extract_bookmarklet()
    html_out = build_html(code)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(html_out)
    print(f"OK 已生成 {OUT}")
    print(f"   书签码长度 {len(code)} 字符")
