import json

import pytest

from tray.history import BUCKET, WEEK, History, project, usage_bars, week_key
from tray.usage_api import Usage

H = 3600
RESETS = 1_800_000_000.0          # end of "this" week
START = RESETS - WEEK


def usage(at, week, five=10, resets=RESETS):
    return Usage(at=at, week=week, week_resets=resets, five=five)


# ---------------------------------------------------------------- History

def test_week_key_absorbs_jitter():
    assert week_key(RESETS - 0.4) == week_key(RESETS + 0.6) == week_key(RESETS)


def test_writes_on_change_only(tmp_path):
    h = History(tmp_path / "h.jsonl")
    h.add(usage(START + 10 * H, 30))
    h.add(usage(START + 10 * H + 300, 30))   # same values, 5 min later: skipped
    h.add(usage(START + 10 * H + 600, 31))   # changed: written
    assert [r["week"] for r in h.rows] == [30, 31]


def test_heartbeat_row_every_hour(tmp_path):
    h = History(tmp_path / "h.jsonl")
    h.add(usage(START + 10 * H, 30))
    h.add(usage(START + 11 * H + 1, 30))
    assert len(h.rows) == 2


def test_persists_and_reloads(tmp_path):
    p = tmp_path / "h.jsonl"
    h = History(p)
    h.add(usage(START + 10 * H, 30))
    h.add(usage(START + 20 * H, 40))
    assert [r["week"] for r in History(p).rows] == [30, 40]


def test_skips_corrupt_lines(tmp_path):
    p = tmp_path / "h.jsonl"
    p.write_text('{"at": 1, "week": 5, "five": 1, "resets": %d}\nGARBAGE\n' % week_key(RESETS), encoding="utf-8")
    assert len(History(p).rows) == 1


def test_keeps_only_this_and_last_week(tmp_path):
    p = tmp_path / "h.jsonl"
    h = History(p)
    for weeks_ago, pct in [(2, 10), (1, 20), (0, 30)]:
        r = RESETS - weeks_ago * WEEK
        h.add(usage(r - 3 * 86400, pct, resets=r))
    assert [r["week"] for r in h.rows] == [20, 30]
    assert [json.loads(l)["week"] for l in p.read_text(encoding="utf-8").splitlines()] == [20, 30]


def test_week_points_split(tmp_path):
    h = History(tmp_path / "h.jsonl")
    h.add(usage(START - 86400, 50, resets=RESETS - WEEK))
    h.add(usage(START + 86400, 12))
    assert h.week_points(RESETS) == [(round(START + 86400), 12)]
    assert h.last_week_points(RESETS) == [(round(START - 86400), 50)]


# ---------------------------------------------------------------- project

def test_collecting_right_after_reset():
    p = project([], pct=2, resets=RESETS, now=START + 2 * H)
    assert p.status == "collecting" and p.eta is None


def test_full():
    p = project([], pct=100, resets=RESETS, now=START + 50 * H)
    assert p.status == "full"


def test_average_rate_needs_no_history():
    # 3.5 days in, 35% used -> 10%/day average.
    now = START + 3.5 * 86400
    p = project([], pct=35, resets=RESETS, now=now)
    assert p.avg_per_day == pytest.approx(10)
    assert p.recent_per_day is None           # no readings in the last 24h
    assert p.status == "ok"                   # 35 + 10*3.5 = 70% at reset
    assert p.final_pct == pytest.approx(70)


def test_exhaust_before_reset():
    now = START + 3.5 * 86400
    p = project([], pct=70, resets=RESETS, now=now)  # 20%/day -> 100% in 1.5 days
    assert p.status == "exhaust"
    assert p.eta == pytest.approx(now + 1.5 * 86400)
    assert p.final_pct == 100


def test_tight_when_running_out_just_after_reset():
    now = START + 6 * 86400
    # 84% after 6 days = 14%/day; 16% left takes ~27.4h, reset is in 24h -> within 12h after reset
    p = project([], pct=84, resets=RESETS, now=now)
    assert p.status == "tight"


def test_recent_rate_uses_regression_but_projection_uses_weekly_average():
    now = START + 4 * 86400
    # Slow week so far, but the last 24h went 40 -> 64 in 1% steps.
    pts = [(now - 24 * H + i * H, 40 + i) for i in range(25)]
    p = project(pts, pct=64, resets=RESETS, now=now)
    assert p.recent_per_day == pytest.approx(24, abs=0.5)
    assert p.avg_per_day == pytest.approx(16)
    # 36% left at the 16%/day average -> 2.25 days, reset is in 3 days -> runs out first.
    assert p.status == "exhaust"
    assert p.eta == pytest.approx(now + 2.25 * 86400)


def test_fast_last_day_does_not_override_weekly_average():
    now = START + 4 * 86400
    # 40% over 4 days (10%/day), the last 24h all of it: 0 -> 40.
    pts = [(now - 24 * H + i * H, i * 40 / 24) for i in range(25)]
    p = project(pts, pct=40, resets=RESETS, now=now)
    assert p.recent_per_day == pytest.approx(40, abs=1)
    assert p.status == "ok"
    assert p.final_pct == pytest.approx(70)


def test_idle_last_day_falls_back_to_average():
    now = START + 4 * 86400
    pts = [(now - 24 * H + i * H, 40) for i in range(25)]
    p = project(pts, pct=40, resets=RESETS, now=now)
    assert p.recent_per_day == pytest.approx(0)
    assert p.avg_per_day == pytest.approx(10)
    assert p.final_pct == pytest.approx(70)


def test_early_week_burst_is_not_called_exhaust():
    """3-6 hours after the reset a morning burst extrapolates to 'runs out' — too little
    data to say that, so wait until 12 hours into the week."""
    for hours, pct in [(3, 2), (6, 8), (11, 15)]:
        p = project([], pct=pct, resets=RESETS, now=START + hours * H)
        assert p.status == "collecting"


def test_recent_window_includes_week_start_anchor():
    # 14 hours into the week, 20% used, readings only in the last hour.
    now = START + 14 * H
    p = project([(now - H, 19)], pct=20, resets=RESETS, now=now)
    assert p.recent_per_day is not None
    assert p.recent_per_day > 0


def test_ignores_points_outside_window_or_week():
    now = START + 5 * 86400
    pts = [(START - 86400, 90), (now - 3 * 86400, 5)]
    p = project(pts, pct=50, resets=RESETS, now=now)
    assert p.recent_per_day is None  # nothing inside the last 24h except "now"


def test_history_ignores_non_dict_lines(tmp_path):
    p = tmp_path / "h.jsonl"
    p.write_text('[1,2]\n"x"\n{"at": 1, "week": 5, "five": 1, "resets": %d}\n{"at": "bad"}\n' % week_key(RESETS),
                 encoding="utf-8")
    h = History(p)
    assert len(h.rows) == 1
    assert h.week_points(RESETS) == [(1, 5)]


def test_last_week_survives_a_shifted_reset(tmp_path):
    """The new week's reset isn't always exactly old + 7 days (the window can start at
    the next use). Last week must still be there."""
    h = History(tmp_path / "h.jsonl")
    old = RESETS - WEEK
    for i in range(0, 168, 6):
        h.add(usage(old - WEEK + i * H + 60, i // 3, resets=old))
    new = RESETS + 9 * H                              # reset moved 9 hours later
    h.add(usage(old + 2 * H, 3, resets=new))
    assert len(h.last_week_points(new)) >= 20
    assert all(p[1] < 60 for p in h.last_week_points(new))


def test_prune_keeps_two_weeks_by_time(tmp_path):
    h = History(tmp_path / "h.jsonl")
    for weeks_ago in (3, 2, 1, 0):
        r = RESETS - weeks_ago * WEEK
        h.add(usage(r - 3 * 86400, 10 + weeks_ago, resets=r))
    assert sorted(r["week"] for r in h.rows) == [10, 11]


def test_idle_week_with_unknown_reset_is_not_recorded(tmp_path):
    h = History(tmp_path / "h.jsonl")
    for i in range(10):
        h.add(Usage(at=START + i * 600, week=0, week_resets=START + i * 600 + WEEK, resets_known=False))
    assert h.rows == []


def test_write_errors_are_swallowed(tmp_path, monkeypatch):
    h = History(tmp_path / "h.jsonl")

    def boom(*a, **k):
        raise OSError("locked")

    monkeypatch.setattr(h, "_write", boom)
    h.add(usage(START + 10 * H, 30))          # must not raise
    assert h.rows and h.rows[-1]["week"] == 30


# ---------------------------------------------------------------- usage bars

def test_bars_add_up_to_this_week():
    pts = [(START + i * 300, min(40, i // 4)) for i in range(1, 600)]
    bars = usage_bars(pts, 40, START, START + 50 * H)
    assert len(bars) == 17                                   # 16 full buckets + the one in progress
    assert [b.start for b in bars[:2]] == [START, START + BUCKET]
    assert sum(b.pct for b in bars) == pytest.approx(40)
    assert not any(b.guessed for b in bars)


def test_bars_show_when_it_was_used():
    pts = [(START + 7 * H, 0), (START + 7 * H + 300, 5), (START + 8 * H, 5)]
    bars = usage_bars(pts, 5, START, START + 11 * H)
    assert [round(b.pct, 3) for b in bars] == [0, 0, 5, 0]   # all of it in the 6-9h bucket


def test_gap_is_spread_evenly_and_marked_guessed():
    # App off from 6h to 18h: the 12% that appeared meanwhile is spread over the 4 buckets.
    pts = [(START + 6 * H, 0), (START + 18 * H, 12)]
    bars = usage_bars(pts, 12, START, START + 18 * H + 60)
    assert [round(b.pct, 3) for b in bars[2:6]] == [3, 3, 3, 3]
    assert [b.guessed for b in bars[2:6]] == [True] * 4
    assert bars[0].pct == bars[1].pct == 0


def test_before_first_reading_counts_from_week_start():
    bars = usage_bars([(START + 20 * H, 10)], 10, START, START + 20 * H + 60)
    assert sum(b.pct for b in bars) == pytest.approx(10)
    assert all(b.guessed for b in bars if b.pct > 0)


def test_dips_are_not_negative_usage():
    pts = [(START + H, 5), (START + H + 300, 4), (START + H + 600, 5)]
    bars = usage_bars(pts, 5, START, START + 2 * H)
    assert all(b.pct >= 0 for b in bars)


def test_bars_ignore_readings_outside_the_week():
    pts = [(START - H, 50), (START + H, 2), (START + 9 * H, 60)]
    bars = usage_bars(pts, 3, START, START + 2 * H)
    assert len(bars) == 1 and bars[0].pct == pytest.approx(3)
