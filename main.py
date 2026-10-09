"""Claude Usage Tray: weekly Claude plan usage in a small always-on-screen pixel widget."""
import logging
import os
import sys
import threading
from logging.handlers import RotatingFileHandler

from tray import __version__, usage_api, winsys
from tray.alerts import Alert, Alerts
from tray.config import CONFIG_FILE, DATA_DIR, LOG_FILE, load_config, save_config
from tray.controller import Controller
from tray.history import History
from tray.ui import UI

log = logging.getLogger("tray")


def setup_logging():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    h = RotatingFileHandler(LOG_FILE, maxBytes=200_000, backupCount=1, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[h])


def main():
    # DPI awareness first: every size below depends on it.
    winsys.enable_dpi_awareness()
    if not winsys.single_instance():
        return
    setup_logging()
    # Never run anything from the folder the exe was started in (e.g. Downloads).
    os.chdir(DATA_DIR)
    cfg = load_config()
    log.info("start v%s", __version__)
    if not cfg["_ok"]:
        log.warning("config.json unreadable: using defaults, not touching it")

    if getattr(sys, "frozen", False) and cfg["_ok"]:
        cmd = winsys.own_startup_command()
        reg_cmd = winsys._Registry().get(winsys.RUN_VALUE)
        if cfg["start_with_windows"] and reg_cmd != cmd:
            winsys.set_startup(True, cmd)
        elif not cfg["start_with_windows"] and reg_cmd is not None:
            winsys.set_startup(False, None)

    controller = Controller(cfg, History(), Alerts(cfg))
    wake = threading.Event()
    stop = threading.Event()

    def login():
        if not usage_api.start_login(on_done=wake.set):
            ui.post("alert", Alert("claude 명령 없음", ["Claude Code를 설치하고", "PATH에 추가해 주세요"], "stale"))

    def save(**changes):
        cfg.update(changes)
        save_config(cfg)

    def toggle_startup():
        on = not winsys.startup_enabled()
        winsys.set_startup(on, winsys.own_startup_command())
        save(start_with_windows=on)

    def quit_app():
        stop.set()
        wake.set()
        ui.post("quit")

    ui = UI(cfg["popup_seconds"], mode=cfg["widget_mode"], anchor=cfg["widget_pos"],
            on_login=login, on_change=save, menu={
                "refresh": wake.set,
                "open_config": lambda: os.startfile(CONFIG_FILE),
                "startup_enabled": winsys.startup_enabled,
                "toggle_startup": toggle_startup,
                "quit": quit_app,
                "version": __version__,
            })

    def poll_loop():
        while not stop.is_set():
            try:
                ui.post("view", controller.poll())
            except Exception:
                log.exception("poll loop")
            wake.wait(cfg["poll_minutes"] * 60)
            wake.clear()

    threading.Thread(target=poll_loop, daemon=True).start()
    ui.run()
    log.info("stop")


if __name__ == "__main__":
    main()
