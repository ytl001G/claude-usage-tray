import io
import os
import json
import logging
import subprocess
import urllib.error

import pytest

from tray import usage_api
from tray.usage_api import AuthError, Credentials, parse_usage, read_credentials, read_snapshot

FAKE_TOKEN = "fake-oauth-token-for-tests-only-0123456789"

# Same shape as a real response; the values are made up.
SAMPLE = {
    "five_hour": {"utilization": 31, "resets_at": "2026-10-09T16:59:59.607123+00:00"},
    "seven_day": {"utilization": 58, "resets_at": "2026-10-10T11:59:59.607144+00:00"},
    "iguana_necktie": {"utilization": 100},
    "limits": [
        {"kind": "session", "percent": 31, "scope": None},
        {"kind": "weekly_all", "percent": 58, "scope": None},
        {"kind": "weekly_scoped", "percent": 9, "scope": {"model": {"id": None, "display_name": "Fable"}}},
    ],
    "seven_day_breakdown": {"rows": [
        {"key": "claude_code", "display_name": "Claude Code", "percent": 64},
        {"key": "chat", "display_name": "Chats", "percent": 35},
        {"key": "cowork", "display_name": "Cowork", "percent": 0},
    ]},
}


# ---------------------------------------------------------------- parse_usage

def test_parse_full_response():
    u = parse_usage(SAMPLE, at=1000.0)
    assert u.week == 58 and u.five == 31
    assert u.week_resets == pytest.approx(1791633599.607144)
    assert u.breakdown == [("Claude Code", 64), ("Chats", 35)]  # 0% rows dropped
    assert u.scoped == [("Fable", 9)]
    assert u.source == "api" and u.at == 1000.0


def test_parse_only_seven_day_is_required():
    u = parse_usage({"seven_day": {"utilization": 40, "resets_at": "2026-10-10T12:00:00+00:00"}}, at=0)
    assert u.week == 40 and u.five is None and u.breakdown == [] and u.scoped == []


@pytest.mark.parametrize("raw", [{}, {"seven_day": None}, {"seven_day": {"utilization": 5}},
                                 {"seven_day": {"resets_at": "2026-10-10T12:00:00+00:00"}}])
def test_parse_without_seven_day_fails(raw):
    with pytest.raises(ValueError):
        parse_usage(raw, at=0)


def test_parse_tolerates_junk_in_optional_fields():
    raw = dict(SAMPLE, limits=["x", None, {"kind": "weekly_scoped"}], seven_day_breakdown={"rows": [None, 3]})
    u = parse_usage(raw, at=0)
    assert u.week == 58 and u.scoped == [] and u.breakdown == []


def test_parse_rounds_fractional_utilization():
    u = parse_usage({"seven_day": {"utilization": 74.6, "resets_at": "2026-10-10T12:00:00+00:00"}}, at=0)
    assert u.week == 75


# ---------------------------------------------------------------- credentials

def write_creds(tmp_path, data):
    p = tmp_path / ".credentials.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def test_reads_claude_ai_token_not_other_entries(tmp_path):
    # An MCP server entry earlier in the file also has an (empty) accessToken.
    p = write_creds(tmp_path, {
        "mcpOAuth": {"plugin:vercel": {"accessToken": ""}},
        "claudeAiOauth": {"accessToken": FAKE_TOKEN, "expiresAt": 2_000_000, "refreshTokenExpiresAt": 3_000_000},
    })
    c = read_credentials(p)
    assert c.access_token == FAKE_TOKEN
    assert c.expires_at == 2000 and c.refresh_expires_at == 3000  # ms -> s


@pytest.mark.parametrize("data", [{}, {"claudeAiOauth": {}}, {"claudeAiOauth": {"accessToken": ""}}])
def test_no_token_gives_none(tmp_path, data):
    assert read_credentials(write_creds(tmp_path, data)) is None


def test_missing_or_broken_file_gives_none(tmp_path):
    assert read_credentials(tmp_path / "nope.json") is None
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert read_credentials(bad) is None


def test_credentials_repr_hides_token():
    c = Credentials(FAKE_TOKEN, 1.0, 2.0)
    assert FAKE_TOKEN not in repr(c) and FAKE_TOKEN not in str(c)


# ---------------------------------------------------------------- fetch_usage

class FakeOpener:
    def __init__(self, fn):
        self.fn = fn

    def open(self, req, timeout):
        return self.fn(req, timeout)


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_fetch_sends_token_only_to_anthropic(monkeypatch):
    seen = {}

    def fake_urlopen(req, timeout):
        seen["url"] = req.full_url
        seen["auth"] = req.get_header("Authorization")
        seen["beta"] = req.get_header("Anthropic-beta")
        return FakeResponse(json.dumps(SAMPLE).encode())

    monkeypatch.setattr(usage_api, "_opener", FakeOpener(fake_urlopen))
    u = usage_api.fetch_usage(Credentials(FAKE_TOKEN, None, None))
    assert u.week == 58
    assert seen["url"] == "https://api.anthropic.com/api/oauth/usage"
    assert seen["auth"] == f"Bearer {FAKE_TOKEN}"
    assert seen["beta"] == "oauth-2025-04-20"


@pytest.mark.parametrize("code", [401, 403])
def test_fetch_auth_error(monkeypatch, code):
    def fake_urlopen(req, timeout):
        raise urllib.error.HTTPError(req.full_url, code, "no", {}, None)

    monkeypatch.setattr(usage_api, "_opener", FakeOpener(fake_urlopen))
    with pytest.raises(AuthError) as e:
        usage_api.fetch_usage(Credentials(FAKE_TOKEN, None, None))
    assert e.value.status == code
    assert FAKE_TOKEN not in str(e.value)


def test_fetch_other_http_error_is_not_auth_error(monkeypatch):
    def fake_urlopen(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 500, "boom", {}, None)

    monkeypatch.setattr(usage_api, "_opener", FakeOpener(fake_urlopen))
    with pytest.raises(urllib.error.HTTPError):
        usage_api.fetch_usage(Credentials(FAKE_TOKEN, None, None))


def test_token_never_logged(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)

    def fake_urlopen(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 401, "no", {}, None)

    monkeypatch.setattr(usage_api, "_opener", FakeOpener(fake_urlopen))
    with pytest.raises(AuthError):
        usage_api.fetch_usage(Credentials(FAKE_TOKEN, None, None))
    assert FAKE_TOKEN not in caplog.text


# ---------------------------------------------------------------- snapshot fallback

def test_read_snapshot(tmp_path):
    p = tmp_path / "usage-snapshot.json"
    p.write_text(json.dumps({"at": 1791500000123, "seven_day": {"used_percentage": 41, "resets_at": 1791600000},
                             "five_hour": {"used_percentage": 7, "resets_at": 1791510000}}), encoding="utf-8")
    u = read_snapshot(p)
    assert (u.week, u.five, u.week_resets, u.source) == (41, 7, 1791600000, "snapshot")
    assert u.at == pytest.approx(1791500000.123)


@pytest.mark.parametrize("content", ["", "{}", '{"at": 1, "seven_day": null}', "[]"])
def test_bad_snapshot_gives_none(tmp_path, content):
    p = tmp_path / "s.json"
    p.write_text(content, encoding="utf-8")
    assert read_snapshot(p) is None


# ---------------------------------------------------------------- refresh / login

class FakeProc:
    def __init__(self, code=0, out="{}", err=""):
        self.returncode, self.out, self.err, self.pid = code, out, err, 1

    def communicate(self, timeout=None):
        return self.out, self.err


def test_refresh_runs_minimal_hidden_claude_call(monkeypatch):
    calls = []
    monkeypatch.setattr(usage_api, "claude_exe", lambda: r"C:\npm\claude.CMD")

    def fake_popen(args, **kw):
        calls.append((args, kw))
        return FakeProc()

    monkeypatch.setattr(usage_api.subprocess, "Popen", fake_popen)
    assert usage_api.refresh_token() is True
    args, kw = calls[0]
    assert args[0] == r"C:\npm\claude.CMD"
    assert "--model" in args and args[args.index("--model") + 1] == "haiku"
    assert args[args.index("--tools") + 1] == ""
    assert "--no-session-persistence" in args
    assert not kw.get("shell")
    assert kw["creationflags"] & usage_api.CREATE_NO_WINDOW


def test_refresh_without_claude_does_nothing(monkeypatch):
    monkeypatch.setattr(usage_api, "claude_exe", lambda: None)
    monkeypatch.setattr(usage_api.subprocess, "Popen", lambda *a, **k: pytest.fail("must not run"))
    assert usage_api.refresh_token() is False


def test_refresh_failure_is_false(monkeypatch):
    monkeypatch.setattr(usage_api, "claude_exe", lambda: "claude")

    def boom(*a, **k):
        raise OSError("x")

    monkeypatch.setattr(usage_api.subprocess, "Popen", boom)
    assert usage_api.refresh_token() is False


def test_refresh_nonzero_exit_is_false(monkeypatch):
    monkeypatch.setattr(usage_api, "claude_exe", lambda: "claude")
    monkeypatch.setattr(usage_api.subprocess, "Popen", lambda args, **k: FakeProc(1, "", "error: unknown option"))
    assert usage_api.refresh_token() is False


def test_redirects_are_refused_so_the_token_cannot_follow():
    h = usage_api._NoRedirect()
    req = usage_api.urllib.request.Request(usage_api.USAGE_URL, headers={"Authorization": "Bearer x"})
    with pytest.raises(urllib.error.HTTPError):
        h.redirect_request(req, None, 302, "Found", {}, "https://evil.example/")


def test_opener_never_follows_redirects():
    handlers = usage_api._opener.handlers
    assert any(isinstance(h, usage_api._NoRedirect) for h in handlers)
    assert not any(type(h) is usage_api.urllib.request.HTTPRedirectHandler for h in handlers)


def test_unparsable_reset_time_fails():
    with pytest.raises(ValueError):
        parse_usage({"seven_day": {"utilization": 5, "resets_at": "not a date"}}, at=0)


def test_idle_week_without_reset_time_is_zero():
    u = parse_usage({"seven_day": {"utilization": 0, "resets_at": None}}, at=1000.0)
    assert u.week == 0 and u.week_resets == 1000.0 + 7 * 86400


def test_used_week_without_reset_time_fails():
    with pytest.raises(ValueError):
        parse_usage({"seven_day": {"utilization": 30, "resets_at": None}}, at=0)


# ---------------------------------------------------------------- finding claude safely

def _touch(p):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("")
    return p


def test_find_on_path_ignores_current_directory(tmp_path, monkeypatch):
    planted = _touch(tmp_path / "cwd" / "claude.cmd")
    real = _touch(tmp_path / "npm" / "claude.cmd")
    monkeypatch.chdir(planted.parent)
    monkeypatch.setenv("PATH", str(real.parent))
    monkeypatch.setenv("PATHEXT", ".COM;.EXE;.BAT;.CMD")
    assert os.path.normcase(usage_api.find_on_path("claude")) == os.path.normcase(str(real))


def test_find_on_path_order_and_missing(tmp_path, monkeypatch):
    d = tmp_path / "bin"
    _touch(d / "claude.cmd")
    exe = _touch(d / "claude.exe")
    monkeypatch.setenv("PATH", f"{tmp_path / 'nope'};;{d}")
    monkeypatch.setenv("PATHEXT", ".EXE;.CMD")
    assert os.path.normcase(usage_api.find_on_path("claude")) == os.path.normcase(str(exe))
    assert usage_api.find_on_path("definitely-not-here") is None


def test_start_login_uses_full_cmd_path_and_visible_console(monkeypatch):
    seen = {}

    class P:
        def wait(self):
            return 0

    def fake_popen(cmd, **kw):
        seen["cmd"], seen["kw"] = cmd, kw
        return P()

    monkeypatch.setattr(usage_api, "claude_exe", lambda: "C:/npm/claude.CMD")
    monkeypatch.setenv("SystemRoot", "C:/Windows")
    monkeypatch.setenv("ComSpec", "C:/tools/some-other-shell.exe")   # must be ignored
    monkeypatch.setattr(usage_api.subprocess, "Popen", fake_popen)
    done = []
    assert usage_api.start_login(on_done=lambda: done.append(1)) is True
    cmd_exe = os.path.join("C:/Windows", "System32", "cmd.exe")
    assert seen["cmd"].startswith(f'"{cmd_exe}" /c ""C:/npm/claude.CMD" auth login')
    assert seen["kw"]["creationflags"] & usage_api.subprocess.CREATE_NEW_CONSOLE
    import time as _t
    for _ in range(100):
        if done:
            break
        _t.sleep(0.01)
    assert done == [1]


def test_start_login_without_claude(monkeypatch):
    monkeypatch.setattr(usage_api, "claude_exe", lambda: None)
    monkeypatch.setattr(usage_api.subprocess, "Popen", lambda *a, **k: pytest.fail("must not run"))
    assert usage_api.start_login(on_done=lambda: None) is False


def test_network_is_blocked_during_tests():
    import socket
    with pytest.raises(RuntimeError):
        socket.create_connection(("api.anthropic.com", 443))


@pytest.mark.parametrize("junk", [
    {"five_hour": [1, 2]},
    {"five_hour": {"utilization": "lots", "resets_at": "x"}},
    {"seven_day_breakdown": [1, 2]},
    {"seven_day_breakdown": {"rows": [{"display_name": "A", "percent": "64.5"}]}},
    {"limits": [{"kind": "weekly_scoped", "percent": 3, "scope": "x"}]},
    {"limits": {"not": "a list"}},
])
def test_broken_optional_fields_are_ignored(junk):
    raw = {"seven_day": {"utilization": 40, "resets_at": "2026-10-10T12:00:00+00:00"}, **junk}
    u = parse_usage(raw, at=0)
    assert u.week == 40


def test_idle_week_reset_is_marked_unknown():
    u = parse_usage({"seven_day": {"utilization": 0, "resets_at": None}}, at=1000.0)
    assert u.resets_known is False


def test_refresh_timeout_kills_the_whole_process_tree(monkeypatch):
    """claude.cmd runs node under cmd.exe: killing only cmd would leave node running."""
    killed = []

    class P:
        pid = 4242
        returncode = None

        def communicate(self, timeout=None):
            if not killed:
                raise usage_api.subprocess.TimeoutExpired("claude", timeout)
            return "", ""

        def kill(self):
            pass

    monkeypatch.setattr(usage_api, "claude_exe", lambda: "C:/npm/claude.CMD")
    monkeypatch.setattr(usage_api.subprocess, "Popen", lambda *a, **k: P())
    monkeypatch.setattr(usage_api, "_kill_tree", lambda pid: killed.append(pid))
    assert usage_api.refresh_token(timeout=1) is False
    assert killed == [4242]


def test_refresh_never_runs_with_api_keys_or_env_tokens(monkeypatch):
    """With ANTHROPIC_API_KEY set, claude -p would bill the API key instead of refreshing
    the subscription login. The refresh runs with those variables removed."""
    seen = {}
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN",
              "ANTHROPIC_BASE_URL", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX"):
        monkeypatch.setenv(k, "x")
    monkeypatch.setenv("KEEP_ME", "1")
    monkeypatch.setattr(usage_api, "claude_exe", lambda: "C:/npm/claude.CMD")

    def fake_popen(args, **kw):
        seen["env"] = kw["env"]
        return FakeProc()

    monkeypatch.setattr(usage_api.subprocess, "Popen", fake_popen)
    usage_api.refresh_token()
    env = seen["env"]
    assert env["KEEP_ME"] == "1"
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN",
              "ANTHROPIC_BASE_URL", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX"):
        assert k not in env
