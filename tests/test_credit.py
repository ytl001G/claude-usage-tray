import json
from datetime import datetime

import pytest

from tray import credit, render
from tray.credit import Credit, last_grant, next_grant, read_credit
from tray.render import main_popup

from test_render import NOW, PALETTE, colors, full_view, usage

ts = lambda *a: datetime(*a).timestamp()


def write(tmp_path, credit_json, usage_lines=()):
    c, u = tmp_path / "credit.json", tmp_path / "usage.jsonl"
    c.write_text(credit_json if isinstance(credit_json, str) else json.dumps(credit_json), encoding="utf-8")
    u.write_text("".join(l if isinstance(l, str) else json.dumps(l) + "\n" for l in usage_lines), encoding="utf-8")
    return c, u


# ------------------------------------------------------------ grant days

def test_grant_days_around_the_grant():
    assert last_grant(ts(2026, 10, 15, 12), 27) == ts(2026, 9, 27)
    assert next_grant(ts(2026, 10, 15, 12), 27) == ts(2026, 10, 27)
    assert last_grant(ts(2026, 10, 27, 0, 0, 1), 27) == ts(2026, 10, 27)
    assert next_grant(ts(2026, 10, 27, 0, 0, 1), 27) == ts(2026, 11, 27)


def test_grant_days_wrap_the_year_and_short_months():
    assert last_grant(ts(2027, 1, 5), 27) == ts(2026, 12, 27)
    assert next_grant(ts(2026, 12, 30), 27) == ts(2027, 1, 27)
    assert next_grant(ts(2027, 2, 1), 31) == ts(2027, 2, 28)    # no Feb 31st
    assert last_grant(ts(2026, 5, 1), 31) == ts(2026, 4, 30)


# ------------------------------------------------------------ balance

def test_reading_minus_jobs_after_it(tmp_path):
    as_of = ts(2026, 10, 15, 9)
    c, u = write(tmp_path, {"monthly": 100, "grant_day": 27, "balance": 98.2, "as_of": as_of},
                 [{"at": as_of - 60, "cost": 5.0, "model": "opus"},     # already in the reading
                  {"at": as_of + 60, "cost": 1.25, "model": "opus"},
                  {"at": as_of + 120, "cost": 0.05, "model": "haiku"}])
    got = read_credit(ts(2026, 10, 15, 12), c, u)
    assert got.balance == pytest.approx(96.9)
    assert got.monthly == 100 and got.used == pytest.approx(3.1)
    assert got.next_grant == ts(2026, 10, 27)


def test_a_grant_after_the_reading_starts_over_without_carry_over(tmp_path):
    as_of = ts(2026, 10, 20)
    c, u = write(tmp_path, {"monthly": 100, "grant_day": 27, "balance": 12.0, "as_of": as_of},
                 [{"at": as_of + 60, "cost": 2.0},                       # spent from the old grant
                  {"at": ts(2026, 10, 28), "cost": 3.5}])
    got = read_credit(ts(2026, 10, 29), c, u)
    assert got.balance == pytest.approx(96.5)
    assert got.next_grant == ts(2026, 11, 27)


def test_balance_never_goes_negative(tmp_path):
    as_of = ts(2026, 10, 15)
    c, u = write(tmp_path, {"balance": 1.0, "as_of": as_of}, [{"at": as_of + 1, "cost": 4.0}])
    assert read_credit(ts(2026, 10, 16), c, u).balance == 0


def test_defaults_when_the_mod_left_fields_out(tmp_path):
    c, u = write(tmp_path, {"balance": 50, "as_of": ts(2026, 10, 15), "monthly": "x", "grant_day": 40})
    got = read_credit(ts(2026, 10, 16), c, u)
    assert got.monthly == credit.DEFAULT_MONTHLY and got.next_grant == ts(2026, 10, credit.DEFAULT_GRANT_DAY)


def test_broken_usage_lines_are_skipped(tmp_path):
    as_of = ts(2026, 10, 15)
    c, u = write(tmp_path, {"balance": 10, "as_of": as_of},
                 ["not json\n", "[1, 2]\n", json.dumps({"at": as_of + 1, "cost": -3}) + "\n",
                  json.dumps({"at": as_of + 1, "cost": True}) + "\n", json.dumps({"at": "x", "cost": 1}) + "\n",
                  json.dumps({"at": as_of + 1, "cost": 0.5}) + "\n"])
    assert read_credit(ts(2026, 10, 16), c, u).balance == pytest.approx(9.5)


def test_no_usage_file_is_fine(tmp_path):
    c, _ = write(tmp_path, {"balance": 10, "as_of": ts(2026, 10, 15)})
    assert read_credit(ts(2026, 10, 16), c, tmp_path / "missing.jsonl").balance == 10


@pytest.mark.parametrize("bad", ["", "{", "[]", json.dumps({"as_of": 1}), json.dumps({"balance": 5}),
                                 json.dumps({"balance": -1, "as_of": 1}), json.dumps({"balance": True, "as_of": 1})])
def test_unusable_credit_file_means_no_line(tmp_path, bad):
    c, u = write(tmp_path, bad)
    assert read_credit(ts(2026, 10, 16), c, u) is None


def test_not_set_up_means_no_line(tmp_path):
    assert read_credit(NOW, tmp_path / "none.json", tmp_path / "none.jsonl") is None


def test_default_paths_are_the_mod_folder():
    # conftest points these at a temp folder; the real ones are fixed by the mod
    assert credit.CREDIT_DIR.parts[-2:] == (".claude", "claude-mode")
    assert read_credit(NOW) is None


# ------------------------------------------------------------ drawing

def cr(balance, monthly=100.0):
    return Credit(balance=balance, monthly=monthly, next_grant=NOW + 12 * 86400, as_of=NOW - 3600)


def test_credit_line_only_in_the_big_panel():
    plain, _ = main_popup(full_view(), now=NOW)
    with_credit, _ = main_popup(full_view(credit=cr(96.9)), now=NOW)
    assert with_credit.height == plain.height + 16
    folded, _ = main_popup(full_view(credit=cr(96.9)), folded=True, now=NOW)
    assert folded.height == main_popup(full_view(), folded=True, now=NOW)[0].height


@pytest.mark.parametrize("balance,color", [(15.0, render.YELLOW), (2.0, render.RED)])
def test_credit_gauge_warns_when_low(balance, color):
    quiet = full_view(usage=usage(five=None, five_resets=None))   # nothing else yellow or red
    assert color not in colors(main_popup(quiet, now=NOW)[0])
    quiet.credit = cr(balance)
    assert color in colors(main_popup(quiet, now=NOW)[0])


@pytest.mark.parametrize("balance", [0.0, 0.004, 99.99, 1234.56, 100000.0])
def test_credit_line_is_pixel_art_and_fits(balance):
    img, regions = main_popup(full_view(credit=cr(balance), login_notice=True, login_expires=NOW + 86400), now=NOW)
    assert colors(img) <= PALETTE
    assert img.width == render.W_FULL and img.height <= 290
