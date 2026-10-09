"""Decides when to show the small alert popup. Remembers what it already showed in state.json."""
import json
import logging
import time
from dataclasses import dataclass

from .config import STATE_FILE
from .history import Projection, week_key
from .usage_api import Usage
from .text import d_day, when

log = logging.getLogger(__name__)

DAY = 86400


@dataclass
class Alert:
    title: str
    lines: list[str]
    color: str           # "ok" | "warn" | "danger" | "stale"
    login: bool = False  # show the [로그인] button


class Alerts:
    def __init__(self, cfg: dict, path=STATE_FILE):
        self.cfg = cfg
        self.path = path
        try:
            self.s = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(self.s, dict):
                self.s = {}
        except (OSError, ValueError):
            self.s = {}

    def _save(self):
        try:
            self._write()
        except OSError as e:
            log.warning("state write failed: %s", type(e).__name__)

    def _write(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.s), encoding="utf-8")
        tmp.replace(self.path)

    def check_usage(self, u: Usage, p: Projection, now: float | None = None) -> list[Alert]:
        now = now or time.time()
        out = []
        wk = str(week_key(u.week_resets))
        exhaust_lines = []
        if p.status == "exhaust" and p.eta:
            hours = max(0, round((u.week_resets - p.eta) / 3600))
            exhaust_lines = [f"이 속도면 {when(p.eta)} 소진", f"(리셋 {hours}시간 전)"]

        # Projection turned to "runs out before reset". The 24h window slides, so the
        # status can flip back and forth; say it at most once a day.
        prev = self.s.get("proj")
        flipped = (p.status == "exhaust" and prev != "exhaust" and p.eta
                   and now - self.s.get("proj_last", 0) >= DAY)
        if p.status != "collecting":
            self.s["proj"] = p.status

        # Weekly thresholds: once each per week; if several were crossed at once, show the
        # highest. A projection flip in the same poll is folded into this one popup.
        fired = self.s.setdefault("thresholds", {})
        for k in [k for k in fired if k != wk]:
            del fired[k]
        done = set(fired.get(wk, []))
        crossed = [t for t in sorted(self.cfg["alert_thresholds"]) if u.week >= t and t not in done]
        if crossed:
            top = crossed[-1]
            lines = exhaust_lines or [f"리셋 {when(u.week_resets)}"]
            out.append(Alert(f"Claude 주간 {u.week}%", lines, "danger" if top >= 95 or exhaust_lines else "warn"))
            fired[wk] = sorted(done | set(crossed))
            if exhaust_lines:
                self.s["proj_last"] = now
        elif flipped:
            out.append(Alert("소진 예상", exhaust_lines, "danger"))
            self.s["proj_last"] = now

        # 5-hour limit at 90%: once per 5-hour window.
        if u.five is not None and u.five >= 90 and u.five_resets:
            fk = week_key(u.five_resets)
            if self.s.get("five") != fk:
                out.append(Alert(f"5시간 한도 {u.five}%", [f"{when(u.five_resets)} 리셋"], "danger"))
                self.s["five"] = fk

        self._save()
        return out

    def check_login(self, refresh_expires_at: float | None, now: float | None = None) -> list[Alert]:
        """From N days before the session expires: daily, then every 3 hours on the last day."""
        now = now or time.time()
        if not refresh_expires_at:
            return []
        left = refresh_expires_at - now
        if left > self.cfg["relogin_notice_days"] * DAY:
            self.s.pop("login_last", None)
            return []
        # Every 3 hours on the last day; daily before that, and daily once it has expired.
        interval = 3 * 3600 if 0 < left <= DAY else DAY
        if now - self.s.get("login_last", 0) < interval:
            return []
        self.s["login_last"] = now
        self._save()
        if left <= 0:
            return [Alert("로그인 만료", ["다시 로그인해야", "사용량을 볼 수 있어요"], "danger", login=True)]
        return [Alert(f"로그인 만료 {d_day(refresh_expires_at, now)}", [f"{when(refresh_expires_at)} 만료", "미리 다시 로그인하세요"],
                      "danger" if left <= DAY else "warn", login=True)]

    def check_failures(self, consecutive: int, auth: bool = False, login_expired: bool = False,
                       now: float | None = None) -> list[Alert]:
        """After 3 failed polls in a row, then once a day. [로그인] only when the token is
        the problem; a network outage isn't fixed by logging in. Silent when the login has
        expired: the login reminder already says so."""
        now = now or time.time()
        if consecutive < 3 or login_expired:
            self.s.pop("fail_last", None)
            return []
        if now - self.s.get("fail_last", 0) < DAY:
            return []
        self.s["fail_last"] = now
        self._save()
        if auth:
            return [Alert("사용량 조회 실패", ["로그인이 만료됐거나", "인증에 실패했어요"], "stale", login=True)]
        return [Alert("사용량 조회 실패", ["인터넷 연결을", "확인해 주세요"], "stale")]
