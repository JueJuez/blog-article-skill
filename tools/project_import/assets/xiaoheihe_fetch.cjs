// 小黑盒(xiaoheihe.cn) 帖子正文抓取器。
// 小黑盒分享链接是 App 深链，通用爬虫只能拿到占位文本；这里用已安装的
// Node Playwright + 系统 Chrome 无头打开链接，等 JS 渲染完成后取 body.innerText，
// 由 Python 侧 content_source.fetch_xiaoheihe 读取 stdout。
//
// 用法: node xiaoheihe_fetch.cjs <url> [waitMs=4000]
//   require('playwright') 优先走 NODE_PATH；找不到则回退到本机 managed workspace 绝对路径。
'use strict';
let chromium;
try {
  chromium = require('playwright').chromium;
} catch (e) {
  const PW_ABS = 'C:\\Users\\O1830\\.workbuddy\\binaries\\node\\workspace\\node_modules\\playwright';
  chromium = require(PW_ABS).chromium;
}

(async () => {
  const url = process.argv[2];
  if (!url) { process.stderr.write('ERR: missing url\n'); process.exit(2); }
  const waitMs = parseInt(process.argv[3] || '4000', 10);
  let browser;
  try {
    browser = await chromium.launch({
      headless: true,
      executablePath: 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
    });
    const page = await browser.newPage({
      userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
      viewport: { width: 1280, height: 800 },
    });
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 });
    await page.waitForTimeout(waitMs);
    // Scroll to trigger any lazy-loaded content, then wait a bit more.
    try {
      await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    } catch (e) {}
    await page.waitForTimeout(2000);
    const text = await page.evaluate(() => document.body.innerText);
    process.stdout.write(text || '');
  } catch (e) {
    process.stderr.write('ERR ' + (e && e.message ? e.message : String(e)) + '\n');
    process.exit(1);
  } finally {
    if (browser) await browser.close();
  }
})();
