"""articles/html_export.py — 笔记 md → 自包含分享版 HTML（机械转换，零 AI）。

两条路径共用这一个渲染器：
- 路径 A（存量笔记）：`scripts/export_note_html.py "<md路径>"` CLI 导出；
- 路径 B（新笔记分享）：`save_summary_only` 传 `share_html=1`，落盘成功后自动导出。

产物：单个自包含 .html（CSS / Chart.js 全部内联，零外链资源），默认与笔记同目录同名。
转换规则（全部确定性代码）：
- Obsidian callout（`> [!warning]` / `[!quote]` / `[!question]` …）→ 样式块；
- `chart` 数据块（YAML/JSON，schema 与 Obsidian Charts 插件原生一致：type/title/labels + series）→ Chart.js 图；
  Chart.js 缺失（assets/chart.umd.min.js 未 vendored）时降级为数据表格，不失败；
- 笔记头部元数据行（#标签行 / **作者** / **来源链接** / **发布时间**）→ 页眉与来源区；
- `## 二级标题` 自动注入锚点并生成目录（TOC）。
转换失败向上抛异常由调用方兜底（绝不影响笔记落盘）。
"""

import html as _html
import json
import os
import re

_MD_EXTENSIONS = ["tables", "fenced_code"]

_CALLOUT_META = {
    "warning": ("⚠️", "warning"), "quote": ("❝", "quote"),
    "question": ("❓", "question"), "info": ("ℹ️", "info"),
    "note": ("📝", "note"), "tip": ("💡", "tip"), "example": ("🧩", "example"),
}

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Chart.js v4 dist 只发布未压缩 UMD（~200KB）；min 版是 CDN 现场产物、npmmirror 镜像无。
# 两个文件名都认，优先 min；缺失时 chart 块降级为数据表格（见 _render_chart）。
_CHARTJS_CANDIDATES = [
    os.path.join(_REPO_ROOT, "assets", "chart.umd.min.js"),
    os.path.join(_REPO_ROOT, "assets", "chart.umd.js"),
]


def _chartjs_file() -> str:
    for p in _CHARTJS_CANDIDATES:
        if os.path.isfile(p) and os.path.getsize(p) > 1000:
            return p
    return ""


def _md_convert(text: str) -> str:
    import markdown
    return markdown.markdown(text, extensions=_MD_EXTENSIONS)


def _chartjs_script() -> str:
    """内联 Chart.js；文件缺失返回空串（chart 块降级为表格）。"""
    path = _chartjs_file()
    if not path:
        return ""
    try:
        with open(path, encoding="utf-8") as f:
            return "<script>" + f.read() + "</script>"
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# 块级预切分：fence 感知，把 callout / chart 块从普通 md 里摘出来
# ---------------------------------------------------------------------------

def _split_blocks(text: str):
    """把 md 文本切成 (kind, payload) 段；kind ∈ md / callout / chart。

    code fence 内的 `>` 与 ```chart 一律不特殊处理（避免把提示词样例误转）。
    callout payload 是剥掉 `>` 的行列表；chart payload 是 JSON 文本。
    """
    lines = text.split("\n")
    segs = []
    i, n, in_fence = 0, len(lines), False
    while i < n:
        stripped = lines[i].strip()
        if stripped.startswith("```"):
            if in_fence:
                in_fence = False
                segs[-1][1].append(lines[i])
                i += 1
                continue
            kind = "chart" if stripped.startswith("```chart") else "md"
            if kind == "chart":
                j = i + 1
                buf = []
                while j < n and not lines[j].strip().startswith("```"):
                    buf.append(lines[j])
                    j += 1
                segs.append(["chart", "\n".join(buf)])
                i = j + 1
                continue
            in_fence = True
            segs.append(["md", [lines[i]]])
            i += 1
            continue
        if not in_fence and stripped.startswith(">"):
            j = i
            buf = []
            while j < n and lines[j].strip().startswith(">"):
                buf.append(lines[j].strip()[1:].lstrip())
                j += 1
            segs.append(["callout", buf])
            i = j
            continue
        if segs and segs[-1][0] == "md" and not (segs[-1][1] and
                                                 segs[-1][1][-1].strip().startswith("```")):
            segs[-1][1].append(lines[i])
        else:
            segs.append(["md", [lines[i]]])
        i += 1
    return [(k, "\n".join(v) if isinstance(v, list) else v) for k, v in segs]


# ---------------------------------------------------------------------------
# callout / chart 渲染
# ---------------------------------------------------------------------------

def _render_callout(buf_lines):
    """`> [!type] 标题` callout → 样式 div；普通引用块返回 None 交回 markdown。"""
    if not buf_lines:
        return None
    m = re.match(r"\[!(\w+)\](-?)\s*(.*)", buf_lines[0].strip())
    if not m:
        return None
    kind = m.group(1).lower()
    emoji, cls = _CALLOUT_META.get(kind, ("📝", "note"))
    title = m.group(3).strip() or kind
    content_md = "\n".join(buf_lines[1:]).strip()
    content_html = _md_convert(content_md) if content_md else ""
    return (f'<div class="callout {cls}"><div class="callout-title">'
            f'<span class="co-emoji">{emoji}</span>{_html.escape(title)}</div>'
            f'<div class="callout-body">{content_html}</div></div>')


def _parse_chart_yaml(text: str):
    """chart 块解析：优先 JSON（合法 YAML 子集），失败回退 PyYAML（插件式 YAML 写法）。

    Obsidian Charts 插件同样按 YAML 解析，两种写法它都吃；这里对齐该语义。
    """
    stripped = text.strip()
    try:
        return json.loads(stripped)
    except Exception:
        import yaml
        return yaml.safe_load(stripped)


def _render_chart(json_text: str):
    """```chart 数据块 → Chart.js 容器；格式非法返回 None 保留原代码块。

    schema 与 Obsidian Charts 插件原生一致：type / title / labels + series[{title,data}]。
    旧扁平 {labels,data} 单序列写法仍容忍解析（首个 series 无 title 时用 unit/空串兜底）。
    Chart.js 未 vendored 时降级为数据表格（自包含优先于图表效果）。
    """
    try:
        data = _parse_chart_yaml(json_text)
        ctype = data["type"]
        labels = [str(l) for l in data["labels"]]
        if ctype not in ("bar", "line", "pie") or not labels:
            raise ValueError("bad chart spec")
        raw_series = data.get("series")
        if isinstance(raw_series, list) and raw_series:
            datasets = []
            for s in raw_series:
                vals = [float(v) for v in s["data"]]
                if len(vals) != len(labels):
                    raise ValueError("labels/data length mismatch")
                datasets.append({"label": str(s.get("title", "")), "data": vals})
        else:
            vals = [float(v) for v in data["data"]]
            if len(vals) != len(labels):
                raise ValueError("labels/data length mismatch")
            datasets = [{"label": str(data.get("unit", "")), "data": vals}]
    except Exception:
        return None
    title = str(data.get("title", ""))
    if not _chartjs_file():
        if len(datasets) == 1:
            rows = "".join(
                f"<tr><td>{_html.escape(str(l))}</td><td>{v:g}</td></tr>"
                for l, v in zip(labels, datasets[0]["data"]))
            head = "<tr><th>项目</th><th>数值</th></tr>"
        else:
            head = "<tr><th>项目</th>" + "".join(
                f"<th>{_html.escape(d['label'])}</th>" for d in datasets) + "</tr>"
            rows = "".join(
                "<tr><td>{}</td>{}</tr>".format(
                    _html.escape(str(labels[i])),
                    "".join(f"<td>{d['data'][i]:g}</td>" for d in datasets))
                for i in range(len(labels)))
        cap = _html.escape(title)
        return (f'<figure class="chart-fallback"><figcaption>{cap}</figcaption>'
                f'<table><thead>{head}</thead>'
                f'<tbody>{rows}</tbody></table></figure>')
    payload = json.dumps({"type": ctype, "title": title,
                          "labels": labels, "datasets": datasets}, ensure_ascii=False)
    return (f'<figure class="chart-block" data-chart=\'{_html.escape(payload, quote=True)}\'>'
            f'<canvas></canvas></figure>')


# ---------------------------------------------------------------------------
# 笔记头部元数据（format_note_with_prompt 权威格式的镜像解析）
# ---------------------------------------------------------------------------

def _split_metadata(text: str):
    """从笔记头部剥出（tags, author, source_url, publish_time, body）。

    识别两类权威行：井号标签行（首个非空行）与 `**作者** / **来源链接** /
    **发布时间**` 元数据行；不匹配的行即视为正文起点。
    """
    tags, author, source_url, publish_time = [], "", "", ""
    lines = text.split("\n")
    i, n = 0, len(lines)
    while i < n:
        s = lines[i].strip()
        if not s:
            i += 1
            continue
        if s.startswith("#") and not s.startswith("# ") and not tags:
            tags = [t for t in re.split(r"[#\s]+", s) if t]
            i += 1
            continue
        if s.startswith("**"):
            m = re.search(r"\*\*作者\*\*[：:]\s*([^|*]+)", s)
            if m:
                author = m.group(1).strip()
            m = re.search(r"\*\*来源链接\*\*[：:].*?\((https?://[^)]+)\)", s)
            if m:
                source_url = m.group(1).strip()
            m = re.search(r"\*\*发布时间\*\*[：:]\s*(.+)$", s)
            if m:
                publish_time = m.group(1).strip()
            i += 1
            continue
        break
    return tags, author, source_url, publish_time, "\n".join(lines[i:])


def _title_from_path(md_path: str) -> str:
    stem = os.path.splitext(os.path.basename(md_path))[0]
    stem = re.sub(r"^\d{8}_", "", stem)
    return stem


# ---------------------------------------------------------------------------
# 目录锚点
# ---------------------------------------------------------------------------

def _anchor_h2(body_html: str):
    """给 <h2> 注入 id 并返回 (注入后的 html, 目录条目 [(id, text)])。"""
    items = []

    def _sub(m):
        idx = len(items)
        inner = m.group(1)
        text = re.sub(r"<[^>]+>", "", inner)
        items.append((f"sec-{idx}", text))
        return f'<h2 id="sec-{idx}">{inner}</h2>'

    new_html = re.sub(r"<h2>(.*?)</h2>", _sub, body_html, flags=re.DOTALL)
    return new_html, items


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

_CSS = """
:root { --fg:#1f2328; --muted:#656d76; --bg:#ffffff; --panel:#f6f8fa;
        --border:#d8dee4; --accent:#0969da; --warn-bg:#fff8c5; --warn-bd:#d4a72c;
        --quote-bg:#f6f8fa; --ques-bg:#ddf4ff; }
* { box-sizing: border-box; }
body { margin:0; padding:0; background:var(--bg); color:var(--fg);
       font:16px/1.75 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif; }
.wrap { max-width: 860px; margin:0 auto; padding:48px 24px 96px; }
header.note-head { border-bottom:1px solid var(--border); padding-bottom:20px; margin-bottom:28px; }
h1.note-title { font-size:26px; line-height:1.35; margin:0 0 12px; }
.meta { color:var(--muted); font-size:14px; display:flex; flex-wrap:wrap; gap:8px 18px; }
.meta a { color:var(--accent); text-decoration:none; }
.tagrow { margin-top:10px; }
.tag { display:inline-block; background:var(--panel); border:1px solid var(--border);
       border-radius:12px; padding:1px 10px; font-size:12px; color:var(--muted); margin:2px 4px 2px 0; }
nav.toc { background:var(--panel); border:1px solid var(--border); border-radius:8px;
          padding:14px 20px; margin-bottom:28px; font-size:14px; }
nav.toc ol { margin:6px 0 0; padding-left:20px; }
nav.toc a { color:var(--accent); text-decoration:none; }
h2 { font-size:21px; margin:40px 0 14px; padding-bottom:6px; border-bottom:1px solid var(--border); }
h3 { font-size:17px; margin:26px 0 10px; }
p { margin:10px 0; }
table { border-collapse:collapse; width:100%; margin:14px 0; font-size:14.5px; }
th,td { border:1px solid var(--border); padding:7px 12px; text-align:left; vertical-align:top; }
th { background:var(--panel); }
blockquote { margin:14px 0; padding:10px 18px; border-left:4px solid var(--border);
             background:var(--quote-bg); color:var(--muted); }
.callout { border:1px solid var(--border); border-left-width:4px; border-radius:8px;
           padding:12px 18px; margin:16px 0; }
.callout-title { font-weight:600; margin-bottom:6px; }
.co-emoji { margin-right:6px; }
.callout.warning { background:var(--warn-bg); border-color:var(--warn-bd); }
.callout.question { background:var(--ques-bg); border-color:var(--accent); }
.callout.quote { background:var(--quote-bg); }
.callout-body p { margin:6px 0; }
pre { background:#0d1117; color:#e6edf3; padding:14px 16px; border-radius:8px;
      overflow-x:auto; font-size:13.5px; line-height:1.6; }
code { background:var(--panel); border-radius:4px; padding:1px 5px; font-size:0.9em; }
pre code { background:none; padding:0; }
figure.chart-block, figure.chart-fallback { margin:22px 0; padding:16px;
    border:1px solid var(--border); border-radius:8px; background:var(--panel); }
figure.chart-fallback figcaption { font-weight:600; margin-bottom:8px; }
footer.note-foot { margin-top:64px; padding-top:14px; border-top:1px solid var(--border);
                   color:var(--muted); font-size:12.5px; }
"""

_CHART_INIT_JS = """
(function(){
  if (typeof Chart === 'undefined') return;
  var PALETTE = ['rgba(9,105,218,0.55)', 'rgba(234,120,0,0.55)', 'rgba(26,127,55,0.55)'];
  document.querySelectorAll('.chart-block').forEach(function(fig){
    var spec = JSON.parse(fig.getAttribute('data-chart'));
    var ds = spec.datasets.map(function(d, i){
      return { label: d.label, data: d.data,
               backgroundColor: spec.type === 'pie' ? undefined : PALETTE[i % PALETTE.length] };
    });
    new Chart(fig.querySelector('canvas'), {
      type: spec.type,
      data: { labels: spec.labels, datasets: ds },
      options: { responsive: true, maintainAspectRatio: false,
                 plugins: { title: { display: !!spec.title, text: spec.title },
                            legend: { display: spec.type === 'pie' || ds.length > 1 } } }
    });
  });
})();
"""


def render_note_html(md_path: str, out_path: str = None) -> str:
    """把一篇笔记 md 渲染为自包含 HTML，返回输出路径。

    默认输出到笔记同目录同名 .html；out_path 可覆盖。
    """
    with open(md_path, encoding="utf-8") as f:
        text = f.read()
    tags, author, source_url, publish_time, body = _split_metadata(text)
    title = _title_from_path(md_path)

    parts = []
    for kind, payload in _split_blocks(body):
        if kind == "callout":
            rendered = _render_callout(payload.split("\n"))
            if rendered is not None:
                parts.append(rendered)
            else:
                # 普通引用块：还原被剥掉的 `>` 前缀再交 markdown，保住引用格式
                parts.append(_md_convert("\n".join(
                    "> " + ln if ln.strip() else ">" for ln in payload.split("\n"))))
        elif kind == "chart":
            rendered = _render_chart(payload)
            parts.append(rendered if rendered is not None
                         else _md_convert("```chart\n" + payload + "\n```"))
        else:
            parts.append(_md_convert(payload))
    body_html, toc_items = _anchor_h2("\n".join(parts))

    tag_html = "".join(f'<span class="tag">{_html.escape(t)}</span>' for t in tags) \
        if tags else ""
    meta_bits = []
    if author:
        meta_bits.append(f"<span>✍️ {_html.escape(author)}</span>")
    if publish_time:
        meta_bits.append(f"<span>📅 {_html.escape(publish_time)}</span>")
    if source_url:
        meta_bits.append(f'<span>🔗 <a href="{_html.escape(source_url)}">原文链接</a></span>')
    meta_html = "".join(meta_bits)
    toc_html = ""
    if toc_items:
        lis = "".join(f'<li><a href="#{i}">{_html.escape(t)}</a></li>'
                      for i, t in toc_items)
        toc_html = f'<nav class="toc">📑 目录<ol>{lis}</ol></nav>'

    page = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_html.escape(title)}</title>
<style>{_CSS}</style>
</head>
<body>
<div class="wrap">
<header class="note-head">
  <h1 class="note-title">{_html.escape(title)}</h1>
  <div class="meta">{meta_html}</div>
  <div class="tagrow">{tag_html}</div>
</header>
{toc_html}
<main>
{body_html}
</main>
<footer class="note-foot">由 blog-article-skill 自动生成的分享版 · 生成自本地笔记归档</footer>
</div>
{_chartjs_script()}
<script>{_CHART_INIT_JS}</script>
</body>
</html>"""

    if out_path is None:
        out_path = os.path.splitext(md_path)[0] + ".html"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(page)
    return out_path


# ---------------------------------------------------------------------------
# 自包含校验（资源零外链；正文里的 <a href> 跳转链接不算资源）
# ---------------------------------------------------------------------------

_RESOURCE_RE = re.compile(
    r"<(?:script|img|link|iframe|source|embed)[^>]+?(?:src|href)\s*=\s*[\"'](https?://[^\"']+)",
    re.IGNORECASE)


def self_contained_violations(html_text: str) -> list:
    """列出引用外部资源的标签（离线打不开的隐患）；正文超链接不算。"""
    return [m for m in _RESOURCE_RE.findall(html_text)]


def export_note_html(md_path: str, out_path: str = None) -> str:
    """带自包含告警的导出入口（落盘钩子与 CLI 共用）。"""
    out = render_note_html(md_path, out_path)
    with open(out, encoding="utf-8") as f:
        bad = self_contained_violations(f.read())
    if bad:
        print(f"   ⚠️ HTML 含外部资源引用（离线可能打不开）：{bad[:3]}")
    return out
