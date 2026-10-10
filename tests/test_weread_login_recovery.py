"""登录恢复与列表计数回归；全程 mock，不发外部请求。"""
import json
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from monitors import weread as w

@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(w, 'QUOTA_PATH', str(tmp_path / 'quota.json'))
    monkeypatch.setattr(w.time, 'sleep', lambda _: None)
    monkeypatch.setattr(w, 'WEREAD_HOURLY_QUOTA', 6)
    monkeypatch.setattr(w, 'WEREAD_DAILY_QUOTA', 25)

def lister(monkeypatch, response):
    page = SimpleNamespace(evaluate=Mock(return_value=response))
    monkeypatch.setattr(w, '_find_or_open_weread_page', lambda s: page)
    return w.WereadLister(SimpleNamespace()), page

# 签名失败在 fetch 前发生，不能消耗列表额度。
def test_signature_failure_does_not_charge(monkeypatch):
    client, page = lister(monkeypatch, {'ok': False, 'sent': False, 'err': 'no_signature:test'})
    with pytest.raises(RuntimeError, match='no_signature'):
        client.fetch_list_raw('MP_WXS_100')
    assert w.quota_today() == 0
    assert client.request_stats == dict(attempted=1, sent=0, not_sent=1, uncertain=0)

# 发出的认证失败/HTTP失败/网络失败都是请求，均须计数。
@pytest.mark.parametrize('response,raises', [
    ({'ok': True, 'sent': True, 'status': 200, 'body': '{"errCode":-2010}'}, False),
    ({'ok': True, 'sent': True, 'status': 503, 'body': ''}, True),
    ({'ok': False, 'sent': True, 'err': 'network'}, True),
])
def test_sent_failures_charge_once(monkeypatch, response, raises):
    client, page = lister(monkeypatch, response)
    if raises:
        with pytest.raises(RuntimeError): client.fetch_list_raw('MP_WXS_100')
    else:
        assert client.fetch_list_raw('MP_WXS_100')['errCode'] == -2010
    assert w.quota_today() == 1 and client.request_stats['sent'] == 1
    assert page.evaluate.call_count == 1

# CDP丢连接时不能宣称未发送，也不能免费重试。
def test_interrupted_evaluate_is_uncertain(monkeypatch):
    client, page = lister(monkeypatch, {})
    page.evaluate.side_effect = RuntimeError('connection lost')
    with pytest.raises(RuntimeError, match='发送状态未知'):
        client.fetch_list_raw('MP_WXS_100')
    assert w.quota_today() == 1 and client.request_stats['uncertain'] == 1

# 配额前置拦截不触发任何页内列表 fetch。
def test_blocked_request_does_not_send(monkeypatch):
    client, page = lister(monkeypatch, {})
    w.quota_record(6)
    with pytest.raises(w.WereadQuotaExhausted): client.fetch_list_raw('MP_WXS_100')
    page.evaluate.assert_not_called()
    assert w.quota_today() == 6 and client.request_stats['not_sent'] == 1

# 无→有和已有值变化都能检测；本地等待不触发列表探活或配额。
@pytest.mark.parametrize('before', [(), (('wr_skey', '.weread.qq.com', '/', 'old'),)])
def test_cookie_change_is_local(monkeypatch, before, capsys):
    session = SimpleNamespace(context=SimpleNamespace(cookies=Mock(side_effect=[
        [], [{'name': 'wr_skey', 'domain': '.weread.qq.com', 'path': '/', 'value': 'private-value'}]])))
    fetch = Mock(side_effect=AssertionError('must not fetch while waiting'))
    monkeypatch.setattr(w.WereadLister, 'fetch_list_raw', fetch)
    assert w._wait_login_cookie_change(session, before, 1)
    assert w.quota_today() == 0
    fetch.assert_not_called()
    assert 'private-value' not in capsys.readouterr().err

# 静态cookie一直存在时不触发变化；不反复网络验证。
def test_unchanged_cookie_times_out_locally(monkeypatch):
    cookie = {'name': 'wr_skey', 'value': 'same'}
    session = SimpleNamespace(context=SimpleNamespace(cookies=lambda _: [cookie]))
    before = w._login_cookie_fingerprint(session)
    times = iter([0, 0, 0, 2, 2])
    monkeypatch.setattr(w.time, 'monotonic', lambda: next(times))
    assert not w._wait_login_cookie_change(session, before, 1)
    assert w.quota_today() == 0

# 登录恢复后的业务第一页直接产出条目；失败一次后停手，不按5秒重试。
@pytest.mark.parametrize('retry_status', ['ok', 'auth', 'quota'])
def test_recovery_uses_business_result_once(monkeypatch, retry_status):
    page = SimpleNamespace()
    client = SimpleNamespace(page=page)
    monkeypatch.setattr(w, 'WereadLister', lambda _: client)
    monkeypatch.setattr(w, 'detect_captcha', lambda _: False)
    monkeypatch.setattr(w, 'has_refresh_token', lambda _: False)
    monkeypatch.setattr(w, 'trigger_weread_relogin', lambda *a: True)
    monkeypatch.setattr(w, '_find_or_open_weread_page', lambda _: page)
    monkeypatch.setattr(w, 'WEREAD_AUTO_RELOGIN', True)
    monkeypatch.setattr(w, 'WEREAD_RELOGIN_WAIT', 1)
    first = ([], [], {'status': 'auth', 'detail': 'errCode=-2010'})
    last = ([{'id': 'fresh'}] if retry_status == 'ok' else [], [],
            {'status': retry_status, 'detail': 'limit' if retry_status == 'quota' else 'errCode=-2010',
             'new_n': 1, 'is_first': False})
    process = Mock(side_effect=[first, last])
    monkeypatch.setattr(w, '_process_one_account', process)
    entries = [{'name': '中金点睛', 'book_id': 'MP_WXS_100', 'category': ''}]
    items, health = w.discover_weread({'sources': {}}, entries, session=SimpleNamespace())
    assert process.call_count == 2 and health['relogin']
    if retry_status == 'ok':
        assert items == [{'id': 'fresh'}] and health['errors'] == []
    elif retry_status == 'quota':
        assert health['quota_exhausted'] and '已检测到登录 cookie 变化' in health['errors'][0][2]
    else:
        assert health['errors'][0][1] == 'auth'
