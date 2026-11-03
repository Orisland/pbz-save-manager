import unittest
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
ICON = ROOT / "assets" / "pbz-save.ico"


class IconTests(unittest.TestCase):
    def test_icon_uses_game_art_and_a_corner_e(self) -> None:
        if not ICON.is_file():
            self.skipTest("图标由 tools/make_icon.py 从本机游戏生成，仓库不附带游戏图标。")
        with Image.open(ICON) as source:
            self.assertTrue({(16, 16), (32, 32), (48, 48), (256, 256)} <= set(source.info["sizes"]))

        corner = _frame((16, 16)).crop((8, 8, 16, 16))
        rows = [
            "".join("#" if corner.getpixel((x, y))[0] > 200 else "." for x in range(corner.width))
            for y in range(corner.height)
        ]
        self.assertEqual(
            rows,
            [
                "........",
                "........",
                ".######.",
                ".#......",
                ".######.",
                ".#......",
                ".######.",
                "........",
            ],
        )

        badge = _frame((256, 256)).crop((150, 150, 256, 256))
        raw = badge.tobytes()
        bright = 0
        dark = 0
        for index in range(0, len(raw), 3):
            red, green, blue = raw[index], raw[index + 1], raw[index + 2]
            if red > 230 and green > 230 and blue > 230:
                bright += 1
            elif red < 30 and green < 30 and blue < 40:
                dark += 1
        self.assertGreater(bright, 80)
        self.assertGreater(dark, 80)


def _frame(size: tuple[int, int]) -> Image.Image:
    with Image.open(ICON) as source:
        source.size = size
        source.load()
        return source.convert("RGB")
