"""Shrink the bundled Galmuri fonts to what the app can draw: ASCII, the 2,350 common Hangul
syllables (KS X 1001) and a few symbols. Cuts ~12 MB of CJK/Japanese glyphs (smaller .exe, less memory).

    python tools/subset_fonts.py <galmuri dist dir>

The full fonts come from the `galmuri` npm package (`npm pack galmuri`) or
https://github.com/quiple/galmuri. Galmuri is OFL-1.1 with no Reserved Font Name, so a
subset may keep the name. tests/test_render.py checks every character we use is kept.
"""
import sys
from pathlib import Path

from fontTools import subset

FONTS = ["Galmuri11.ttf", "Galmuri11-Bold.ttf", "Galmuri9.ttf"]


def _ks_x_1001(c: int) -> bool:
    # Python's euc_kr codec encodes the other syllables too, as 8-byte jamo sequences;
    # the 2,350 precomposed ones are exactly those that take 2 bytes.
    try:
        return len(chr(c).encode("euc_kr")) == 2
    except UnicodeEncodeError:
        return False


UNICODES = (
    list(range(0x20, 0x7F))                                    # ASCII
    + [c for c in range(0xAC00, 0xD7A4) if _ks_x_1001(c)]      # 2,350 common syllables
    + [ord(c) for c in "▶·…"]
)


def main(src: Path, dst: Path) -> None:
    opts = subset.Options()
    opts.layout_features = ["*"]
    opts.notdef_outline = True
    for name in FONTS:
        font = subset.load_font(str(src / name), opts)
        sub = subset.Subsetter(opts)
        sub.populate(unicodes=UNICODES)
        sub.subset(font)
        subset.save_font(font, str(dst / name), opts)
        print(f"{name}: {(src / name).stat().st_size // 1024} KB -> {(dst / name).stat().st_size // 1024} KB")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(__file__).resolve().parent.parent / "assets")
