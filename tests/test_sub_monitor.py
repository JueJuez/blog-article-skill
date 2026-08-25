"""tests/test_sub_monitor.py — 订阅监控单元测试（GREEN）。

用 mock 替换网络层，验证：
1. WechatSource 首次运行只取最近 N 条，且全部 id 入 seen
2. WechatSource 增量(daily)只取当天发布、最多 5 条
3. 通过 share_url 能解析出 mp_id
4. state 读写与去重
5. 广告过滤（标题级 + 正文级）
6. 首次/增量模式可显式指定
"""
import sys
import os
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from monitors.state import load_state, save_state, get_seen, mark_seen  # noqa: E402
from monitors.wechat import WereadClient, WechatSource  # noqa: E402
from monitors.ad_filter import is_ad_by_title, is_ad_by_content, is_fully_ad, purify_content, today_start_ts  # noqa: E402
from monitors.bilibili import BilibiliSource, _normalize_dynamics  # noqa: E402


def _client_with(articles):
    client = WereadClient(token="t", vid="1")
    client.list_articles = MagicMock(return_value=articles)
    client.resolve_mp = MagicMock(return_value={"id": "MP_X", "name": "测试号"})
    return client


def _make_articles(n=20, start_ts=None):
    if start_ts is None:
        start_ts = int(time.time())  # 贴近 now：时间窗口机制引入后，远古时间戳会被窗口过滤导致误判
    return [{"id": f"a{i}", "title": f"t{i}", "publishTime": start_ts - i}
            for i in range(n)]


def _today_articles(n=8):
    ts = today_start_ts()
    return [{"id": f"d{i}", "title": f"t{i}", "publishTime": ts + i} for i in range(n)]


class TestWechatSource(unittest.TestCase):

    def test_first_run_limits_to_n(self):
        client = _client_with(_make_articles(20))
        src = WechatSource(client, mp_id="MP_X", name="测试号")
        state = load_state()
        new = src.discover(state, first_run_limit=5)
        self.assertEqual(len(new), 5)
        self.assertEqual([n["id"] for n in new], [f"a{i}" for i in range(5)])
        # 全部 20 条都标记为已见（后续增量不再重复处理历史）
        self.assertEqual(len(get_seen(state, "wechat:MP_X")), 20)

    def test_first_run_explicit_even_if_seen(self):
        # mode="first" 强制首次逻辑：即使已有 seen 也取最近 5
        client = _client_with(_make_articles(20))
        src = WechatSource(client, mp_id="MP_X")
        state = load_state()
        mark_seen(state, "wechat:MP_X", ["old"])
        new = src.discover(state, mode="first")
        self.assertEqual(len(new), 5)
        self.assertEqual([n["id"] for n in new], [f"a{i}" for i in range(5)])

    def test_incremental_auto_takes_recent_not_today(self):
        # 增量(auto)：抓最近 N 篇 + 去重，不限制「当天」——避免跨天边界文章永久遗漏
        client = _client_with(_make_articles(20))  # 非当天
        src = WechatSource(client, mp_id="MP_X")
        state = load_state()
        mark_seen(state, "wechat:MP_X", ["old1"])  # 非空 -> 增量
        new = src.discover(state)
        self.assertEqual(len(new), 5)
        self.assertEqual([n["id"] for n in new], [f"a{i}" for i in range(5)])

    def test_daily_removed_mode_is_rejected(self):
        # daily 模式已从 CLI 移除（语义误导），run.py 仅接受 auto/first
        import argparse
        from monitors.run import main as _unused  # noqa: F401 确保模块可导入
        parser = argparse.ArgumentParser()
        # 复刻 run.py 的 mode 选项校验
        parser.add_argument("--mode", choices=["auto", "first"], default="auto")
        with self.assertRaises(SystemExit):
            parser.parse_args(["--mode", "daily"])

    def test_resolve_via_share_url(self):
        client = _client_with([{"id": "a0", "title": "t", "publishTime": int(time.time())}])
        src = WechatSource(client, share_url="https://mp.weixin.qq.com/s/xxx")
        state = load_state()
        src.discover(state)
        client.resolve_mp.assert_called_once()
        self.assertEqual(src.mp_id, "MP_X")

    def test_article_url_shape(self):
        client = _client_with([{"id": "abc123", "title": "t", "publishTime": int(time.time())}])
        src = WechatSource(client, mp_id="MP_X")
        state = load_state()
        new = src.discover(state)
        self.assertEqual(new[0]["url"], "https://mp.weixin.qq.com/s/abc123")
        self.assertEqual(new[0]["route"], "article")

    def test_ad_filtered_in_discover(self):
        # 标题含营销词的文章在 discover 阶段被剔除，且不补
        now = int(time.time())
        arts = [
            {"id": "good1", "title": "干货文章一篇", "publishTime": now - 100},
            {"id": "ad1", "title": "生财3天免费体验营第161期即将开营", "publishTime": now - 200},
            {"id": "ad2", "title": "限时报名领取福利", "publishTime": now - 300},
            {"id": "good2", "title": "另一篇干货", "publishTime": now - 400},
            {"id": "good3", "title": "第三篇干货", "publishTime": now - 500},
        ]
        client = _client_with(arts)
        src = WechatSource(client, mp_id="MP_X")
        state = load_state()
        new = src.discover(state, first_run_limit=5)
        ids = [n["id"] for n in new]
        self.assertIn("good1", ids)
        self.assertIn("good2", ids)
        self.assertIn("good3", ids)
        self.assertNotIn("ad1", ids)
        self.assertNotIn("ad2", ids)


class TestAdFilter(unittest.TestCase):

    def test_title_keywords(self):
        self.assertTrue(is_ad_by_title("生财体验营第161期即将开营"))
        self.assertTrue(is_ad_by_title("限时报名领取福利"))
        self.assertFalse(is_ad_by_title("干货文章一篇"))
        self.assertFalse(is_ad_by_title("哥飞的朋友们2026年中分享会"))

    def test_content_short_with_phrases(self):
        content = "点击报名加入生财，戳链接加入，免费体验营名额有限"
        self.assertTrue(is_ad_by_content("某文章", content))
        # 长干货文即使含个别营销词也不误杀
        long_content = "A" * 2000 + "加入生财"
        self.assertFalse(is_ad_by_content("长文", long_content))


class TestPurify(unittest.TestCase):
    """验证「干货夹广告」场景：不整篇删，而是净化掉广告段保留干货。"""

    def test_purify_keeps_goods_drops_ad_block(self):
        content = (
            "这是第一段真正的干货，讲清楚了核心方法。\n\n"
            "加微信 abc123 领取资料，限时优惠，扫码进群一起学习。\n\n"
            "这是第三段干货，给出了可落地的步骤和注意事项。\n\n"
            "![二维码](https://img.example.com/qrcode_abc.png)"
        )
        out = purify_content(content)
        # 干货保留
        self.assertIn("第一段真正的干货", out)
        self.assertIn("第三段干货", out)
        # 广告段被剔除
        self.assertNotIn("加微信 abc123", out)
        self.assertNotIn("限时优惠", out)
        # 二维码图片被剔除
        self.assertNotIn("qrcode", out)

    def test_purify_keeps_long_article_with_one_mention(self):
        # 长干货文中仅出现一次营销词（嵌在长段中间），不应被误删
        content = "干货段落一，详细论述。\n\n" + "A" * 500 + "\n\n" + \
                  "干货段落二，" + "X" * 200 + "加微信可交流，但整体是干货。" + "Y" * 200 + "\n\n" + "B" * 500
        out = purify_content(content)
        self.assertIn("干货段落一", out)
        self.assertIn("干货段落二", out)

    def test_fully_ad_short_is_skipped(self):
        # 整篇短广告 -> 应被 is_fully_ad 判为整篇广告（供 apply skip）
        self.assertTrue(is_fully_ad(
            "某文", "戳链接加入生财，免费体验营，添加助理微信限时名额"))
        # 整篇长干货 -> 不应整篇删
        self.assertFalse(is_fully_ad("长文", "A" * 2000 + "加入生财"))


class TestState(unittest.TestCase):

    def test_roundtrip(self):
        state = load_state()
        mark_seen(state, "bilibili:1", ["BV1", "BV2"])
        self.assertEqual(get_seen(state, "bilibili:1"), {"BV1", "BV2"})
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "..", "monitors", "_test_state.json")
        save_state(state, path)
        reloaded = load_state(path)
        self.assertEqual(get_seen(reloaded, "bilibili:1"), {"BV1", "BV2"})
        try:
            os.remove(path)
        except OSError:
            pass  # 沙箱环境可能禁止删除，忽略

    def test_missing_source_returns_empty(self):
        state = load_state()
        self.assertEqual(get_seen(state, "wechat:nope"), set())


class TestBilibiliSource(unittest.TestCase):

    def _videos(self, n=5):
        now = int(time.time())
        return [{"bvid": f"BV{i}", "title": f"视频{i}", "author": "UP主",
                 "created": now - i * 3600} for i in range(n)]

    def _dynamics(self):
        # 真实 B站 动态 item 结构（嵌套在 modules 中）；时间戳取「最近」以通过时间窗口过滤
        now = int(time.time())
        return [
            {  # 视频转发 -> 跳过
                "id_str": "111", "type": "DYNAMIC_TYPE_AV",
                "modules": {
                    "module_author": {"name": "UP主", "pub_ts": now - 30},
                    "module_dynamic": {"desc": {"text": "转发了个视频"}},
                },
            },
            {  # 纯文字动态 -> 保留
                "id_str": "222", "type": "DYNAMIC_TYPE_WORD",
                "modules": {
                    "module_author": {"name": "UP主", "pub_ts": now - 60},
                    "module_dynamic": {"desc": {"text": "今天复盘大盘，讲了三个要点，仅供参考"}},
                },
            },
            {  # 图文但无文案 -> 跳过
                "id_str": "333", "type": "DYNAMIC_TYPE_DRAW",
                "modules": {
                    "module_author": {"name": "UP主", "pub_ts": now - 120},
                    "module_dynamic": {"desc": {"text": ""}},
                },
            },
        ]

    def test_video_only(self):
        with patch("monitors.bilibili._fetch_vlist", return_value=self._videos(5)), \
             patch("monitors.bilibili._fetch_dynamics", return_value=[]):
            src = BilibiliSource("123", types=["video"])
            state = load_state()
            new = src.discover(state, first_run_limit=5)
        self.assertEqual(len(new), 5)
        self.assertEqual(new[0]["route"], "video")
        self.assertEqual(new[0]["url"], "https://www.bilibili.com/video/BV0")

    def test_normalize_dynamics_skips_av_and_empty(self):
        # 纯函数单测：AV 转发 / 无文案 DRAW 被跳过，仅 WORD 保留
        out = _normalize_dynamics(self._dynamics())
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["id"], "222")
        self.assertIn("复盘大盘", out[0]["text"])

    def test_dynamic_only_keeps_word_skips_av(self):
        # discover 拿到的是已标准化的动态（AV/空文案已由 _normalize_dynamics 剔除）
        norm = _normalize_dynamics(self._dynamics())
        with patch("monitors.bilibili._fetch_vlist", return_value=[]), \
             patch("monitors.bilibili._fetch_dynamics", return_value=norm):
            src = BilibiliSource("123", types=["dynamic"])
            state = load_state()
            new = src.discover(state, first_run_limit=5)
        self.assertEqual(len(new), 1)
        self.assertEqual(new[0]["route"], "dynamic")
        self.assertEqual(new[0]["id"], "dyn:222")
        self.assertIn("复盘大盘", new[0]["content"])
        self.assertEqual(new[0]["url"], "https://t.bilibili.com/222")

    def test_both_types_combined(self):
        with patch("monitors.bilibili._fetch_vlist", return_value=self._videos(3)), \
             patch("monitors.bilibili._fetch_dynamics", return_value=_normalize_dynamics(self._dynamics())):
            src = BilibiliSource("123")  # 默认 video+dynamic
            state = load_state()
            new = src.discover(state, first_run_limit=5)
        routes = {n["route"] for n in new}
        self.assertIn("video", routes)
        self.assertIn("dynamic", routes)

    def test_dynamic_first_run_caps_to_limit(self):
        now = int(time.time())
        many = [{"id": str(i), "type": "DYNAMIC_TYPE_WORD", "author": "UP",
                 "pub_ts": now - i * 60,
                 "text": f"动态内容{i}：今天复盘大盘讲了三个要点，分别是仓位管理、止损纪律和情绪控制，仅供参考"} for i in range(8)]
        with patch("monitors.bilibili._fetch_vlist", return_value=[]), \
             patch("monitors.bilibili._fetch_dynamics", return_value=many):
            src = BilibiliSource("123", types=["dynamic"])
            state = load_state()
            new = src.discover(state, first_run_limit=5)
        self.assertEqual(len(new), 5)

    def test_force_all_skips_seen_but_dedup_blocks_summarized(self):
        # --all-videos（force_all）补历史：seen 门禁被跳过（错标/漏标的历史能补回），
        # 但已总结过的（dedup 索引命中）必须跳过，否则重复入队/重复落盘。
        now = int(time.time())
        videos = [
            {"bvid": "BV_SEEN_SUMM", "title": "已总结", "author": "UP", "created": now - 100},
            {"bvid": "BV_SEEN_NOTSUMM", "title": "错标历史", "author": "UP", "created": now - 200},
            {"bvid": "BV_NEW", "title": "新视频", "author": "UP", "created": now - 300},
        ]
        with patch("monitors.bilibili._fetch_vlist", return_value=videos), \
             patch("monitors.bilibili._fetch_dynamics", return_value=[]), \
             patch("articles.dedup.batch_is_summarized",
                   return_value={"https://www.bilibili.com/video/BV_SEEN_SUMM"}):
            src = BilibiliSource("123", types=["video"], force_all=True)
            state = load_state()
            mark_seen(state, src.source_key(), ["BV_SEEN_SUMM", "BV_SEEN_NOTSUMM"])
            new = src.discover(state, first_run_limit=50)
        ids = {n["id"] for n in new}
        self.assertNotIn("BV_SEEN_SUMM", ids)      # 已总结 -> dedup 挡住
        self.assertIn("BV_SEEN_NOTSUMM", ids)      # 错标历史 -> seen 被跳过，补回
        self.assertIn("BV_NEW", ids)               # 未 seen 未总结 -> 正常

    def test_window_outside_not_marked_seen(self):
        # 修复②：窗口外的视频/动态绝不 mark_seen（与微信对齐）。
        # 否则断跑后 effective_window_days 补齐窗口时，本该补抓的旧内容已被 seen 永久跳过=漏抓。
        now = int(time.time())
        # 首跑窗口默认 30 天（_BILI_FIRST_WINDOW_DAYS），90 天前的视频在窗口外
        videos = [
            {"bvid": "BV_OLD", "title": "老视频", "author": "UP", "created": now - 90 * 86400},
            {"bvid": "BV_NEW2", "title": "新视频", "author": "UP", "created": now - 60},
        ]
        with patch("monitors.bilibili._fetch_vlist", return_value=videos), \
             patch("monitors.bilibili._fetch_dynamics", return_value=[]):
            src = BilibiliSource("123", types=["video"])
            state = load_state()
            new = src.discover(state, first_run_limit=50, mode="first")
        self.assertEqual([n["id"] for n in new], ["BV_NEW2"])
        seen = get_seen(state, src.source_key())
        self.assertNotIn("BV_OLD", seen)   # 窗口外不标记
        self.assertIn("BV_NEW2", seen)     # 窗口内标记


if __name__ == "__main__":
    unittest.main()
