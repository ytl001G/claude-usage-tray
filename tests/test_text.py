from datetime import datetime

from tray.text import d_day, weekday, when

NOW = datetime(2026, 10, 9, 22, 0).timestamp()  # Friday


def ts(*a):
    return datetime(*a).timestamp()


def test_when():
    assert when(ts(2026, 10, 9, 23, 5), NOW) == "오늘 23:05"
    assert when(ts(2026, 10, 10, 18, 0), NOW) == "내일 18:00"
    assert when(ts(2026, 10, 12, 9, 30), NOW) == "10/12(월) 09:30"


def test_d_day():
    assert d_day(ts(2026, 10, 10, 1, 0), NOW) == "D-1"
    assert d_day(ts(2026, 10, 9, 23, 0), NOW) == "D-DAY"
    assert d_day(ts(2026, 11, 7, 15, 0), NOW) == "D-29"


def test_weekday():
    assert weekday(NOW) == "금"
