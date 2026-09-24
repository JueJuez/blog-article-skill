"""B站 cookie 轮换：克隆会话已死时立即强制重克隆默认 profile 重试（2026-09-24）。

核心行为：rotate_bili_cookie_if_dead 首次 CDP 提取未拿到有效 cookie → 调
shared.cdp_session.force_refresh_clone() 立即重克隆默认 profile → 重试一次提取。
克隆本身新鲜/首次即成功 → 不误触发重克隆（避免每次轮换都关用户 Chrome）。

全部用 mock 隔离真实浏览器与 robocopy，不触达本机 Chrome、不写 .env / 轮换 json。
"""
import videos.fetch as fetch_mod


VALID = "SESSDATA=valid_fresh_cookie"
DEAD = "SESSDATA=stale_cookie"


def _patch_validate(monkeypatch):
    """nav 校验：仅 VALID 通过，其余（含 DEAD、None）判死。"""
    monkeypatch.setattr(fetch_mod, "validate_bilibili_cookies", lambda c: c == VALID)


def _patch_bookkeeping(monkeypatch):
    """隔离落盘副作用：不写 .env / 不写轮换 json；当前 env cookie 设为 DEAD 确保走 CDP 路径。"""
    monkeypatch.setattr(fetch_mod, "_bili_record_rotation_event", lambda *a, **k: None)
    monkeypatch.setattr(fetch_mod, "_persist_cookie_to_env", lambda c: True)
    monkeypatch.setattr(fetch_mod, "_bili_build_cookies_from_env", lambda: DEAD)


def test_first_extract_ok_no_clone(monkeypatch):
    _patch_validate(monkeypatch)
    _patch_bookkeeping(monkeypatch)
    extracts, clones = {"n": 0}, {"n": 0}

    def fake_extract(wait_s=None):
        extracts["n"] += 1
        return VALID

    def fake_clone():
        clones["n"] += 1
        return True

    monkeypatch.setattr(fetch_mod, "_bili_extract_cookies_cdp", fake_extract)
    monkeypatch.setattr("shared.cdp_session.force_refresh_clone", fake_clone)

    state, fresh = fetch_mod.rotate_bili_cookie_if_dead(trigger="manual", force=True)

    assert state == "rotated"
    assert fresh == VALID
    assert extracts["n"] == 1
    assert clones["n"] == 0  # 首次成功 → 不触发重克隆


def test_first_fail_then_clone_retry_ok(monkeypatch):
    _patch_validate(monkeypatch)
    _patch_bookkeeping(monkeypatch)
    extracts, clones = {"n": 0}, {"n": 0}

    def fake_extract(wait_s=None):
        extracts["n"] += 1
        return None if extracts["n"] == 1 else VALID

    def fake_clone():
        clones["n"] += 1
        return True

    monkeypatch.setattr(fetch_mod, "_bili_extract_cookies_cdp", fake_extract)
    monkeypatch.setattr("shared.cdp_session.force_refresh_clone", fake_clone)

    state, fresh = fetch_mod.rotate_bili_cookie_if_dead(trigger="manual", force=True)

    assert state == "rotated"
    assert fresh == VALID
    assert clones["n"] == 1    # 克隆死 → 立即触发一次重克隆
    assert extracts["n"] == 2  # 重克隆后重试一次提取


def test_clone_fails_no_second_extract(monkeypatch):
    _patch_validate(monkeypatch)
    _patch_bookkeeping(monkeypatch)
    extracts, clones = {"n": 0}, {"n": 0}

    def fake_extract(wait_s=None):
        extracts["n"] += 1
        return None

    def fake_clone():
        clones["n"] += 1
        return False

    monkeypatch.setattr(fetch_mod, "_bili_extract_cookies_cdp", fake_extract)
    monkeypatch.setattr("shared.cdp_session.force_refresh_clone", fake_clone)

    state, fresh = fetch_mod.rotate_bili_cookie_if_dead(trigger="manual", force=True)

    assert state == "failed"
    assert fresh is None
    assert clones["n"] == 1
    assert extracts["n"] == 1  # 重克隆失败 → 不再重试提取


def test_both_attempts_fail(monkeypatch):
    _patch_validate(monkeypatch)
    _patch_bookkeeping(monkeypatch)
    extracts, clones = {"n": 0}, {"n": 0}

    def fake_extract(wait_s=None):
        extracts["n"] += 1
        return None

    def fake_clone():
        clones["n"] += 1
        return True

    monkeypatch.setattr(fetch_mod, "_bili_extract_cookies_cdp", fake_extract)
    monkeypatch.setattr("shared.cdp_session.force_refresh_clone", fake_clone)

    state, fresh = fetch_mod.rotate_bili_cookie_if_dead(trigger="manual", force=True)

    assert state == "failed"
    assert fresh is None
    assert clones["n"] == 1
    assert extracts["n"] == 2  # 重克隆成功但第二次提取仍失败


def test_force_refresh_clone_callable():
    from shared.cdp_session import force_refresh_clone
    assert callable(force_refresh_clone)
