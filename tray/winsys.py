"""Small Windows helpers: DPI, popup placement, no-activate windows, full-screen check,
single instance, and the HKCU Run entry for starting with Windows."""
import ctypes
import sys
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "ClaudeUsageTray"

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080   # no taskbar button
WS_EX_NOACTIVATE = 0x08000000   # never takes focus
WS_EX_TOPMOST = 0x00000008
SW_SHOWNOACTIVATE = 4
HWND_TOPMOST = -1
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010


# ---------------------------------------------------------------- pure helpers (tested)

def ui_scale(dpi: int) -> int:
    """Integer scale for pixel art. Rounds down until 175% so the UI stays small."""
    return max(1, int(dpi / 96 + 0.25))


def popup_position(work: tuple[int, int, int, int], size: tuple[int, int],
                   margin: int, stack: int = 0) -> tuple[int, int]:
    """Bottom-right corner of the work area (above the taskbar)."""
    left, top, right, bottom = work
    w, h = size
    return max(left, right - margin - w), max(top, bottom - margin - h - stack)


def startup_command(frozen: bool, executable: str, script: str | None) -> str:
    if frozen:
        return f'"{executable}"'
    pyw = Path(executable).with_name("pythonw.exe")
    return f'"{pyw if pyw.exists() else executable}" "{script}"'


class _Registry:
    """HKCU Run key. Only ever reads/writes our own value."""

    def get(self, name):
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
                return winreg.QueryValueEx(k, name)[0]
        except OSError:
            return None

    def set(self, name, value):
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)

    def delete(self, name):
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, name)
        except FileNotFoundError:
            pass


def startup_enabled(reg=None) -> bool:
    return (reg or _Registry()).get(RUN_VALUE) is not None


def set_startup(enabled: bool, command: str | None, reg=None) -> None:
    reg = reg or _Registry()
    if enabled:
        reg.set(RUN_VALUE, command)
    else:
        reg.delete(RUN_VALUE)


def own_startup_command() -> str:
    frozen = getattr(sys, "frozen", False)
    script = None if frozen else str(Path(sys.argv[0]).resolve())
    return startup_command(frozen, sys.executable, script)


# ---------------------------------------------------------------- Win32 (manual testing)

def enable_dpi_awareness() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def system_dpi() -> int:
    try:
        return ctypes.windll.user32.GetDpiForSystem()
    except (AttributeError, OSError):
        return 96


def work_area() -> tuple[int, int, int, int]:
    """Primary monitor minus the taskbar."""
    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
    r = RECT()
    ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0)  # SPI_GETWORKAREA
    return r.left, r.top, r.right, r.bottom


def work_area_at(x: int, y: int) -> tuple[int, int, int, int]:
    """Work area (minus taskbar) of the monitor at or nearest to the point."""
    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", RECT), ("rcWork", RECT),
                    ("dwFlags", ctypes.c_ulong)]

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    u = ctypes.windll.user32
    u.MonitorFromPoint.restype = ctypes.c_void_p
    u.MonitorFromPoint.argtypes = [POINT, ctypes.c_ulong]
    mon = u.MonitorFromPoint(POINT(int(x), int(y)), 2)  # MONITOR_DEFAULTTONEAREST
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    if not mon or not u.GetMonitorInfoW(ctypes.c_void_p(mon), ctypes.byref(info)):
        return work_area()
    r = info.rcWork
    return r.left, r.top, r.right, r.bottom


def make_unobtrusive(hwnd: int) -> None:
    """No taskbar button, never steals focus, always on top."""
    u = ctypes.windll.user32
    get = getattr(u, "GetWindowLongPtrW", u.GetWindowLongW)
    put = getattr(u, "SetWindowLongPtrW", u.SetWindowLongW)
    get.restype, get.argtypes = ctypes.c_ssize_t, [ctypes.c_void_p, ctypes.c_int]
    put.restype, put.argtypes = ctypes.c_ssize_t, [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
    put(hwnd, GWL_EXSTYLE, get(hwnd, GWL_EXSTYLE) | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_TOPMOST)


def show_no_activate(hwnd: int) -> None:
    u = ctypes.windll.user32
    u.ShowWindow(ctypes.c_void_p(hwnd), SW_SHOWNOACTIVATE)
    u.SetWindowPos(ctypes.c_void_p(hwnd), ctypes.c_void_p(HWND_TOPMOST), 0, 0, 0, 0,
                   SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE)


def foreground_window() -> int:
    u = ctypes.windll.user32
    u.GetForegroundWindow.restype = ctypes.c_void_p
    return u.GetForegroundWindow() or 0


def give_back_foreground(previous: int) -> None:
    """If our hidden window took the foreground, return it to the window that had it."""
    import os
    u = ctypes.windll.user32
    fg = foreground_window()
    if not previous or not fg or fg == previous:
        return
    pid = ctypes.c_ulong()
    u.GetWindowThreadProcessId(ctypes.c_void_p(fg), ctypes.byref(pid))
    if pid.value == os.getpid():
        u.SetForegroundWindow(ctypes.c_void_p(previous))


def dpi_at(x: int, y: int) -> int:
    """Effective DPI of the monitor at the point (per-monitor DPI), else the system DPI."""
    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
    try:
        u = ctypes.windll.user32
        u.MonitorFromPoint.restype = ctypes.c_void_p
        u.MonitorFromPoint.argtypes = [POINT, ctypes.c_ulong]
        mon = u.MonitorFromPoint(POINT(int(x), int(y)), 2)
        dx, dy = ctypes.c_uint(), ctypes.c_uint()
        if mon and ctypes.windll.shcore.GetDpiForMonitor(ctypes.c_void_p(mon), 0, ctypes.byref(dx), ctypes.byref(dy)) == 0:
            return int(dx.value) or system_dpi()
    except (AttributeError, OSError):
        pass
    return system_dpi()


def right_button_down() -> bool:
    return bool(ctypes.windll.user32.GetAsyncKeyState(0x02) & 0x8000)  # VK_RBUTTON


def left_button_down() -> bool:
    return bool(ctypes.windll.user32.GetAsyncKeyState(0x01) & 0x8000)  # VK_LBUTTON


def cursor_pos() -> tuple[int, int]:
    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
    p = POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def is_busy_state(state: int) -> bool:
    # QUERY_USER_NOTIFICATION_STATE: 1 locked / screensaver (nobody would see an alert),
    # 2 busy (full screen), 3 D3D full screen, 4 presentation mode, 7 full-screen Store app
    return state in (1, 2, 3, 4, 7)


def fullscreen_busy() -> bool:
    """True while a full-screen app, game or presentation is running."""
    state = ctypes.c_int(0)
    try:
        if ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state)) != 0:
            return False
    except (AttributeError, OSError):
        return False
    return is_busy_state(state.value)


_mutex = None


def single_instance(name: str = "Local\\ClaudeUsageTray") -> bool:
    """False if another copy is already running."""
    global _mutex
    k = ctypes.windll.kernel32
    _mutex = k.CreateMutexW(None, False, name)
    return k.GetLastError() != 183  # ERROR_ALREADY_EXISTS
