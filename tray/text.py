"""Korean date/time phrasing shared by the popup and alerts."""
from datetime import datetime, timedelta

WEEKDAYS = "월화수목금토일"


def when(ts: float, now: float | None = None) -> str:
    """'오늘 18:00', '내일 18:00', or '10/12(일) 18:00'."""
    t = datetime.fromtimestamp(ts)
    n = datetime.fromtimestamp(now) if now else datetime.now()
    hm = t.strftime("%H:%M")
    if t.date() == n.date():
        return f"오늘 {hm}"
    if t.date() == (n + timedelta(days=1)).date():
        return f"내일 {hm}"
    return f"{t.month}/{t.day}({WEEKDAYS[t.weekday()]}) {hm}"


def d_day(ts: float, now: float | None = None) -> str:
    n = datetime.fromtimestamp(now) if now else datetime.now()
    days = (datetime.fromtimestamp(ts).date() - n.date()).days
    return "D-DAY" if days <= 0 else f"D-{days}"


def weekday(ts: float) -> str:
    return WEEKDAYS[datetime.fromtimestamp(ts).weekday()]
