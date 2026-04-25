"""Vendor-specific bytes interleaved with BrainLink EEG (e.g. soft headband HR frames)."""

from __future__ import annotations

# Packets observed in NUS notify hex dumps: signature AA BB 0C + 10 payload bytes.
AABB0C: bytes = bytes((0xAA, 0xBB, 0x0C))


def try_parse_aabb0c_hr_payload(ten: bytes) -> int | None:
    """If *ten* is 10 bytes after ``AA BB 0C``, return a plausible BPM or None.

    Heuristic (experimental, from captured logs — not official spec):
    - Type ``02 .. .. .. .. .. 00 00 00 XX``: treat *XX* as BPM when 40–200.
    - Type ``01 .. .. .. .. .. .. .. .. XX``: treat *XX* as BPM when 50–220 (e.g. exercise).
    """
    if len(ten) != 10:
        return None
    a0 = ten[0]
    last = int(ten[9])
    if a0 == 0x02 and ten[6:9] == b"\x00\x00\x00" and 40 <= last <= 200:
        return last
    if a0 == 0x01 and 50 <= last <= 220:
        return last
    return None


class Aabb0cHeartRateParser:
    """Incremental scan of notify chunks for ``AA BB 0C`` + 10-byte HR payloads."""

    def __init__(self, *, max_buffer: int = 96) -> None:
        self._buf = bytearray()
        self._max_buffer = max(32, int(max_buffer))

    def feed(self, data: bytes | bytearray) -> list[int]:
        self._buf.extend(data)
        out: list[int] = []
        scan = 0
        while True:
            j = self._buf.find(AABB0C, scan)
            if j < 0:
                if len(self._buf) > self._max_buffer:
                    self._buf = self._buf[-self._max_buffer :]
                return out
            if j + 3 + 10 > len(self._buf):
                # Need more bytes; keep from signature onward.
                if j > 0:
                    del self._buf[:j]
                if len(self._buf) > self._max_buffer:
                    self._buf = self._buf[-self._max_buffer :]
                return out
            ten = bytes(self._buf[j + 3 : j + 13])
            bpm = try_parse_aabb0c_hr_payload(ten)
            if bpm is not None:
                out.append(bpm)
            scan = j + 3
