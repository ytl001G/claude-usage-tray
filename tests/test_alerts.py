import pytest

from tray.alerts import Alerts
from tray.history import WEEK, Projection
from tray.usage_api import Usage

DAY = 86400
RESETS = 1_800_000_000.0
CFG = {"alert_thresholds": [80, 90, 95], "relogin_notice_days": 3}


def U(week, five=10, resets=RESETS, five_resets=RESETS - 2 * DAY):
    return Usage(at=0, week=week, week_resets=resets, five=five, five_resets=five_resets)


OK = Projection("ok", 10, None, None, 60)


def make(tmp_path):
    return Alerts(CFG, path=tmp_path / "state.json")


def test_below_thresholds_nothing(tmp_path):
    assert make(tmp_path).check_usage(U(79), OK) == []


def test_threshold_fires_once_per_week(tmp_path):
    a = make(tmp_path)
    first = a.check_usage(U(81), OK)
    assert len(first) == 1 and "81%" in first[0].title and first[0].color == "warn"
    assert a.check_usage(U(82), OK) == []
    assert len(a.check_usage(U(90), OK)) == 1


def test_jump_over_several_thresholds_shows_one(tmp_path):
    a = make(tmp_path)
    out = a.check_usage(U(96), OK)
    assert len(out) == 1 and out[0].color == "danger"
    assert a.check_usage(U(97), OK) == []


def test_new_week_rearms_thresholds(tmp_path):
    a = make(tmp_path)
    a.check_usage(U(85), OK)
    assert len(a.check_usage(U(85, resets=RESETS + WEEK), OK)) == 1


def test_state_survives_restart(tmp_path):
    make(tmp_path).check_usage(U(85), OK)
    assert make(tmp_path).check_usage(U(85), OK) == []


def test_projection_flip_to_exhaust(tmp_path):
    a = make(tmp_path)
    ex = Projection("exhaust", 30, 30, RESETS - 3 * 3600, 100)
    assert a.check_usage(U(50), OK, now=NOW) == []
    out = a.check_usage(U(50), ex, now=NOW)
    assert len(out) == 1 and out[0].title == "소진 예상" and "3시간" in out[0].lines[1]
    assert a.check_usage(U(51), ex, now=NOW + 300) == []          # still exhaust: no repeat


def test_projection_flapping_alerts_at_most_daily(tmp_path):
    """The 24h window slides, so the status can cross the line several times a day."""
    a = make(tmp_path)
    ex = Projection("exhaust", 30, 30, RESETS - 3600, 100)
    a.check_usage(U(50), ex, now=NOW)
    for i in range(1, 6):
        a.check_usage(U(50), OK, now=NOW + i * 3600)
        assert a.check_usage(U(50), ex, now=NOW + i * 3600 + 60) == []
    a.check_usage(U(50), OK, now=NOW + DAY)
    assert len(a.check_usage(U(50), ex, now=NOW + DAY + 60)) == 1


def test_threshold_and_exhaust_in_one_poll_merge(tmp_path):
    a = make(tmp_path)
    ex = Projection("exhaust", 30, 30, RESETS - 3600, 100)
    out = a.check_usage(U(91), ex, now=NOW)
    assert len(out) == 1
    assert "91%" in out[0].title and "소진" in out[0].lines[0]
    assert a.check_usage(U(91), ex, now=NOW + 60) == []


def test_collecting_does_not_reset_projection_state(tmp_path):
    a = make(tmp_path)
    ex = Projection("exhaust", 30, 30, RESETS - 3600, 100)
    a.check_usage(U(50), ex, now=NOW)
    a.check_usage(U(50), Projection("collecting", None, None, None, None), now=NOW + DAY)
    assert a.check_usage(U(50), ex, now=NOW + DAY + 60) == []


def test_five_hour_once_per_window(tmp_path):
    a = make(tmp_path)
    out = a.check_usage(U(50, five=91), OK)
    assert len(out) == 1 and "5시간" in out[0].title
    assert a.check_usage(U(50, five=95), OK) == []
    assert len(a.check_usage(U(50, five=92, five_resets=RESETS - DAY), OK)) == 1


# ---------------------------------------------------------------- login

NOW = 1_700_000_000.0


def test_login_quiet_before_notice_period(tmp_path):
    assert make(tmp_path).check_login(NOW + 4 * DAY, now=NOW) == []


def test_login_daily_in_notice_period(tmp_path):
    a = make(tmp_path)
    exp = NOW + 2.5 * DAY
    out = a.check_login(exp, now=NOW)
    assert len(out) == 1 and out[0].login and "D-" in out[0].title
    assert a.check_login(exp, now=NOW + 3600) == []
    assert len(a.check_login(exp, now=NOW + DAY)) == 1


def test_login_every_three_hours_on_last_day(tmp_path):
    a = make(tmp_path)
    exp = NOW + 20 * 3600
    assert len(a.check_login(exp, now=NOW)) == 1
    assert a.check_login(exp, now=NOW + 2 * 3600) == []
    out = a.check_login(exp, now=NOW + 3 * 3600)
    assert len(out) == 1 and out[0].color == "danger"


def test_login_expired(tmp_path):
    out = make(tmp_path).check_login(NOW - 10, now=NOW)
    assert len(out) == 1 and out[0].title == "로그인 만료" and out[0].login


def test_relogin_clears_state(tmp_path):
    a = make(tmp_path)
    a.check_login(NOW + DAY, now=NOW)
    assert a.check_login(NOW + 28 * DAY, now=NOW + 60) == []   # logged in again
    assert len(a.check_login(NOW + 2 * DAY, now=NOW + 120)) == 1


def test_login_unknown_expiry(tmp_path):
    assert make(tmp_path).check_login(None, now=NOW) == []


# ---------------------------------------------------------------- failures

def test_failures_after_three_then_daily(tmp_path):
    a = make(tmp_path)
    assert a.check_failures(2, now=NOW) == []
    out = a.check_failures(3, now=NOW)
    assert len(out) == 1 and not out[0].login
    assert a.check_failures(4, now=NOW + 3600) == []
    assert len(a.check_failures(9, now=NOW + DAY)) == 1
    a.check_failures(0, now=NOW + DAY + 1)                  # recovered
    assert len(a.check_failures(3, now=NOW + DAY + 2)) == 1


def test_auth_failures_offer_login(tmp_path):
    out = make(tmp_path).check_failures(3, auth=True, now=NOW)
    assert out[0].login and "로그인" in " ".join(out[0].lines)


def test_corrupt_state_file_is_ignored(tmp_path):
    (tmp_path / "state.json").write_text("[1, 2]", encoding="utf-8")
    a = make(tmp_path)
    assert len(a.check_usage(U(85), OK)) == 1


def test_login_d_day_matches_the_widget(tmp_path):
    from datetime import datetime
    from tray.text import d_day
    now = datetime(2026, 10, 9, 22, 0).timestamp()
    exp = datetime(2026, 10, 11, 23, 0).timestamp()
    out = make(tmp_path).check_login(exp, now=now)
    assert d_day(exp, now) in out[0].title          # both say D-2


def test_expired_login_reminds_once_a_day(tmp_path):
    a = make(tmp_path)
    assert len(a.check_login(NOW - 10, now=NOW)) == 1
    assert a.check_login(NOW - 10, now=NOW + 3 * 3600) == []
    assert len(a.check_login(NOW - 10, now=NOW + DAY)) == 1


def test_failure_alert_not_repeated_when_login_alert_covers_it(tmp_path):
    a = make(tmp_path)
    assert a.check_failures(5, auth=True, login_expired=True, now=NOW) == []


def test_state_write_errors_are_swallowed(tmp_path, monkeypatch):
    a = make(tmp_path)

    def boom(*a, **k):
        raise OSError("locked")

    monkeypatch.setattr(a, "_write", boom)
    assert len(a.check_usage(U(85), OK)) == 1
