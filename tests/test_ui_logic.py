"""Pure logic behind the Tk windows (the windows themselves are checked by hand)."""
import pytest

from tray.alerts import Alert
from tray.ui import AlertQueue, next_mode


def A(title, color="warn"):
    return Alert(title, ["x"], color)


def test_queue_fifo():
    q = AlertQueue()
    q.push([A("a"), A("b")])
    assert [q.pop().title, q.pop().title, q.pop()] == ["a", "b", None]


def test_queue_keeps_latest_of_same_title():
    q = AlertQueue()
    first, second = Alert("사용량 조회 실패", ["old"], "stale"), Alert("사용량 조회 실패", ["new"], "stale")
    q.push([first, A("b")])
    q.push([second])
    assert [a.lines[0] if a.title == "사용량 조회 실패" else a.title for a in q.items] == ["b", "new"]


def test_queue_is_bounded():
    """A day of full-screen video must not pile up dozens of stale popups."""
    q = AlertQueue(limit=3)
    q.push([A(str(i)) for i in range(10)])
    assert [a.title for a in q.items] == ["7", "8", "9"]
    assert len(q) == 3


# ---------------------------------------------------------------- widget placement

from tray.ui import anchor_of, default_anchor, place  # noqa: E402

WORK = (0, 0, 1920, 1040)


def test_default_anchor_is_bottom_right_above_taskbar():
    assert default_anchor(WORK, margin=8) == (1912, 1032)


def test_place_grows_up_and_left_from_anchor():
    # Folding/unfolding keeps the bottom-right corner where it was.
    assert place((1912, 1032), (300, 74), WORK) == (1612, 958)
    assert place((1912, 1032), (300, 212), WORK) == (1612, 820)


def test_place_keeps_window_fully_on_screen():
    assert place((100, 50), (300, 212), WORK) == (0, 0)            # dragged into the top-left corner
    assert place((5000, 5000), (300, 74), WORK) == (1620, 966)     # monitor removed / resolution change
    assert place((1000, 500), (300, 74), (1920, 0, 3840, 1080)) == (1920, 426)


def test_anchor_of_window():
    assert anchor_of(1612, 958, (300, 74)) == (1912, 1032)


def test_anchor_survives_round_trip():
    a = (1500, 900)
    x, y = place(a, (300, 74), WORK)
    assert anchor_of(x, y, (300, 74)) == a


@pytest.mark.parametrize("mode,action,expected", [
    ("compact", "fold", "full"), ("full", "fold", "compact"),
    ("compact", "mini", "mini"), ("full", "mini", "mini"),
    # a single click on the body only opens the chip; the bar and the panel ignore it
    ("mini", "click", "compact"), ("compact", "click", "compact"), ("full", "click", "full"),
    # a double click flips between the bar and the big panel
    ("compact", "double", "full"), ("full", "double", "compact"), ("mini", "double", "compact"),
    ("full", "outside", "compact"), ("compact", "outside", "compact"), ("mini", "outside", "mini"),
    # mouse wheel over the widget: up = bigger, down = smaller
    ("mini", "bigger", "compact"), ("compact", "bigger", "full"), ("full", "bigger", "full"),
    ("full", "smaller", "compact"), ("compact", "smaller", "mini"), ("mini", "smaller", "mini"),
])
def test_next_mode(mode, action, expected):
    assert next_mode(mode, action, resting="compact") == expected


def test_closing_full_returns_to_the_resting_size():
    # Opened from the chip: clicking outside goes back to the chip, not the bar.
    assert next_mode("full", "outside", resting="mini") == "mini"
    assert next_mode("full", "fold", resting="mini") == "compact"   # ▴ explicitly asks for the bar


# ---------------------------------------------------------------- snapping to edges

from tray.ui import snap  # noqa: E402


@pytest.mark.parametrize("pos,expected", [
    ((1000, 500), (1000, 500)),          # nowhere near an edge: unchanged
    ((1580, 900), (1580, 900)),          # 40px from the right edge: unchanged
    ((1603, 949), (1603, 949)),          # 17px from right and bottom: just outside the magnet
    ((1620 - 10, 500), (1620, 500)),     # 10px from the right edge -> flush right
    ((1625, 500), (1620, 500)),          # a bit past it (dragged off) -> flush right
    ((12, 500), (0, 500)),               # near the left edge
    ((-30, 500), (-30, 500)),            # far off-screen: not snapping (place() clamps later)
    ((800, 960), (800, 966)),            # near the taskbar (bottom of the work area)
    ((800, 9), (800, 0)),                # near the top
    ((1612, 960), (1620, 966)),          # corner: both axes snap
])
def test_snap(pos, expected):
    assert snap(*pos, (300, 74), WORK, dist=16) == expected


def test_snap_on_second_monitor():
    second = (1920, 0, 3840, 1040)
    assert snap(1930, 300, (300, 74), second, dist=16) == (1920, 300)
    assert snap(3530, 300, (300, 74), second, dist=16) == (3540, 300)


# ---------------------------------------------------------------- alert next to the widget

from tray.ui import alert_position  # noqa: E402


def test_alert_sits_above_the_widget():
    widget = (1620, 966, 300, 74)                     # x, y, w, h at the bottom-right
    x, y = alert_position(widget, (240, 64), WORK, gap=6)
    assert (x + 240, y + 64) == (1920, 966 - 6)       # right-aligned, just above


def test_alert_goes_below_when_the_widget_is_at_the_top():
    widget = (1620, 0, 300, 74)                       # snapped to the top edge
    x, y = alert_position(widget, (240, 64), WORK, gap=6)
    assert y == 74 + 6 and x + 240 == 1920


def test_alert_never_covers_a_tall_widget_near_the_top():
    widget = (0, 30, 340, 238)                        # big panel, little room above
    x, y = alert_position(widget, (240, 64), WORK, gap=6)
    assert y >= 30 + 238                              # below it, not on top of it


# ---------------------------------------------------------------- menu placement

from tray.ui import menu_position  # noqa: E402


def test_menu_opens_at_the_cursor():
    assert menu_position((500, 300), (160, 200), WORK) == (500, 300)


def test_menu_flips_left_and_up_near_the_bottom_right():
    # Right-click on the widget in the bottom-right corner: open up and to the left.
    assert menu_position((1900, 1000), (160, 200), WORK) == (1741, 801)


@pytest.mark.parametrize("cursor", [(500, 300), (1900, 1000), (1900, 300), (500, 1000), (0, 0), (1919, 1039)])
def test_the_cursor_is_always_inside_the_opened_menu(cursor):
    """Otherwise the click that opened it can count as a click outside and close it."""
    x, y = menu_position(cursor, (160, 200), WORK)
    assert x <= cursor[0] < x + 160 and y <= cursor[1] < y + 200


from tray.ui import click_dismisses_menu  # noqa: E402


@pytest.mark.parametrize("now,closed_at,dismiss", [
    (10.10, 10.00, True),     # the press that closed the menu: its release must do nothing else
    (10.45, 10.00, True),
    (10.70, 10.00, False),    # a later, separate click
    (10.00, None, False),     # menu wasn't open
])
def test_click_that_closed_the_menu_does_nothing_else(now, closed_at, dismiss):
    assert click_dismisses_menu(now, closed_at) is dismiss


def test_menu_stays_on_screen():
    x, y = menu_position((50, 1030), (160, 1100), WORK)
    assert x >= 0 and y >= 0
