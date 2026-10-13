"""Regenerate docs/screenshot.png from sample data (no real account data).

    .venv\\Scripts\\python tools\\screenshot.py
"""
import sys
from datetime import datetime
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tray import render  # noqa: E402
from tray.history import WEEK, project  # noqa: E402
from tray.render import View, alert_popup, main_popup, mini_chip  # noqa: E402
from tray.text import when  # noqa: E402
from tray.usage_api import Usage  # noqa: E402

BACKDROP = "#2b2b2b"


def unkey(img: Image.Image) -> Image.Image:
    """The transparency key shows as magenta outside the app; paint it as the backdrop."""
    out = img.convert("RGB")
    px = out.load()
    key = Image.new("RGB", (1, 1), render.KEY).getpixel((0, 0))
    bg = Image.new("RGB", (1, 1), BACKDROP).getpixel((0, 0))
    for y in range(out.height):
        for x in range(out.width):
            if px[x, y] == key:
                px[x, y] = bg
    return out


def sample_week(start: float, now: float, final: int) -> list[tuple[float, int]]:
    """History rows for a made-up week: busy afternoons and evenings, idle nights, and one
    stretch with the PC off (no rows), ending at `final`%."""
    def busy(t):
        h = datetime.fromtimestamp(t)
        weight = {10: 2, 11: 3, 12: 1, 14: 4, 15: 5, 16: 4, 17: 3, 21: 2, 22: 3, 23: 2}.get(h.hour, 0)
        return weight * (1.6 if h.weekday() in (2, 3) else 1)
    off = (start + 2 * 86400 + 4 * 3600, start + 2 * 86400 + 17 * 3600)   # Tue 01:00 - 14:00
    steps = [start + i * 300 for i in range(int((now - start) / 300) + 1)]
    total = sum(busy(t) for t in steps)
    rows, used, last = [], 0.0, None
    for t in steps:
        used += busy(t) * final / total
        if off[0] <= t < off[1]:
            continue
        pct = int(used)
        if last is None or pct != last[1] or t - last[0] >= 3600:
            last = (t, pct)
            rows.append(last)
    return rows


def main() -> None:
    now = datetime(2026, 10, 9, 9, 0).timestamp()
    resets = datetime(2026, 10, 10, 21, 0).timestamp()
    start = resets - WEEK
    pts = sample_week(start, now, 82)
    last = [(start - WEEK + h * 3600, int(h * 0.5)) for h in range(0, 168, 3)]
    u = Usage(at=now, week=82, week_resets=resets, five=23, five_resets=now + 3 * 3600,
              breakdown=[("Claude Code", 74), ("Chats", 25)])
    proj = project(pts, u.week, resets, now=now)
    danger = View(usage=u, proj=proj,
                  points=pts, last_points=last, color="danger", login_expires=now + 24 * 86400, stale_minutes=0)
    ok = View(usage=Usage(at=now, week=42, week_resets=resets, five=8, five_resets=now + 2 * 3600),
              proj=project([], 42, resets, now=now), color="ok", login_expires=now + 24 * 86400)

    full = unkey(main_popup(danger, now=now)[0])
    fold = unkey(main_popup(ok, folded=True, now=now)[0])
    alert = unkey(alert_popup("Claude 주간 80%", [f"이 속도면 {when(proj.eta, now)} 소진",
                                                         f"(리셋 {round((resets - proj.eta) / 3600)}시간 전)"],
                             "danger", False)[0])

    out = Image.new("RGB", (full.width + 30 + max(fold.width, alert.width) + 10, full.height + 20), BACKDROP)
    out.paste(full, (10, 10))
    x2 = full.width + 30
    out.paste(fold, (x2, 10))
    out.paste(alert, (x2, 20 + fold.height))
    x = x2
    for pct, status, badge in [(42, "ok", False), (85, "warn", False), (97, "danger", True), (None, "stale", False)]:
        chip = unkey(mini_chip(pct, status, badge))
        out.paste(chip, (x, 30 + fold.height + alert.height))
        x += chip.width + 8
    path = ROOT / "docs" / "screenshot.png"
    out.resize((out.width * 2, out.height * 2), Image.NEAREST).save(path)
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
