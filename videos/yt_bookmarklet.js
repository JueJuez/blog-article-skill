/*
 * YouTube 字幕抓取书签（yt_subtitle_grabber）v6
 *
 * 用法：
 *   1. （可选）启动本地桥：用 venv 的 python 跑
 *        C:/Users/O1830/.workbuddy/binaries/python/envs/default/Scripts/python.exe -m videos.yt_bridge
 *      桥没开也行——字幕会自动复制到剪贴板兜底。
 *   2. 打开 videos/yt_bookmark_install.html，把蓝色链接拖到书签栏（或复制文本框内容手动建书签）。
 *   3. 在【你自己的浏览器】打开任意 YouTube 视频页，点一下该书签。
 *   4. 字幕自动 POST 到本机 8899 → 生成笔记存 Obsidian；桥没开则字幕进剪贴板。
 *
 * 为什么在浏览器里点：本环境 Python 因 Clash 代理只认浏览器扩展客户端，无法直连 YouTube。
 *
 * v6 关键修复（相对 v5）：
 *   - 根因：v2/v5 用 ytInitialPlayerResponse 里的 baseUrl 直接抓，YouTube 2024+ 常返回
 *     空响应（HTTP200 len0），随后 JSON.parse("") → "Unexpected end of JSON input"。
 *   - 新增策略 A：用页面 ytcfg 调 innertube /youtubei/v1/player 重新拉一份【新鲜】字幕轨，
 *     其 baseUrl 更可能有效；策略 B 保留页面自带轨道兜底。
 *   - 每条轨道按 json3 → srv3 → srv1 多格式回退，空响应自动跳下一格式/下一轨道。
 *   - 桥（8899）未启动时，把字幕写入剪贴板兜底，绝不空手而归。
 *   - 全程收集 diag 诊断，失败时 alert 明确原因。
 */

// ===== 一行版：复制下面两个标记之间那一整行到书签 URL 栏（安装页会自动嵌入）=====
//BM_START
javascript:(async()=>{try{const P=window.ytInitialPlayerResponse;if(!P||!P.videoDetails){alert('未找到播放数据，请在视频播放页等页面加载完再点');return;}const vid=P.videoDetails.videoId,title=P.videoDetails.title,desc=P.videoDetails.shortDescription||'';const chapters=((P.microformat&&P.microformat.playerMicroformatRenderer&&P.microformat.playerMicroformatRenderer.chapters)||[]).map(c=>({title:(c.title&&c.title.simpleText)||'',start:c.startTimeSeconds||0}));const order=['zh-Hans','zh-Hant','zh-CN','zh','en-US','en'];const diag=[];const gt=pr=>(pr&&pr.captions&&pr.captions.playerCaptionsTracklistRenderer&&pr.captions.playerCaptionsTracklistRenderer.captionTracks)||[];let sets=[{src:'page',tracks:gt(P)}];try{const key=window.ytcfg&&ytcfg.get&&ytcfg.get('INNERTUBE_API_KEY');const ctx=window.ytcfg&&ytcfg.get&&ytcfg.get('INNERTUBE_CONTEXT');if(key&&ctx){const r=await fetch('/youtubei/v1/player?key='+key,{method:'POST',credentials:'include',headers:{'Content-Type':'application/json'},body:JSON.stringify({context:ctx,videoId:vid})});if(r.ok){sets.push({src:'innertube',tracks:gt(await r.json())});}else{diag.push('player http '+r.status);}}else{diag.push('no ytcfg');}}catch(e){diag.push('player err '+e.message);}const pick=ts=>{let p=null;for(const l of order){p=ts.find(t=>t.languageCode===l);if(p)break;}return p||ts[0]||null;};const mk=(b,f)=>b+(b.includes('?')?'&':'?')+'fmt='+f;const ft=async b=>{for(const f of ['json3','srv3','srv1']){try{const r=await fetch(mk(b,f),{credentials:'include'});if(!r.ok){diag.push(f+' http '+r.status);continue;}const t=await r.text();if(!t){diag.push(f+' empty');continue;}let o='';if(f==='json3'){const j=JSON.parse(t);o=(j.events||[]).map(e=>(e.segs||[]).map(s=>s.utf8||'').join('')).join(' ');}else{const x=new DOMParser().parseFromString(t,'text/xml');o=Array.from(x.getElementsByTagName('text')).map(n=>n.textContent).join(' ');}o=o.replace(/\s+/g,' ').trim();if(o)return o;diag.push(f+' parsed-empty');}catch(e){diag.push(f+' err '+e.message);}}return '';};let text='',lang='';for(const s of sets){if(!s.tracks.length){diag.push(s.src+' no-tracks');continue;}const tk=pick(s.tracks);if(!tk){diag.push(s.src+' no-pick');continue;}const t=await ft(tk.baseUrl);if(t){text=t;lang=tk.languageCode;diag.push('OK '+s.src+'/'+lang);break;}}if(!text){alert('未取到字幕（该视频可能无CC字幕，或需登录）:\n'+diag.join('\n'));return;}try{const res=await fetch('http://127.0.0.1:8899/submit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url:location.href,video_id:vid,title,description:desc,chapters,lang,text})});const jr=await res.json().catch(()=>({}));alert((res.ok&&jr.ok?'字幕已存入笔记!':'取到字幕但本地服务异常')+'\n语言:'+lang+' 字数:'+text.length+'\n'+(jr.note_paths?'存:'+(jr.note_paths[0]||''):(jr.error||'')));}catch(e){try{await navigator.clipboard.writeText(text);alert('本地服务(8899)未启动，字幕已复制到剪贴板('+text.length+'字/'+lang+')。\n开服务后重点，或把剪贴板内容直接粘给助手。');}catch(_){alert('本地服务未开且剪贴板不可用。已取到字幕，长度'+text.length+'。诊断:\n'+diag.join('\n'));}}}catch(e){alert('脚本异常:'+e.message);}})();
//BM_END

// ===== 可读版（仅供维护参考，实际以上面一行版为准）=====
/*
javascript:(async () => {
  try {
    const P = window.ytInitialPlayerResponse;
    if (!P || !P.videoDetails) { alert('未找到播放数据，请在视频播放页等页面加载完再点'); return; }
    const vid = P.videoDetails.videoId;
    const title = P.videoDetails.title;
    const desc = P.videoDetails.shortDescription || '';
    const chapters = ((P.microformat && P.microformat.playerMicroformatRenderer &&
      P.microformat.playerMicroformatRenderer.chapters) || [])
      .map(c => ({ title: (c.title && c.title.simpleText) || '', start: c.startTimeSeconds || 0 }));

    const order = ['zh-Hans', 'zh-Hant', 'zh-CN', 'zh', 'en-US', 'en'];
    const diag = [];
    const gt = pr => (pr && pr.captions && pr.captions.playerCaptionsTracklistRenderer &&
      pr.captions.playerCaptionsTracklistRenderer.captionTracks) || [];

    // 候选轨道来源：① 页面自带 ② innertube 重新拉一份新鲜的
    let sets = [{ src: 'page', tracks: gt(P) }];
    try {
      const key = window.ytcfg && ytcfg.get && ytcfg.get('INNERTUBE_API_KEY');
      const ctx = window.ytcfg && ytcfg.get && ytcfg.get('INNERTUBE_CONTEXT');
      if (key && ctx) {
        const r = await fetch('/youtubei/v1/player?key=' + key, {
          method: 'POST', credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ context: ctx, videoId: vid })
        });
        if (r.ok) { sets.push({ src: 'innertube', tracks: gt(await r.json()) }); }
        else { diag.push('player http ' + r.status); }
      } else { diag.push('no ytcfg'); }
    } catch (e) { diag.push('player err ' + e.message); }

    const pick = ts => { let p = null; for (const l of order) { p = ts.find(t => t.languageCode === l); if (p) break; } return p || ts[0] || null; };
    const mk = (b, f) => b + (b.includes('?') ? '&' : '?') + 'fmt=' + f;

    const ft = async (b) => {
      for (const f of ['json3', 'srv3', 'srv1']) {
        try {
          const r = await fetch(mk(b, f), { credentials: 'include' });
          if (!r.ok) { diag.push(f + ' http ' + r.status); continue; }
          const t = await r.text();
          if (!t) { diag.push(f + ' empty'); continue; }
          let o = '';
          if (f === 'json3') {
            const j = JSON.parse(t);
            o = (j.events || []).map(e => (e.segs || []).map(s => s.utf8 || '').join('')).join(' ');
          } else {
            const x = new DOMParser().parseFromString(t, 'text/xml');
            o = Array.from(x.getElementsByTagName('text')).map(n => n.textContent).join(' ');
          }
          o = o.replace(/\s+/g, ' ').trim();
          if (o) return o;
          diag.push(f + ' parsed-empty');
        } catch (e) { diag.push(f + ' err ' + e.message); }
      }
      return '';
    };

    let text = '', lang = '';
    for (const s of sets) {
      if (!s.tracks.length) { diag.push(s.src + ' no-tracks'); continue; }
      const tk = pick(s.tracks);
      if (!tk) { diag.push(s.src + ' no-pick'); continue; }
      const t = await ft(tk.baseUrl);
      if (t) { text = t; lang = tk.languageCode; diag.push('OK ' + s.src + '/' + lang); break; }
    }
    if (!text) { alert('未取到字幕（该视频可能无CC字幕，或需登录）:\n' + diag.join('\n')); return; }

    // 优先发本地桥；桥没开则复制到剪贴板兜底
    try {
      const res = await fetch('http://127.0.0.1:8899/submit', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: location.href, video_id: vid, title, description: desc, chapters, lang, text })
      });
      const jr = await res.json().catch(() => ({}));
      alert((res.ok && jr.ok ? '字幕已存入笔记!' : '取到字幕但本地服务异常') +
        '\n语言:' + lang + ' 字数:' + text.length + '\n' +
        (jr.note_paths ? '存:' + (jr.note_paths[0] || '') : (jr.error || '')));
    } catch (e) {
      try {
        await navigator.clipboard.writeText(text);
        alert('本地服务(8899)未启动，字幕已复制到剪贴板(' + text.length + '字/' + lang + ')。\n开服务后重点，或把剪贴板内容直接粘给助手。');
      } catch (_) {
        alert('本地服务未开且剪贴板不可用。已取到字幕，长度' + text.length + '。诊断:\n' + diag.join('\n'));
      }
    }
  } catch (e) { alert('脚本异常:' + e.message); }
})();
*/
