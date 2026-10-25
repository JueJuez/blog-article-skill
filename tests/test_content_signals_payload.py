# -*- coding: utf-8 -*-
"""P0：锚点构造前剥离抓取 payload（2026-10-19 留出轮）。

守住两件事：
1. JSON 里的图片尺寸 / logid / note_id 不再进入锚点分母（否则好稿被误拦）；
2. 正文里的长金额（如 4793700000000）不能被「长数字=ID」误删。
"""
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from prompts import content_signals as CS  # noqa: E402


PAYLOAD = ('{"debug_url":"https://www.coze.cn/work_flow?execute_id=7632605219155017762",'
           '"cover":{"height":1440,"width":1920},"note_id":"52303000000001",'
           '"logid":"202604251606268DBB987A377DA7B91229"}')


def test_payload_line_removed_and_prose_kept():
    src = PAYLOAD + "\n公司营收4793700000000元，同比增长30%，2024年目标。"
    out = CS.strip_source_payload(src)
    assert "1440" not in out and "7632605219155017762" not in out
    assert "4793700000000" in out and "30%" in out


def test_payload_numbers_not_anchors_but_real_amount_is():
    src = PAYLOAD + "\n公司营收4793700000000元，同比增长30%。"
    a = CS.anchors_recall("营收4793700000000元，增长30%", src)
    displays = [m.split(":", 1)[1] for m in a["missed"]]
    assert "1440" not in displays and "3370" not in displays
    assert "4793700000000" not in displays
    assert a["total"] >= 2


def test_code_example_json_is_not_stripped_blindly():
    # 教程里的配置示例是正文内容，没有 payload 字段就不能删
    line = '{"timeScope": "2"}  // 1=一天内, 2=一周内'
    assert "timeScope" in CS.strip_source_payload(line)


def test_url_removed_but_surrounding_text_kept():
    out = CS.strip_source_payload("看这个 https://a.test/x 页面，讲 SEO。")
    assert "https://" not in out and "SEO" in out


def test_comment_box_chrome_stripped_but_real_number_kept():
    # scys 评论框占位符「0 / 1200 发布」被拆行抓进正文（2026-10-25 批量实拦 4 篇），
    # 紧邻序列整体剥离；正文里独立出现的 1200（如「月入 1200 元」）必须保留。
    chrome = "等629人点赞\n77人投了108个锚点\n\n\n\n\n0\n/\n1200\n发布\n全部评论"
    out = CS.strip_source_payload(chrome)
    assert "1200" not in out
    assert CS.strip_source_payload("副业月入1200元，稳定半年。") .count("1200") == 1
