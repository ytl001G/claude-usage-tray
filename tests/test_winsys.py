import pytest

from tray import winsys


@pytest.mark.parametrize("dpi,k", [(96, 1), (120, 1), (144, 1), (168, 2), (192, 2), (288, 3)])
def test_ui_scale(dpi, k):
    # Integer scale only (pixel art); 125% and 150% stay at 1x to keep the UI small.
    assert winsys.ui_scale(dpi) == k


def test_popup_bottom_right_above_taskbar():
    work = (0, 0, 1920, 1040)                       # taskbar at the bottom
    assert winsys.popup_position(work, (300, 232), margin=8) == (1920 - 8 - 300, 1040 - 8 - 232)


def test_popup_with_offset_work_area():
    work = (1920, 0, 3840, 1080)                    # second monitor on the right
    x, y = winsys.popup_position(work, (240, 64), margin=8, stack=72)
    assert (x, y) == (3840 - 8 - 240, 1080 - 8 - 64 - 72)


def test_popup_never_off_screen():
    x, y = winsys.popup_position((0, 0, 200, 100), (300, 232), margin=8)
    assert x >= 0 and y >= 0


def test_startup_command_frozen():
    cmd = winsys.startup_command(frozen=True, executable=r"C:\Apps\ClaudeUsageTray.exe", script=None)
    assert cmd == r'"C:\Apps\ClaudeUsageTray.exe"'


def test_startup_command_from_source(tmp_path):
    py = tmp_path / "python.exe"
    pyw = tmp_path / "pythonw.exe"
    py.write_text(""); pyw.write_text("")
    cmd = winsys.startup_command(frozen=False, executable=str(py), script=r"C:\src\main.py")
    assert cmd == f'"{pyw}" "C:\\src\\main.py"'


class FakeReg:
    """In-memory stand-in for winreg's HKCU Run key."""

    def __init__(self):
        self.values = {}

    def get(self, name):
        return self.values.get(name)

    def set(self, name, value):
        self.values[name] = value

    def delete(self, name):
        self.values.pop(name, None)


def test_set_startup_touches_only_our_value():
    reg = FakeReg()
    reg.values["SomethingElse"] = "x"
    winsys.set_startup(True, '"C:\\a.exe"', reg=reg)
    assert reg.values == {"SomethingElse": "x", winsys.RUN_VALUE: '"C:\\a.exe"'}
    assert winsys.startup_enabled(reg=reg)
    winsys.set_startup(False, None, reg=reg)
    assert reg.values == {"SomethingElse": "x"}
    assert not winsys.startup_enabled(reg=reg)


@pytest.mark.parametrize("state,busy", [(1, True), (2, True), (3, True), (4, True), (5, False), (6, False), (7, True)])
def test_fullscreen_states(state, busy):
    # 1 locked/screensaver, 2 busy, 3 D3D full screen, 4 presentation, 7 full-screen Store app;
    # 5 accepts notifications, 6 quiet time (only affects toasts)
    assert winsys.is_busy_state(state) is busy
