import re
from pathlib import Path

import pytest

from tray import render
from tray.config import asset
from tray.history import WEEK, Projection
from tray.render import View, alert_popup, main_popup, tray_icon
from tray.usage_api import Usage

DAY = 86400
NOW = 1_800_000_000.0
RESETS = NOW + 2 * DAY
STATUSES = ["ok", "warn", "danger", "stale"]


def usage(**kw):
    base = dict(at=NOW, week=75, week_resets=RESETS, five=16, five_resets=NOW + 3600,
                breakdown=[("Claude Code", 77), ("Chats", 22)], scoped=[("Fable", 13)])
    base.update(kw)
    return Usage(**base)


PROJ = {
    "collecting": Projection("collecting", None, None, None, None),
    "ok": Projection("ok", 8, 5, NOW + 5 * DAY, 91),
    "tight": Projection("tight", 12, 12, RESETS + 3600, 99),
    "exhaust": Projection("exhaust", 20, 30, NOW + DAY, 100),
    "full": Projection("full", None, None, NOW, 100),
}


def full_view(**kw):
    pts = [(NOW - 3 * DAY + i * 3600, min(75, i)) for i in range(0, 72, 3)]
    last = [(NOW - WEEK - 2 * DAY + i * 3600, i // 2) for i in range(0, 100, 5)]
    base = dict(usage=usage(), proj=PROJ["ok"], points=pts, last_points=last, color="ok",
                login_expires=NOW + 20 * DAY, stale_minutes=0)
    base.update(kw)
    return View(**base)


def in_bounds(img, regions):
    for x0, y0, x1, y1 in regions.values():
        assert 0 <= x0 < x1 <= img.width and 0 <= y0 < y1 <= img.height


@pytest.mark.parametrize("status", PROJ)
@pytest.mark.parametrize("color", STATUSES)
@pytest.mark.parametrize("login_notice", [False, True])
@pytest.mark.parametrize("folded", [False, True])
def test_main_popup_all_states(status, color, login_notice, folded):
    v = full_view(proj=PROJ[status], color=color, login_expires=NOW + 2 * DAY, login_notice=login_notice)
    img, regions = main_popup(v, folded=folded, now=NOW)
    assert img.width == (render.W_COMPACT if folded else render.W_FULL)
    assert img.height <= (110 if folded else 290)          # the bar stays small; the panel may be bigger
    assert {"fold", "mini"} <= set(regions)
    assert ("login" in regions) == login_notice
    in_bounds(img, regions)


def test_folded_is_much_smaller():
    full, _ = main_popup(full_view(), folded=False, now=NOW)
    small, _ = main_popup(full_view(), folded=True, now=NOW)
    assert small.height < full.height / 2
    assert full.width > small.width                         # the big panel is a bit wider


@pytest.mark.parametrize("folded", [False, True])
def test_size_buttons_are_easy_to_hit(folded):
    _, regions = main_popup(full_view(), folded=folded, now=NOW)
    for name in ("fold", "mini"):
        x0, y0, x1, y1 = regions[name]
        assert x1 - x0 >= 14 and y1 - y0 >= 14


def test_main_popup_without_data():
    img, regions = main_popup(View(usage=None, proj=None, message="Claude Code 로그인 필요"), folded=True, now=NOW)
    assert img.height <= 140 and "mini" in regions      # can still be shrunk to the chip


def test_main_popup_stale_and_sparse():
    v = View(usage=usage(five=None, five_resets=None, breakdown=[], scoped=[]), proj=None,
             stale_minutes=42, color="stale")
    main_popup(v, now=NOW)


def test_main_popup_points_outside_week_are_safe():
    v = full_view(usage=usage(week=100), proj=PROJ["full"], color="danger",
                  points=[(NOW - 30 * DAY, 5), (NOW + DAY, 120), (NOW, -3)],
                  last_points=[(NOW + 5 * DAY, 3), (NOW - 40 * DAY, 9)])
    main_popup(v, now=NOW)


def test_footer_drops_whole_parts_instead_of_cutting():
    f = render.F10()
    assert render._fit_parts(["CC 74%", "채팅 25%", "로그인 D-24", "Sonnet 21%"], f, 170) == "CC 74% · 채팅 25% · 로그인 D-24"
    assert render._fit_parts(["CC 74%", "A very very long part", "x 1%"], f, 80) == "CC 74% · x 1%"
    assert render._fit_parts([], f, 80) == ""


def test_long_footer_does_not_overflow():
    v = full_view(usage=usage(breakdown=[("A very long product name", 50), ("Another long one", 49)],
                              scoped=[("Some Model With A Long Name", 99)]))
    img, _ = main_popup(v, now=NOW)
    assert img.width == render.W_FULL


PALETTE = {render.BG, render.PANEL, render.BORDER, render.TEXT, render.DIM, render.FAINT,
           render.ORANGE, render.YELLOW, render.RED, render.GREY, render.KEY}


@pytest.mark.parametrize("folded", [False, True])
def test_popup_is_pixel_art(folded):
    """No anti-aliasing: only palette colors (plus the transparency key) appear."""
    v = full_view(proj=PROJ["exhaust"], color="danger", login_expires=NOW + DAY, login_notice=True)
    img, _ = main_popup(v, folded=folded, now=NOW)
    hexes = {"#%02x%02x%02x" % c for _, c in img.getcolors(10000)}
    assert hexes <= PALETTE


def test_theme_is_claude_orange():
    assert render.ORANGE == "#d97757"
    img, _ = main_popup(full_view(), now=NOW)
    hexes = {"#%02x%02x%02x" % c for _, c in img.getcolors(10000)}
    assert render.ORANGE in hexes


@pytest.mark.parametrize("pct", [0, 7, 75, 99, 100, None])
@pytest.mark.parametrize("size", [16, 32, 48, 64])
@pytest.mark.parametrize("color", STATUSES)
def test_tray_icon(pct, size, color):
    img = tray_icon(pct, color, False, size=size)
    assert img.size == (size, size) and img.mode == "RGBA"


def test_tray_icon_badge_changes_pixels():
    assert tray_icon(50, "ok", False, size=16).tobytes() != tray_icon(50, "ok", True, size=16).tobytes()


def test_tray_icon_status_changes_pixels():
    icons = {c: tray_icon(50, c, False, size=16).tobytes() for c in STATUSES}
    assert len(set(icons.values())) == len(STATUSES)


def test_alert_popup():
    img, regions = alert_popup("Claude 주간 90%", ["이 속도면 내일 18:00 소진", "(리셋 3시간 전)"], "danger", login=True)
    assert img.size == (render.AW, render.AH)
    assert {"close", "login"} <= set(regions)
    in_bounds(img, regions)
    img, regions = alert_popup("x", ["a"], "warn", login=False)
    assert "login" not in regions


def _korean_chars():
    src = "".join(p.read_text(encoding="utf-8") for p in Path(render.__file__).parent.glob("*.py"))
    return set(re.findall(r"[가-힣]", src)) | set("월화수목금토일")


@pytest.mark.parametrize("name", ["Galmuri11.ttf", "Galmuri11-Bold.ttf", "Galmuri9.ttf"])
def test_fonts_cover_every_character_we_draw(name):
    """Guards the font subsetting: every Hangul syllable in the source, plus ASCII and the
    symbols we use, must exist in the bundled fonts."""
    from fontTools.ttLib import TTFont

    cmap = TTFont(asset(name)).getBestCmap()
    needed = _korean_chars() | {chr(c) for c in range(0x20, 0x7F)} | set("▶·%")
    missing = sorted(ch for ch in needed if ord(ch) not in cmap)
    assert not missing, f"{name} lacks {''.join(missing)}"


@pytest.mark.parametrize("minutes,text", [(0, "방금"), (1, "1분 전"), (59, "59분 전"), (60, "1시간 전"),
                                          (60 * 23 + 59, "23시간 전"), (60 * 24, "1일 전"), (60 * 24 * 9, "9일 전")])
def test_ago(minutes, text):
    assert render.ago(minutes) == text


def test_footer_fresh():
    left, right, stale = render.footer(full_view(), now=NOW)
    assert left.startswith("CC 77%") and right.endswith("기준") and not stale


def test_footer_stale_shows_age_and_reason():
    v = full_view(stale_minutes=125, message="인증 실패 (HTTP 403)")
    left, right, stale = render.footer(v, now=NOW)
    assert stale and right == "! 2시간 전 데이터" and left == "인증 실패 (HTTP 403)"


def test_graph_line_ends_at_reading_time_when_stale():
    """Drawing must not extend the line to 'now' while the data is old."""
    u = usage(at=NOW - 2 * DAY)
    v = full_view(usage=u, stale_minutes=2 * 24 * 60, color="stale")
    main_popup(v, now=NOW)   # must not raise; covered visually


# ---------------------------------------------------------------- tiny chip (minimized widget)

@pytest.mark.parametrize("pct", [0, 7, 77, 100, None])
@pytest.mark.parametrize("color", STATUSES)
@pytest.mark.parametrize("badge", [False, True])
def test_mini_chip(pct, color, badge):
    img = render.mini_chip(pct, color, badge)
    # Small, but big enough to read and click.
    assert 44 <= img.width <= 72 and 24 <= img.height <= 32
    hexes = {"#%02x%02x%02x" % c for _, c in img.getcolors(10000)}
    assert hexes <= PALETTE


def test_mini_chip_shows_status():
    a = render.mini_chip(77, "ok", False).tobytes()
    b = render.mini_chip(77, "danger", False).tobytes()
    c = render.mini_chip(78, "ok", False).tobytes()
    assert len({a, b, c}) == 3


@pytest.mark.parametrize("folded", [False, True])
def test_login_button_when_auth_failed(folded):
    v = full_view(needs_login=True, login_notice=False, message="인증 실패 (HTTP 401)")
    _, regions = main_popup(v, folded=folded, now=NOW)
    assert "login" in regions


def test_login_button_without_any_data():
    v = View(usage=None, proj=None, message="Claude Code 로그인 필요", needs_login=True)
    _, regions = main_popup(v, folded=True, now=NOW)
    assert "login" in regions


@pytest.mark.parametrize("folded", [False, True])
def test_idle_week_does_not_show_a_made_up_reset_time(folded, monkeypatch):
    """No usage yet this week -> the server gives no reset time; don't print a fake one."""
    drawn = []
    real_text = render.ImageDraw.ImageDraw.text

    def spy(self, xy, text, *a, **k):
        drawn.append(text)
        return real_text(self, xy, text, *a, **k)

    monkeypatch.setattr(render.ImageDraw.ImageDraw, "text", spy)
    v = full_view(usage=usage(week=0, resets_known=False), proj=PROJ["collecting"])
    main_popup(v, folded=folded, now=NOW)
    assert not any(t.startswith("리셋 ") for t in drawn)
    assert any("이번 주 사용 없음" in t for t in drawn)


# ---------------------------------------------------------------- pixel right-click menu

MENU = [("refresh", "지금 새로고침", "cmd"), ("-", "", "sep"),
        ("full", "크게 (그래프)", "radio"), ("compact", "작은 막대", "radio"), ("mini", "아주 작게 (아이콘)", "radio"),
        ("reset", "위치 초기화", "cmd"), ("-", "", "sep"),
        ("config", "설정 파일 열기", "cmd"), ("startup", "Windows 시작 시 실행", "check"), ("-", "", "sep"),
        ("quit", "종료 (v1.0.0)", "cmd")]


def test_menu_has_a_region_per_item():
    img, regions = render.context_menu(MENU, hover=None, checked={"compact", "startup"})
    assert set(regions) == {k for k, _, kind in MENU if kind != "sep"}
    in_bounds(img, regions)
    rows = sorted(regions.values(), key=lambda r: r[1])
    for (a, b) in zip(rows, rows[1:]):
        assert a[3] <= b[1]                            # no overlap between rows
        assert a[3] - a[1] >= 16                       # easy to hit


def test_menu_hover_and_checks_change_pixels():
    base, _ = render.context_menu(MENU, hover=None, checked=set())
    hovered, _ = render.context_menu(MENU, hover="quit", checked=set())
    checked, _ = render.context_menu(MENU, hover=None, checked={"startup"})
    assert len({base.tobytes(), hovered.tobytes(), checked.tobytes()}) == 3


def test_menu_is_pixel_art():
    img, _ = render.context_menu(MENU, hover="full", checked={"full", "startup"})
    hexes = {"#%02x%02x%02x" % c for _, c in img.getcolors(10000)}
    assert hexes <= PALETTE
