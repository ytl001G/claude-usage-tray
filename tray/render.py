"""Pixel-art drawing with Pillow. Everything is drawn at 1x with no anti-aliasing and then
scaled up by an integer factor with nearest-neighbour, so pixels stay square."""
import time
from dataclasses import dataclass, field

from PIL import Image, ImageDraw, ImageFont

from .config import asset
from .history import WEEK, Projection
from .text import d_day, weekday, when
from .usage_api import Usage

# Warm dark palette around Claude orange.
ORANGE = "#d97757"
BG = "#1c1a17"
PANEL = "#26231f"
BORDER = ORANGE
FAINT = "#463f37"
TEXT = "#f2ede6"
DIM = "#a39b90"
YELLOW = "#f2c94c"
RED = "#ff5f56"
GREY = "#77716a"
KEY = "#ff00ff"  # window transparency key, used for the cut corners

# Status -> accent color. "ok" is the brand orange.
STATUS = {"ok": ORANGE, "warn": YELLOW, "danger": RED, "stale": GREY}

_fonts = {}


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    if (name, size) not in _fonts:
        _fonts[name, size] = ImageFont.truetype(str(asset(name)), size)
    return _fonts[name, size]


F12 = lambda: font("Galmuri11.ttf", 12)
F10 = lambda: font("Galmuri9.ttf", 10)
FBIG = lambda: font("Galmuri11-Bold.ttf", 24)


def accent(status: str) -> str:
    return STATUS.get(status, GREY)


def new_canvas(w: int, h: int, fill=KEY):
    img = Image.new("RGB", (w, h), fill)
    d = ImageDraw.Draw(img)
    d.fontmode = "1"  # no anti-aliasing
    return img, d


def box(d, x, y, w, h, fill, border, t=2):
    """Box with a t-pixel border and notched (stepped) corners."""
    d.rectangle([x + t, y, x + w - 1 - t, y + h - 1], fill=border)
    d.rectangle([x, y + t, x + w - 1, y + h - 1 - t], fill=border)
    d.rectangle([x + t, y + t, x + w - 1 - t, y + h - 1 - t], fill=fill)


def text_w(s, f) -> int:
    return int(f.getlength(s))


def text_r(d, right, y, s, f, fill):
    d.text((right - text_w(s, f), y), s, font=f, fill=fill)


def fit(s: str, f, max_w: int) -> str:
    """Cut text with '..' so it fits in max_w pixels."""
    if text_w(s, f) <= max_w:
        return s
    while s and text_w(s + "..", f) > max_w:
        s = s[:-1]
    return s.rstrip(" ·") + ".."


def gauge(d, x, y, pct, n, bw, bh, color, gap=1):
    """Row of n blocks that fill up one by one."""
    filled = min(n, int(pct * n / 100 + 0.5)) if pct else 0
    for i in range(n):
        bx = x + i * (bw + gap)
        if i < filled:
            d.rectangle([bx, y, bx + bw - 1, y + bh - 1], fill=color)
        else:
            d.rectangle([bx, y, bx + bw - 1, y + bh - 1], outline=FAINT)


def dots(d, p0, p1, color, step=3, size=1):
    """Dotted straight line made of square pixels."""
    (x0, y0), (x1, y1) = p0, p1
    n = max(1, int(max(abs(x1 - x0), abs(y1 - y0)) / step))
    for i in range(n + 1):
        x = round(x0 + (x1 - x0) * i / n)
        y = round(y0 + (y1 - y0) * i / n)
        d.rectangle([x, y, x + size - 1, y + size - 1], fill=color)


def close_x(d, x, y, color):
    for i in range(5):
        d.point([(x + i, y + i), (x + 4 - i, y + i)], fill=color)


def triangle(d, x, y, up: bool, color):
    """7x4 pixel arrow."""
    for r in range(4):
        row = r if not up else 3 - r
        d.line([(x + r, y + row), (x + 6 - r, y + row)], fill=color)


def button(d, x, y, label, regions, name, border=ORANGE):
    bw = text_w(label, F12()) + 12
    box(d, x, y, bw, 17, PANEL, border, t=1)
    d.text((x + 6, y + 3), label, font=F12(), fill=TEXT)
    regions[name] = (x, y, x + bw, y + 17)
    return bw


def scale(img: Image.Image, k: int) -> Image.Image:
    return img if k == 1 else img.resize((img.width * k, img.height * k), Image.NEAREST)


# ---------------------------------------------------------------- tray icon

# 5x7 digits.
DIGITS = {
    "0": ["01110", "10001", "10011", "10101", "11001", "10001", "01110"],
    "1": ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
    "2": ["01110", "10001", "00001", "00010", "00100", "01000", "11111"],
    "3": ["11110", "00001", "00001", "01110", "00001", "00001", "11110"],
    "4": ["00010", "00110", "01010", "10010", "11111", "00010", "00010"],
    "5": ["11111", "10000", "11110", "00001", "00001", "10001", "01110"],
    "6": ["00110", "01000", "10000", "11110", "10001", "10001", "01110"],
    "7": ["11111", "00001", "00010", "00100", "01000", "01000", "01000"],
    "8": ["01110", "10001", "10001", "01110", "10001", "10001", "01110"],
    "9": ["01110", "10001", "10001", "01111", "00001", "00010", "01100"],
    "!": ["00100", "00100", "00100", "00100", "00100", "00000", "00100"],
    "-": ["00000", "00000", "00000", "11111", "00000", "00000", "00000"],
}


def tray_icon(pct: int | None, status: str, badge: bool, size: int = 32) -> Image.Image:
    """16x16 pixel icon (dark tile with a status-colored frame and the weekly %),
    scaled up to `size`."""
    img = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    frame = accent(status)
    digits = TEXT if status == "ok" else frame
    box(d, 0, 0, 16, 16, BG, frame, t=1)
    s = "--" if pct is None else "!!" if pct >= 100 else str(pct)
    w = len(s) * 5 + (len(s) - 1)
    x0 = (16 - w) // 2
    for i, ch in enumerate(s):
        for row, bits in enumerate(DIGITS[ch]):
            for col, b in enumerate(bits):
                if b == "1":
                    d.point((x0 + i * 6 + col, 4 + row), fill=digits)
    if badge:  # login notice: white so it shows on every frame color
        d.rectangle([12, 0, 15, 3], fill=BG)
        d.rectangle([13, 0, 15, 2], fill=TEXT)
    k = max(1, size // 16)
    out = img.resize((16 * k, 16 * k), Image.NEAREST)
    if out.size != (size, size):
        pad = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        pad.paste(out, ((size - out.width) // 2, (size - out.height) // 2))
        out = pad
    return out


# ---------------------------------------------------------------- main popup

W_COMPACT = 300   # the bar that stays on screen
W_FULL = 340      # the big panel may be a bit bigger
X0 = 10
BTN = 15          # size buttons: big enough to hit easily


@dataclass
class View:
    usage: Usage | None
    proj: Projection | None
    points: list = field(default_factory=list)       # this week [(t, pct)]
    last_points: list = field(default_factory=list)  # last week [(t, pct)]
    color: str = "stale"            # ok | warn | danger | stale
    login_expires: float | None = None
    login_notice: bool = False      # within the re-login notice period (or expired)
    needs_login: bool = False       # the token is missing/rejected right now
    stale_minutes: int | None = None
    message: str | None = None      # shown instead of data when there is none


def size_buttons(d, x1, y, folded: bool, regions):
    """[–] shrinks to the tiny chip, [▾]/[▴] opens/closes the big panel."""
    bx = x1 - BTN
    box(d, bx, y, BTN, BTN, PANEL, FAINT, t=1)
    triangle(d, bx + 4, y + 5, up=not folded, color=TEXT)
    regions["fold"] = (bx, y, bx + BTN, y + BTN)
    mx = bx - BTN - 3
    box(d, mx, y, BTN, BTN, PANEL, FAINT, t=1)
    d.rectangle([mx + 4, y + 9, mx + 10, y + 10], fill=TEXT)
    regions["mini"] = (mx, y, mx + BTN, y + BTN)
    return mx


def main_popup(v: View, folded: bool = False, now: float | None = None):
    """Returns (image at 1x, {region name: (x0, y0, x1, y1)}).

    Rows, most important first: weekly % + gauge, the verdict, then (unless folded)
    the graph, the 5-hour limit and a footer."""
    now = now or time.time()
    regions = {}
    W = W_COMPACT if folded else W_FULL
    X1 = W - 10
    body, d = new_canvas(W, 400, fill=BG)
    y = 9

    if v.login_notice or v.needs_login:
        left = (v.login_expires or now) - now
        if v.login_notice:
            label = "! 로그인 만료됨" if left <= 0 else f"! 로그인 만료 {d_day(v.login_expires, now)}"
        else:
            label = "! 로그인 필요"
        d.text((X0, y + 2), label, font=F12(), fill=RED if left <= 86400 or v.needs_login else YELLOW)
        bw = text_w("로그인", F12()) + 12
        button(d, X1 - bw, y - 1, "로그인", regions, "login", YELLOW)
        y += 21

    if not v.usage:
        size_buttons(d, X1, y + 1, folded, regions)
        d.text((X0, y + 4), "Claude 사용량", font=F12(), fill=ORANGE)
        d.text((X0, y + 26), fit(v.message or "불러오는 중...", F12(), X1 - X0), font=F12(), fill=DIM)
        return _frame(body, W, y + 48, regions)

    u, p = v.usage, v.proj
    col = accent(v.color)

    # 1. Header, big %, gauge.
    d.text((X0, y + 1), "주간 사용량", font=F12(), fill=ORANGE)
    left_of_buttons = size_buttons(d, X1, y - 2, folded, regions)
    reset_text = f"리셋 {when(u.week_resets, now)}" if u.resets_known else "이번 주 사용 없음"
    text_r(d, left_of_buttons - 6, y + 2, reset_text, F10(), DIM)
    y += 18
    num = f"{u.week}%"
    d.text((X0, y - 1), num, font=FBIG(), fill=TEXT if v.color == "ok" else col)
    gx = X0 + max(text_w(num, FBIG()), 56) + 8
    n = 18
    bw = (X1 - gx - (n - 1)) // n
    gauge(d, gx, y + 9, u.week, n, bw, 8, col)
    y += 29

    # 2. Verdict.
    line1, line2, c = _verdict(u, p, now)
    d.text((X0, y), fit(line1, F12(), X1 - X0), font=F12(), fill=c)
    y += 15
    if folded:
        return _frame(body, W, y + 5, regions)
    if line2:
        d.text((X0, y), fit(line2, F10(), X1 - X0), font=F10(), fill=DIM)
    y += 15

    # 3. Graph.
    gh = 112
    box(d, X0, y, X1 - X0, gh, PANEL, FAINT, t=1)
    _graph(d, v, X0, y, X1 - X0, gh, now)
    y += gh + 6

    # 4. 5-hour limit.
    if u.five is not None:
        fc = RED if u.five >= 90 else YELLOW if u.five >= 70 else ORANGE
        d.text((X0, y + 1), "5시간", font=F10(), fill=DIM)
        d.text((X0 + 30, y), f"{u.five}%", font=F12(), fill=TEXT)
        gauge(d, X0 + 62, y + 3, u.five, 10, 8, 6, fc)
        if u.five_resets:
            text_r(d, X1, y + 1, f"{when(u.five_resets, now)} 리셋", F10(), DIM)
        y += 16

    # 5. Footer: where the usage went (or why the data is old), and how fresh it is.
    left, right, stale = footer(v, now, W)
    text_r(d, X1, y, right, F10(), GREY if stale else DIM)
    d.text((X0, y), left, font=F10(), fill=GREY if stale else DIM)
    y += 12
    return _frame(body, W, y + 6, regions)


STALE_MINUTES = 15


def ago(minutes: int) -> str:
    if minutes < 1:
        return "방금"
    if minutes < 60:
        return f"{minutes}분 전"
    if minutes < 24 * 60:
        return f"{minutes // 60}시간 전"
    return f"{minutes // (24 * 60)}일 전"


def footer(v: View, now: float, w: int = W_FULL) -> tuple[str, str, bool]:
    """(left text, right text, stale?) for the bottom line of the big panel."""
    X1 = w - 10
    u = v.usage
    stale = v.stale_minutes is not None and v.stale_minutes >= STALE_MINUTES
    if stale:
        right = f"! {ago(v.stale_minutes)} 데이터"
    else:
        right = time.strftime("%H:%M 기준", time.localtime(u.at))
    right_w = text_w(right, F10())
    left_w = X1 - X0 - right_w - 8
    if stale and v.message:
        return fit(v.message, F10(), left_w), right, True
    # In priority order; whatever doesn't fit is dropped whole rather than cut mid-word.
    wanted = [f"{_short(name)} {pct}%" for name, pct in u.breakdown[:2]]
    if v.login_expires and not v.login_notice:
        wanted.append(f"로그인 {d_day(v.login_expires, now)}")
    wanted += [f"{name} {pct}%" for name, pct in u.scoped[:1]]
    return _fit_parts(wanted, F10(), left_w), right, stale


def _frame(body, w, h, regions):
    """Crop the drawn body to its height and put the orange pixel frame around it."""
    img, d = new_canvas(w, h)
    box(d, 0, 0, w, h, BG, BORDER)
    img.paste(body.crop((2, 2, w - 2, h - 2)), (2, 2))
    return img, regions


def _fit_parts(parts: list[str], f, max_w: int) -> str:
    kept = []
    for part in parts:
        if text_w(" · ".join(kept + [part]), f) <= max_w:
            kept.append(part)
    return fit(" · ".join(kept), f, max_w)  # a single over-long part still gets cut


def _short(name: str) -> str:
    return {"Claude Code": "CC", "Chats": "채팅"}.get(name, name)


def _verdict(u: Usage, p: Projection | None, now):
    """(main line, detail line, color) answering "will this week's limit last?"."""
    if not u.resets_known:
        return "이번 주 사용 없음 · 쓰기 시작하면 표시", None, DIM
    if not p or p.status == "collecting":
        return "예측 준비 중 · 몇 시간 뒤 표시", None, DIM
    rates = []
    if p.recent_per_day is not None:
        rates.append(f"24시간 +{p.recent_per_day:.0f}%/일")
    if p.avg_per_day is not None:
        rates.append(f"평균 +{p.avg_per_day:.0f}%/일")
    rate_line = " · ".join(rates) or None
    if p.status == "full":
        return f"! 주간 한도 소진 · {when(u.week_resets, now)} 리셋", None, RED
    if p.status == "exhaust":
        hours = max(0, round((u.week_resets - p.eta) / 3600))
        detail = f"리셋 {hours}시간 전 소진" + (f" · {rate_line}" if rate_line else "")
        return f"! 이 속도면 {when(p.eta, now)} 소진", detail, RED
    if p.status == "tight":
        return f"아슬아슬 · 리셋 때 약 {p.final_pct:.0f}%", rate_line, YELLOW
    return f"리셋까지 여유 · 예상 {p.final_pct:.0f}%", rate_line, TEXT


def _graph(d, v: View, x, y, w, h, now):
    u = v.usage
    now = min(now, u.at)  # while stale, the line stops at the last reading
    start = u.week_resets - WEEK
    px0, px1 = x + 22, x + w - 6
    py0, py1 = y + 6, y + h - 16
    X = lambda t: round(px0 + (t - start) / WEEK * (px1 - px0))
    Y = lambda pct: round(py1 - min(max(pct, 0), 100) / 100 * (py1 - py0))

    # Axes and labels.
    for pct in (100, 50, 0):
        text_r(d, px0 - 3, Y(pct) - 5, str(pct), F10(), DIM)
    for gx in range(px0, px1 + 1, 4):
        d.point((gx, Y(100)), fill=FAINT)
        d.point((gx, Y(50)), fill=FAINT)
    d.line([(px0, py1), (px1, py1)], fill=FAINT)
    for i in range(7):
        mid = start + i * 86400 + 43200
        d.text((X(mid) - 5, py1 + 3), weekday(mid), font=F10(), fill=DIM)
        d.point((X(start + i * 86400), py1 + 1), fill=DIM)

    # Last week, faint, at the same offset into its week.
    prev = sorted((t + WEEK, pct) for t, pct in v.last_points if start <= t + WEEK <= u.week_resets)
    _steps(d, [(start, 0)] + prev, X, Y, FAINT, 1)

    # This week: real readings as steps. Before the first reading we only know the week
    # started at 0%, so that stretch is a dotted guess rather than a fake flat line.
    pts = sorted(t for t in v.points if start <= t[0] <= now)
    if pts and pts[0][0] - start > 3600:
        dots(d, (X(start), Y(0)), (X(pts[0][0]), Y(pts[0][1])), FAINT, step=3)
        _steps(d, pts + [(now, u.week)], X, Y, ORANGE, 2)
    else:
        _steps(d, [(start, 0)] + pts + [(now, u.week)], X, Y, ORANGE, 2)

    # Projection to 100% or to the reset, whichever comes first.
    p = v.proj
    if p and p.status not in ("collecting", "full") and p.eta:
        end_t = min(p.eta, u.week_resets)
        end_p = 100 if p.eta <= u.week_resets else p.final_pct
        c = accent(v.color) if v.color != "ok" else DIM
        dots(d, (X(now), Y(u.week)), (X(end_t), Y(end_p)), c, step=4, size=2)
        if p.eta <= u.week_resets:
            ex, ey = X(p.eta), Y(100)
            d.rectangle([ex - 2, ey - 2, ex + 2, ey + 2], fill=RED)

    # Now marker.
    nx = X(now)
    d.rectangle([nx - 1, Y(u.week) - 1, nx + 1, Y(u.week) + 1], fill=TEXT)


def _steps(d, pts, X, Y, color, width):
    for (t0, p0), (t1, p1) in zip(pts, pts[1:]):
        xa, xb, ya, yb = X(t0), X(t1), Y(p0), Y(p1)
        if xb < xa:
            continue
        d.rectangle([xa, ya - width + 1, xb, ya], fill=color)
        d.rectangle([xb, min(ya, yb) - width + 1, xb + width - 1, max(ya, yb)], fill=color)


# ---------------------------------------------------------------- tiny chip

PERCENT = ["11001", "11010", "00010", "00100", "01000", "01011", "10011"]


def mini_chip(pct: int | None, status: str, badge: bool) -> Image.Image:
    """The minimized widget: just the weekly % in chunky 2x pixel digits (click to open)."""
    s = "--" if pct is None else "!!" if pct >= 100 else str(pct)
    glyphs = [DIGITS[ch] for ch in s] + ([PERCENT] if pct is not None and pct < 100 else [])
    px = 2                                   # each font pixel is 2x2
    text_w = len(glyphs) * 6 * px - px
    img, d = new_canvas(max(text_w + 16, 44), 7 * px + 12)
    frame = accent(status)
    box(d, 0, 0, img.width, img.height, BG, frame)
    x0 = (img.width - text_w) // 2
    y0 = (img.height - 7 * px) // 2
    fg = TEXT if status == "ok" else frame
    for i, g in enumerate(glyphs):
        for row, bits in enumerate(g):
            for col, b in enumerate(bits):
                if b == "1":
                    x = x0 + (i * 6 + col) * px
                    y = y0 + row * px
                    d.rectangle([x, y, x + px - 1, y + px - 1], fill=fg)
    if badge:  # login notice
        d.rectangle([img.width - 7, 3, img.width - 4, 6], fill=TEXT)
    return img


# ---------------------------------------------------------------- right-click menu

ROW = 18
SEP = 7


def context_menu(items, hover: str | None, checked: set):
    """Pixel-art menu. items: [(key, label, kind)] with kind cmd | check | radio | sep.
    Returns (image, {key: (x0, y0, x1, y1)})."""
    w = max(text_w(label, F12()) for _, label, kind in items if kind != "sep") + 34
    h = 6 + sum(SEP if kind == "sep" else ROW for _, _, kind in items) + 4
    img, d = new_canvas(w, h)
    box(d, 0, 0, w, h, PANEL, BORDER)
    regions = {}
    y = 5
    for key, label, kind in items:
        if kind == "sep":
            d.line([(8, y + 3), (w - 9, y + 3)], fill=FAINT)
            y += SEP
            continue
        on = key == hover
        if on:
            d.rectangle([3, y, w - 4, y + ROW - 1], fill=ORANGE)
        fg = BG if on else TEXT
        if kind in ("check", "radio") and key in checked:
            mark = BG if on else ORANGE
            if kind == "check":   # pixel tick
                for dx, dy in [(0, 3), (1, 4), (2, 5), (3, 4), (4, 3), (5, 2), (6, 1)]:
                    d.rectangle([9 + dx, y + 5 + dy, 9 + dx, y + 6 + dy], fill=mark)
            else:                 # dot
                d.rectangle([10, y + 7, 14, y + 11], fill=mark)
        d.text((22, y + 3), label, font=F12(), fill=fg)
        regions[key] = (2, y, w - 2, y + ROW)
        y += ROW
    return img, regions


# ---------------------------------------------------------------- alert popup

AW, AH = 240, 64


def alert_popup(title: str, lines: list[str], status: str, login: bool):
    img, d = new_canvas(AW, AH)
    c = accent(status)
    box(d, 0, 0, AW, AH, BG, c)
    regions = {}
    d.text((9, 8), fit(f"▶ {title}", F12(), AW - 40), font=F12(), fill=c)
    close_x(d, AW - 15, 10, DIM)
    regions["close"] = (AW - 22, 3, AW - 3, 22)
    text_max = AW - 22 - (text_w("로그인", F12()) + 16 if login else 0)
    for i, line in enumerate(lines[:2]):
        d.text((11, 25 + i * 15), fit(line, F12(), text_max if i == 1 else AW - 22), font=F12(), fill=TEXT)
    if login:
        bw = text_w("로그인", F12()) + 12
        button(d, AW - 9 - bw, AH - 25, "로그인", regions, "login", c)
    return img, regions
