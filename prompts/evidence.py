"""Scoped deterministic evidence checks. No network, model calls, or writes.

Only explicit quote claims / resource bindings / constant arithmetic can block.
Other findings are evidence for review, never a semantic verdict. See
references/note-evidence-contract.md for supported syntax and limitations.
"""
from __future__ import annotations

import ast
import re
from fractions import Fraction

from .provenance import text_hash

EVIDENCE_VERSION = '20261015-scoped-v1'
_PLATFORM_AUTHORS = ('生财有术', 'B站', 'bilibili', 'YouTube', '公众号')
_PUNCT = str.maketrans('，。；：！？“”‘’（）', ',.;:!?""\'\'()')
_QUOTE_HEADING = re.compile(r'金句|原话|原文原句|逐字引文')
_QUOTE_FIELD = re.compile(r'(?:原话|原文原句|逐字引文)\s*[：:]\s*(.+)')
_QUOTED = re.compile(r'[“「"]([^”」"\n]+)[”」"]')
_LINK = re.compile(r'\[([^\]\n]+)\]\((https?://[^\s)]+)\)')
_AUTHOR = re.compile(r'^\s*>?\s*\*{0,2}作者\*{0,2}\s*[：:]\s*([^|\n]+)')
_NUMBER = re.compile(r'(?<![\dA-Za-z_.])\d+(?:\.\d+)?\s*(?:%|％|万元|亿美元|美元|元|万|亿|倍|人|个|次|条|天|小时|分钟)')
_AMOUNT = re.compile(r'(\d+(?:\.\d+)?)(万元|亿美元|美元|元|万|亿|%|％|倍)?')
_MATH = re.compile(r'(?P<lhs>[-+（(]?\d[\d.\s+\-*/×÷()（）万元亿美%％倍,]*?)\s*(?P<op>[=＝≈])\s*(?P<rhs>[+-]?\d+(?:,\d{3})*(?:\.\d+)?\s*(?:万元|亿美元|美元|元|万|亿|%|％|倍)?)')


def _norm(text):
    text = re.sub(r'\*\*([^*]+)\*\*|__([^_]+)__', lambda m: m[1] or m[2], text)
    text = re.sub(r'\s+', '', text.translate(_PUNCT))
    # 可补回ASR的句读；小数点、负号、百分号和运算符不能被消掉。
    text = re.sub(r'[,:;!?"\'()]', '', text)
    return re.sub(r'(?<![0-9])\.|\.(?![0-9])', '', text)


def _lines(text):
    """Preserve line numbers, blank out code, source headers, and metadata."""
    rows = []
    fence = None
    header = bool(re.match(r'^>\s*(?:原始文章内容|标题[：:]|来源[：:])', text)) and '\n---' in text
    for number, original in enumerate(text.splitlines(), 1):
        line = original
        marker = re.match(r'^\s*(`{3,}|~{3,})', line)
        code = bool(marker or fence)
        if marker:
            token = marker[1][0]
            if fence is None:
                fence = token
            elif fence == token:
                fence = None
            line = ''
        elif fence:
            line = ''
        if header:
            if re.fullmatch(r'\s*---\s*', original):
                header = False
            line = ''
        # Metadata has its own author check; it is not numeric / quotation evidence.
        if re.match(r'^\s*(?:>\s*)?(?:\*\*)?(?:作者|来源链接|来源|抓取时间|发布时间|标题|渠道|标签)(?:\*\*)?\s*[：:]', line):
            line = ''
        if re.match(r'^\s*#[^\s#]', line):
            line = ''
        line = re.sub(r'(`+).*?\1', '', line)
        rows.append((number, line, '' if code else original))
    return rows


def _span(line, text, end=None):
    return {'line_start': line, 'line_end': end or line, 'text': text[:240]}


def prose_lines_for_numbers(text):
    """Shared numeric scope for new evidence and the existing numeric anchors."""
    for n, line, _ in _lines(text):
        if line.lstrip().startswith('#') or re.fullmatch(r'\s*(?:19|20)\d{2}[-/.]\d{1,2}[-/.]\d{1,2}(?:[ T].*)?\s*', line):
            continue
        if re.match(r'^\s*(?:(?:python3?|node|npm|pip|uv|curl)\s+|--[A-Za-z][\w-]*(?:[= ]|$))', line):
            continue
        prose = re.sub(r'https?://\S+', '', line)
        prose = re.sub(r'^\s*\d+[.、)]\s*', '', prose)
        yield n, prose


def _paragraphs(rows):
    paragraphs, current = [], []
    for n, line, _ in rows + [(0, '', '')]:
        if line.strip():
            current.append((n, line))
        elif current:
            position_lines = [n for n, text in current for _ in _norm(text)]
            paragraphs.append((''.join(_norm(text) for _, text in current), current[:], position_lines))
            current = []
    return paragraphs


def _locate_quote(quote, paragraphs):
    def span_at(paragraph, pos, length):
        _, rows, line_map = paragraph
        start, end = line_map[pos], line_map[pos + length - 1]
        return _span(start, '\n'.join(text for n, text in rows if start <= n <= end), end)

    fragments = [x for x in re.split(r'(?:…+|\.{3,}|【删节】|\[删节\])', quote) if _norm(x)]
    if len(fragments) <= 1:
        needle = _norm(quote)
        return [span_at(p, p[0].find(needle), len(needle))
                for p in paragraphs if needle and needle in p[0]][:3]
    # Explicit ellipsis allows ordered fragments across paragraphs, never reordering.
    combined = '\n'.join(p[0] for p in paragraphs)
    offset, spans = 0, []
    for fragment in fragments:
        needle = _norm(fragment)
        pos = combined.find(needle, offset)
        if pos < 0:
            return []
        offset = pos + len(needle)
        para_index = combined[:pos].count('\n')
        within = pos - (combined.rfind('\n', 0, pos) + 1)
        spans.append(span_at(paragraphs[para_index], within, len(needle)))
    return spans


def _quotes(rows):
    active_level = None
    mixed = False
    for n, line, _ in rows:
        heading = re.match(r'^\s*(#{1,6})\s+(.+)', line)
        if heading:
            level = len(heading[1])
            if active_level is not None and level <= active_level:
                active_level = None
            if _QUOTE_HEADING.search(heading[2]):
                active_level = level
                mixed = bool(re.search(r'数据|事实|转述|改写|意译', heading[2]))
            continue
        field = _QUOTE_FIELD.search(line)
        if field and not re.search(r'不是|并非|不属于', line[:field.start()]):
            quoted = _QUOTED.findall(field[1])
            for text in quoted or [field[1].strip().strip('“”「」"')]:
                yield n, text
        elif active_level is not None and line.strip():
            callout = re.match(r'^\s*>\s*\[!quote\]\s*(.*)', line, re.I)
            if callout:
                if callout[1].strip():
                    yield n, callout[1].strip()
                continue
            if mixed:
                continue
            quoted = _QUOTED.findall(line)
            if quoted:
                for text in quoted:
                    yield n, text
            elif re.match(r'^\s*(?:>|[-*]\s)', line) and '[!' not in line:
                yield n, re.sub(r'^\s*(?:>|[-*])\s*', '', line).strip('“”「」"')


def _bindings(rows):
    out = {}
    for n, line, _ in rows:
        pairs = [(m[1], m[2]) for m in _LINK.finditer(line)]
        plain = re.fullmatch(r'\s*(?:[-*]\s*)?([^：:\[\]\n]{1,60})[：:]\s*(https?://\S+)\s*', line)
        if plain:
            pairs.append((plain[1], plain[2]))
        for name, url in pairs:
            name = name.strip().strip('*`')
            out.setdefault(name, []).append((url.rstrip('。，；'), _span(n, line)))
    return out


def _quantity(expression):
    """Parse bounded constant arithmetic; return exact value and money dimension.

    No eval, functions, powers, names, or exponent literals. Dollar and yuan are
    distinct dimensions: currency conversion cannot pass without a supplied rate.
    """
    expression = expression.translate(str.maketrans('×÷（）', '*/()')).strip()
    expression = re.sub(r'(?<=\d),(?=\d{3}(?:\D|$))', '', expression)
    if len(expression) > 160:
        raise ValueError('expression_too_long')
    values = {}

    def replace(match):
        number, unit = match.groups()
        if len(number) > 24:
            raise ValueError('number_too_long')
        factor = {'万元': 10000, '万': 10000, '亿': 100000000, '亿美元': 100000000,
                  '%': Fraction(1, 100), '％': Fraction(1, 100)}.get(unit, 1)
        dimension = ('cny', 1) if unit in ('元', '万元') else (('usd', 1) if unit in ('美元', '亿美元') else ('scalar', 0))
        key = f'q{len(values)}'
        values[key] = (Fraction(number) * factor, dimension)
        return key

    encoded = _AMOUNT.sub(replace, expression)
    if re.search(r'[^q0-9\s+\-*/()]', encoded):
        raise ValueError('unsupported_syntax')
    tree = ast.parse(encoded, mode='eval')
    if len(list(ast.walk(tree))) > 64:
        raise ValueError('expression_too_complex')

    def visit(node):
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Name) and node.id in values:
            return values[node.id]
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value, dim = visit(node.operand)
            return (-value if isinstance(node.op, ast.USub) else value), dim
        if isinstance(node, ast.BinOp):
            left, ld = visit(node.left)
            right, rd = visit(node.right)
            if isinstance(node.op, (ast.Add, ast.Sub)) and ld == rd:
                return (left + right if isinstance(node.op, ast.Add) else left - right), ld
            if isinstance(node.op, ast.Mult):
                if ld[1] == 0:
                    return left * right, rd
                if rd[1] == 0:
                    return left * right, ld
            if isinstance(node.op, ast.Div) and right:
                if ld == rd:
                    return left / right, ('scalar', 0)
                if rd[1] == 0:
                    return left / right, ld
            raise ValueError('unsupported_dimension_or_operator')
        raise ValueError('unsupported_syntax')

    return visit(tree)


def check_note_evidence(note: str, source: str = '', source_author: str = '') -> dict:
    """Return scoped findings bound to exact input hashes and original line spans."""
    note_rows, source_rows = _lines(note), _lines(source)
    paragraphs = _paragraphs(source_rows)
    findings = []
    incomplete = bool(re.search(r'【源不完整】|【正文截断】|正文未获取|源文本截断', source))

    def add(code, decision, line, text, reason, spans=None, **details):
        findings.append({'code': code, 'decision': decision, 'note': _span(line, text),
                         'source': spans or [], 'reason': reason, 'details': details})

    if not source.strip():
        add('source_unavailable', 'review', 1, '', '缺源，只能检查笔记内部可核算常量；不能判定原话不存在')
    elif incomplete:
        add('source_incomplete', 'review', 1, '', '源明确标访问范围不完整，找不到的引文不能硬判不存在')

    corrections = []
    numeric_prose = dict(prose_lines_for_numbers(note))
    for n, line, _ in note_rows:
        if re.search(r'ASR\s*修正|转录修正', line, re.I):
            corrections.append((n, line))
            add('asr_correction', 'review', n, line, '修正需核对实体/含义，不以模糊匹配自动放行', correction=line)

    for n, quote in _quotes(note_rows):
        if not source.strip():
            continue
        translated = re.fullmatch(r'(.+[A-Za-z].*)[（(]([^()（）]*[\u4e00-\u9fff][^()（）]*)[）)]', quote)
        if translated:
            add('quote_translation', 'review', n, quote, '附加中文译释需语义核验；逐字检查只定位原语言片段', translation=translated[2])
            quote = translated[1].strip()
        located = _locate_quote(quote, paragraphs)
        if located:
            add('quote_located', 'supported', n, quote, '规范化后的原话可定位；不证明说话者/语义归属', located)
        else:
            # A correction record is evidence to inspect, not a license for all quotes.
            relevant = any(any(token in quote for token in _QUOTED.findall(line)) for _, line in corrections)
            add('quote_asr_review' if relevant else ('quote_source_incomplete' if incomplete else 'quote_not_found'), 'review' if relevant or incomplete else 'block', n, quote,
                '有对应ASR修正记录，需语义核对' if relevant else '明确声称原话但全文无连续匹配（跨段须显式删节）')

    bindings = _bindings(source_rows)
    source_urls = {url for values in bindings.values() for url, _ in values}
    for name, values in _bindings(note_rows).items():
        expected = bindings.get(name, [])
        for url, span in values:
            if url in {u for u, _ in expected}:
                add('resource_bound', 'supported', span['line_start'], span['text'], '名称与源内入口对应', [s for u, s in expected if u == url])
            elif expected and len({u for u, _ in expected}) == 1 and url in source_urls:
                add('resource_mismatch', 'block', span['line_start'], span['text'], '同名资源唯一入口与笔记入口冲突', [s for _, s in expected], expected_urls=[u for u, _ in expected], actual_url=url)
            else:
                add('resource_unverified', 'review', span['line_start'], span['text'], '未建立名称与入口绑定，不推断链接内容/可用性')

    for n, line, _ in source_rows:
        attachment = re.search(r'附件[：:]\s*([^，,（(\s]+)', line)
        mentions = [(nn, row) for nn, row, _ in note_rows if attachment and attachment[1] in row]
        if attachment and mentions and not any(
                attachment[1] in row and re.search(r'未读|未读取|未获取', row) for _, row, _ in note_rows):
            mention_line, mention = mentions[0]
            add('attachment_unread', 'review', mention_line, mention, '仅有附件名称或未读声明，笔记需保留访问范围', [_span(n, line)])

    def math_line(line):
        line = re.sub(r'\*\*([^*]+)\*\*', r'\1', line)
        return line.replace('−', '-')

    source_equations = {_norm(m[0]): _span(n, line) for n, line, _ in source_rows for m in _MATH.finditer(math_line(line))}
    for n, line, _ in note_rows:
        calculation = bool(re.search(r'复算|重算|计算|验算|自洽', line))
        matches = list(_MATH.finditer(math_line(line)))
        if calculation and not matches and re.search(r'[=＝≈]|自洽', line):
            add('calculation_unparsed', 'review', n, line, '未能解析常量输入/公式/结果，不背书自洽')
        for m in matches:
            if not re.search(r'[+\-*/×÷]', m['lhs']):
                if calculation:
                    add('calculation_unparsed', 'review', n, line, '计算声明未提供支持的完整常量算式，不能核验')
                continue  # 等号定义、年份等不作运算。
            original = source_equations.get(_norm(m[0]))
            own_check = bool(re.search(r'笔记者|复算|重算|验算|自洽', line))
            if '笔记者' not in line and re.search(r'作者(?:计算|复算)|未(?:经)?(?:复算|验算|验证)', line):
                own_check = False
            try:
                actual, dimension = _quantity(m['lhs'])
                reported, result_dimension = _quantity(m['rhs'])
                if dimension != result_dimension:
                    raise ValueError('result_dimension_mismatch')
                # ≈ follows the last displayed decimal place; = is exact.
                tolerance = Fraction(0)
                if m['op'] == '≈':
                    raw = re.search(r'-?\d+(?:\.(\d+))?', m['rhs'].replace(',', ''))
                    places = len(raw[1] or '')
                    unit = _AMOUNT.fullmatch(m['rhs'].strip().lstrip('+-').replace(',', ''))
                    multiplier = {'万元': 10000, '万': 10000, '亿': 100000000, '亿美元': 100000000,
                                  '%': Fraction(1, 100), '％': Fraction(1, 100)}.get(unit[2] if unit else '', 1)
                    tolerance = Fraction(1, 2 * 10 ** places) * multiplier
                correct = abs(actual - reported) <= tolerance
                counterexample = bool(re.search(r'错误反例|错误算式|请勿按此', line)) and not own_check
                decision = 'supported' if correct else ('observed' if counterexample else ('review' if original and not own_check else 'block'))
                code = 'calculation_checked' if correct else ('calculation_counterexample' if counterexample else ('source_calculation_questionable' if decision == 'review' else 'calculation_wrong'))
                add(code, decision, n, line, '只核常量运算，不验证输入口径/因果/现实真实性', [original] if original else [],
                    expression=m['lhs'], claimed=m['rhs'], computed=str(actual), tolerance=str(tolerance), source_restatement=bool(original))
            except (ValueError, SyntaxError, ZeroDivisionError, RecursionError) as exc:
                add('calculation_unparsed', 'review', n, line, '不支持的公式/单位/除零，需核验', error=str(exc))

        # Only quantities in prose: exclude list ordinals, headings, URLs, inline code.
        if n not in numeric_prose:
            continue
        prose = numeric_prose[n]
        for match in _NUMBER.finditer(prose):
            number = re.match(r'\d+(?:\.\d+)?', match[0])[0]
            sources = []
            for k, row, _ in source_rows:
                hit = re.search(r'(?<![\d.])' + re.escape(number) + r'(?![\d.])', row)
                if hit:
                    sources.append(_span(k, row[max(0, hit.start() - 80):hit.end() + 120]))
                if len(sources) >= 3:
                    break
            sensitive = any(re.search(r'只指|仅|假设|如果|可能|阶段|净利|GMV|分母|口径|税|预测', s['text'], re.I) for s in sources)
            same_quantity = any(_norm(match[0]) in _norm(s['text']) for s in sources)
            decision = 'review' if not sources or sensitive or not same_quantity else 'observed'
            add('numeric_context' if sources else 'unsupported_number', decision, n, line,
                '保留数值邻文；词面出现不证明对象/期间/分母/假设正确' if sources else '新数量没有源支持，先复核，不能直接判编造', sources, value=match[0])
        for before, after in [('发布前', '发布后'), ('上线前', '上线后')]:
            if before in line and after in source or after in line and before in source:
                spans = [_span(k, row) for k, row, _ in source_rows if before in row or after in row][:3]
                add('temporal_relation', 'review', n, line, '前后关系存在反向措辞，可能为不同事件，需语义核对', spans)

    authors = [(n, _AUTHOR.match(original)[1].strip().strip('*')) for n, _, original in source_rows if _AUTHOR.match(original)]
    explicit_author = authors[0][1] if len(set(a for _, a in authors)) == 1 else ''
    for n, _, original in note_rows:
        field = _AUTHOR.match(original)
        if field and field[1].strip().strip('*') in _PLATFORM_AUTHORS and field[1].strip().strip('*') != explicit_author:
            add('author_platform_label', 'block', n, original, '平台名不能充当真实作者；应移入平台栏，作者未知则明确未知')
    expected_author = explicit_author or (source_author if source_author not in _PLATFORM_AUTHORS else '')
    if explicit_author and source_author and explicit_author != source_author:
        add('author_metadata_conflict', 'review', 1, source_author, '调用方作者与源内元数据冲突，以源内明确作者核验，需检查抓取元数据',
            [_span(k, a) for k, a in authors])
    if expected_author:
        for n, _, original in note_rows:
            author = _AUTHOR.match(original)
            if author and author[1].strip().strip('*') != expected_author:
                add('author_mismatch', 'block', n, original, '明确作者字段与来源作者元数据冲突（不校验书作者/嘉宾）',
                    [_span(k, a) for k, a in authors], expected_author=expected_author)
    else:
        for n, _, original in note_rows:
            if _AUTHOR.match(original):
                add('author_unverified', 'review', n, original, '源未提供明确作者元数据，不能将模型作者字段视为已核验')
    return {'version': EVIDENCE_VERSION, 'source_sha256': text_hash(source) if source else 'unknown',
            'note_sha256': text_hash(note), 'semantic_status': 'not_reviewed',
            'scope': 'explicit_quotes_resource_bindings_constant_arithmetic_and_review_candidates',
            'findings': findings}


def resolve_source_author(source, supplied=''):
    """只用明确元数据；不从正文猜人名，不把平台当作者。"""
    authors = {m[1].strip().strip('*') for _, _, original in _lines(source) if (m := _AUTHOR.match(original))}
    if len(authors) == 1:
        return next(iter(authors))
    return '' if supplied in _PLATFORM_AUTHORS else supplied


def attach_evidence(context, report, note_type=''):
    """保存范围检查的证据，不能改写完整语义审核状态。"""
    from .provenance import legacy_context
    out = dict(context or legacy_context(note_type))
    out['evidence_check'] = report
    return out


def evidence_issues(report):
    return [f"证据缺陷[{f['code']}] 第{f['note']['line_start']}行：{f['reason']}"
            for f in report['findings'] if f['decision'] == 'block']


def evidence_review_flags(report):
    return [f"证据待复核[{f['code']}] 第{f['note']['line_start']}行：{f['reason']}"
            for f in report['findings'] if f['decision'] == 'review']
