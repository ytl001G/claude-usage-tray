"""API credit balance kept by claude-mode, a Claude Code mod that runs work on API credit.

There is no API to read the Console balance with a user key, so the balance is worked out
from `usage.jsonl`, where the mod writes one line per worker job with its cost. The credit
is a monthly grant with no carry-over, so the balance is the monthly amount minus what was
used since the last grant day. `credit.json` is an optional correction: a balance the user
read off the Console (`/mode credit 98.20`) counts instead until the next grant day passes.
Without `usage.jsonl` the mod isn't in use and there is no line."""
import calendar
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

# The mod writes under the home folder whatever CLAUDE_CONFIG_DIR says.
CREDIT_DIR = Path.home() / ".claude" / "claude-mode"
CREDIT_FILE = CREDIT_DIR / "credit.json"
USAGE_FILE = CREDIT_DIR / "usage.jsonl"

DEFAULT_MONTHLY = 100.0
DEFAULT_GRANT_DAY = 27


@dataclass
class Credit:
    balance: float      # estimated balance left now (USD)
    monthly: float      # amount granted each month
    next_grant: float   # when the next grant lands (local midnight of the grant day)
    as_of: float | None # when the balance was last read off the Console (None: never)

    @property
    def used(self) -> float:
        """Used out of this month's grant."""
        return max(0.0, self.monthly - self.balance)


def _grant_at(year: int, month: int, day: int) -> float:
    day = min(day, calendar.monthrange(year, month)[1])  # day 31 in a 30-day month
    return datetime(year, month, day).timestamp()


def last_grant(now: float, day: int) -> float:
    n = datetime.fromtimestamp(now)
    t = _grant_at(n.year, n.month, day)
    if t <= now:
        return t
    y, m = (n.year, n.month - 1) if n.month > 1 else (n.year - 1, 12)
    return _grant_at(y, m, day)


def next_grant(now: float, day: int) -> float:
    n = datetime.fromtimestamp(now)
    t = _grant_at(n.year, n.month, day)
    if t > now:
        return t
    y, m = (n.year, n.month + 1) if n.month < 12 else (n.year + 1, 1)
    return _grant_at(y, m, day)


def _num(v) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v:
        return None
    return float(v)


def _costs(path: Path) -> list[tuple[float, float]]:
    """[(at, cost)] from usage.jsonl; broken lines are skipped."""
    out = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if not isinstance(e, dict):
            continue
        at, cost = _num(e.get("at")), _num(e.get("cost"))
        if at is not None and cost is not None and cost >= 0:
            out.append((at, cost))
    return out


def _reading(path: Path) -> dict | None:
    """The user's Console reading from credit.json, or None when there is no usable one."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        log.warning("credit: credit.json unreadable")
        return None
    return raw if isinstance(raw, dict) else None


def read_credit(now: float, credit_path: Path | None = None, usage_path: Path | None = None) -> Credit | None:
    """None when the mod isn't in use (no usage.jsonl and no reading)."""
    credit_path = credit_path or CREDIT_FILE
    usage_path = usage_path or USAGE_FILE
    raw = _reading(credit_path) or {}
    balance, as_of = _num(raw.get("balance")), _num(raw.get("as_of"))
    if balance is None or as_of is None or balance < 0:
        balance = as_of = None
    if as_of is None and not usage_path.exists():
        return None
    monthly = _num(raw.get("monthly"))
    monthly = monthly if monthly is not None and monthly > 0 else DEFAULT_MONTHLY
    day = _num(raw.get("grant_day"))
    day = int(day) if day is not None and 1 <= day <= 31 else DEFAULT_GRANT_DAY

    granted = last_grant(now, day)
    if as_of is None or granted > as_of:
        base, since = monthly, granted   # no reading, or a grant after it replaced what was left
    else:
        base, since = balance, as_of
    spent = sum(cost for at, cost in _costs(usage_path) if at > since)
    return Credit(balance=max(0.0, base - spent), monthly=monthly,
                  next_grant=next_grant(now, day), as_of=as_of)
