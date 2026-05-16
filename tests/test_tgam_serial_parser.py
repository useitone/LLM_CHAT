"""Unit tests for ThinkGear-style serial parser (TGAM / research COM path)."""

from __future__ import annotations

from neurosync_pro.eeg.tgam_serial_parser import ThinkgearSerialParser


def _frame_bytes(poor: int, att: int, med: int) -> bytes:
    payload = [0x02, poor & 0xFF, 0x04, att & 0xFF, 0x05, med & 0xFF]
    ln = len(payload)
    chk = (~(sum(payload) & 0xFF)) & 0xFF
    return bytes([0xAA, 0xAA, ln, *payload, chk])


def test_tgam_parser_one_valid_frame() -> None:
    raw = _frame_bytes(0, 50, 60)
    p = ThinkgearSerialParser()
    got = None
    for b in raw:
        got = p.feed_byte(b)
    assert got is not None
    assert got.poor_signal == 0
    assert got.attention == 50
    assert got.meditation == 60


def test_tgam_parser_invalid_checksum_yields_none() -> None:
    raw = bytearray(_frame_bytes(0, 10, 20))
    raw[-1] ^= 0xFF
    p = ThinkgearSerialParser()
    got = None
    for b in raw:
        got = p.feed_byte(b)
    assert got is None


def test_tgam_parser_recover_after_bad_checksum() -> None:
    bad = bytearray(_frame_bytes(0, 1, 2))
    bad[-1] ^= 0x55
    good = _frame_bytes(0, 7, 8)
    p = ThinkgearSerialParser()
    frames: list = []
    for b in bad + good:
        f = p.feed_byte(b)
        if f is not None:
            frames.append(f)
    assert len(frames) >= 1
    assert frames[-1].attention == 7
    assert frames[-1].meditation == 8
