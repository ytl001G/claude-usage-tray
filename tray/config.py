"""Paths and user config."""
import json
import os
import sys
from pathlib import Path

APP_NAME = "ClaudeUsageTray"
DATA_DIR = Path(os.environ.get("APPDATA") or Path.home()) / APP_NAME
# Claude Code honours CLAUDE_CONFIG_DIR; so do we, or we'd read a different login.
_custom = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
CLAUDE_DIR = Path(os.path.expandvars(os.path.expanduser(_custom))) if _custom else Path.home() / ".claude"
CREDENTIALS_FILE = CLAUDE_DIR / ".credentials.json"
SNAPSHOT_FILE = CLAUDE_DIR / "usage-snapshot.json"
CONFIG_FILE = DATA_DIR / "config.json"
HISTORY_FILE = DATA_DIR / "history.jsonl"
STATE_FILE = DATA_DIR / "state.json"
LOG_FILE = DATA_DIR / "app.log"

DEFAULT_CONFIG = {
    "poll_minutes": 5,
    "alert_thresholds": [80, 90, 95],
    "relogin_notice_days": 3,
    "projection_window_hours": 24,
    "popup_seconds": 8,
    "start_with_windows": True,
    "widget_mode": "compact",  # what stays on screen: "compact" bar or the tiny "mini" chip
    "widget_pos": None,        # [right, bottom] of the widget after the user drags it
}


def asset(name: str) -> Path:
    # PyInstaller unpacks bundled files under sys._MEIPASS.
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / "assets" / name


# key: (min, max) for numbers
RANGES = {
    "poll_minutes": (1, 60),
    "popup_seconds": (2, 60),
    "relogin_notice_days": (1, 14),
    "projection_window_hours": (3, 168),
}


def _number(value, default, lo, hi):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if v != v:  # NaN
        return default
    v = min(max(v, lo), hi)
    return int(v) if v == int(v) else v


def _validate(raw: dict) -> dict:
    cfg = dict(DEFAULT_CONFIG)
    for key, (lo, hi) in RANGES.items():
        if key in raw:
            cfg[key] = _number(raw[key], DEFAULT_CONFIG[key], lo, hi)
    if isinstance(raw.get("start_with_windows"), bool):
        cfg["start_with_windows"] = raw["start_with_windows"]
    if raw.get("widget_mode") in ("compact", "mini"):
        cfg["widget_mode"] = raw["widget_mode"]
    pos = raw.get("widget_pos")
    if (isinstance(pos, list) and len(pos) == 2
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in pos)):
        cfg["widget_pos"] = [int(round(v)) for v in pos]
    t = raw.get("alert_thresholds")
    if isinstance(t, list):
        cfg["alert_thresholds"] = sorted({int(x) for x in t
                                          if isinstance(x, (int, float)) and not isinstance(x, bool) and 1 <= x <= 100})
    return cfg


def load_config() -> dict:
    """Defaults merged with config.json, every value type- and range-checked.
    `_ok` is False when the file exists but can't be read: the app then neither
    overwrites it nor changes the startup entry."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        cfg = dict(DEFAULT_CONFIG)
        save_config(cfg)
        return {**cfg, "_ok": True}
    except (OSError, ValueError):
        return {**DEFAULT_CONFIG, "_ok": False}
    if not isinstance(raw, dict):
        return {**DEFAULT_CONFIG, "_ok": False}
    return {**_validate(raw), "_ok": True}


def save_config(cfg: dict) -> None:
    if cfg.get("_ok") is False:
        return  # never clobber a file the user is still fixing
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    data = {k: v for k, v in cfg.items() if not k.startswith("_")}
    # Write a temp file and swap it in, so a crash can't leave half a config.json.
    tmp = CONFIG_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(CONFIG_FILE)
