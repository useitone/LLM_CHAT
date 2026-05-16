"""ACK parsing for iPIXEL notify characteristic (``fa03``).

Logic aligned with ``pypixelcolor.lib.transport.ack_manager.AckManager`` (MIT,
https://github.com/lucagoc/pypixelcolor). Used after chunked GATT writes so the
panel can acknowledge each logical window.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass
class AckPolicy:
    ack_per_window: bool = True
    ack_final: bool = True


class IpixelAckManager:
    """Parses notify frames and signals per-window / final completion."""

    def __init__(self) -> None:
        self.window_event = asyncio.Event()
        self.all_event = asyncio.Event()

    def reset(self) -> None:
        self.window_event.clear()
        self.all_event.clear()

    def make_notify_handler(self):
        def handler(_sender: object, data: bytearray | bytes) -> None:
            if not data:
                return
            raw = bytes(data)
            # Protocol: 0x05 ... code in data[4]
            if len(raw) == 5 and raw[0] == 0x05:
                code = raw[4]
                if code in (0, 1):
                    self.window_event.set()
                elif code == 3:
                    self.window_event.set()
                    self.all_event.set()
                return
            b0 = raw[0]
            b4 = raw[4] if len(raw) > 4 else None
            if b0 == 0x05 and b4 is not None:
                if b4 in (0, 1):
                    self.window_event.set()
                elif b4 == 3:
                    self.window_event.set()
                    self.all_event.set()

        return handler
