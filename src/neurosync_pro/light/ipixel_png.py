"""Minimal RGB PNG (truecolor) — solid fill for iPIXEL ``send_image``-style transfer."""

from __future__ import annotations

import struct
import zlib


def solid_rgb_png(width: int, height: int, r: int, g: int, b: int) -> bytes:
    """Return PNG bytes for an image filled with ``(r,g,b)`` (8-bit RGB)."""

    def _chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    w = max(1, int(width))
    h = max(1, int(height))
    r0 = max(0, min(255, int(r)))
    g0 = max(0, min(255, int(g)))
    b0 = max(0, min(255, int(b)))

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    row = b"\x00" + bytes([r0, g0, b0]) * w
    raw = row * h
    idat = zlib.compress(raw, 9)
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", idat) + _chunk(b"IEND", b"")
