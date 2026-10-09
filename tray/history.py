"""Usage history (this week + last week) and the "when does it run out" projection."""
import json
import logging
import time
from dataclasses import dataclass

from .config import HISTORY_FILE
from .usage_api import Usage

log = logging.getLogger(__name__)

WEEK = 7 * 86400
HEARTBEAT = 3600         # write a row at least this often even when nothing changed
MIN_SPAN = 3 * 3600      # the recent rate needs readings spanning at least this long
EARLY = 12 * 3600        # no projection this soon after the reset: one burst would read as "runs out"


def week_key(resets: float) -> int:
    # resets_at jitters by a fraction of a second between calls; round to 10 minutes.
    return int(round(resets / 600) * 600)


class History:
    """this week + last week of readings, one JSON object per line. Write errors (locked
    file, full disk) are logged and skipped: losing a row must never stop a poll."""

    def __init__(self, path=HISTORY_FILE):
        self.path = path
        self.rows: list[dict] = []
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and all(isinstance(row.get(k), (int, float)) for k in ("at", "week", "resets")):
                    self.rows.append(row)
        except OSError:
            pass

    def add(self, u: Usage) -> None:
        if not u.resets_known:
            return  # idle week with no reset time yet: its "reset" moves every poll
        row = {"at": round(u.at), "week": u.week, "five": u.five, "resets": week_key(u.week_resets)}
        last = self.rows[-1] if self.rows else None
        changed = not last or any(last.get(k) != row[k] for k in ("week", "five", "resets"))
        if not changed and row["at"] - last["at"] < HEARTBEAT:
            return
        self.rows.append(row)
        pruned = self._prune(row["resets"])
        try:
            self._write(rewrite=pruned, row=row)
        except OSError as e:
            log.warning("history write failed: %s", type(e).__name__)

    def _prune(self, current: int) -> bool:
        """Keep this week and last week, by time: a reset that isn't exactly 7 days after
        the previous one must not wipe last week."""
        cutoff = current - 2 * WEEK - 6 * 3600
        keep = [r for r in self.rows if r["at"] >= cutoff]
        dropped = len(keep) != len(self.rows)
        self.rows = keep
        return dropped

    def _write(self, rewrite: bool, row: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if rewrite:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text("".join(json.dumps(r) + "\n" for r in self.rows), encoding="utf-8")
            tmp.replace(self.path)
        else:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")

    def week_points(self, resets: float) -> list[tuple[float, int]]:
        k = week_key(resets)
        return [(r["at"], r["week"]) for r in self.rows if abs(r["resets"] - k) <= 3600]

    def last_week_points(self, resets: float) -> list[tuple[float, int]]:
        """Readings of the most recent earlier week, whatever its exact reset time."""
        k = week_key(resets)
        earlier = [r["resets"] for r in self.rows if r["resets"] < k - 3600]
        if not earlier:
            return []
        prev = max(earlier)
        return [(r["at"], r["week"]) for r in self.rows if abs(r["resets"] - prev) <= 3600]

    def latest(self) -> Usage | None:
        """The newest reading, so an offline start can show something."""
        if not self.rows:
            return None
        r = self.rows[-1]
        return Usage(at=r["at"], week=r["week"], week_resets=r["resets"], five=r.get("five"), source="history")


@dataclass
class Projection:
    status: str               # collecting | ok | tight | exhaust | full
    avg_per_day: float | None
    recent_per_day: float | None
    eta: float | None         # epoch seconds of 100%, using the faster rate
    final_pct: float | None   # expected % at reset, using the faster rate


def _slope(points: list[tuple[float, float]]) -> float | None:
    """Least-squares slope in %/s; fits the whole window so 1%-steps average out."""
    if len(points) < 2:
        return None
    n = len(points)
    mt = sum(t for t, _ in points) / n
    mp = sum(p for _, p in points) / n
    var = sum((t - mt) ** 2 for t, _ in points)
    if var == 0:
        return None
    return sum((t - mt) * (p - mp) for t, p in points) / var


def project(points: list[tuple[float, int]], pct: int, resets: float,
            window_hours: float = 24, now: float | None = None) -> Projection:
    now = now or time.time()
    start = resets - WEEK
    if pct >= 100:
        return Projection("full", None, None, now, 100)
    if now - start < EARLY:
        return Projection("collecting", None, None, None, None)

    # The week starts at 0%, so the weekly average needs no history at all.
    avg = pct / (now - start)

    # Recent rate: regression over the window, anchored at 0% if the week began inside it.
    lo = max(now - window_hours * 3600, start)
    window = [(t, p) for t, p in points if lo <= t <= now]
    if start >= lo:
        window.insert(0, (start, 0))
    window.append((now, pct))
    recent = _slope(window) if window[-1][0] - window[0][0] >= MIN_SPAN else None
    if recent is not None:
        recent = max(recent, 0.0)

    rate = max(r for r in (avg, recent) if r is not None)
    eta = now + (100 - pct) / rate if rate > 0 else None
    final = pct + rate * (resets - now)

    if eta is not None and eta < resets:
        status = "exhaust"
    elif eta is not None and eta < resets + 12 * 3600:
        status = "tight"
    else:
        status = "ok"
    per_day = lambda r: r * 86400 if r is not None else None
    return Projection(status, per_day(avg), per_day(recent), eta, min(final, 100))
