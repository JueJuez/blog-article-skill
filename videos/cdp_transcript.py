"""CDP 方式完整抓取 YouTube 字幕 + 元数据，返回结构化 dict。

依赖：本机 Chrome 已用 --remote-debugging-port=9222 --proxy-server=127.0.0.1:7890 启动（见 start_cdp_chrome 注释）。
页面网络走系统/显式代理，能上 YouTube；本脚本只通过 localhost CDP 注入 JS。
"""
import sys, json, time
sys.path.insert(0, "D:/Code/Skills/blog-article-skill/videos")
from cdp_helper import CDP, get_page_ws

VID = "05vPagqJ7nM"
PREF_LANGS = ["en", "en-US", "zh", "zh-CN"]  # 优先语言


def fetch(video_id, pref_langs=PREF_LANGS):
    cdp = CDP(get_page_ws())
    cdp.navigate(f"https://www.youtube.com/watch?v={video_id}")
    time.sleep(5)  # 等 SPA + 字幕轨就绪

    JS = r"""
    (async () => {
      const VID = new URLSearchParams(location.search).get('v');
      const ipr = window.ytInitialPlayerResponse || {};
      const key = window.ytcfg && window.ytcfg.get ? window.ytcfg.get('INNERTUBE_API_KEY') : null;
      const ctx = window.ytcfg && window.ytcfg.get ? window.ytcfg.get('INNERTUBE_CONTEXT') : null;
      let tracks = (ipr.captions && ipr.captions.playerCaptionsTracklistRenderer)
                   ? ipr.captions.playerCaptionsTracklistRenderer.captionTracks : [];
      if (!tracks.length && key && ctx) {
        const r = await fetch('https://www.youtube.com/youtubei/v1/player?key='+key, {
          method:'POST', headers:{'Content-Type':'application/json'},
          body: JSON.stringify({context: ctx, videoId: VID})
        });
        const j = await r.json();
        tracks = (j.captions && j.captions.playerCaptionsTracklistRenderer)
                 ? j.captions.playerCaptionsTracklistRenderer.captionTracks : [];
      }
      const langs = tracks.map(t => t.languageCode);
      const PREF = __PREF__;
      let chosen = null;
      for (const p of PREF) { chosen = tracks.find(t => t.languageCode === p); if (chosen) break; }
      if (!chosen) chosen = tracks[0];
      let text = null, usedLang = null;
      if (chosen) {
        const xml = await (await fetch(chosen.baseUrl)).text();
        const re = /<text[^>]*>([\s\S]*?)<\/text>/g; let m; const parts=[];
        const dec = {'&amp;':'&','&lt;':'<','&gt;':'>','&#39;':"'",'&quot;':'"'};
        while((m=re.exec(xml))) parts.push(m[1].replace(/&amp;|&lt;|&gt;|&#39;|&quot;/g, c=>dec[c]||c));
        text = parts.join(' ');
        usedLang = chosen.languageCode;
      }
      // 章节
      let chapters = [];
      try {
        const d = ipr.microformat?.microformatDataRenderer;
        const desc = ipr.videoDetails ? ipr.videoDetails.description : '';
        const re2 = /(?:(\d{1,2}):)?(\d{1,2}):(\d{2})\s+(.+)/g; let mm;
        while((mm=re2.exec(desc))) {
          const h = mm[1]?parseInt(mm[1]):0, mi=parseInt(mm[2]), s=parseInt(mm[3]);
          chapters.push({title: mm[4].trim(), start: h*3600+mi*60+s});
        }
      } catch(e){}
      const vd = ipr.videoDetails || {};
      return JSON.stringify({
        videoId: VID,
        title: vd.title || document.title,
        author: vd.author || '',
        description: vd.description || '',
        lang: usedLang, langs: langs, text: text, chapterCount: chapters.length,
        chapters: chapters
      });
    })()
    """.replace("__PREF__", json.dumps(pref_langs))

    raw = cdp.eval_js(JS, timeout_ms=30000)
    cdp.close()
    return json.loads(raw)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("video_id", nargs="?", default=VID)
    ap.add_argument("--json", action="store_true", help="只打印 JSON")
    args = ap.parse_args()
    data = fetch(args.video_id)
    if args.json:
        print(json.dumps(data, ensure_ascii=False))
    else:
        print("标题:", data["title"])
        print("作者:", data["author"])
        print("字幕语言:", data["lang"], "| 可用:", data["langs"])
        print("章节数:", data["chapterCount"])
        print("字幕长度(字符):", len(data["text"] or ""))
        print("字幕预览:", (data["text"] or "")[:300])
