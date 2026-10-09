import json

import pytest

from tray import config
from tray.config import DEFAULT_CONFIG, load_config


@pytest.fixture
def cfg_file(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    return tmp_path / "config.json"


def write(path, data):
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")


def test_missing_file_writes_defaults(cfg_file):
    cfg = load_config()
    assert {k: v for k, v in cfg.items() if k != "_ok"} == DEFAULT_CONFIG and cfg["_ok"] is True
    assert json.loads(cfg_file.read_text(encoding="utf-8")) == DEFAULT_CONFIG


def test_partial_file_fills_defaults(cfg_file):
    write(cfg_file, {"poll_minutes": 10})
    cfg = load_config()
    assert cfg["poll_minutes"] == 10 and cfg["popup_seconds"] == 8


@pytest.mark.parametrize("bad", ["{not json", "[1, 2]", "null", '"text"'])
def test_broken_file_uses_defaults_but_is_flagged(cfg_file, bad):
    write(cfg_file, bad)
    cfg = load_config()
    assert {k: cfg[k] for k in DEFAULT_CONFIG} == DEFAULT_CONFIG
    assert cfg["_ok"] is False                     # so the app won't overwrite it or touch startup
    assert cfg_file.read_text(encoding="utf-8") == bad


@pytest.mark.parametrize("key,value,expected", [
    ("poll_minutes", 0, 1), ("poll_minutes", -5, 1), ("poll_minutes", 999, 60), ("poll_minutes", "5", 5),
    ("poll_minutes", "abc", 5), ("poll_minutes", None, 5), ("poll_minutes", 2.5, 2.5),
    ("popup_seconds", 0, 2), ("popup_seconds", 1000, 60),
    ("relogin_notice_days", 0, 1), ("relogin_notice_days", 99, 14),
    ("projection_window_hours", 1, 3), ("projection_window_hours", 500, 168),
    ("start_with_windows", "yes", True), ("start_with_windows", 0, True), ("start_with_windows", False, False),
    ("widget_mode", "mini", "mini"), ("widget_mode", "compact", "compact"), ("widget_mode", "full", "compact"),
    ("widget_mode", "hidden", "compact"), ("widget_mode", 3, "compact"),
    ("widget_pos", [100, 200], [100, 200]), ("widget_pos", [100.7, 200.2], [101, 200]),
    ("widget_pos", [1, 2, 3], None), ("widget_pos", "x", None), ("widget_pos", [True, 5], None),
    ("widget_pos", None, None),
    ("alert_thresholds", [95, 80, 80, 200, -1, "x", 90], [80, 90, 95]),
    ("alert_thresholds", "80", [80, 90, 95]), ("alert_thresholds", [], []),
])
def test_values_are_validated(cfg_file, key, value, expected):
    write(cfg_file, {key: value})
    assert load_config()[key] == expected


def test_unknown_keys_are_ignored(cfg_file):
    write(cfg_file, {"whatever": 1})
    assert "whatever" not in load_config()


def test_save_drops_internal_keys(cfg_file):
    cfg = load_config()
    cfg["widget_mode"] = "mini"
    config.save_config(cfg)
    saved = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert saved["widget_mode"] == "mini" and "_ok" not in saved


def test_widget_is_shown_by_default():
    """Something always stays on screen: the small bar by default, or the tiny chip."""
    assert DEFAULT_CONFIG["widget_mode"] == "compact" and DEFAULT_CONFIG["widget_pos"] is None
    assert "folded" not in DEFAULT_CONFIG and "show_widget" not in DEFAULT_CONFIG


def test_claude_config_dir_is_respected(monkeypatch, tmp_path):
    import importlib
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "custom"))
    try:
        c = importlib.reload(config)
        assert c.CREDENTIALS_FILE == tmp_path / "custom" / ".credentials.json"
        assert c.SNAPSHOT_FILE == tmp_path / "custom" / "usage-snapshot.json"
    finally:
        monkeypatch.delenv("CLAUDE_CONFIG_DIR")
        importlib.reload(config)


def test_config_is_written_atomically(cfg_file, monkeypatch):
    """A crash mid-write must not leave a half-written config.json."""
    cfg = load_config()
    real_replace = type(cfg_file).replace
    used = []

    def spy(self, target):
        used.append((self.name, str(target)))
        return real_replace(self, target)

    monkeypatch.setattr(type(cfg_file), "replace", spy)
    cfg["widget_mode"] = "mini"
    config.save_config(cfg)
    assert used and used[0][1] == str(cfg_file)
    assert json.loads(cfg_file.read_text(encoding="utf-8"))["widget_mode"] == "mini"


@pytest.mark.parametrize("value", ["~/claude-cfg", r"%USERPROFILE%\claude-cfg"])
def test_claude_config_dir_expands_home_and_variables(monkeypatch, tmp_path, value):
    import importlib
    from pathlib import Path
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", value)
    try:
        c = importlib.reload(config)
        assert c.CLAUDE_DIR == Path(tmp_path) / "claude-cfg"
    finally:
        monkeypatch.delenv("CLAUDE_CONFIG_DIR")
        importlib.reload(config)
