"""派单/生成过程证据；不参与路由、去重或输出正文。"""
import hashlib

from prompts.classify import classify_note_type_with_evidence
from prompts.templates import NOTE_TEMPLATES, get_note_prompt, source_chars_of, QUALITY_GATE_SELFCHECK


def text_hash(text):
    """对实际读取文本的UTF-8编码求SHA256（换行已由文本读取规范化）。"""
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def legacy_context(note_type=''):
    return {'schema_version': 1, 'note_type': note_type or 'unknown',
            'type_origin': 'legacy', 'reason': 'unknown', 'classifier_version': 'unknown',
            'source_sha256': 'unknown', 'prompt_sha256': 'unknown', 'prompt_scope': 'unknown',
            'review': {'status': 'unknown', 'coverage': 'unknown'}}


def generation_context(title, source, note_type, origin, prompt, scope='dispatch_template'):
    if note_type not in NOTE_TEMPLATES:
        raise ValueError(f'未知模板类型: {note_type}')
    evidence = classify_note_type_with_evidence(title, source or '')
    return {'schema_version': 1, 'note_type': note_type, 'type_origin': origin,
            'reason': evidence['reason'] if origin == 'auto' else ('explicit_type' if origin == 'manual' else 'unknown'),
            'classifier_version': evidence['classifier_version'] if origin == 'auto' else 'unknown',
            'needs_review': evidence['needs_review'] if origin == 'auto' else origin == 'legacy',
            'source_sha256': text_hash(source) if source else 'unknown',
            'source_hash_scope': 'utf8_text_normalized_newlines',
            'prompt_sha256': text_hash(prompt), 'prompt_scope': scope,
            'review': ({'status': 'not_performed', 'coverage': 'none'} if scope == 'dispatch_template'
                       else {'status': 'unknown', 'coverage': 'unknown'})}


def dispatch_fields(title, source, note_type='', origin='auto'):
    if origin not in ('auto', 'manual', 'legacy'):
        origin = 'legacy'
    if not note_type or origin == 'auto':
        note_type = classify_note_type_with_evidence(title, source or '')['note_type']
    prompt = get_note_prompt(note_type, source_chars_of(source or '')) + QUALITY_GATE_SELFCHECK
    return {'note_type': note_type, 'prompt': prompt,
            'generation_context': generation_context(title, source, note_type, origin, prompt)}


def validate_dispatch(entry, source_text=None, check_prompt=True):
    """只校验已有证据；旧数据不猜版本，版本失配不能登记为该次生成。"""
    context = entry.get('generation_context') or {}
    if not context:
        return
    fields = [('note_type', entry.get('note_type'))]
    if check_prompt:
        fields.append(('prompt_sha256', text_hash(entry.get('prompt', ''))))
    for field, actual in fields:
        recorded = context.get(field)
        if recorded and recorded != 'unknown' and recorded != actual:
            raise ValueError(f'PROVENANCE_MISMATCH: {field}')
    recorded = context.get('source_sha256')
    if recorded and recorded != 'unknown':
        if source_text is not None:
            actual = text_hash(source_text)
        else:
            path = entry.get('raw_file') or entry.get('output') or entry.get('source_path')
            try:
                with open(path, encoding='utf-8') as f:
                    actual = text_hash(f.read())
            except (OSError, TypeError, UnicodeError) as exc:
                raise ValueError('PROVENANCE_MISMATCH: source_unavailable') from exc
        if actual != recorded:
            raise ValueError('PROVENANCE_MISMATCH: source_sha256')
