# testing_rules — 动手时的 TDD 流程规则

> 本文件是 `RULES.md §6 grill_rules` 的「动手时」配套规则。grill_rules 管「动手前」（拷问→决策清单+RED 测试草稿），本文件管「动手时」（把 RED 测试转绿）。两者一前一后，覆盖完整开发闭环。

## 1. 核心循环（RED → GREEN → REFACTOR）

1. **RED**：先写会失败的测试（来自 grill_rules 的「产出 B」或新需求推导）。测试锁定「需求到底是什么」。
2. **GREEN**：用最小代码让测试转绿，**允许丑陋**，不许顺手重构。
3. **REFACTOR**：在测试保护下清理代码（提取函数、消重复、调命名），保持全绿。
4. 循环直到需求覆盖完整。

> 禁止跳过 RED 直接写实现——没有失败测试保护的需求等于没拉齐。

## 2. 测试存放与命名

| 项 | 规范 |
|----|------|
| 目录 | `tests/` |
| 文件 | `test_{feature}.py`（如 `test_youtube_cdp.py`、`test_chunking.py`） |
| 函数 | `test_<行为>_<场景>`（如 `test_fetch_transcript_youtube_cdp_fallback`） |
| 类 | 需要共享 fixture / 分组时用 `class TestXxx:` |

## 3. Fixture 与断言规范

- **离线优先**：所有测试必须离线可跑。网络/外部 AI/文件系统用 `unittest.mock` 或 `pytest.monkeypatch` 隔离。
- **不污染用户环境**：禁止在测试里真实写入 `notes/`、Obsidian、飞书。写盘相关逻辑一律 mock（参考 `tests/test_prd.py` 的 `BaseOutput` mock 手法）。
- **Provider 可切换**：测试前 `os.environ["AI_PROVIDER"] = "mock"` 启用内置 MockProvider，保证 AI 总结可离线跑（见 `tests/test_prd.py` 头部）。
- **断言讲人话**：断言失败信息要说明「期望什么 / 实际什么 / 为什么」。优先 `assert x == y, f"期望 {y}，实际 {x}"`。
- **边界与异常必测**：grill_rules 拷问中确认的边界条件、异常场景，全部转成测试用例，且每条加场景注释说明「这个测试在保护什么需求」。

## 4. 与 grill_rules 的衔接

- grill_rules 产出的「测试草稿」直接落到 `tests/`，命名遵循 §2。
- 写测试前先确认：`tests/test_prd.py` 是否已覆盖类似路径——能复用既有的 mock/fixture 就复用，不重复造轮子（对应 `RULES.md §4.1` 复用原则）。
- 测试转绿即代表需求落地；随后才允许提交（提交规范见各决策清单）。

## 5. 运行方式

```bash
# 跑全量（离线）
python -m pytest tests/ -q

# 跑单个文件 / 用例
python -m pytest tests/test_prd.py -q
python -m pytest tests/test_prd.py::test_xxx -q
```

> 性能相关约束见 `RULES.md §4.1`：测试里若有循环/串行/同步阻塞，同样适用「一次性查询后筛选 / 改并行」原则（如多链接测试批量构造输入再断言，而非串行逐条）。
