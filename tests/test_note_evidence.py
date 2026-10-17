"""批次3行为契约：可证缺陷硬拦，关系与访问范围疑点转复核；离线脱敏源。"""
import pytest

from prompts.verifier import verify_note_mechanical


def check(note, source, **kwargs):
    from prompts.evidence import check_note_evidence
    return check_note_evidence(note, source, **kwargs)


def codes(report, decision=None):
    return {f['code'] for f in report['findings'] if decision is None or f['decision'] == decision}


@pytest.mark.parametrize('note,source,code', [
    ('## 金句原话\n> Success is always guaranteed.', '他只介绍三种演讲技巧，没有说过该英文句。', 'quote_not_found'),
    ('原话：“先发布再修订。”', '他的原话是先修订再发布。', 'quote_not_found'),
    ('资源：[甲项目](https://b.test/start)', '[甲项目](https://a.test/start)\n[乙项目](https://b.test/start)', 'resource_mismatch'),
    ('【笔记者复算】2500 - 1833.9915 = 566.0085，因此自洽。', '原始记录同时出现2500和1833.9915，缺口数字有噪声。', 'calculation_wrong'),
    ('## 金句摘录\n> [!quote] True intellect is the ability to adapt.', '字幕只讨论发言技巧。', 'quote_not_found'),
    ('**成绩单复算自洽（笔记者补充）**：2099÷1201.4 ≈ **+74.7%**', '两个输入为2099和1201.4，转录有噪声。', 'calculation_wrong'),
])
def test_confirmed_defects_block(note, source, code):
    # D03金句、资源串线及R1新增错误复算的同类缺陷。
    assert code in codes(check(note, source), 'block')


@pytest.mark.parametrize('note,source', [
    ('> [!abstract] TL;DR\n> 从多个步骤重组得到的速览。', '先收集证据，再分阶段行动。'),
    ('```python\n# 原话：不存在的句子\nx = 1 + 2\n```', '这是一份代码教学材料。'),
    ('这只是“自洽”一词的讨论，不是作者原话。', '正文解释怎样检查计算。'),
    ('原话：“先练习，再表达！”', '先练习,再表达!'),
    ('原话：“甲项目先试……结果再议。”', '甲项目先试。\n\n先收集多轮反馈。\n\n结果再议。'),
    ('【作者计算】100 - 30 = 80', '作者计算：100 - 30 = 80。此处算值可能有误。'),
    ('【笔记者复算】100 / 3 ≈ 33.33', '投入100元，分为3份。'),
    ('【笔记者复算】2万元 - 5000元 = 15000元', '原始投入2万元，支出5000元。'),
    ('【笔记者复算】1,000 + 20 = 1,020', '原始数量1000，另加20。'),
    ('公式示例：`原话：“这是代码字符串。”`', '这里没有代码中的句子。'),
    ('原话：“先练习，再表达！”', '先练习\n再表达'),
    ('这不是作者原话：“这是笔记者分析。”', '作者只谈练习。'),
    ('## 金句 / 关键数据\n> 作者有三项不同的阶段成果。', '原文有多项成果，细节另见正文。'),
    ('原话：“Keep practicing.（继续练习）”', 'Keep practicing.'),
    ('原话：“先练习。”', '> 先练习。\n\n---\n这是原文自己的引用区。'),
    ('原话：“先练习再表达。”', '先**练习**再表达。'),
    ('【作者复算】100 - 30 = 80，尚未经验证。', '作者复算：100 - 30 = 80。'),
    ('错误反例：1 + 1 = 3，请勿按此计算。', '讲解如何辨别错误算式。'),
])
def test_valid_or_non_claims_do_not_block(note, source):
    # 合法格式、明确删节、按精度近似和源内已有计算不能误判编造。
    assert not codes(check(note, source), 'block')


def test_cross_paragraph_splice_needs_explicit_ellipsis():
    assert 'quote_not_found' in codes(check('原话：“甲项目先试结果再议。”', '甲项目先试\n\n结果再议'), 'block')


def test_unknown_source_is_not_quote_failure_or_semantic_pass():
    r = check('原话：“无源句子”', '')
    assert 'source_unavailable' in codes(r, 'review')
    assert 'quote_not_found' not in codes(r, 'block')
    assert r['semantic_status'] == 'not_reviewed'


def test_incomplete_source_does_not_assert_quote_absence():
    r = check('原话：“可能在没抓到的后半段。”', '【正文截断】这里只抓取开头。')
    assert 'source_incomplete' in codes(r, 'review')
    assert not codes(r, 'block')


def test_unknown_resource_mirror_is_reviewed_not_proven_wrong():
    r = check('[甲项目](https://mirror.test/start)', '[甲项目](https://a.test/start)')
    assert 'resource_unverified' in codes(r, 'review')
    assert not codes(r, 'block')


def test_author_in_code_is_not_metadata():
    r = check('```md\n**作者**：示例\n```', '**作者**：小林', source_author='小林')
    assert 'author_mismatch' not in codes(r)


def test_formatter_keeps_source_author_distinct_from_book_author():
    from prompts.templates import format_note_with_prompt
    note = format_note_with_prompt('**书作者**：作家乙\n正文', author='解说者甲', url='https://example.test/book')
    assert '**作者**：解说者甲' in note and '**书作者**：作家乙' in note


def test_evidence_keeps_original_line_numbers_and_hashes():
    from prompts.provenance import text_hash
    source = '> 抓取时间：2026-10-15\n\n---\n\n先练习，再表达。\n'
    note = '## 金句原话\n> 先练习，再表达。'
    r = check(note, source)
    f = next(f for f in r['findings'] if f['code'] == 'quote_located')
    assert f['note']['line_start'] == 2
    assert f['source'][0]['line_start'] == 5
    assert r['source_sha256'] == text_hash(source) and r['note_sha256'] == text_hash(note)


def test_quote_location_is_specific_within_paragraph():
    r = check('原话：“最后行动。”', '先解释概念。\n然后准备。\n最后行动。')
    f = next(f for f in r['findings'] if f['code'] == 'quote_located')
    assert f['source'][0]['line_start'] == f['source'][0]['line_end'] == 3


def test_quote_decimal_and_negative_sign_are_not_normalized_away():
    assert 'quote_not_found' in codes(check('原话：“收益12%。”', '收益1.2%。'), 'block')
    assert 'quote_not_found' in codes(check('原话：“收益7%。”', '收益-7%。'), 'block')


@pytest.mark.parametrize('formula', ['1 / 0 = 3', '2元 + 3美元 = 5元', '2**3 = 8', '2^3 = 8'])
def test_unsupported_or_invalid_arithmetic_never_becomes_checked(formula):
    r = check('【笔记者复算】' + formula, '只是算式示例。')
    assert 'calculation_checked' not in codes(r)
    assert 'calculation_unparsed' in codes(r, 'review')


def test_known_author_metadata_wins_over_platform_fallback():
    from prompts.evidence import resolve_source_author
    assert resolve_source_author('**作者**：小林\n正文', '生财有术') == '小林'
    assert resolve_source_author('正文没有作者元数据', '生财有术') == ''


def test_platform_is_not_silently_saved_as_unknown_source_author():
    r = check('**作者**：生财有术\n正文', '原文未给出真实作者', source_author='生财有术')
    assert 'author_platform_label' in codes(r, 'block')


def test_successful_queue_keeps_full_evidence_in_review_and_trace(monkeypatch):
    import json
    from pathlib import Path
    from articles import main as am
    from prompts import review_rubric
    captured = {}
    def save(*a, **kw):
        captured.update(kw)
        return 'formatted', 'sample.md'
    monkeypatch.setattr(am, 'save_summarized_article', save)
    note = '成功率80%，这是需要核验的数量。\n' + '说明具体方法与限制。\n' * 50
    result = am.save_summary_only({'summarized_content': note, 'source_text': '原文未提供成功率。', 'force': True, 'folder': '固定目录'})
    assert result['success']
    report = captured['generation_context']['evidence_check']
    queued = json.loads(Path(review_rubric.REVIEW_QUEUE).read_text(encoding='utf-8').splitlines()[-1])
    assert queued['evidence'] == report
    assert 'unsupported_number' in codes(report, 'review')
    assert report['semantic_status'] == 'not_reviewed'


def test_asr_correction_is_recorded_and_reviewed():
    r = check('ASR修正：原片段“里格”→“RAG”。\n原话：“RAG可以检索。”', '里格可以检索。')
    assert 'asr_correction' in codes(r, 'review')
    assert 'quote_not_found' not in codes(r, 'block')
    assert any('里格' in str(f.get('details', {})) for f in r['findings'])


@pytest.mark.parametrize('note,source,code', [
    ('作者在发布前维护评论区。', '作者在发布后维护评论区。', 'temporal_relation'),
    ('本次营收623元。', '这里623元只指第一阶段。', 'numeric_context'),
    ('成功率80%，值得尝试。', '这里没有成功率数据。', 'unsupported_number'),
    ('附件：手册.pdf，按手册执行。', '附件：手册.pdf（正文未读取）', 'attachment_unread'),
    ('【笔记者复算】利润 = 营收 - 成本，因此自洽。', '营收与成本口径不明确。', 'calculation_unparsed'),
])
def test_uncertain_relations_review_instead_of_block(note, source, code):
    assert code in codes(check(note, source), 'review')
    assert not codes(check(note, source), 'block')


def test_metadata_code_and_list_numbers_are_excluded():
    r = check('#标签2026\n**发布时间**：2026-10-15\n1. 第3章\n```sh\nrun --port 8888\n```', '没有数值陈述。')
    assert 'unsupported_number' not in codes(r)


def test_attachment_name_only_in_code_is_not_a_reading_claim():
    assert 'attachment_unread' not in codes(check('```sh\nopen 手册.pdf\n```', '附件：手册.pdf（正文未读取）'))


def test_existing_numeric_anchor_gate_excludes_metadata_code_and_urls():
    from prompts.content_signals import _numeric_anchors
    source = '#标签2026\n**发布时间**：2026-10-15\n2026-10-15 12:30\n1. 第3章\n```sh\nrun --port 8888\n```\nhttps://example.test/123456789\n实际营收623元。'
    assert _numeric_anchors(source) == {'623'}


def test_media_file_ids_not_numeric_or_stock_anchors():
    from prompts.content_signals import _numeric_anchors, _anchor_specs
    source = ('营收5000元，参考飞书20260910-180529.mp4【在线播放】；样例成本623元。\n'
              'a08858f610285862786562351e398a56_raw.mp4【在线播放】\n'
              '20260801-0438-27.8402268.mp4\n')
    assert _numeric_anchors(source) == {'5000', '623'}
    assert not [s for s in _anchor_specs(source) if s[0] == '代码']


def test_actual_business_numbers_and_stock_codes_survive_attachment_filter():
    from prompts.content_signals import _numeric_anchors, _anchor_specs
    source = ('附件：样例623.mp4；该阶段成本623元，总播放3000000次。\n'
              '股票 600519，成本1000元；另附图20260910.png。')
    assert {'623', '3000000', '600519', '1000'} <= _numeric_anchors(source)
    assert any(s[0] == '代码' and s[1] == '600519' for s in _anchor_specs(source))


def test_profile_share_boilerplate_is_not_business_numeric_evidence():
    from prompts.content_signals import _numeric_anchors
    source = ('@测试账号 在小红书收获了1056次赞与收藏，查看Ta主页 https://example.test/x\n'
              '@另一个账号 在小红书收获了6.3万次赞与收藏，另本次营收5000元。\n'
              '阅读 4948\n业务复盘：推广支出623元。')
    assert _numeric_anchors(source) == {'5000', '623'}


def test_clear_author_mismatch_blocks_without_confusing_book_author():
    assert 'author_mismatch' in codes(check('**作者**：平台甲\n正文', '**作者**：小林\n正文', source_author='小林'), 'block')
    assert 'author_mismatch' not in codes(check('**书作者**：作家乙\n正文', '**作者**：解说者甲\n正文', source_author='解说者甲'))


def test_verifier_classifies_evidence_as_unbypassable_issue():
    note = '原话：“根本不存在的金句。”\n' + '补充说明，只用于满足长度兜底。\n' * 25
    result = verify_note_mechanical(note, source_text='原文只谈练习的方法。')
    assert result['passed'] is False
    assert result['evidence_report']['semantic_status'] == 'not_reviewed'
    assert any('quote_not_found' in issue for issue in result['issues'])


def test_queue_force_cannot_bypass_evidence_defect(monkeypatch):
    import json
    from pathlib import Path
    from articles import main as am
    from shared import gate_blockers
    monkeypatch.setattr(am, 'save_summarized_article', lambda *a, **k: pytest.fail('确证缺陷不得落盘'))
    result = am.save_summary_only({'summarized_content': '原话：“不存在的句子。”\n' + '正常正文说明。\n' * 60,
                                  'source_text': '只谈练习方法。', 'force': True})
    assert result['success'] is False and 'quote_not_found' in result['message']
    report = result['evidence_report']
    path = next(Path(gate_blockers.GATE_BLOCKERS_BASE).parent.glob('gate_blockers*.jsonl'))
    logged = json.loads(path.read_text(encoding='utf-8').splitlines()[-1])
    assert logged['evidence'] == report
    assert report['source_sha256'] != 'unknown' and report['note_sha256']


def test_video_force_cannot_bypass_evidence_defect(monkeypatch):
    from videos import main as vm
    monkeypatch.setattr(vm, '_summarize_segments', lambda *a, **k: '原话：“不存在的句子。”\n' + '正常正文说明。\n' * 60)
    monkeypatch.setattr(vm, 'save_summarized_article', lambda *a, **k: pytest.fail('确证缺陷不得落盘'))
    with pytest.raises(ValueError, match='quote_not_found'):
        vm._summarize_and_save('只谈练习方法。', '', '练习', '小林', [], 'general', True)


def test_inplace_force_keeps_existing_note_on_evidence_defect(tmp_path, monkeypatch):
    import sys
    import json
    from articles import main as am
    from scripts import resum_save_batch as runner
    from shared import env
    monkeypatch.setattr(env, 'load_env', lambda **kw: None)
    monkeypatch.setattr(am, 'save_summarized_article', lambda *a, **k: pytest.fail('确证缺陷不得覆盖旧稿'))
    note = tmp_path / 'old.md'
    note.write_text('旧稿保留', encoding='utf-8')
    source = tmp_path / 'source.md'
    source.write_text('只谈练习方法。', encoding='utf-8')
    batch = tmp_path / 'batch.json'
    batch.write_text(json.dumps([{'title': '练习', 'source_path': str(source), 'note_path': str(note)}]), encoding='utf-8')
    (tmp_path / 'batch_00.md').write_text('原话：“不存在的句子。”\n' + '正常正文说明。\n' * 60 + '\n【核心主题词】练习 | 方法', encoding='utf-8')
    monkeypatch.setattr(sys, 'argv', ['resum_save_batch.py', str(batch), str(tmp_path), '--force'])
    runner.main()
    assert note.read_text(encoding='utf-8') == '旧稿保留'


def test_direct_article_blocks_before_save(monkeypatch):
    from articles import main as am
    monkeypatch.setattr(am, 'summarize_content', lambda *a, **k: {'summary': '原话：“不存在的句子。”\n' + '正常正文说明。\n' * 60})
    monkeypatch.setattr(am, 'save_summarized_article', lambda *a, **k: pytest.fail('直连文章也应检查证据'))
    result = am.summarize_and_save('练习方法的原文。' * 30, note_type='general')
    assert 'quote_not_found' in (result[-1] or '')
