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
from tray.history import WEEK, Projection  # noqa: E402
from tray.render import View, alert_popup, main_popup, mini_chip  # noqa: E402
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


def main() -> None:
    now = datetime(2026, 10, 8, 15, 0).timestamp()
    resets = datetime(2026, 10, 10, 21, 0).timestamp()
    start = resets - WEEK
    pts = [(start + h * 3600, min(68, int(h * 0.55))) for h in range(0, int((now - start) / 3600), 2)]
    last = [(start - WEEK + h * 3600, int(h * 0.5)) for h in range(0, 168, 3)]
    u = Usage(at=now, week=68, week_resets=resets, five=23, five_resets=now + 3 * 3600,
              breakdown=[("Claude Code", 74), ("Chats", 25)])
    danger = View(usage=u, proj=Projection("exhaust", 13.6, 22, datetime(2026, 10, 9, 23, 30).timestamp(), 100),
                  points=pts, last_points=last, color="danger", login_expires=now + 24 * 86400, stale_minutes=0)
    ok = View(usage=Usage(at=now, week=42, week_resets=resets, five=8, five_resets=now + 2 * 3600),
              proj=Projection("ok", 8.2, 6, None, 63), color="ok", login_expires=now + 24 * 86400)

    full = unkey(main_popup(danger, now=now)[0])
    fold = unkey(main_popup(ok, folded=True, now=now)[0])
    alert = unkey(alert_popup("Claude 주간 90%", ["이 속도면 내일 23:30 소진", "(리셋 22시간 전)"], "danger", False)[0])

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
