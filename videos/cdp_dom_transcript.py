import sys, json, time
sys.path.insert(0, "D:/Code/Skills/blog-article-skill/videos")
from cdp_helper import CDP, get_page_ws

VID = "05vPagqJ7nM"
cdp = CDP(get_page_ws())
cdp.navigate(f"https://www.youtube.com/watch?v={VID}")
time.sleep(6)

JS = r"""
(async () => {
  const sleep = ms => new Promise(r=>setTimeout(r,ms));
  const log = [];
  // 视频动作的“更多操作”按钮（在 ytd-watch-metadata 内）
  const all = [...document.querySelectorAll('button')].filter(b =>
    (b.textContent||b.getAttribute('aria-label')||'').includes('更多操作'));
  const moreBtn = all.find(b => b.closest('ytd-watch-metadata')) || all[0];
  log.push('候选更多操作数: '+all.length+', 选用: '+(moreBtn? (moreBtn.getAttribute('aria-label')||moreBtn.textContent.trim()):'none'));
  if (moreBtn) moreBtn.click();
  await sleep(1200);
  // dump 菜单项
  const items = [...document.querySelectorAll('ytd-menu-service-item-renderer, tp-yt-paper-item, [role=menuitem], ytd-menu-navigation-item-renderer')]
    .map(i => (i.textContent||'').replace(/\s+/g,' ').trim()).filter(Boolean);
  log.push('菜单项: '+items.join(' | '));
  let clicked=null;
  for (const it of [...document.querySelectorAll('ytd-menu-service-item-renderer, tp-yt-paper-item, [role=menuitem], ytd-menu-navigation-item-renderer')]) {
    const t=(it.textContent||'').trim();
    if (/字幕|transcript|caption/i.test(t)){ it.click(); clicked=t; break; }
  }
  if(!clicked){
    const any=[...document.querySelectorAll('*')].find(e=>/字幕记录|打开字幕|显示字幕/i.test((e.textContent||'').trim())&&(e.textContent||'').trim().length<40);
    if(any){any.click();clicked=any.textContent.trim();}
  }
  log.push('clicked 字幕项: '+clicked);
  let seg=null;
  for(let i=0;i<30;i++){seg=document.querySelector('ytd-transcript-segment-renderer');if(seg)break;await sleep(500);}
  if(!seg) return JSON.stringify({ok:false,log,note:'no seg'});
  const segs=[...document.querySelectorAll('ytd-transcript-segment-renderer')];
  const parts=segs.map(s=>{const ts=s.querySelector('.segment-timestamp');const tx=s.querySelector('.segment-text');return {ts:ts?ts.textContent.trim():'',tx:tx?tx.textContent.trim():''};});
  return JSON.stringify({ok:true,count:parts.length,text:parts.map(p=>p.tx).join(' '),sample:parts.slice(0,3),log});
})()
"""
raw = cdp.eval_js(JS, timeout_ms=40000)
cdp.close()
data = json.loads(raw)
print(json.dumps(data, ensure_ascii=False, indent=2)[:1600])
