"""事实密度量尺（`scripts/measure_fact_density.py`）的口径护栏。

守四件事——每一条都对应一个真实误判来源，改口径前先想清楚会不会重新引入：

1. **标题行不算数据**：`### 10.1` 这类小节编号曾被算成「源内数值」，让密度虚高；
2. **源侧元信息块不算源内容**：`> 标题：…100 倍` / `> 时间：2026-09-15 16:07:03` 里的数字
   是集数与抓取时间戳，算法里会把它们当成「笔记可复用的数值」；
3. **单位与百分号不敏感**：源写 `97%`、笔记写 `97 人` 应算命中；
4. **源里没有的数值不计命中**：源外数字不能抬高密度。
"""
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from scripts import measure_fact_density as M  # noqa: E402

SRC = """> 原始文章内容（自动暂存）
> 标题：90% 和女生的短期关系！比技巧好用100倍！
> 时间：2026-09-15 16:07:03

---

正文第一段：满意度 97% 的用户会复购。
正文第二段：只有 3 个前提。"""


def test_metadata_block_is_not_source_content():
    assert M.source_numbers(SRC) == {"97", "3"}


def test_heading_lines_are_not_data():
    note = "## 10.1 小结\n\n满意 97% 的用户会复购。\n"
    d, hit, total, _ = M.density(note, SRC)
    assert (hit, total) == (1, 1), "标题里的 10.1 不应被算成数值"
    assert d > 0


def test_unit_and_percent_insensitive():
    note = "- 满意度 97（百分比口径）\n- 复购来自 3 个前提\n"
    assert M.density(note, SRC)[1] == 2


def test_numbers_absent_from_source_do_not_count():
    note = "- 满意度 88%\n- 复购 42 次\n"
    assert M.density(note, SRC)[1] == 0


def test_density_is_per_thousand_words():
    note = "- 97\n"
    words = M.count_note_words(note) or 1
    d, hit, _, used = M.density(note, SRC)
    assert used == words
    assert abs(d - 1 / (words / 1000.0)) < 1e-9
