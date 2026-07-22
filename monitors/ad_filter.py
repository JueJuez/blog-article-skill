"""monitors/ad_filter.py — 公众号广告/无干货内容处理。

区分两种广告（关键）：
1. 整篇纯广告（引流卖课、体验营开营）—— 整篇剔除（is_fully_ad / 别名 is_ad_by_content）。
2. 干货里夹带广告（文章总体有价值，但中间/结尾插了「加我微信」「扫码领资料」）——
   **不整篇删**，而是 purify_content() 抠掉广告段，保留干货再喂给总结管线。

过滤后「剩几篇算几篇，不往前补」——调用方负责不补。

两级：
- 标题级（discover 阶段即可用，无需正文）：命中营销/引流关键词即判整篇广告。
- 正文级（apply 阶段，已抓到正文后）：
    - is_fully_ad：短正文 + 多个强转化话术 → 整篇纯广告，skip。
    - purify_content：去除夹带的广告段与二维码图片，保留干货正文。
"""
import re
from datetime import datetime, timezone, timedelta

# 上海时区（UTC+8），不依赖 tzdata
CST = timezone(timedelta(hours=8))

# ---- 标题级关键词：明显营销/引流/无干货（整篇广告信号） ----
AD_TITLE_KEYWORDS = [
    "体验营", "开营", "报名", "领取", "免费领", "加微信", "扫码添加", "私域",
    "训练营", "大航海", "限时", "优惠券", "福利领取", "入群", "公开课", "招募",
    "招商", "代理", "加盟", "免费体验", "体验卡", "抢购", "秒杀", "促销",
    "免费领取", "扫码进群", "报名链接", "免费体验卡",
]

# ---- 正文级强转化话术（用于判定整篇广告 / 广告段） ----
AD_CONTENT_PHRASES = [
    "加入生财", "戳链接加入", "免费体验卡", "回复【1】", "领取体验", "添加微信",
    "扫码加入", "私信我领", "报名链接", "21天训练营", "限时名额", "扫码进群",
    "加我微信", "免费体验营", "立即报名", "点击报名", "添加助理", "联系顾问",
    "回复【", "扫码关注", "扫码添加", "免费领", "福利领取", "入群",
]

# ---- 单条即可疑的强广告词（用于广告段判定，需结合短段） ----
AD_BLOCK_STRONG = [
    "加微信", "加我微信", "扫码添加", "扫码进群", "添加助理", "联系顾问",
    "私信我", "回复【", "回复[", "戳链接", "扫码加入", "扫码关注",
    "免费领", "领取资料", "限时优惠", "欢迎关注", "关注我们", "点击上方",
]

# 二维码/推广图片 URL 特征
QR_IMG_HINTS = r"(?:qr|qrcode|weixin|mpvote|scan|ewm|二维码)"


def is_ad_by_title(title: str) -> bool:
    t = title or ""
    return any(k in t for k in AD_TITLE_KEYWORDS)


def is_fully_ad(title: str, content: str) -> bool:
    """整篇纯广告判定：标题命中，或短文+多转化话术。用于整篇剔除（skip）。"""
    if is_ad_by_title(title):
        return True
    if not content:
        return False
    # 长干货文即使含少量营销词也不误杀；仅短引流文判广告
    if len(content) < 1500:
        hits = sum(1 for p in AD_CONTENT_PHRASES if p in content)
        if hits >= 2:
            return True
    return False


# 兼容旧名
is_ad_by_content = is_fully_ad


def _strip_md(text: str) -> str:
    """粗略去除 markdown 标记，得到近似纯文本（用于广告段判定）。"""
    t = re.sub(r'!\[[^\]]*\]\([^)]*\)', '', text)  # 去图片
    t = re.sub(r'[#>*\-_`]', '', t)
    t = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', t)  # 去链接保留文字
    return t


def _is_ad_block(block: str) -> bool:
    """判断单个段落是否为广告段（夹在干货中的引流段）。"""
    if not block.strip():
        return False
    text = _strip_md(block)
    text = text.strip()
    if not text:
        return False
    # 短引流段：含强广告词且较短
    strong_hits = sum(1 for w in AD_BLOCK_STRONG if w in text)
    if strong_hits >= 1 and len(text) < 140:
        return True
    # 多话术段：含 ≥2 个转化短语（无论长短，明显引流）
    phrase_hits = sum(1 for p in AD_CONTENT_PHRASES if p in text)
    if phrase_hits >= 2:
        return True
    return False


def purify_content(content: str) -> str:
    """去除夹带的广告段与二维码图片，保留干货正文。

    适用于「总体是干货、但夹了广告」的文章：整篇保留，只抠掉广告段。
    返回净化后的正文（markdown / 纯文本，与输入同格式）。
    """
    if not content:
        return content
    # 1) 去二维码/推广图片（避免笔记里出现无意义二维码图）
    content = re.sub(
        r'!\[[^\]]*\]\((https?://[^\s)]*' + QR_IMG_HINTS + r'[^\s)]*)\)',
        '', content,
    )
    # 2) 分段（按空行），剔除广告段，保留干货段
    blocks = re.split(r'\n\s*\n', content)
    kept = [b for b in blocks if not _is_ad_block(b)]
    cleaned = '\n\n'.join(b.strip() for b in kept if b.strip()).strip()
    return cleaned


def today_start_ts() -> int:
    """上海时区今日 00:00 的 Unix 时间戳。"""
    now = datetime.now(CST)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(start.timestamp())
