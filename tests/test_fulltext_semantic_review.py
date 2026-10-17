"""批次4 RED草稿：仅模拟审核返回，不联网、不写生产库。"""
import json

import pytest

from prompts import templates


@pytest.fixture
def reviewer(monkeypatch):
    from articles import main
    calls = []

    def call(prompt, content, **kwargs):
        calls.append((prompt, content))
        return {'content': json.dumps({'score': 96, 'pass': True, 'issues': []})}

    monkeypatch.setattr(templates, 'QUALITY_GATE_ENABLED', True)
    monkeypatch.setattr(main, 'call_ai_summary_with_meta', call)
    return calls


def test_review_receives_source_tail(reviewer):
    source = '前段信息。\n' * 1800 + '尾部限制：仅测试三账号，尚未验证整体盈利。'
    templates.verify_note('笔记正文。', source, 'general')
    assert any('尾部限制：仅测试三账号' in content for _, content in reviewer)


def test_missing_source_cannot_pass(reviewer):
    result = templates.verify_note('笔记正文。', '', 'general')
    assert result is not None and result['passed'] is False
    assert not reviewer, '没有原文时不应浪费一次外部审核调用'


def test_malformed_high_score_cannot_pass():
    result = templates.parse_quality_gate('审核返回损坏 score: 99')
    assert result['passed'] is False


def test_explicit_failure_cannot_be_cancelled_by_score(monkeypatch):
    monkeypatch.setattr(templates, 'QUALITY_GATE_ENABLED', True)
    result = templates.parse_quality_gate(
        '{"score":99,"pass":false,"issues":["假设人数被写成实际裁员结果"]}'
    )
    assert result['passed'] is False
    assert templates.should_gate_retry(result) is True


def test_review_binds_actual_source_and_draft(reviewer):
    from prompts.provenance import text_hash
    source, note = '作者只提出可能性。', '作者提出可能性。'
    result = templates.verify_note(note, source, 'general')
    assert result.get('source_sha256') == text_hash(source)
    assert result.get('note_sha256') == text_hash(note)
    assert result.get('coverage') == 'full'


@pytest.mark.parametrize('extra', [
    {'coverage': 'partial'}, {'status': 'unknown'}, {'reviewer_role': 'self_check'},
    {'findings': [{'severity': 'P1', 'reason': '假设变事实'}]},
])
def test_incomplete_or_p1_cannot_claim_pass(extra):
    raw = {'score': 99, 'pass': True, 'issues': [], **extra}
    assert not templates.parse_quality_gate(json.dumps(raw))['passed']


def test_timeout_preserves_partial_coverage(monkeypatch, reviewer):
    from articles import main
    from prompts import semantic_review as sr
    monkeypatch.setattr(sr, 'BLOCK_CHARS', 10)
    count = 0

    def call(*a, **k):
        nonlocal count
        count += 1
        if count == 2:
            raise TimeoutError('超时')
        return {'content': '{"score":96,"pass":true,"issues":[]}'}

    monkeypatch.setattr(main, 'call_ai_summary_with_meta', call)
    result = templates.verify_note('笔记', '源' * 25, 'general')
    assert result['passed'] is False and result['coverage'] == 'partial'
    assert result['status'] == 'needs_review' and len(result['ranges']) == 1


def test_long_source_and_note_complete_cartesian_and_reconcile(monkeypatch, reviewer):
    from prompts import semantic_review as sr
    monkeypatch.setattr(sr, 'BLOCK_CHARS', 10)
    result = templates.verify_note('稿' * 15, '源' * 25, 'general')
    assert len(result['ranges']) == 6
    assert len(reviewer) == 7 and '跨块汇总' in reviewer[-1][1]
    assert result['passed'] and result['coverage'] == 'full'
    assert {r['source']['start'] for r in result['ranges']} == {0, 10, 20}
    assert {r['note']['start'] for r in result['ranges']} == {0, 10}


def test_budget_exhaustion_never_truncates(monkeypatch, reviewer):
    from prompts import semantic_review as sr
    monkeypatch.setattr(sr, 'MAX_REVIEW_CALLS', 1)
    result = templates.verify_note('稿', '源' * 20001, 'general')
    assert not reviewer and result['status'] == 'needs_review'


def test_explicit_incomplete_source_not_certified(reviewer):
    result = templates.verify_note('稿', '【正文截断】原文前段', 'general')
    assert not result['passed'] and not reviewer


def test_disabled_no_calls_and_not_independent_pass(monkeypatch):
    from prompts.semantic_review import admit_candidate
    from articles import main
    monkeypatch.setattr(templates, 'QUALITY_GATE_ENABLED', False)
    monkeypatch.setattr(main, 'call_ai_summary_with_meta', lambda *a, **k: pytest.fail('默认关闭不能新增调用'))
    trace = admit_candidate('稿', '源', 'general')
    assert trace['review']['status'] == 'not_performed'
    assert trace['review']['reviewer']['role'] == 'none'


def test_admission_blocks_before_overwrite_and_retains_candidate(monkeypatch, tmp_path):
    from articles import main
    from prompts import review_rubric
    old = tmp_path / 'good.md'
    old.write_text('旧好稿', encoding='utf-8')
    monkeypatch.setattr(templates, 'QUALITY_GATE_ENABLED', True)
    monkeypatch.setattr(main, 'call_ai_summary_with_meta', lambda *a, **k: None)
    monkeypatch.setattr(main, 'OutputManager', lambda **k: pytest.fail('失败应先于输出管理器'))
    with pytest.raises(ValueError, match='SEMANTIC_REVIEW_FAILED'):
        main.save_summarized_article('新候选', note_type='general', source_text='完整源',
                                     note_path=str(old), overwrite=True)
    assert old.read_text(encoding='utf-8') == '旧好稿'
    rec = review_rubric.pending_reviews()[-1]
    assert rec['evidence']['candidate'] == '新候选'
    assert rec['evidence']['source_text'] == '完整源'


def test_article_rechecks_each_revision_and_stops_after_two(monkeypatch):
    from articles import main
    generated, reviewed = [], []
    monkeypatch.setattr(templates, 'QUALITY_GATE_ENABLED', True)

    def generate(*a, **k):
        generated.append(a)
        return {'content': '稿' + str(len(generated))}

    def verify(note, source, note_type):
        reviewed.append((note, source))
        return {'score': 99, 'passed': False, 'status': 'failed', 'issues': ['假设变事实']}

    monkeypatch.setattr(main, 'call_ai_summary_with_meta', generate)
    monkeypatch.setattr(main, 'verify_note', verify)
    source = '原文' * 5000 + '尾部限制'
    result = main.summarize_content(source, note_type='general')
    assert len(generated) == len(reviewed) == 3
    assert all(s == source for _, s in reviewed)
    assert reviewed[-1][0] == result['summary']
    assert len(result['review_history']) == 3 and not result['semantic_review']['passed']


def test_video_review_uses_full_transcript(monkeypatch):
    from videos import main
    from prompts import templates
    seen = []
    monkeypatch.setattr(templates, 'QUALITY_GATE_ENABLED', True)
    monkeypatch.setattr(main, '_ai_summarize', lambda *a, **k: '稿')
    monkeypatch.setattr(main, 'two_stage_summarize', lambda chunks, summarize, merge: '稿')
    monkeypatch.setattr(main, 'verify_note', lambda note, src, typ: seen.append(src) or {'passed': True})
    source = '原文' * 5000 + '尾部限制'
    main._summarize_segments(source, 'general')
    assert seen == [source]


def test_stale_draft_or_prompt_requires_fresh_review(reviewer):
    from prompts.semantic_review import admit_candidate
    trace = admit_candidate('稿', '源', 'general', {'prompt_sha256': 'generation-v1'})
    count = len(reviewer)
    trace2 = admit_candidate('改稿', '源', 'general', trace)
    assert len(reviewer) == count + 1
    assert trace2['review']['note_sha256'] != trace['review']['note_sha256']
    trace2['prompt_sha256'] = 'generation-v2'
    admit_candidate('改稿', '源', 'general', trace2)
    assert len(reviewer) == count + 2


def test_unchanged_version_reuses_review_without_another_call(reviewer):
    from prompts.semantic_review import admit_candidate
    trace = admit_candidate('稿', '源', 'general', {'prompt_sha256': 'generation-v1'})
    admit_candidate('稿', '源', 'general', trace)
    assert len(reviewer) == 1


def test_technical_failure_not_automatically_retried_at_save(monkeypatch, reviewer):
    from articles import main
    from prompts.semantic_review import admit_candidate
    calls = []
    monkeypatch.setattr(main, 'call_ai_summary_with_meta', lambda *a, **k: calls.append(a) or None)
    report = templates.verify_note('稿', '源', 'general')
    report['generation_prompt_sha256'] = 'v1'
    with pytest.raises(ValueError, match='SEMANTIC_REVIEW_FAILED'):
        admit_candidate('稿', '源', 'general', {'prompt_sha256': 'v1', 'review': report})
    assert len(calls) == 1


def session_report(note='稿', source='源'):
    from prompts.semantic_review import VERSION, digest
    return {'version': VERSION, 'source_sha256': digest(source), 'note_sha256': digest(note),
            'prompt_sha256': digest(templates.QUALITY_GATE_PROMPT), 'note_type': 'general',
            'generation_prompt_sha256': 'actual-dispatch-hash', 'passed': True, 'score': 95,
            'status': 'passed', 'coverage': 'full', 'issues': [], 'findings': [],
            'reviewer': {'role': 'independent_session', 'id': 'reviewer-B', 'author_id': 'author-A'},
            'ranges': [{'source': [0, len(source)], 'note': [0, len(note)]}]}


def test_session_review_allowed_without_external_call(monkeypatch):
    from prompts.semantic_review import admit_candidate
    monkeypatch.setattr(templates, 'QUALITY_GATE_ENABLED', False)
    trace = {'prompt_sha256': 'actual-dispatch-hash', 'review': session_report()}
    result = admit_candidate('稿', '源', 'general', trace)
    assert result['review']['passed'] and result['review']['reviewer']['id'] == 'reviewer-B'


@pytest.mark.parametrize('mutation', ['author', 'partial', 'stale', 'unknown_prompt', 'p1'])
def test_session_self_partial_stale_never_pass(monkeypatch, mutation):
    from prompts.semantic_review import admit_candidate
    monkeypatch.setattr(templates, 'QUALITY_GATE_ENABLED', False)
    report = session_report()
    trace = {'prompt_sha256': 'actual-dispatch-hash', 'review': report}
    if mutation == 'author':
        report['reviewer']['id'] = 'author-A'
    elif mutation == 'partial':
        report['ranges'][0]['source'] = [0, 0]
    elif mutation == 'stale':
        report['note_sha256'] = 'old-note'
    elif mutation == 'unknown_prompt':
        trace['prompt_sha256'] = report['generation_prompt_sha256'] = 'unknown'
    else:
        report['findings'] = [{'severity': 'P1'}]
    with pytest.raises(ValueError, match='SEMANTIC_REVIEW_FAILED'):
        admit_candidate('稿', '源', 'general', trace)


def test_unresolved_cross_block_claims_need_review(monkeypatch, reviewer):
    from articles import main
    monkeypatch.setattr(main, 'call_ai_summary_with_meta', lambda *a, **k: {
        'content': '{"score":99,"pass":true,"issues":[],"claims":["单位未确认"]}'})
    result = templates.verify_note('稿', '源', 'general')
    assert not result['passed'] and result['status'] == 'needs_review'


@pytest.mark.parametrize('report', [[], {'reviewer': None}, {'reviewer': {'role': 'self_check'}}])
def test_explicit_report_cannot_silently_degrade_to_unreviewed(monkeypatch, report):
    from prompts.semantic_review import admit_candidate
    monkeypatch.setattr(templates, 'QUALITY_GATE_ENABLED', False)
    with pytest.raises(ValueError, match='SEMANTIC_REVIEW_FAILED'):
        admit_candidate('稿', '源', 'general', {'review_required': True, 'review': report})


@pytest.mark.parametrize('raw', ['[]', 'null', 'true', '99', None])
def test_non_object_review_json_is_incomplete(raw):
    assert templates.parse_quality_gate(raw)['status'] == 'needs_review'


def isolated_gate(*a, **k):
    return {'passed': True, 'issues': [], 'content_blockers': [], 'review_flags': [],
            'evidence_report': {'findings': [], 'semantic_status': 'not_reviewed'}}


def test_queue_force_cannot_bypass_semantic_failure(monkeypatch):
    from articles import main
    from prompts import verifier
    monkeypatch.setattr(templates, 'QUALITY_GATE_ENABLED', True)
    monkeypatch.setattr(verifier, 'verify_note_mechanical', isolated_gate)
    monkeypatch.setattr(main, 'call_ai_summary_with_meta', lambda *a, **k: None)
    monkeypatch.setattr(main, 'OutputManager', lambda **k: pytest.fail('force不可绕过语义失败'))
    result = main.save_summary_only({'summarized_content': '候选正文', 'source_text': '完整原文',
                                     'note_type': 'general', 'force': True})
    assert not result['success'] and 'SEMANTIC_REVIEW_FAILED' in result['message']


def test_resum_failure_preserves_original_path(monkeypatch, tmp_path):
    import sys
    from scripts import resum_save_batch
    from shared import env
    from prompts import verifier
    from articles import main
    monkeypatch.setattr(templates, 'QUALITY_GATE_ENABLED', True)
    monkeypatch.setattr(env, 'load_env', lambda **k: None)
    monkeypatch.setattr(verifier, 'verify_note_mechanical', isolated_gate)
    monkeypatch.setattr(main, 'call_ai_summary_with_meta', lambda *a, **k: None)
    old, source = tmp_path / 'old.md', tmp_path / 'source.md'
    old.write_text('旧好稿', encoding='utf-8')
    source.write_text('完整原文', encoding='utf-8')
    batch = tmp_path / 'batch.json'
    batch.write_text(json.dumps([{'note_path': str(old), 'source_path': str(source),
                                  'title': '测试', 'note_type': 'general'}]), encoding='utf-8')
    (tmp_path / 'batch_00.md').write_text('候选正文\n【核心主题词】测试', encoding='utf-8')
    monkeypatch.setattr(sys, 'argv', ['resum_save_batch.py', str(batch), str(tmp_path), '--force'])
    assert resum_save_batch.main() == 3
    assert old.read_text(encoding='utf-8') == '旧好稿'


def test_series_failure_stops_before_formatter(monkeypatch, tmp_path):
    from videos import main
    monkeypatch.setattr(templates, 'QUALITY_GATE_ENABLED', True)
    monkeypatch.setattr(main, 'verify_note_mechanical', isolated_gate)
    monkeypatch.setattr(main, 'format_note_with_prompt', lambda **k: pytest.fail('失败不能进入输出格式化'))
    from articles import main as am
    monkeypatch.setattr(am, 'call_ai_summary_with_meta', lambda *a, **k: None)
    with pytest.raises(ValueError, match='SEMANTIC_REVIEW_FAILED'):
        main._save_series_note('稿', str(tmp_path), 'episode', '作者', '', [], 'general', source_text='源')


def test_landing_cannot_publish_legacy_unreviewed_draft_when_enabled(monkeypatch, tmp_path):
    from articles import main
    monkeypatch.setattr(templates, 'QUALITY_GATE_ENABLED', True)
    monkeypatch.setattr(main, 'DRAFT_DIR', str(tmp_path))
    monkeypatch.setattr(main, 'OutputManager', lambda **k: pytest.fail('未审草稿不得通过landing绕行'))
    draft = tmp_path / 'draft.md'
    draft.write_text('旧候选', encoding='utf-8')
    (tmp_path / 'draft.md.meta.json').write_text(json.dumps({'filename': 'draft.md',
                                                           'draft_path': str(draft)}), encoding='utf-8')
    assert main.land_drafts() == 0
    assert draft.read_text(encoding='utf-8') == '旧候选'


@pytest.mark.parametrize('changed', [False, True])
def test_landing_uses_review_and_final_file_binding(monkeypatch, tmp_path, reviewer, changed):
    from articles import main
    from prompts.semantic_review import admit_candidate, digest
    trace = admit_candidate('稿', '源', 'general', {'prompt_sha256': 'v1'})
    count = len(reviewer)
    draft = tmp_path / 'draft.md'
    draft.write_text('格式化稿' + ('改动' if changed else ''), encoding='utf-8')
    meta = {'filename': 'draft.md', 'draft_path': str(draft), 'semantic_body': '稿',
            'source_text': '源', 'note_type': 'general', 'generation_context': trace,
            'formatted_sha256': digest('格式化稿')}
    (tmp_path / 'draft.md.meta.json').write_text(json.dumps(meta), encoding='utf-8')
    monkeypatch.setattr(main, 'DRAFT_DIR', str(tmp_path))
    saved = []

    class Manager:
        def __init__(self, **kwargs):
            pass

        def save_all(self, *a, **k):
            saved.append(a)

    monkeypatch.setattr(main, 'OutputManager', Manager)
    assert main.land_drafts() == (0 if changed else 1)
    assert len(saved) == (0 if changed else 1)
    assert len(reviewer) == count, '已审版本可复用；改动稿应先停下而非偷偷重新发布'
