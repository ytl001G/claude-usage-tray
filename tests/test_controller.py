import urllib.error

import pytest

from tray.alerts import Alerts
from tray.controller import Controller
from tray.history import WEEK, History
from tray.usage_api import AuthError, Credentials, Usage

DAY = 86400
NOW = 1_800_000_000.0
RESETS = NOW + 2 * DAY
CFG = {"poll_minutes": 5, "alert_thresholds": [80, 90, 95], "relogin_notice_days": 3}


def good_creds(expires_in=4 * 3600, login_left=20 * DAY):
    return Credentials("tok", NOW + expires_in, NOW + login_left)


def U(week=50, at=NOW, source="api"):
    return Usage(at=at, week=week, week_resets=RESETS, five=10, five_resets=NOW + 3600, source=source)


class Fake:
    """Scriptable stand-ins for the network, credentials file and claude CLI."""

    def __init__(self, creds=None, fetch=None, refresh=True, snapshot=None):
        self.creds = [creds or good_creds()]
        self.fetch_results = list(fetch or [U()])
        self.refresh_result = refresh
        self.snapshot = snapshot
        self.fetches = 0
        self.refreshes = 0

    def read_credentials(self):
        return self.creds[0] if len(self.creds) == 1 else self.creds.pop(0)

    def fetch_usage(self, creds):
        self.fetches += 1
        r = self.fetch_results.pop(0) if len(self.fetch_results) > 1 else self.fetch_results[0]
        if isinstance(r, Exception):
            raise r
        return r

    def refresh_token(self):
        self.refreshes += 1
        return self.refresh_result

    def read_snapshot(self):
        return self.snapshot


@pytest.fixture
def clock():
    return {"t": NOW}


def make(tmp_path, fake, clock):
    return Controller(
        CFG, History(tmp_path / "h.jsonl"), Alerts(CFG, path=tmp_path / "s.json"),
        read_credentials=fake.read_credentials, fetch_usage=fake.fetch_usage,
        refresh_token=fake.refresh_token, read_snapshot=fake.read_snapshot,
        clock=lambda: clock["t"],
    )


def test_success(tmp_path, clock):
    f = Fake()
    c = make(tmp_path, f, clock)
    view, alerts = c.poll()
    assert view.usage.week == 50 and view.color == "ok"
    assert f.refreshes == 0 and c.failures == 0
    assert len(c.history.rows) == 1


def test_no_credentials(tmp_path, clock):
    f = Fake()
    f.creds = [None]
    c = make(tmp_path, f, clock)
    view, _ = c.poll()
    assert view.usage is None and "로그인" in view.message
    assert f.fetches == 0 and c.failures == 1


def test_expired_token_is_refreshed_on_401(tmp_path, clock):
    f = Fake(fetch=[AuthError(401), U()])
    f.creds = [good_creds(expires_in=-10), good_creds()]
    view, _ = make(tmp_path, f, clock).poll()
    assert f.refreshes == 1 and view.usage.week == 50


def renewed():
    return good_creds(expires_in=8 * 3600)


def test_auth_error_refreshes_and_retries_once(tmp_path, clock):
    f = Fake(fetch=[AuthError(401), U(week=60)])
    f.creds = [good_creds(expires_in=30), renewed()]
    view, _ = make(tmp_path, f, clock).poll()
    assert f.refreshes == 1 and f.fetches == 2 and view.usage.week == 60


def test_auth_error_twice_is_a_failure(tmp_path, clock):
    f = Fake(fetch=[AuthError(401)])
    f.creds = [good_creds(expires_in=30), renewed()]
    c = make(tmp_path, f, clock)
    view, _ = c.poll()
    assert f.refreshes == 1 and f.fetches == 2 and c.failures == 1
    assert view.usage is None


def test_failed_refresh_is_not_retried_every_poll(tmp_path, clock):
    """Each refresh costs a little usage, so after a failure wait 30 minutes."""
    f = Fake(creds=good_creds(expires_in=-60), fetch=[AuthError(401)], refresh=False)
    c = make(tmp_path, f, clock)
    c.poll()
    clock["t"] += 5 * 60
    c.poll()
    clock["t"] += 5 * 60
    c.poll()
    assert f.refreshes == 1
    clock["t"] += 25 * 60
    c.poll()
    assert f.refreshes == 2


def test_network_error_falls_back_to_newer_snapshot(tmp_path, clock):
    f = Fake(fetch=[urllib.error.URLError("offline")], snapshot=U(week=70, at=NOW - 60, source="snapshot"))
    c = make(tmp_path, f, clock)
    view, _ = c.poll()
    assert view.usage.week == 70 and view.usage.source == "snapshot"
    assert c.failures == 1 and f.refreshes == 0


def test_does_not_use_older_snapshot(tmp_path, clock):
    f = Fake(fetch=[U(week=50), urllib.error.URLError("offline")], snapshot=U(week=40, at=NOW - 3600))
    c = make(tmp_path, f, clock)
    c.poll()
    clock["t"] += 300
    view, _ = c.poll()
    assert view.usage.week == 50 and view.stale_minutes == 5


def test_bad_response_counts_as_failure(tmp_path, clock):
    f = Fake(fetch=[ValueError("seven_day missing")])
    c = make(tmp_path, f, clock)
    view, _ = c.poll()
    assert c.failures == 1 and view.usage is None


def test_failures_reset_on_success(tmp_path, clock):
    f = Fake(fetch=[urllib.error.URLError("x"), urllib.error.URLError("x"), U()])
    c = make(tmp_path, f, clock)
    c.poll(); c.poll()
    assert c.failures == 2
    c.poll()
    assert c.failures == 0


def test_stale_view_turns_grey(tmp_path, clock):
    f = Fake(fetch=[U(week=50), urllib.error.URLError("x")])
    c = make(tmp_path, f, clock)
    c.poll()
    clock["t"] += 20 * 60
    view, _ = c.poll()
    assert view.color == "stale" and view.stale_minutes == 20


@pytest.mark.parametrize("week,color", [(50, "ok"), (80, "warn"), (95, "danger"), (100, "danger")])
def test_colors(tmp_path, clock, week, color):
    # Six days in with a day to go: even 80% (13%/day) ends the week at ~93%, so the
    # projection stays "ok" and only the thresholds decide the color.
    f = Fake(fetch=[Usage(at=NOW, week=week, week_resets=NOW + DAY)])
    view, _ = make(tmp_path, f, clock).poll()
    assert view.color == color


def test_exhaust_projection_is_red(tmp_path, clock):
    f = Fake(fetch=[Usage(at=NOW, week=60, week_resets=NOW + 4 * DAY)])  # 20%/day, 2 days left
    view, _ = make(tmp_path, f, clock).poll()
    assert view.proj.status == "exhaust" and view.color == "danger"


def test_login_notice_and_alert(tmp_path, clock):
    f = Fake(creds=good_creds(login_left=2 * DAY))
    view, alerts = make(tmp_path, f, clock).poll()
    assert view.login_notice and view.login_expires == NOW + 2 * DAY
    assert any(a.login for a in alerts)


def test_no_login_notice_when_far(tmp_path, clock):
    view, alerts = make(tmp_path, Fake(), clock).poll()
    assert not view.login_notice and not any(a.login for a in alerts)


def test_threshold_alert_passes_through(tmp_path, clock):
    f = Fake(fetch=[Usage(at=NOW, week=85, week_resets=NOW + WEEK - 4 * 3600)])
    _, alerts = make(tmp_path, f, clock).poll()
    assert any("85%" in a.title for a in alerts)


def test_failure_alert_after_three(tmp_path, clock):
    f = Fake(fetch=[urllib.error.URLError("x")])
    c = make(tmp_path, f, clock)
    out = []
    for _ in range(3):
        out += c.poll()[1]
    assert any(a.title == "사용량 조회 실패" for a in out)


def test_refresh_that_does_not_renew_the_token_backs_off(tmp_path, clock):
    """claude -p exits 0 but the stored token stays expired (e.g. it used another
    credential): must not burn a call every poll."""
    f = Fake(creds=good_creds(expires_in=-60), fetch=[AuthError(401)], refresh=True)
    c = make(tmp_path, f, clock)
    for _ in range(6):            # 30 minutes of polling
        c.poll()
        clock["t"] += 5 * 60
    assert f.refreshes == 1
    c.poll()                      # 30 min after the first attempt
    assert f.refreshes == 2


def test_refresh_gap_applies_even_after_success(tmp_path, clock):
    f = Fake(fetch=[AuthError(401), U(), AuthError(401)])
    f.creds = [good_creds(expires_in=-10), renewed(), good_creds(expires_in=-10)]
    c = make(tmp_path, f, clock)
    c.poll()
    assert f.refreshes == 1
    clock["t"] += 5 * 60
    c.poll()                      # "expired" again 5 minutes later: no second call
    assert f.refreshes == 1


def test_403_does_not_refresh(tmp_path, clock):
    f = Fake(fetch=[AuthError(403)])
    c = make(tmp_path, f, clock)
    c.poll()
    assert f.refreshes == 0 and c.failures == 1


def test_no_refresh_when_login_expired(tmp_path, clock):
    f = Fake(creds=good_creds(expires_in=-60, login_left=-10), fetch=[AuthError(401)])
    c = make(tmp_path, f, clock)
    view, alerts = c.poll()
    assert f.refreshes == 0
    assert "로그인" in view.message and view.login_notice
    assert any(a.login for a in alerts)


def _failure_alerts(c, n=3):
    out = []
    for _ in range(n):
        out += c.poll()[1]
    return [a for a in out if a.title == "사용량 조회 실패"]


def test_network_failure_alert_has_no_login_button(tmp_path, clock):
    fail = _failure_alerts(make(tmp_path, Fake(fetch=[urllib.error.URLError("x")]), clock))
    assert fail and not fail[0].login


def test_auth_failure_alert_has_login_button(tmp_path, clock):
    fail = _failure_alerts(make(tmp_path, Fake(creds=good_creds(expires_in=-10), fetch=[AuthError(401)], refresh=False), clock))
    assert fail and fail[0].login


def test_past_week_reading_rolls_forward(tmp_path, clock):
    """Offline across a reset (or an old snapshot at startup): don't show last week as now."""
    old = Usage(at=NOW - 3 * DAY, week=88, week_resets=NOW - DAY, five=40, five_resets=NOW - 2 * DAY,
                source="snapshot")
    f = Fake(fetch=[urllib.error.URLError("offline")], snapshot=old)
    view, alerts = make(tmp_path, f, clock).poll()
    assert view.usage.week == 0 and view.usage.week_resets == NOW + 6 * DAY
    assert view.usage.five is None and view.color == "stale"
    assert not any("88" in a.title for a in alerts)


def test_projection_is_made_at_reading_time(tmp_path, clock):
    f = Fake(fetch=[Usage(at=NOW, week=60, week_resets=NOW + 4 * DAY), urllib.error.URLError("x")])
    c = make(tmp_path, f, clock)
    first, _ = c.poll()
    clock["t"] += 6 * 3600
    later, _ = c.poll()
    assert later.proj.eta == first.proj.eta   # no pretending there was zero usage while stale


def test_401_with_a_valid_token_does_not_refresh(tmp_path, clock):
    """Hours left on the token but the server says 401: not an expiry problem, so a
    refresh can't help and would only cost usage."""
    f = Fake(creds=good_creds(expires_in=48 * 3600), fetch=[AuthError(401)])
    c = make(tmp_path, f, clock)
    for _ in range(24 * 12):          # a whole day of polls
        c.poll()
        clock["t"] += 5 * 60
    assert f.refreshes == 0


def test_never_renewing_refresh_backs_off_exponentially(tmp_path, clock):
    """Expired token, claude -p exits 0 but nothing changes: at most a handful of calls a day."""
    f = Fake(creds=good_creds(expires_in=-60), fetch=[AuthError(401)], refresh=True)
    c = make(tmp_path, f, clock)
    for _ in range(24 * 12):
        c.poll()
        clock["t"] += 5 * 60
    assert f.refreshes <= 8


def test_failing_refresh_backs_off_too(tmp_path, clock):
    f = Fake(creds=good_creds(expires_in=-60), fetch=[AuthError(401)], refresh=False)
    c = make(tmp_path, f, clock)
    for _ in range(24 * 12):
        c.poll()
        clock["t"] += 5 * 60
    assert f.refreshes <= 8


def test_backoff_resets_after_a_real_renewal(tmp_path, clock):
    f = Fake(creds=good_creds(expires_in=-60), fetch=[AuthError(401)], refresh=False)
    c = make(tmp_path, f, clock)
    for _ in range(12 * 12):         # 12 hours of failing
        c.poll()
        clock["t"] += 5 * 60
    # Now the refresh works and the token really changes.
    f.refresh_result = True
    f.fetch_results = [AuthError(401), U(week=55)]
    f.creds = [good_creds(expires_in=-60), renewed()]
    for _ in range(9 * 12):          # within the next backoff step it gets through
        view, _ = c.poll()
        if view.usage and view.usage.week == 55:
            break
        clock["t"] += 5 * 60
    assert view.usage.week == 55
    assert c._refresh_fails == 0


def test_offline_start_shows_the_last_recorded_reading(tmp_path, clock):
    h = History(tmp_path / "h.jsonl")
    h.add(Usage(at=NOW - 600, week=61, week_resets=RESETS, five=12))
    f = Fake(fetch=[urllib.error.URLError("offline")])
    c = Controller(CFG, h, Alerts(CFG, path=tmp_path / "s.json"),
                   read_credentials=f.read_credentials, fetch_usage=f.fetch_usage,
                   refresh_token=f.refresh_token, read_snapshot=f.read_snapshot, clock=lambda: clock["t"])
    view, _ = c.poll()
    assert view.usage.week == 61 and view.stale_minutes == 10


def test_poll_survives_disk_errors(tmp_path, clock, monkeypatch):
    c = make(tmp_path, Fake(), clock)

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(c.history, "_write", boom)
    monkeypatch.setattr(c.alerts, "_write", boom)
    view, _ = c.poll()
    assert view.usage.week == 50


def test_auth_failure_offers_login_in_the_widget(tmp_path, clock):
    f = Fake()
    f.creds = [None]
    view, _ = make(tmp_path, f, clock).poll()
    assert view.needs_login


def test_no_login_prompt_when_fine(tmp_path, clock):
    view, _ = make(tmp_path, Fake(), clock).poll()
    assert not view.needs_login


def with_credit(tmp_path, clock, read_credit):
    f = Fake()
    return Controller(CFG, History(tmp_path / "h.jsonl"), Alerts(CFG, path=tmp_path / "s.json"),
                      read_credentials=f.read_credentials, fetch_usage=f.fetch_usage,
                      refresh_token=f.refresh_token, read_snapshot=f.read_snapshot,
                      read_credit=read_credit, clock=lambda: clock["t"])


def test_credit_is_passed_to_the_view(tmp_path, clock):
    from tray.credit import Credit
    got = Credit(balance=96.9, monthly=100.0, next_grant=NOW + DAY, as_of=NOW)
    seen = []
    view, _ = with_credit(tmp_path, clock, lambda now: seen.append(now) or got).poll()
    assert view.credit is got and seen == [NOW]


def test_credit_failure_does_not_break_the_poll(tmp_path, clock):
    def boom(now):
        raise OSError("disk")

    view, _ = with_credit(tmp_path, clock, boom).poll()
    assert view.usage.week == 50 and view.credit is None


def test_no_credit_set_up_means_no_line(tmp_path, clock):
    view, _ = make(tmp_path, Fake(), clock).poll()
    assert view.credit is None
