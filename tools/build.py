"""Build dist/ClaudeUsageTray.exe (one file, no console).

    .venv\\Scripts\\python tools\\build.py
"""
import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tray import __version__  # noqa: E402
from tray.render import tray_icon  # noqa: E402


def make_icon(path: Path) -> None:
    """App icon = the tray icon at 76%, in every size Windows asks for."""
    sizes = [16, 32, 48, 64, 128, 256]
    imgs = [tray_icon(76, "ok", False, size=s) for s in sizes]
    imgs[-1].save(path, format="ICO", sizes=[(s, s) for s in sizes], append_images=imgs[:-1])


def version_file(path: Path) -> None:
    # Windows wants four numbers; a "-dev" suffix only goes into the version strings.
    nums = tuple(int(x) for x in __version__.split("-")[0].split(".")) + (0,)
    path.write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={nums}, prodvers={nums}),
  kids=[StringFileInfo([StringTable('040904B0', [
    StringStruct('FileDescription', 'Claude Usage Tray'),
    StringStruct('ProductName', 'Claude Usage Tray'),
    StringStruct('FileVersion', '{__version__}'),
    StringStruct('ProductVersion', '{__version__}'),
    StringStruct('OriginalFilename', 'ClaudeUsageTray.exe'),
    StringStruct('LegalCopyright', 'MIT License')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])]
)
""", encoding="utf-8")


def main() -> None:
    build = ROOT / "build"
    build.mkdir(exist_ok=True)
    icon, ver = build / "app.ico", build / "version.txt"
    make_icon(icon)
    version_file(ver)
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
        "--name", "ClaudeUsageTray", "--icon", str(icon), "--version-file", str(ver),
        "--add-data", f"{ROOT / 'assets'};assets",
        "--exclude-module", "matplotlib", "--exclude-module", "numpy", "--exclude-module", "pytest",
        "--exclude-module", "fontTools",
        "--distpath", str(ROOT / "dist"), "--workpath", str(build / "pyi"), "--specpath", str(build),
        str(ROOT / "main.py"),
    ], check=True, cwd=ROOT)
    exe = ROOT / "dist" / "ClaudeUsageTray.exe"
    # The exe isn't code-signed; publish its SHA-256 so downloads can be verified.
    digest = hashlib.sha256(exe.read_bytes()).hexdigest()
    (exe.parent / (exe.name + ".sha256")).write_text(f"{digest}  {exe.name}\n", encoding="utf-8")
    print(f"built {exe} ({exe.stat().st_size / 1e6:.1f} MB) v{__version__}")
    print(f"sha256 {digest}")


if __name__ == "__main__":
    main()
