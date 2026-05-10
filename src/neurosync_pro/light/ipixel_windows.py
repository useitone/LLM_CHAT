"""Split PNG file bytes into BLE windows (same layout as ``pypixelcolor`` ``send_image`` for static PNG).

Derived from ``pypixelcolor.commands.send_image._build_send_plan`` (non-GIF path).
See: https://github.com/lucagoc/pypixelcolor — MIT.
"""

from __future__ import annotations

import binascii


def _crc32_le(data: bytes) -> bytes:
    calculated_crc = binascii.crc32(data) & 0xFFFFFFFF
    return calculated_crc.to_bytes(4, byteorder="little")


def _len_prefix_for(inner: bytes) -> bytes:
    """Legacy length prefix: ``2 + len(inner)`` as uint16 LE."""
    return int(2 + len(inner)).to_bytes(2, byteorder="little")


def build_png_transfer_windows(png_file_bytes: bytes, *, save_slot: int = 0) -> list[bytes]:
    """
    Each returned element is one logical window (may still be chunked to 244 bytes on write).

    ``save_slot``: ``0`` for live buffer in typical apps; ``1``–``9`` stores to a slot (device-dependent).
    """

    payload = png_file_bytes
    size_bytes = len(payload).to_bytes(4, byteorder="little")
    crc_bytes = _crc32_le(payload)

    windows: list[bytes] = []
    window_size = 12 * 1024
    pos = 0
    window_index = 0
    save_slot = int(save_slot) & 0xFF

    while pos < len(payload):
        window_end = min(pos + window_size, len(payload))
        chunk_payload = payload[pos:window_end]
        option = 0x00 if window_index == 0 else 0x02
        cur_tail = bytes([0x00, save_slot])
        header = bytes([0x02, 0x00, option]) + size_bytes + crc_bytes + cur_tail
        frame = header + chunk_payload
        prefix = _len_prefix_for(frame)
        message = prefix + frame
        windows.append(message)
        window_index += 1
        pos = window_end

    return windows
