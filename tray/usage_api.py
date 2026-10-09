"""Reads Claude plan usage from the endpoint Claude Code uses, and keeps the token fresh.

The OAuth token is read from ~/.claude/.credentials.json and only ever sent to
api.anthropic.com. It is never logged or written anywhere by this app; refreshing
is left to Claude Code itself (see refresh_token).
"""
import json
import logging
import os
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime

from .config import CREDENTIALS_FILE, SNAPSHOT_FILE

log = logging.getLogger(__name__)

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"

# Smallest possible Claude Code run: no default system prompt, tools, MCP, settings or
# skills, and no saved session. Claude Code refreshes an expired token before the call.
REFRESH_ARGS = [
    "-p", ".",
    "--model", "haiku",
    "--system-prompt", "Reply with one character.",
    "--tools", "",
    "--strict-mcp-config",
    "--setting-sources", "",
    "--disable-slash-commands",
    "--no-session-persistence",
    "--output-format", "json",
]

CREATE_NO_WINDOW = 0x08000000

# Any of these makes `claude -p` use something other than the stored subscription login
# (an API key would even be billed), so the refresh call runs without them.
_AUTH_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN",
             "ANTHROPIC_BASE_URL", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX")


def _refresh_env() -> dict:
    return {k: v for k, v in os.environ.items() if k.upper() not in _AUTH_ENV}


WEEK = 7 * 86400


class AuthError(Exception):
    """The server rejected the token (401 = expired/invalid, 403 = not allowed)."""

    def __init__(self, status: int = 401):
        super().__init__(f"HTTP {status}")
        self.status = status


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """urllib would copy the Authorization header to wherever a redirect points.
    The endpoint never redirects, so refuse instead of following."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "redirect refused", headers, fp)


_opener = urllib.request.build_opener(_NoRedirect)


@dataclass
class Usage:
    at: float                      # epoch seconds this reading was taken
    week: int                      # 7-day utilization, %
    week_resets: float             # epoch seconds
    five: int | None = None        # 5-hour utilization, %
    five_resets: float | None = None
    breakdown: list = field(default_factory=list)  # [(label, pct)] share of this week's usage
    scoped: list = field(default_factory=list)     # [(label, pct)] per-model weekly limits
    source: str = "api"            # "api" or "snapshot"
    resets_known: bool = True      # False when nothing is used yet and the server gave no reset


@dataclass
class Credentials:
    access_token: str
    expires_at: float | None           # epoch seconds
    refresh_expires_at: float | None   # epoch seconds

    def __repr__(self):  # keep the token out of any accidental log line
        return f"Credentials(expires_at={self.expires_at}, refresh_expires_at={self.refresh_expires_at})"


def read_credentials(path=CREDENTIALS_FILE) -> Credentials | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        o = data.get("claudeAiOauth") or {}
    except (OSError, ValueError, AttributeError):
        return None
    if not o.get("accessToken"):
        return None
    ms = lambda v: v / 1000 if isinstance(v, (int, float)) else None
    return Credentials(o["accessToken"], ms(o.get("expiresAt")), ms(o.get("refreshTokenExpiresAt")))


def _iso(s) -> float | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s).timestamp()
    except (TypeError, ValueError):
        return None


def parse_usage(raw: dict, at: float) -> Usage:
    """Only seven_day is required; everything else is optional because the endpoint is
    internal and grows new fields all the time."""
    week = raw.get("seven_day") or {}
    if week.get("utilization") is None:
        raise ValueError("seven_day missing from usage response")
    week_resets = _iso(week.get("resets_at"))
    known = week_resets is not None
    if not known:
        if week.get("resets_at") or week["utilization"]:
            raise ValueError("seven_day.resets_at missing or unreadable")
        # Nothing used yet: the 7-day window only starts with the next use.
        week_resets = at + WEEK

    # Everything below is optional: a field that changed shape is skipped, not fatal.
    five, five_resets = None, None
    try:
        f = raw.get("five_hour") or {}
        if f.get("utilization") is not None:
            five = int(round(float(f["utilization"])))
            five_resets = _iso(f.get("resets_at"))
    except (AttributeError, TypeError, ValueError):
        five, five_resets = None, None

    breakdown = []
    try:
        for row in (raw.get("seven_day_breakdown") or {}).get("rows") or []:
            if isinstance(row, dict) and row.get("percent"):
                breakdown.append((str(row.get("display_name") or row.get("key") or "?"),
                                  int(round(float(row["percent"])))))
    except (AttributeError, TypeError, ValueError):
        breakdown = []

    scoped = []
    try:
        for lim in raw.get("limits") or []:
            if not isinstance(lim, dict) or lim.get("kind") != "weekly_scoped":
                continue
            model = ((lim.get("scope") or {}).get("model") or {}).get("display_name")
            if model and lim.get("percent") is not None:
                scoped.append((str(model), int(round(float(lim["percent"])))))
    except (AttributeError, TypeError, ValueError):
        scoped = []

    return Usage(
        at=at,
        week=int(round(float(week["utilization"]))),
        week_resets=week_resets,
        five=five,
        five_resets=five_resets,
        breakdown=breakdown,
        scoped=scoped,
        resets_known=known,
    )


def fetch_usage(creds: Credentials, timeout: float = 20) -> Usage:
    req = urllib.request.Request(USAGE_URL, headers={
        "Authorization": f"Bearer {creds.access_token}",
        "anthropic-beta": "oauth-2025-04-20",
        "Content-Type": "application/json",
        "User-Agent": "claude-usage-tray/1.0",
    })
    try:
        with _opener.open(req, timeout=timeout) as res:
            raw = json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise AuthError(e.code) from None
        raise
    return parse_usage(raw, time.time())


def read_snapshot(path=SNAPSHOT_FILE) -> Usage | None:
    """Fallback: the numbers the status line script saves while Claude Code runs."""
    try:
        s = json.loads(path.read_text(encoding="utf-8"))
        week = s.get("seven_day") or {}
        five = s.get("five_hour") or {}
        if week.get("used_percentage") is None or not week.get("resets_at"):
            return None
        return Usage(
            at=s["at"] / 1000,
            week=int(round(week["used_percentage"])),
            week_resets=float(week["resets_at"]),
            five=int(round(five["used_percentage"])) if five.get("used_percentage") is not None else None,
            five_resets=float(five["resets_at"]) if five.get("resets_at") else None,
            source="snapshot",
        )
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def find_on_path(name: str) -> str | None:
    """Like shutil.which, but never looks in the current directory (Python 3.11's which
    does on Windows), so a planted claude.cmd next to the downloaded exe can't run."""
    exts = [e for e in os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(";") if e]
    for d in os.environ.get("PATH", "").split(os.pathsep):
        d = d.strip().strip('"')
        if not d or not os.path.isabs(d):
            continue
        for ext in exts:
            candidate = os.path.join(d, name + ext)
            if os.path.isfile(candidate):
                return candidate
    return None


def claude_exe() -> str | None:
    return find_on_path("claude")


def _cmd_exe() -> str:
    # Always the system cmd.exe: the login command uses cmd syntax, and ComSpec could
    # point at some other shell.
    return os.path.join(os.environ.get("SystemRoot") or r"C:\Windows", "System32", "cmd.exe")


def _kill_tree(pid: int) -> None:
    """claude.cmd runs node under cmd.exe; killing only cmd would leave node running."""
    root = os.environ.get("SystemRoot", r"C:\Windows")
    subprocess.run([os.path.join(root, "System32", "taskkill.exe"), "/PID", str(pid), "/T", "/F"],
                   capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=30)


def refresh_token(timeout: float = 180) -> bool:
    """Let Claude Code refresh the token by making the cheapest call it can."""
    exe = claude_exe()
    if not exe:
        log.warning("refresh: claude command not found")
        return False
    try:
        p = subprocess.Popen(
            [exe, *REFRESH_ARGS],
            cwd=tempfile.gettempdir(),  # keep project CLAUDE.md files out of the prompt
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace", creationflags=CREATE_NO_WINDOW,
            env=_refresh_env(),
        )
    except OSError as e:
        log.warning("refresh failed: %s", type(e).__name__)
        return False
    try:
        _, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        log.warning("refresh: timed out")
        try:
            _kill_tree(p.pid)
        except (OSError, subprocess.SubprocessError):
            p.kill()
        return False
    if p.returncode != 0:
        # First line only, shortened: enough to see e.g. an unknown CLI option.
        first = (err or "").strip().splitlines()[:1]
        log.warning("refresh: exit %s %s", p.returncode, first[0][:120] if first else "")
    else:
        log.info("refresh: exit 0")
    return p.returncode == 0


def start_login(on_done) -> bool:
    """Open a visible console for `claude auth login`: it asks the user to paste a code
    from the browser, so it cannot run hidden. on_done() runs when the console closes.
    Returns False if the claude command can't be found."""
    import threading

    exe = claude_exe()
    if not exe:
        log.warning("login: claude command not found")
        return False
    # A string, not a list: cmd strips the outer quotes itself, while list2cmdline
    # would backslash-escape the inner ones, which cmd does not understand.
    p = subprocess.Popen(
        f'"{_cmd_exe()}" /c ""{exe}" auth login & echo. & pause"',
        creationflags=subprocess.CREATE_NEW_CONSOLE,
    )
    threading.Thread(target=lambda: (p.wait(), on_done()), daemon=True).start()
    return True
