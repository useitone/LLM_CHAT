"""Heuristic extraction of RR/HRV values from opaque extend payloads.

BrainLink Pro advertises HRV (RR intervals) in its SDK, but the exact BLE layout
is not documented here. This module provides conservative extraction helpers
for debugging and incremental rollout.
"""

from __future__ import annotations

from typing import Iterable


def rr_ms_to_bpm(rr_ms: float) -> float:
    return 60000.0 / float(rr_ms)


def _scan_u16(buf: bytes, *, little: bool) -> list[int]:
    out: list[int] = []
    n = len(buf)
    for i in range(0, n - 1):
        b0, b1 = buf[i], buf[i + 1]
        v = (b0 | (b1 << 8)) if little else ((b0 << 8) | b1)
        out.append(int(v))
    return out


def try_extract_rr_ms_candidates(
    extend_raw: bytes,
    *,
    rr_min_ms: int = 300,
    rr_max_ms: int = 2000,
    min_payload_len: int = 8,
    max_values: int = 6,
) -> list[int]:
    """
    Best-effort RR extraction from extend_raw.

    We scan all overlapping u16 values (little+big) and keep those in plausible
    RR interval range. Returns up to max_values last-seen candidates.
    """
    if not extend_raw:
        return []
    # Drop trailing 0x55 if present.
    buf = extend_raw[:-1] if extend_raw[-1] == 0x55 else extend_raw
    # Very short payloads tend to produce false positives (random u16 values within range).
    if len(buf) < max(2, int(min_payload_len)):
        return []
    cand_le = [v for v in _scan_u16(buf, little=True) if rr_min_ms <= v <= rr_max_ms]
    cand_be = [v for v in _scan_u16(buf, little=False) if rr_min_ms <= v <= rr_max_ms]
    # Prefer the endianness that yields a denser set.
    c = cand_le if len(cand_le) >= len(cand_be) else cand_be
    if not c:
        return []
    # Keep tail values (often arrays are appended towards the end).
    return [int(v) for v in c[-max_values:]]


def pick_rr_ms(rrs: Iterable[int]) -> int | None:
    xs = [int(x) for x in rrs if int(x) > 0]
    if not xs:
        return None
    xs.sort()
    return int(xs[len(xs) // 2])

