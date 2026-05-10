"""iPIXEL solid PNG and windowing (no BLE)."""

from neurosync_pro.light.ipixel_png import solid_rgb_png
from neurosync_pro.light.ipixel_windows import build_png_transfer_windows


def test_solid_png_magic_and_size() -> None:
    png = solid_rgb_png(8, 4, 10, 20, 30)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(png) >= 60


def test_png_windows_nonempty() -> None:
    png = solid_rgb_png(32, 16, 255, 0, 0)
    wins = build_png_transfer_windows(png)
    assert len(wins) >= 1
    assert all(len(w) > 10 for w in wins)
