"""批次2：保护主线分类、来源边界和版本绑定。全部离线。"""
import copy
import hashlib
import json

import pytest

from prompts.classify import classify_note_type


@pytest.mark.parametrize('title,body,expected', [
    ('项目失败复盘', '这不是抄作业。先试了甲方案，再试乙方案，最后关停项目。', 'case'),
    ('学习方法', '上周读完一本书，今天讲如何安排练习。', 'structured'),
    ('《交易体系篇》第三课', '今天讲仓位管理的步骤。', 'structured'),
    ('第17集_漫画原著精讲：保护线', '这一集围绕这本书的故事。这本书用人物行动展开论点，书中还有反例。', 'reading'),
    ('第01集_学习脑科学', '本期讲知识内化的机制与学习步骤。', 'structured'),
    ('教你应对领导', '下面模拟对话。领导：你当时为什么没有交？我：你后来改了需求。你是怎么想的？', 'general'),
    ('人生9大公式', '你当时听父母的建议，你后来可以再选，你是怎么决定的？', 'structured'),
    ('一个普通人怎么做出产品', '我是小林，这是我从0到1做产品的完整经历，最后累计售卖1万。', 'case'),
    ('一个月日流水从0到3K', '我是真的在干，我开始搭建系统，记录了收入和售后。', 'case'),
    ('AI带货我是怎么找对标、拆爆款', '我们复盘几个月的营收和视频结构。', 'dissection'),
    ('访谈创业者小林', '主持人：你当时为什么创业？\n小林：我想解决客户问题。\n主持人：你后来呢？\n小林：我开始试做。', 'interview'),
    ('三款笔记软件同条件横评', '在相同设备上测试甲、乙、丙三款软件，比较价格、速度和导出格式。', 'roundup'),
    ('美业小程序失败案例复盘', '局部对比甲乙两种玩法的价格，接着记录整个项目的失败和售后。', 'case'),
    ('《学习之道》书评', '本书作者讨论学习方法，第一章的论点与第二章的论据相互补充。', 'reading'),
])
def test_mainline_classification(title, body, expected):
    # 防止否定、推广、书名号、模拟对话和局部比较抢主线。
    assert classify_note_type(title, body) == expected


def test_dispatch_hashes_and_manual_override():
    from prompts.provenance import dispatch_fields
    data = dispatch_fields('访谈创业者', '主持人：你好。嘉宾：你好。', 'reading', 'manual')
    assert data['note_type'] == 'reading'
    trace = data['generation_context']
    assert trace['type_origin'] == 'manual'
    assert trace['prompt_sha256'] == hashlib.sha256(data['prompt'].encode()).hexdigest()
    assert trace['source_sha256'] == hashlib.sha256('主持人：你好。嘉宾：你好。'.encode()).hexdigest()
    assert trace['review']['status'] == 'not_performed'


@pytest.mark.parametrize('origin,expected', [('auto', 'general'), ('manual', 'interview'), ('legacy', 'interview')])
def test_refresh_origin_boundary(tmp_path, origin, expected):
    from scripts.filter_pending import refresh_prompt
    raw = tmp_path / 'source.md'
    raw.write_text('下面模拟领导对话，你当时为什么，你后来怎么想。', encoding='utf-8')
    entry = {'title': '教你发疯', 'note_type': 'interview', 'raw_file': str(raw),
             'prompt': '旧模板', 'folder': '固定目录', 'url': 'https://example.test/1',
             'generation_context': {'type_origin': origin}}
    before = copy.deepcopy(entry)
    refresh_prompt(entry)
    assert entry['note_type'] == expected
    assert entry['folder'] == before['folder'] and entry['url'] == before['url']
    if origin == 'legacy':
        assert entry == before  # legacy差异只进入报告，绝不悄改派单。
    else:
        assert entry['generation_context']['note_type'] == expected


def test_dry_run_never_writes(tmp_path):
    from scripts.filter_pending import _run_queue
    raw = tmp_path / 'raw.md'
    raw.write_text('普通记录', encoding='utf-8')
    path = tmp_path / 'queue.json'
    path.write_text(json.dumps([{'title': '普通记录', 'note_type': 'interview', 'raw_file': str(raw), 'prompt': 'old'}]), encoding='utf-8')
    original = path.read_bytes()
    stats = _run_queue(str(path), lambda es: (es, []), dry_run=True)
    assert path.read_bytes() == original
    assert stats['type_differences'][0]['suggested_type'] == 'general'


def test_payload_carries_context_without_inventing_legacy():
    from scripts.save_pending import build_payload
    for queue in ('scys', 'monitors'):
        trace = {'type_origin': 'manual', 'note_type': 'reading'}
        assert build_payload(queue, {'generation_context': trace, 'note_type': 'reading'}, '稿', False)['generation_context'] == trace
        assert build_payload(queue, {}, '稿', False)['generation_context']['type_origin'] == 'legacy'


def test_registry_trace_and_type_change_keep_url_identity():
    # 类型变化不能成为新去重键；没证据的重做不能继承旧生成版本。
    from articles import dedup
    from prompts.provenance import dispatch_fields
    url = 'https://example.test/stable'
    trace = dispatch_fields('书评', '本书的论点', 'reading', 'manual')['generation_context']
    dedup.mark_summarized(url=url, filename='same.md', note_type='reading', generation_context=trace)
    first = dedup.is_summarized(url=url)
    dedup.mark_summarized(url=url, note_type='general', filename='same.md')
    second = dedup.is_summarized(url=url)
    assert first['generation_context'] == trace
    assert second['key'] == first['key'] and second['filename'] == 'same.md'
    assert second['generation_context']['source_sha256'] == 'unknown'


def test_registration_type_change_does_not_keep_old_prompt_evidence():
    # 只改登记类型，没有新生成证据时不能让旧prompt继续背书新类型。
    from articles import dedup
    from prompts.provenance import dispatch_fields
    url = 'https://example.test/registration'
    trace = dispatch_fields('书评', '本书论点', 'reading', 'manual')['generation_context']
    dedup.mark_summarized(url=url, note_type='reading', generation_context=trace)
    rec = dedup.register(url=url, note_type='general')
    assert rec['note_type'] == 'general'
    assert rec['generation_context']['prompt_sha256'] == 'unknown'


@pytest.mark.parametrize('field,value', [('prompt', '被改写的派单'), ('note_type', 'reading')])
def test_dispatch_mutation_cannot_claim_old_version(tmp_path, field, value):
    from prompts.provenance import dispatch_fields
    from scripts.save_pending import build_payload
    raw = tmp_path / 'raw.md'
    raw.write_text('原始正文', encoding='utf-8')
    entry = {**dispatch_fields('普通记录', '原始正文'), 'raw_file': str(raw)}
    entry[field] = value
    with pytest.raises(ValueError, match='PROVENANCE_MISMATCH'):
        build_payload('monitors', entry, '总结', False)


def test_changed_source_cannot_claim_old_version(tmp_path):
    from prompts.provenance import dispatch_fields
    from scripts.save_pending import build_payload
    raw = tmp_path / 'raw.md'
    raw.write_text('已经替换的源', encoding='utf-8')
    entry = {**dispatch_fields('普通记录', '原始正文'), 'raw_file': str(raw)}
    with pytest.raises(ValueError, match='PROVENANCE_MISMATCH'):
        build_payload('monitors', entry, '总结', False)


def test_video_records_actual_chunk_and_merge_prompts(monkeypatch):
    # 分块与合并的额外指令也必须进入hash，不能只记基础模板。
    from videos import main as vm
    from prompts.provenance import text_hash
    actual = []
    monkeypatch.setattr(vm, '_ai_summarize', lambda p, t: actual.append((p, t)) or '小结')
    monkeypatch.setattr(vm, 'verify_note', lambda *a, **k: {})
    monkeypatch.setattr(vm, 'should_gate_retry', lambda g: False)
    monkeypatch.setattr(vm, 'two_stage_summarize', lambda chunks, summarize, merge: merge([summarize('正文', 0, 1)]))
    calls = []
    vm._summarize_segments('正文', 'general', generation_calls=calls)
    assert [{k: c[k] for k in ('prompt_sha256', 'input_sha256')} for c in calls] == [
        {'prompt_sha256': text_hash(p), 'input_sha256': text_hash(t)} for p, t in actual]
    assert all(c['elapsed_seconds'] >= 0 and c['usage'] == 'not_exposed' for c in calls)
    assert len(calls) == 2 and calls[0]['prompt_sha256'] != calls[1]['prompt_sha256']


def test_article_records_retry_prompt(monkeypatch):
    # 最终稿来自重试时，追踪不能仍指向第一次prompt。
    from articles import main as am
    calls = []
    monkeypatch.setattr(am, 'call_ai_summary_with_meta', lambda p, t: calls.append(p) or {'content': '总结'})
    monkeypatch.setattr(am, 'verify_note', lambda *a: {'needs_retry': True})
    monkeypatch.setattr(am, 'should_gate_retry', lambda g: True)
    monkeypatch.setattr(am, 'build_gate_critique', lambda g: '\n具体修订要求')
    result = am.summarize_content('完整原文' * 50, note_type='general')
    assert result['generation_prompt'] == calls[-1]
    assert calls[-1].endswith('具体修订要求') and len(calls) == 3


def test_direct_save_rejects_stale_source_before_output(monkeypatch):
    # 调用方绕过队列runner时，保存主入口也不能把陈旧版本当本次证据。
    from articles import main as am
    from prompts.provenance import dispatch_fields
    fields = dispatch_fields('普通记录', '原始源')
    monkeypatch.setattr(am, 'save_summarized_article', lambda *a, **k: pytest.fail('版本失配不应触发输出'))
    result = am.save_summary_only({**fields, 'summarized_content': '总结',
                                   'source_text': '变过的源', 'force': True})
    assert result['success'] is False and 'PROVENANCE_MISMATCH' in result['message']
