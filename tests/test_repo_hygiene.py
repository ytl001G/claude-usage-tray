"""Nothing personal may end up in the public repository."""
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

PATTERNS = {
    "email address": re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
    "Windows user folder": re.compile(r"[A-Za-z]:\\?Users\\?(?!<|%|Public|USERNAME)[\w.-]+", re.I),
    "OAuth/API token": re.compile(r"sk-ant-[a-z]+\d*-[\w-]{20,}"),
}
ALLOWED_EMAILS = {
    "noreply@anthropic.com",   # the Co-Authored-By trailer convention
    "quiple@quiple.dev",       # Galmuri font author, required by the OFL notice
}


def tracked_files():
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [ROOT / f for f in out.splitlines() if not f.endswith((".ttf", ".png", ".ico"))]


@pytest.mark.parametrize("path", tracked_files(), ids=lambda p: p.name)
def test_no_personal_data_in_tracked_files(path):
    text = path.read_text(encoding="utf-8", errors="replace")
    for what, rx in PATTERNS.items():
        hits = [m for m in rx.findall(text) if m not in ALLOWED_EMAILS]
        assert not hits, f"{what} in {path.name}: {hits[:3]}"


def test_commit_identities_are_anonymous():
    out = subprocess.run(["git", "log", "--all", "--format=%ae%n%ce"], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout
    for email in set(out.split()):
        assert email.endswith("@users.noreply.github.com"), email


@pytest.mark.parametrize("path", [p for p in tracked_files() if p.suffix == ".py"], ids=lambda p: p.name)
def test_every_python_file_compiles(path):
    """Catches syntax errors in files the other tests don't import (e.g. tools/build.py)."""
    compile(path.read_text(encoding="utf-8"), str(path), "exec")


def test_no_env_files_are_tracked():
    """No secrets are needed (the token is read from Claude Code's own login file), so a
    .env file in the repo could only be an accident."""
    names = [p.name for p in tracked_files()]
    assert not [n for n in names if n == ".env" or n.startswith(".env.")]


def test_env_files_are_ignored():
    out = subprocess.run(["git", "check-ignore", ".env", ".env.local"], cwd=ROOT,
                         capture_output=True, text=True)
    assert set(out.stdout.split()) == {".env", ".env.local"}


# GitHub (CommonMark) won't treat ** as bold when a closing ** comes right after
# punctuation and is followed by a letter ("**사용량(%)**을"), or an opening ** comes right
# after a letter and is followed by punctuation. The asterisks then show up literally.
_PUNCT = set(""")]}%.,:;!?'"`」』…·~([{「『""")


def _is_word(ch: str) -> bool:
    return ch.isalnum()


def broken_bold(line: str) -> bool:
    prose = re.sub(r"`[^`]*`", "", line)                  # inline code can contain anything
    opening = True
    for m in re.finditer(r"\*\*", prose):
        before = prose[m.start() - 1] if m.start() > 0 else " "
        after = prose[m.end()] if m.end() < len(prose) else " "
        if opening and _is_word(before) and after in _PUNCT:
            return True
        if not opening and before in _PUNCT and _is_word(after):
            return True
        opening = not opening
    return False


@pytest.mark.parametrize("line,broken", [
    ("화면 구석에 **Claude 주간 사용량(%)**을 띄웁니다", True),     # rendered literally on GitHub
    ("값이고 **정수 %**입니다", True),
    ("화면 구석에 **Claude 주간 사용량**(%)을 띄웁니다", False),     # renders fine
    ("조회가 **401**이고, 토큰이 **이미 만료**(또는 60초 이내)", False),
    ("**두 창 모두** 포커스를 빼앗지 않음", False),
    ("`a**(b)**c` 코드는 무시", False),
])
def test_broken_bold_detector(line, broken):
    assert broken_bold(line) is broken


@pytest.mark.parametrize("path", [p for p in tracked_files() if p.suffix == ".md"], ids=lambda p: p.name)
def test_bold_markers_render_on_github(path):
    bad = [f"{path.name}:{n}" for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
           if broken_bold(line)]
    assert not bad, bad


# ---------------------------------------------------------------- links between docs

def github_slug(heading: str) -> str:
    """GitHub's heading anchor: lowercase, drop punctuation (keep letters incl. Hangul,
    digits, spaces, hyphens), spaces -> hyphens."""
    text = re.sub(r"`|\*\*|\*|_", "", heading.strip().lower())
    text = "".join(ch for ch in text if ch.isalnum() or ch in " -")
    return text.replace(" ", "-")


def anchors_of(md: Path) -> set:
    found, in_code = set(), False
    for line in md.read_text(encoding="utf-8").splitlines():
        if line.startswith("```"):
            in_code = not in_code
        elif not in_code and re.match(r"#{1,6} ", line):
            found.add(github_slug(line.lstrip("#")))
    return found


def test_github_slug():
    assert github_slug(" 보안과 개인정보") == "보안과-개인정보"
    assert github_slug("자원 사용량 (Windows 11, v1.0.0, 대기 상태)") == "자원-사용량-windows-11-v100-대기-상태"


@pytest.mark.parametrize("path", [p for p in tracked_files() if p.suffix == ".md"], ids=lambda p: p.name)
def test_relative_links_and_anchors_resolve(path):
    text = re.sub(r"```.*?```", "", path.read_text(encoding="utf-8"), flags=re.S)
    bad = []
    for target in re.findall(r"\]\(([^)\s]+)\)", text):
        if re.match(r"[a-z]+:", target) or target.startswith("../../"):
            continue                      # web URLs, and GitHub pages like ../../releases
        file_part, _, anchor = target.partition("#")
        dest = (path.parent / file_part).resolve() if file_part else path
        if not dest.exists():
            bad.append(f"{target}: no such file")
        elif anchor and dest.suffix == ".md" and anchor not in anchors_of(dest):
            bad.append(f"{target}: no heading #{anchor}")
    assert not bad, bad
