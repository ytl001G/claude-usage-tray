"""Tk windows: the on-screen widget (tiny chip / small bar / big panel) and the alert popup.

Tk runs on the main thread. Other threads talk to it only through `UI.post()`.
"""
import logging
import queue
import time
import tkinter as tk

from PIL import ImageTk

from . import render, winsys
from .alerts import Alert

log = logging.getLogger(__name__)

MARGIN = 8
GAP = 6
DRAIN_MS = 200
SLIDE_MS = 16
WATCH_MS = 50
FULLSCREEN_MS = 2_000
DRAG_THRESHOLD = 4   # pixels before a press becomes a drag
MENU_GRACE = 0.3     # seconds after opening the menu during which presses don't close it
DISMISS_WINDOW = 0.5 # seconds: a widget click right after closing the menu only closed it
WHEEL_STEP = 0.4     # seconds: one size step per wheel flick, however many notches it sends
DOUBLE_AFTER_CHANGE = 0.5  # seconds: a double click right after a size change belongs to it
SNAP = 16            # pixels: closer than this to a screen edge and the widget sticks to it


# ---------------------------------------------------------------- pure logic (tested)

class AlertQueue:
    """Pending alerts. A newer alert replaces an older one with the same title, and only
    the latest few are kept, so a long full-screen session can't pile up stale popups."""

    def __init__(self, limit: int = 3):
        self.limit = limit
        self.items: list[Alert] = []

    def push(self, alerts: list[Alert]) -> None:
        for a in alerts:
            self.items = [x for x in self.items if x.title != a.title] + [a]
        self.items = self.items[-self.limit:]

    def pop(self) -> Alert | None:
        return self.items.pop(0) if self.items else None

    def __len__(self):
        return len(self.items)


def next_mode(mode: str, action: str, resting: str) -> str:
    """Widget size after an action. Modes: "mini" (tiny chip), "compact" (small bar),
    "full" (big panel). `resting` is the small size the user last chose; clicking outside
    the big panel goes back to it."""
    if action == "mini":
        return "mini"
    if action == "fold":
        return "compact" if mode == "full" else "full"
    if action == "click":      # a single click only opens the chip
        return "compact" if mode == "mini" else mode
    if action == "double":     # flip between the bar and the big panel
        return {"compact": "full", "full": "compact"}.get(mode, "compact")
    if action == "bigger":
        return {"mini": "compact", "compact": "full"}.get(mode, "full")
    if action == "smaller":
        return {"full": "compact", "compact": "mini"}.get(mode, "mini")
    if action == "outside" and mode == "full":
        return resting
    return mode


def default_anchor(work: tuple[int, int, int, int], margin: int) -> tuple[int, int]:
    """Bottom-right corner of the widget by default: just above the taskbar."""
    return work[2] - margin, work[3] - margin


def place(anchor: tuple[int, int], size: tuple[int, int], work: tuple[int, int, int, int]) -> tuple[int, int]:
    """Top-left for a window whose bottom-right corner sits at `anchor`, so changing size
    grows it up and to the left. Always kept fully inside the work area."""
    left, top, right, bottom = work
    w, h = size
    x = min(max(anchor[0] - w, left), right - w)
    y = min(max(anchor[1] - h, top), bottom - h)
    return max(x, left), max(y, top)


def snap(x: int, y: int, size: tuple[int, int], work: tuple[int, int, int, int], dist: int) -> tuple[int, int]:
    """Stick to an edge of the work area (screen edges and the taskbar) when within `dist`."""
    left, top, right, bottom = work
    w, h = size
    if abs(x - left) <= dist:
        x = left
    elif abs(x + w - right) <= dist:
        x = right - w
    if abs(y - top) <= dist:
        y = top
    elif abs(y + h - bottom) <= dist:
        y = bottom - h
    return x, y


def anchor_of(x: int, y: int, size: tuple[int, int]) -> tuple[int, int]:
    return x + size[0], y + size[1]


def menu_position(cursor: tuple[int, int], size: tuple[int, int],
                  work: tuple[int, int, int, int]) -> tuple[int, int]:
    """Open at the cursor; flip left/up when it would run off the work area. The cursor
    always ends up inside the menu, so the opening click can't count as an outside click."""
    left, top, right, bottom = work
    cx, cy = cursor
    w, h = size
    x = cx if cx + w <= right else cx - w + 1
    y = cy if cy + h <= bottom else cy - h + 1
    return min(max(x, left), max(left, right - w)), min(max(y, top), max(top, bottom - h))


def click_dismisses_menu(now: float, closed_at: float | None) -> bool:
    """The mouse-down that closed the menu also produces a mouse-up on the widget; that
    click was meant to dismiss the menu, not to resize the widget."""
    return closed_at is not None and 0 <= now - closed_at < DISMISS_WINDOW


def alert_position(widget: tuple[int, int, int, int], size: tuple[int, int],
                   work: tuple[int, int, int, int], gap: int) -> tuple[int, int]:
    """Right-aligned with the widget, just above it, or just below it when there isn't
    room above (widget snapped to the top). Never on top of the widget."""
    wx, wy, ww, wh = widget
    w, h = size
    left, top, right, bottom = work
    x = min(max(wx + ww - w, left), right - w)
    y = wy - gap - h
    if y < top:
        y = wy + wh + gap
    return x, y


# ---------------------------------------------------------------- windows

class UI:
    def __init__(self, popup_seconds: int, *, mode: str = "compact", anchor=None,
                 on_login, on_change, menu):
        """on_change(**settings) persists widget_mode / widget_pos.
        menu: callbacks for the right-click menu (refresh, open_config, startup_enabled,
        toggle_startup, quit) and "version"."""
        winsys.enable_dpi_awareness()
        # Creating Tk grabs the foreground even with every window hidden; remember who had
        # it and hand it back once the windows exist.
        previous = winsys.foreground_window()
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.report_callback_exception = lambda *exc: log.error("tk callback", exc_info=exc)
        self.k = winsys.ui_scale(winsys.system_dpi())   # updated per monitor in _show_widget
        self.popup_ms = int(popup_seconds * 1000)
        self.on_login = on_login
        self.on_change = on_change
        self.q: queue.Queue = queue.Queue()
        self.view: render.View | None = None
        self.mode = self.resting = mode if mode in ("compact", "mini") else "compact"
        self.anchor = tuple(anchor) if anchor else None
        self._paused_for_fullscreen = False

        # Neither window ever takes focus, so they never interrupt typing in another app.
        self.widget = _PixelWindow(self.root, self.k, self._widget_click, draggable=True,
                                   on_drag=self._dragged, on_wheel=self._wheel, on_menu=self._open_menu,
                                   on_double=self._widget_double,
                                   snap_to=lambda x, y, size: snap(x, y, size, winsys.work_area_at(
                                       x + size[0] // 2, y + size[1] // 2), SNAP * self.k))
        self._watch_id = None
        self._menu_spec = menu
        self.menu = _PixelWindow(self.root, self.k, self._menu_click, on_hover=self._menu_hover)
        self._menu_hover_key = None
        self._startup_on = False
        self._menu_opened_at = 0.0
        self._menu_closed_by_click_at = None
        self._last_wheel = 0.0
        self._last_mode_change = 0.0

        self.alert = _PixelWindow(self.root, self.k, self._alert_click)
        self.alert.win.bind("<Enter>", lambda e: self._alert_hold(True))
        self.alert.win.bind("<Leave>", lambda e: self._alert_hold(False))
        self._alerts = AlertQueue()
        self._current: Alert | None = None
        self._alert_timer = None

        self.root.update_idletasks()
        winsys.give_back_foreground(previous)
        self._show_widget()
        self.root.after(100, self._drain)
        self.root.after(FULLSCREEN_MS, self._fullscreen_watch)

    # ------------------------------------------------------------ cross-thread entry

    def post(self, kind: str, payload=None) -> None:
        self.q.put((kind, payload))

    def run(self) -> None:
        self.root.mainloop()

    def _drain(self):
        """Handle messages from other threads. One failing message must not stop the
        loop, and "quit" must always get through."""
        try:
            while True:
                try:
                    kind, payload = self.q.get_nowait()
                except queue.Empty:
                    break
                if kind == "quit":
                    self.root.quit()
                    return
                try:
                    self._handle(kind, payload)
                except Exception:
                    log.exception("ui: %s", kind)
        finally:
            self.root.after(DRAIN_MS, self._drain)

    def _handle(self, kind, payload):
        if kind == "view":
            self.view, alerts = payload
            self._alerts.push(alerts)
            if not self._paused_for_fullscreen:
                self._show_widget()
            self._next_alert()
        elif kind == "alert":
            self._alerts.push([payload])
            self._next_alert()

    # ------------------------------------------------------------ widget

    def _render(self):
        v = self.view or render.View(usage=None, proj=None, message="불러오는 중...")
        if self.mode == "mini":
            pct = v.usage.week if v.usage else None
            return render.mini_chip(pct, v.color if v.usage else "stale", v.login_notice), {}
        return render.main_popup(v, folded=self.mode == "compact")

    def _anchor(self):
        return self.anchor or default_anchor(winsys.work_area(), MARGIN * self.k)

    def _show_widget(self):
        if self.widget.dragging:
            return  # a poll arrived mid-drag: redraw after the drop instead of jumping
        anchor = self._anchor()
        # Scale for the monitor the widget is on (monitors can have different DPI).
        self.k = winsys.ui_scale(winsys.dpi_at(anchor[0] - 1, anchor[1] - 1))
        img, regions = self._render()
        self.widget.set_image(img, regions, self.k)
        size = (img.width * self.k, img.height * self.k)
        # The monitor the widget is on (a second screen keeps its own edges).
        x, y = place(anchor, size, winsys.work_area_at(anchor[0] - 1, anchor[1] - 1))
        self.widget.show(x, y)
        self._ensure_watch()

    def _set_mode(self, action: str):
        new = next_mode(self.mode, action, self.resting)
        if new == self.mode:
            return
        self._last_mode_change = time.monotonic()
        self.mode = new
        if new in ("compact", "mini") and new != self.resting:
            self.resting = new
            self.on_change(widget_mode=new)
        self._show_widget()

    def _ensure_watch(self):
        """Watch for clicks outside while the big panel or the menu is open."""
        if self._watch_id is None and (self.mode == "full" or self.menu.visible):
            self._watch_id = self.root.after(WATCH_MS, self._watch_outside_click, True)

    def _watch_outside_click(self, was_down):
        self._watch_id = None
        if not (self.mode == "full" or self.menu.visible) or not self.widget.visible:
            return
        down = winsys.left_button_down() or winsys.right_button_down()
        if down and not was_down:
            pos = winsys.cursor_pos()
            in_menu = self.menu.visible and self.menu.contains(*pos)
            just_opened = time.monotonic() - self._menu_opened_at < MENU_GRACE
            if self.menu.visible and not in_menu and not just_opened:
                self._close_menu(by_click=True)
            if self.mode == "full" and not in_menu and not self.widget.contains(*pos):
                self._set_mode("outside")
        self._watch_id = self.root.after(WATCH_MS, self._watch_outside_click, down)

    def _widget_click(self, region):
        if click_dismisses_menu(time.monotonic(), self._menu_closed_by_click_at):
            self._menu_closed_by_click_at = None
            return
        if region == "login":
            self.on_login()
        elif region in ("fold", "mini"):
            self._set_mode(region)
        else:
            self._set_mode("click")

    def _widget_double(self, region):
        """Double click on the bar or the panel (not on a button) flips between them."""
        if region is not None:
            return  # buttons already acted on the single clicks
        if time.monotonic() - self._last_mode_change < DOUBLE_AFTER_CHANGE:
            return  # e.g. the chip just opened into the bar under this same double click
        if click_dismisses_menu(time.monotonic(), self._menu_closed_by_click_at):
            return
        self._set_mode("double")

    def _wheel(self, delta):
        now = time.monotonic()
        if now - self._last_wheel < WHEEL_STEP:
            return
        self._last_wheel = now
        self._set_mode("bigger" if delta > 0 else "smaller")

    def _dragged(self, x, y):
        self.anchor = anchor_of(x, y, self.widget.size())
        self.on_change(widget_pos=list(self.anchor))
        self._show_widget()  # redraw with the latest data and keep it fully on screen

    def _reset_position(self):
        self.anchor = None
        self.on_change(widget_pos=None)
        self._show_widget()

    def _go(self, mode: str):
        """Jump straight to a size (menu items name the size they lead to, so nothing
        depends on what the size was a moment earlier)."""
        if mode == self.mode:
            return
        self.mode = mode
        if mode in ("compact", "mini") and mode != self.resting:
            self.resting = mode
            self.on_change(widget_mode=mode)
        self._show_widget()

    def _menu_items(self):
        return [
            ("refresh", "지금 새로고침", "cmd"),
            ("-", "", "sep"),
            ("full", "크게 (그래프)", "radio"),
            ("compact", "작은 막대", "radio"),
            ("mini", "아주 작게 (아이콘)", "radio"),
            ("reset", "위치 초기화", "cmd"),
            ("-", "", "sep"),
            ("config", "설정 파일 열기", "cmd"),
            ("startup", "Windows 시작 시 실행", "check"),
            ("-", "", "sep"),
            ("quit", f"종료 (v{self._menu_spec['version']})", "cmd"),
        ]

    def _draw_menu(self):
        checked = {self.mode} | ({"startup"} if self._startup_on else set())
        img, regions = render.context_menu(self._menu_items(), self._menu_hover_key, checked)
        self.menu.set_image(img, regions, self.k)
        return img

    def _open_menu(self, x_root, y_root):
        """Pixel-art menu at the cursor. Like the widget, it never takes focus."""
        self._startup_on = bool(self._menu_spec["startup_enabled"]())
        self._menu_hover_key = None
        img = self._draw_menu()
        size = (img.width * self.k, img.height * self.k)
        x, y = menu_position((x_root, y_root), size, winsys.work_area_at(x_root, y_root))
        self.menu.show(x, y)
        self._menu_opened_at = time.monotonic()
        self._menu_closed_by_click_at = None
        self._ensure_watch()

    def _menu_hover(self, key):
        if key != self._menu_hover_key and self.menu.visible:
            self._menu_hover_key = key
            x, y = self.menu.position()
            self._draw_menu()
            self.menu.move(x, y)

    def _close_menu(self, by_click: bool = False):
        if by_click and self.menu.visible:
            self._menu_closed_by_click_at = time.monotonic()
        self.menu.hide()
        self._menu_hover_key = None

    def _menu_click(self, key):
        if key is None:
            return  # padding or a separator: keep the menu open
        self._close_menu()
        m = self._menu_spec
        actions = {
            "refresh": m["refresh"],
            "full": lambda: self._go("full"),
            "compact": lambda: self._go("compact"),
            "mini": lambda: self._go("mini"),
            "reset": self._reset_position,
            "config": m["open_config"],
            "startup": m["toggle_startup"],
            "quit": m["quit"],
        }
        actions[key]()

    def _fullscreen_watch(self):
        """Step aside while a game, video or presentation is full screen."""
        try:
            busy = winsys.fullscreen_busy()
            if busy and not self._paused_for_fullscreen:
                self._paused_for_fullscreen = True
                self._close_menu()
                self.widget.hide()
            elif not busy and self._paused_for_fullscreen:
                self._paused_for_fullscreen = False
                self._show_widget()
                self._next_alert()
        finally:
            self.root.after(FULLSCREEN_MS, self._fullscreen_watch)

    # ------------------------------------------------------------ alert popup

    def _next_alert(self):
        if self._current or not len(self._alerts) or self._paused_for_fullscreen:
            return
        self._current = self._alerts.pop()
        a = self._current
        img, regions = render.alert_popup(a.title, a.lines, a.color, a.login)
        self.alert.set_image(img, regions, self.k)
        size = (img.width * self.k, img.height * self.k)
        # Next to the widget (above, or below if it sits at the top), never covering it.
        if self.widget.visible:
            wx, wy = self.widget.position()
            ww, wh = self.widget.size()
            x, y = alert_position((wx, wy, ww, wh), size, winsys.work_area_at(wx, wy), GAP * self.k)
        else:
            anchor = self._anchor()
            x, y = place(anchor, size, winsys.work_area_at(anchor[0] - 1, anchor[1] - 1))
        self.alert.show(x, y + 12 * self.k)
        self._slide(x, y, 12 * self.k)
        self._alert_hold(False)

    def _slide(self, x, y, left):
        """Pop up from just below, like a game dialog box."""
        if left <= 0 or not self.alert.visible:
            return
        step = max(1, left // 3)
        self.alert.move(x, y + left - step)
        self.root.after(SLIDE_MS, self._slide, x, y, left - step)

    def _alert_hold(self, hold: bool):
        if self._alert_timer:
            self.root.after_cancel(self._alert_timer)
            self._alert_timer = None
        if not hold and self._current:
            self._alert_timer = self.root.after(self.popup_ms, self._close_alert)

    def _close_alert(self):
        self._alert_hold(True)
        self.alert.hide()
        self._current = None
        self.root.after(300, self._next_alert)

    def _alert_click(self, region):
        login = region == "login" and self._current is not None and self._current.login
        self._close_alert()
        if login:
            self.on_login()
        elif region != "close" and self.mode != "full":
            self.mode = "full"
            self._show_widget()


class _PixelWindow:
    """Borderless, always-on-top, never-focused window showing one pre-rendered image."""

    def __init__(self, root, k, on_click, draggable=False, on_drag=None, on_wheel=None, on_menu=None,
                 snap_to=None, on_hover=None, on_double=None):
        self.k = k
        self.on_click = on_click
        self.on_drag = on_drag
        self.snap_to = snap_to
        self.win = tk.Toplevel(root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.attributes("-transparentcolor", render.KEY)
        self.win.configure(bg=render.KEY)
        self.label = tk.Label(self.win, bd=0, highlightthickness=0, bg=render.KEY)
        self.label.pack()
        self.label.bind("<ButtonPress-1>", self._press)
        self.label.bind("<ButtonRelease-1>", self._release)
        if draggable:
            self.label.bind("<B1-Motion>", self._motion)
        if on_wheel:
            self.label.bind("<MouseWheel>", lambda e: on_wheel(e.delta))
        if on_menu:
            self.label.bind("<Button-3>", lambda e: on_menu(e.x_root, e.y_root))
        if on_double:
            self.label.bind("<Double-Button-1>", lambda e: on_double(self.region_at(e.x, e.y)))
        if on_hover:
            self.label.bind("<Motion>", lambda e: on_hover(self.region_at(e.x, e.y)))
            self.label.bind("<Leave>", lambda e: on_hover(None))
        self.regions = {}
        self.photo = None
        self.visible = False
        self._press_at = None
        self._dragging = False
        self._drag_to = None
        self.win.update_idletasks()
        self.hwnd = int(self.win.wm_frame(), 16)
        winsys.make_unobtrusive(self.hwnd)

    def set_image(self, img, regions, k=None):
        if k:
            self.k = k
        self.photo = ImageTk.PhotoImage(render.scale(img, self.k))
        self.label.configure(image=self.photo)
        self.regions = regions
        self._size = (img.width * self.k, img.height * self.k)

    def size(self):
        return self._size

    def position(self):
        return self.win.winfo_x(), self.win.winfo_y()

    def show(self, x, y):
        self.win.geometry(f"{self._size[0]}x{self._size[1]}+{x}+{y}")
        self.win.deiconify()
        winsys.show_no_activate(self.hwnd)
        self.visible = True

    def contains(self, x, y) -> bool:
        w = self.win
        return w.winfo_rootx() <= x < w.winfo_rootx() + w.winfo_width() and \
            w.winfo_rooty() <= y < w.winfo_rooty() + w.winfo_height()

    def move(self, x, y):
        self.win.geometry(f"+{x}+{y}")

    def hide(self):
        self.win.withdraw()
        self.visible = False

    @property
    def dragging(self) -> bool:
        return self._dragging

    def _press(self, e):
        self._press_at = (e.x_root, e.y_root, self.win.winfo_x(), self.win.winfo_y())
        self._dragging = False
        self._drag_to = None

    def _motion(self, e):
        if not self._press_at:
            return
        px, py, wx, wy = self._press_at
        dx, dy = e.x_root - px, e.y_root - py
        if not self._dragging and max(abs(dx), abs(dy)) < DRAG_THRESHOLD:
            return
        self._dragging = True
        x, y = wx + dx, wy + dy
        if self.snap_to:  # magnet: stick to screen edges while dragging
            x, y = self.snap_to(x, y, self._size)
        self._drag_to = (x, y)
        self.move(x, y)

    def _release(self, e):
        if self._dragging:
            self._dragging = False
            self._press_at = None
            # Use where we moved it, not winfo_x/y: Tk may not have applied the last move yet.
            if self.on_drag and self._drag_to:
                self.on_drag(*self._drag_to)
            return
        self._press_at = None
        self.on_click(self.region_at(e.x, e.y))

    def region_at(self, x, y):
        x, y = x // self.k, y // self.k
        for name, (x0, y0, x1, y1) in self.regions.items():
            if x0 <= x < x1 and y0 <= y < y1:
                return name
        return None
