"""One poll: credentials -> (refresh) -> usage -> history/projection/alerts -> View."""
import logging
import time
from dataclasses import replace

from . import usage_api
from .alerts import Alert, Alerts
from .history import WEEK, History, project
from .render import STALE_MINUTES, View
from .usage_api import AuthError, Credentials, Usage

log = logging.getLogger(__name__)

EXPIRED_SLACK = 60            # a token this close to its expiry counts as expired
REFRESH_GAP = 30 * 60         # minimum time between refresh calls, whatever happened
REFRESH_MAX_GAP = 8 * 3600    # backoff cap after repeated failures


class Controller:
    def __init__(self, cfg: dict, history: History, alerts: Alerts, *,
                 read_credentials=usage_api.read_credentials, fetch_usage=usage_api.fetch_usage,
                 refresh_token=usage_api.refresh_token, read_snapshot=usage_api.read_snapshot,
                 clock=time.time):
        self.cfg = cfg
        self.history = history
        self.alerts = alerts
        self._read_credentials = read_credentials
        self._fetch_usage = fetch_usage
        self._refresh_token = refresh_token
        self._read_snapshot = read_snapshot
        self._clock = clock
        self.failures = 0
        self.last: Usage | None = history.latest()   # an offline start still shows something
        self._next_refresh_at: float | None = None
        self._refresh_fails = 0
        self._auth_failed = False

    # ------------------------------------------------------------ token

    def _refresh(self, creds: Credentials) -> Credentials | None:
        """Ask Claude Code to refresh the token. Each call costs a little usage, so it is
        spaced out (30 min, doubling after each failure up to 8 h) and only counts as
        working if the stored token really changed."""
        now = self._clock()
        if creds.refresh_expires_at is not None and creds.refresh_expires_at <= now:
            return None  # the login itself expired; only `claude auth login` helps
        if self._next_refresh_at is not None and now < self._next_refresh_at:
            return None
        new = self._read_credentials() if self._refresh_token() else None
        renewed = new is not None and (new.access_token != creds.access_token
                                       or (new.expires_at or 0) > (creds.expires_at or 0))
        if renewed:
            self._refresh_fails = 0
            self._next_refresh_at = now + REFRESH_GAP
            return new
        log.warning("refresh: token was not renewed")
        self._refresh_fails += 1
        self._next_refresh_at = now + min(REFRESH_GAP * 2 ** (self._refresh_fails - 1), REFRESH_MAX_GAP)
        return None

    def _fetch(self, creds: Credentials) -> Usage:
        try:
            return self._fetch_usage(creds)
        except AuthError as e:
            # Refresh only for an expired token: a 403, or a 401 on a token with hours
            # left, won't be fixed by refreshing and would just cost usage.
            fresh_token = creds.expires_at is not None and creds.expires_at > self._clock() + EXPIRED_SLACK
            if e.status != 401 or fresh_token:
                raise
            new = self._refresh(creds)
            if not new:
                raise
            return self._fetch_usage(new)

    # ------------------------------------------------------------ poll

    def poll(self) -> tuple[View, list[Alert]]:
        now = self._clock()
        alerts: list[Alert] = []
        creds = self._read_credentials()
        login_expired = bool(creds and creds.refresh_expires_at is not None and creds.refresh_expires_at <= now)
        message = None
        fresh = None
        self._auth_failed = False
        if not creds:
            message = "Claude Code 로그인 필요"
            self._auth_failed = True
        else:
            try:
                fresh = self._fetch(creds)
            except AuthError as e:
                self._auth_failed = True
                message = "로그인 만료 · 다시 로그인 필요" if login_expired else f"인증 실패 (HTTP {e.status})"
                log.warning("poll: auth failed (HTTP %s)", e.status)
            except Exception as e:  # network, bad response, anything: fall back below
                message = "사용량을 가져오지 못했어요"
                log.warning("poll: %s", type(e).__name__)

        if fresh:
            self.failures = 0
            self._refresh_fails = 0
            self.last = fresh
            self.history.add(fresh)
        else:
            self.failures += 1
            snap = self._read_snapshot()
            if snap and (self.last is None or snap.at > self.last.at):
                self.last = snap
                self.history.add(snap)

        view = View(usage=None, proj=None, message=message, needs_login=self._auth_failed)
        if creds and creds.refresh_expires_at:
            view.login_expires = creds.refresh_expires_at
            view.login_notice = creds.refresh_expires_at - now <= self.cfg["relogin_notice_days"] * 86400
            alerts += self.alerts.check_login(creds.refresh_expires_at, now=now)

        u = self.last
        if u and u.week_resets <= now:
            # The reading is from a week that has already reset (offline across the reset,
            # or an old snapshot): show the new week, which we know nothing about yet.
            resets = u.week_resets
            while resets <= now:
                resets += WEEK
            u = replace(u, week=0, week_resets=resets, five=None, five_resets=None, breakdown=[], scoped=[])
        if u:
            view.usage = u
            # Project from when the reading was taken, not from now: while the data is
            # stale we don't know what happened since.
            view.proj = project(self.history.week_points(u.week_resets), u.week, u.week_resets,
                                self.cfg["projection_window_hours"], now=min(u.at, now))
            view.points = self.history.week_points(u.week_resets)
            view.last_points = self.history.last_week_points(u.week_resets)
            view.stale_minutes = max(0, int((now - u.at) // 60))
            view.color = self._color(u, view)
            if fresh:
                alerts += self.alerts.check_usage(u, view.proj, now=now)
        alerts += self.alerts.check_failures(self.failures, auth=self._auth_failed,
                                             login_expired=login_expired, now=now)
        return view, alerts

    @staticmethod
    def _color(u: Usage, v: View) -> str:
        if v.stale_minutes is not None and v.stale_minutes >= STALE_MINUTES:
            return "stale"
        status = v.proj.status if v.proj else None
        if u.week >= 95 or status in ("exhaust", "full"):
            return "danger"
        if u.week >= 80 or status == "tight":
            return "warn"
        return "ok"
