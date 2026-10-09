"""Real Tk windows, driven programmatically (they flash briefly in the screen corner).
Checks what pure-logic tests can't: sizes on screen, mode changes, drag + snap, the pixel
menu, alert placement, and that nothing ever takes the keyboard focus."""
import ctypes
import os
import time
from types import SimpleNamespace as Ev

import pytest

from tray import render, ui as ui_mod, winsys
from tray.alerts import Alert
from tray.history import Projection
from tray.usage_api import Usage

NOW = time.time()


def our_window_has_focus() -> bool:
    h = winsys.foreground_window()
    pid = ctypes.c_ulong()
    ctypes.windll.user32.GetWindowThreadProcessId(ctypes.c_void_p(h), ctypes.byref(pid))
    return pid.value == os.getpid()


def view(**kw):
    u = Usage(at=NOW, week=77, week_resets=NOW + 86400, five=30, five_resets=NOW + 3600,
              breakdown=[("Claude Code", 70), ("Chats", 30)])
    base = dict(usage=u, proj=Projection("ok", 10, 8, None, 88), color="ok", stale_minutes=0,
                login_expires=NOW + 20 * 86400)
    base.update(kw)
    return render.View(**base)


@pytest.fixture(autouse=True)
def isolate_from_the_real_mouse(monkeypatch):
    """Clicking anywhere on the PC (or playing a full-screen video) while these tests run
    must not change their outcome: fake the global mouse buttons and full-screen state."""
    monkeypatch.setattr(winsys, "left_button_down", lambda: False)
    monkeypatch.setattr(winsys, "right_button_down", lambda: False)
    monkeypatch.setattr(winsys, "fullscreen_busy", lambda: False)


@pytest.fixture(scope="module")
def shared_ui():
    """One Tk for the whole module: creating and destroying Tk repeatedly in one process
    is flaky in Tcl itself (the real app creates it once)."""
    calls = {}
    menu = {"refresh": lambda: calls.__setitem__("refresh", calls["refresh"] + 1),
            "open_config": lambda: calls.__setitem__("config", calls["config"] + 1),
            "startup_enabled": lambda: True,
            "toggle_startup": lambda: calls.__setitem__("startup", calls["startup"] + 1),
            "quit": lambda: calls.__setitem__("quit", calls["quit"] + 1),
            "version": "9.9.9"}
    u = ui_mod.UI(8, mode="compact", anchor=None, on_change=lambda **kw: calls["change"].append(kw),
                  on_login=lambda: calls.__setitem__("login", calls["login"] + 1), menu=menu)
    u.calls = calls
    yield u
    u.root.destroy()


@pytest.fixture
def app(shared_ui):
    u = shared_ui
    u.calls.clear()
    u.calls.update({"change": [], "login": 0, "refresh": 0, "config": 0, "startup": 0, "quit": 0})
    u._close_menu()
    u._close_alert()
    u._alerts.items.clear()
    u.mode = u.resting = "compact"
    u.anchor = None
    u.post("view", (view(), []))
    pump(u)
    return u


def pump(u, ms=250):
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        u.root.update()
        time.sleep(0.01)


def rect(w):
    w.win.update()
    return w.win.winfo_x(), w.win.winfo_y(), w.win.winfo_width(), w.win.winfo_height()


def bottom_right(w):
    x, y, ww, hh = rect(w)
    return x + ww, y + hh


def test_starts_as_the_small_bar_bottom_right(app):
    assert app.mode == "compact" and app.widget.visible
    x, y, w, h = rect(app.widget)
    assert (w, h) == (300 * app.k, 76 * app.k)
    work = winsys.work_area()
    assert bottom_right(app.widget) == (work[2] - 8 * app.k, work[3] - 8 * app.k)
    assert not our_window_has_focus()


def test_resizing_keeps_the_bottom_right_corner(app):
    corner = bottom_right(app.widget)
    app._last_wheel = 0
    app._wheel(120)
    pump(app)
    assert app.mode == "full" and rect(app.widget)[2:] == (340 * app.k, 238 * app.k)
    assert bottom_right(app.widget) == corner
    app._last_wheel = 0; app._wheel(-120)
    app._last_wheel = 0; app._wheel(-120)
    pump(app)
    assert app.mode == "mini" and bottom_right(app.widget) == corner
    w, h = rect(app.widget)[2:]
    assert 44 * app.k <= w and 24 * app.k <= h
    assert {"widget_mode": "mini"} in app.calls["change"]
    assert not our_window_has_focus()


def test_buttons_and_click(app):
    app._widget_click("mini"); pump(app)
    assert app.mode == "mini"
    app._widget_click(None); pump(app)          # click the chip
    assert app.mode == "compact"
    app._widget_click("fold"); pump(app)
    assert app.mode == "full"
    app._widget_click("fold"); pump(app)
    assert app.mode == "compact"


def test_single_click_on_the_bar_does_nothing(app):
    app._widget_click(None); pump(app)
    assert app.mode == "compact"
    app._go("full"); pump(app, 50)
    app._widget_click(None); pump(app)
    assert app.mode == "full"


def test_double_click_flips_between_bar_and_big_panel(app):
    app._last_mode_change = 0
    app._widget_double(None); pump(app)
    assert app.mode == "full"
    app._last_mode_change = 0
    app._widget_double(None); pump(app)
    assert app.mode == "compact"


def test_double_click_on_a_button_is_left_to_the_button(app):
    app._last_mode_change = 0
    app._widget_double("fold"); pump(app)
    assert app.mode == "compact"


def test_double_click_on_the_chip_stops_at_the_bar(app):
    """First click opens the chip into the bar under the cursor; the second click of the
    same double click must not go on to the big panel."""
    app._go("mini"); pump(app, 50)
    app._widget_click(None)                    # first click: chip -> bar
    app._widget_double(None); pump(app)        # same double click, second press
    assert app.mode == "compact"


def test_drag_snaps_to_the_edge_and_is_saved(app):
    w = app.widget
    x0, y0, ww, hh = rect(w)
    work = winsys.work_area()
    w._press(Ev(x_root=x0 + 5, y_root=y0 + 5, x=5, y=5))
    dx = (work[2] - ww - 10) - x0 - 0          # end 10px short of the right edge
    w._motion(Ev(x_root=x0 + 5 + dx - 200, y_root=y0 - 300, x=0, y=0))
    w._motion(Ev(x_root=x0 + 5 + dx, y_root=y0 - 300, x=0, y=0))
    w._release(Ev(x_root=x0 + 5 + dx, y_root=y0 - 300, x=0, y=0))
    pump(app)
    assert bottom_right(w)[0] == work[2]      # stuck to the edge
    assert app.calls["change"][-1]["widget_pos"][0] == work[2]
    assert app.mode == "compact"              # a drag is not a click


def test_pixel_menu_opens_without_focus_and_runs_items(app):
    x, y, w, h = rect(app.widget)
    app._open_menu(x + 20, y + 20)
    pump(app)
    assert app.menu.visible and not our_window_has_focus()
    mx, my, mw, mh = rect(app.menu)
    work = winsys.work_area()
    assert mx >= work[0] and my >= work[1] and mx + mw <= work[2] and my + mh <= work[3]
    app._menu_hover("quit"); pump(app, 50)
    app._menu_click("mini"); pump(app)
    assert not app.menu.visible and app.mode == "mini"
    app._open_menu(x + 20, y + 20); pump(app, 50)
    app._menu_click("refresh"); app._open_menu(x + 20, y + 20); app._menu_click("startup")
    app._open_menu(x + 20, y + 20); app._menu_click("quit")
    assert (app.calls["refresh"], app.calls["startup"], app.calls["quit"]) == (1, 1, 1)


def test_menu_separator_click_keeps_it_open(app):
    app._open_menu(100, 100); pump(app, 50)
    app._menu_click(None)
    assert app.menu.visible


def test_alert_appears_above_the_widget_without_covering_it(app):
    app.post("view", (view(), [Alert("Claude 주간 90%", ["a", "b"], "danger", login=True)]))
    pump(app, 500)
    assert app.alert.visible and not our_window_has_focus()
    ax, ay, aw, ah = rect(app.alert)
    wx, wy, ww, wh = rect(app.widget)
    assert ay + ah <= wy                       # above, not overlapping
    assert ax + aw == wx + ww                  # right-aligned
    app._alert_click("login")
    assert app.calls["login"] == 1 and not app.alert.visible


def test_login_button_in_the_bar(app):
    app.post("view", (view(needs_login=True, message="인증 실패 (HTTP 401)"), []))
    pump(app)
    assert "login" in app.widget.regions
    app._widget_click("login")
    assert app.calls["login"] == 1


def test_one_bad_message_does_not_stop_the_ui_and_quit_gets_through(app):
    app.post("view", "not a tuple")            # would raise inside the handler
    app.post("view", (view(usage=Usage(at=NOW, week=12, week_resets=NOW + 86400)), []))
    pump(app, 500)
    assert app.view.usage.week == 12
    quit_called = []
    app.root.quit = lambda: quit_called.append(1)
    app.post("quit")
    pump(app, 400)
    assert quit_called


def test_mid_drag_poll_does_not_yank_the_widget(app):
    w = app.widget
    x0, y0, *_ = rect(w)
    w._press(Ev(x_root=x0 + 5, y_root=y0 + 5, x=5, y=5))
    w._motion(Ev(x_root=x0 - 300, y_root=y0 - 300, x=0, y=0))
    pos = rect(w)[:2]
    app.post("view", (view(), []))
    pump(app, 400)
    assert rect(w)[:2] == pos                  # still where the user is holding it
    w._release(Ev(x_root=x0 - 300, y_root=y0 - 300, x=0, y=0))


def test_clicking_the_widget_to_close_the_menu_only_closes_it(app):
    x, y, w, h = rect(app.widget)
    app._open_menu(x + 20, y + 20); pump(app, 50)
    app._close_menu(by_click=True)            # what the outside-click watcher does on mouse-down
    app._widget_click(None)                   # ...then the same click's mouse-up reaches the widget
    pump(app)
    assert app.mode == "compact" and not app.menu.visible


def test_one_fast_wheel_flick_moves_one_step(app):
    """Scrolling a document past the widget sends several notches at once: one step only."""
    app._go("mini"); pump(app, 50)
    app._last_wheel = 0
    for _ in range(5):
        app._wheel(120)
    pump(app)
    assert app.mode == "compact"


def test_outside_click_collapses_the_big_panel(app, monkeypatch):
    app._go("full"); pump(app, 50)
    x, y, w, h = rect(app.widget)
    monkeypatch.setattr(winsys, "cursor_pos", lambda: (x - 50, y - 50))   # outside
    monkeypatch.setattr(winsys, "left_button_down", lambda: False)        # button up first...
    pump(app, 150)
    monkeypatch.setattr(winsys, "left_button_down", lambda: True)         # ...then a new press
    pump(app, 200)
    assert app.mode == "compact"


def test_full_screen_hides_and_restores_the_widget(app, monkeypatch):
    monkeypatch.setattr(winsys, "fullscreen_busy", lambda: True)
    app._fullscreen_watch(); pump(app, 50)
    assert not app.widget.visible
    monkeypatch.setattr(winsys, "fullscreen_busy", lambda: False)
    app._fullscreen_watch(); pump(app, 50)
    assert app.widget.visible
