"""用影之刃零程序图标做管理器图标，并在右下角加上字母 E。

图标从本机已安装的游戏程序里取出，不改游戏文件。
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import POINTER, byref, c_void_p, sizeof, wintypes
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
ICON_PATH = ROOT / "assets" / "pbz-save.ico"
PNG_PATH = ROOT / "assets" / "pbz-save.png"
SIZES = (256, 128, 64, 48, 32, 16)
FONT_CANDIDATES = (
    Path(r"C:\Windows\Fonts\ariblk.ttf"),
    Path(r"C:\Windows\Fonts\seguibl.ttf"),
    Path(r"C:\Windows\Fonts\arialbd.ttf"),
    Path(r"C:\Windows\Fonts\segoeuib.ttf"),
)


class _IconInfo(ctypes.Structure):
    _fields_ = (
        ("fIcon", wintypes.BOOL),
        ("xHotspot", wintypes.DWORD),
        ("yHotspot", wintypes.DWORD),
        ("hbmMask", c_void_p),
        ("hbmColor", c_void_p),
    )


class _Bitmap(ctypes.Structure):
    _fields_ = (
        ("bmType", wintypes.LONG),
        ("bmWidth", wintypes.LONG),
        ("bmHeight", wintypes.LONG),
        ("bmWidthBytes", wintypes.LONG),
        ("bmPlanes", wintypes.WORD),
        ("bmBitsPixel", wintypes.WORD),
        ("bmBits", c_void_p),
    )


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = (
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    )


def main() -> None:
    exe = _game_exe()
    if exe is None:
        if ICON_PATH.is_file():
            return
        raise SystemExit("没有找到影之刃零，也没有现成的图标。")
    frames = [stamp_corner_e(extract_icon(exe, size)) for size in SIZES]
    ICON_PATH.parent.mkdir(parents=True, exist_ok=True)
    _write_icon(frames, ICON_PATH)
    frames[0].save(PNG_PATH)


def stamp_corner_e(source: Image.Image) -> Image.Image:
    """在右下角画一块深色底，上面放白色字母 E。"""
    image = source.convert("RGBA")
    if image.width <= 16:
        return _stamp_tiny_e(image)
    size = image.width
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    left, top, right, bottom = _badge_box(size)
    radius = max(1, (right - left) // 6)
    shift = max(1, size // 80)
    draw.rounded_rectangle(
        (left + shift, top + shift, right + shift, bottom + shift),
        radius=radius,
        fill=(0, 0, 0, 110),
    )
    outline = max(1, size // 96)
    draw.rounded_rectangle(
        (left, top, right, bottom),
        radius=radius,
        fill=(10, 16, 22, 242),
        outline=(236, 240, 244, 255),
        width=outline,
    )
    _draw_e(draw, left, top, right, bottom, size)
    return Image.alpha_composite(image, overlay)


def extract_icon(exe: Path, size: int) -> Image.Image:
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    user32.PrivateExtractIconsW.argtypes = [
        wintypes.LPCWSTR,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        POINTER(c_void_p),
        c_void_p,
        ctypes.c_uint,
        ctypes.c_uint,
    ]
    user32.PrivateExtractIconsW.restype = ctypes.c_uint
    user32.GetIconInfo.argtypes = [c_void_p, POINTER(_IconInfo)]
    user32.GetIconInfo.restype = wintypes.BOOL
    user32.DestroyIcon.argtypes = [c_void_p]
    user32.GetDC.restype = c_void_p
    user32.ReleaseDC.argtypes = [c_void_p, c_void_p]
    gdi32.GetObjectW.argtypes = [c_void_p, ctypes.c_int, c_void_p]
    gdi32.GetObjectW.restype = ctypes.c_int
    gdi32.GetDIBits.argtypes = [
        c_void_p,
        c_void_p,
        ctypes.c_uint,
        ctypes.c_uint,
        c_void_p,
        c_void_p,
        ctypes.c_uint,
    ]
    gdi32.GetDIBits.restype = ctypes.c_int
    gdi32.DeleteObject.argtypes = [c_void_p]

    handle = c_void_p()
    count = user32.PrivateExtractIconsW(str(exe), 0, size, size, byref(handle), None, 1, 0)
    if count == 0 or not handle.value:
        raise SystemExit(f"没有从游戏程序里取出 {size} 像素的图标。")
    info = _IconInfo()
    try:
        if not user32.GetIconInfo(handle, byref(info)):
            raise SystemExit("游戏图标读不出来。")
        bitmap = _Bitmap()
        if gdi32.GetObjectW(info.hbmColor, sizeof(bitmap), byref(bitmap)) == 0:
            raise SystemExit("游戏图标没有颜色层。")
        width, height = bitmap.bmWidth, abs(bitmap.bmHeight)
        header = _BitmapInfoHeader()
        header.biSize = sizeof(_BitmapInfoHeader)
        header.biWidth = width
        header.biHeight = -height
        header.biPlanes = 1
        header.biBitCount = 32
        header.biCompression = 0
        buffer = ctypes.create_string_buffer(width * height * 4)
        device = user32.GetDC(None)
        try:
            lines = gdi32.GetDIBits(device, info.hbmColor, 0, height, buffer, byref(header), 0)
        finally:
            user32.ReleaseDC(None, device)
        if lines != height:
            raise SystemExit("游戏图标像素没有读全。")
        return Image.frombuffer("RGBA", (width, height), buffer, "raw", "BGRA", 0, 1).copy()
    finally:
        user32.DestroyIcon(handle)
        if info.hbmColor:
            gdi32.DeleteObject(info.hbmColor)
        if info.hbmMask:
            gdi32.DeleteObject(info.hbmMask)


def _stamp_tiny_e(image: Image.Image) -> Image.Image:
    """16 像素时用固定点阵，避免圆角和笔画挤成一块。"""
    pattern = (
        "........",
        "........",
        ".######.",
        ".#......",
        ".######.",
        ".#......",
        ".######.",
        "........",
    )
    pixels = image.load()
    origin_x = image.width - len(pattern[0])
    origin_y = image.height - len(pattern)
    dark = (8, 12, 18, 255)
    white = (255, 255, 255, 255)
    for y, row in enumerate(pattern):
        for x, cell in enumerate(row):
            pixels[origin_x + x, origin_y + y] = white if cell == "#" else dark
    return image


def _badge_box(size: int) -> tuple[int, int, int, int]:
    if size <= 16:
        margin, badge = 0, 8
    elif size <= 32:
        margin, badge = 1, 14
    elif size <= 48:
        margin, badge = 1, 20
    else:
        margin = max(2, round(size * 0.035))
        badge = round(size * 0.36)
    right = size - margin - 1
    bottom = size - margin - 1
    return right - badge + 1, bottom - badge + 1, right, bottom


def _draw_e(draw: ImageDraw.ImageDraw, left: int, top: int, right: int, bottom: int, size: int) -> None:
    if size < 48:
        _draw_block_e(draw, left, top, right, bottom)
        return
    font = _font(int((bottom - top) * 0.68))
    center_x = (left + right) / 2
    center_y = (top + bottom) / 2 + size * 0.006
    draw.text((center_x, center_y), "E", font=font, fill=(255, 255, 255, 255), anchor="mm")


def _draw_block_e(draw: ImageDraw.ImageDraw, left: int, top: int, right: int, bottom: int) -> None:
    width = right - left + 1
    height = bottom - top + 1
    pad = max(1, round(width * 0.18))
    bar = max(1, round(height * 0.16))
    x1 = left + pad
    x2 = right - pad
    y1 = top + pad
    y2 = bottom - pad
    middle = (y1 + y2) // 2
    color = (255, 255, 255, 255)
    draw.rectangle((x1, y1, x2, y1 + bar - 1), fill=color)
    draw.rectangle((x1, middle - bar // 2, x2, middle - bar // 2 + bar - 1), fill=color)
    draw.rectangle((x1, y2 - bar + 1, x2, y2), fill=color)
    draw.rectangle((x1, y1, x1 + bar - 1, y2), fill=color)


def _font(pixel_size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in FONT_CANDIDATES:
        if path.is_file():
            return ImageFont.truetype(str(path), pixel_size)
    return ImageFont.load_default(pixel_size)


def _write_icon(frames: list[Image.Image], path: Path) -> None:
    largest = frames[0]
    # 用位图而不是内嵌 PNG。资源管理器对程序里的 PNG 图标经常仍显示系统默认图标。
    largest.save(
        path,
        format="ICO",
        bitmap_format="bmp",
        sizes=[(frame.width, frame.height) for frame in frames],
        append_images=frames[1:],
    )


def _game_exe() -> Path | None:
    src = ROOT / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    from pbz_save_manager.locate import locate

    found = locate()
    if found.exe_path is not None and found.exe_path.is_file():
        return found.exe_path
    return None


if __name__ == "__main__":
    main()
