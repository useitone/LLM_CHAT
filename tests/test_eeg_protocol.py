"""Tests for BrainLink frame decode (neurosync_pro.eeg.protocol)."""

from __future__ import annotations

from pathlib import Path

import pytest

from neurosync_pro.eeg.protocol import (
    decode_gyro,
    extract_all_eeg_frames,
    scan_payload,
)


def _make_eeg_packet_tlv(
    attention: int = 11,
    meditation: int = 22,
    delta: int = 0xABCDEF,
    theta: int = 0x010203,
) -> bytes:
    # Build 32-byte ThinkGear-like TLV payload used by pybrainlink state machine.
    # Layout: 0x02 <signal> 0x83 0x18 <8*3-byte powers> 0x04 <att> 0x05 <med>
    payload = bytearray(32)
    idx = 0
    payload[idx] = 0x02
    payload[idx + 1] = 0  # signal
    idx += 2
    payload[idx] = 0x83
    payload[idx + 1] = 0x18
    idx += 2
    # 8 powers * 3 bytes each
    powers = [
        delta,
        theta,
        0x040506,  # low_alpha
        0x070809,  # high_alpha
        0x0A0B0C,  # low_beta
        0x0D0E0F,  # high_beta
        0x101112,  # low_gamma
        0x131415,  # high_gamma
    ]
    for v in powers:
        payload[idx : idx + 3] = int(v).to_bytes(3, "big")
        idx += 3
    payload[idx] = 0x04
    payload[idx + 1] = attention & 0xFF
    idx += 2
    payload[idx] = 0x05
    payload[idx + 1] = meditation & 0xFF
    idx += 2
    # checksum: (~sum(payload)) & 0xFF
    checksum = (~(sum(payload) & 0xFF)) & 0xFF
    return bytes([0xAA, 0xAA, 0x20]) + bytes(payload) + bytes([checksum])


def test_decode_gyro_14_wire() -> None:
    # x=1, y=-2, z=3, extra=4, footer 23 23
    body = (
        bytes([0xAA, 0xAA, 0x07, 0x03])
        + (1).to_bytes(2, "big", signed=True)
        + (-2).to_bytes(2, "big", signed=True)
        + (3).to_bytes(2, "big", signed=True)
        + (4).to_bytes(2, "big", signed=True)
        + bytes([0x23, 0x23])
    )
    assert len(body) == 14
    g = decode_gyro(body)
    assert g is not None
    assert g.x == 1 and g.y == -2 and g.z == 3
    assert g.extra_int16 == 4
    assert g.wire_len == 14


def test_scan_payload_finds_eeg() -> None:
    short = bytes.fromhex("aaaa04800200710c2323")
    eeg = _make_eeg_packet_tlv(5, 6, 99, 123)
    buf = eeg + short
    stats: dict = {
        "eeg_count": 0,
        "short_count": 0,
        "gyro_count": 0,
        "extend_count": 0,
        "skipped_at_aa": 0,
        "eeg_samples": [],
        "short_samples": [],
        "gyro_samples": [],
        "extend_samples": [],
        "max_samples": 10,
    }
    scan_payload(buf, stats, packet_timestamp_utc="2026-01-01T00:00:00+00:00")
    assert stats["eeg_count"] == 1
    assert stats["short_count"] == 1
    assert stats["eeg_samples"][0]["attention"] == 5
    assert stats["eeg_samples"][0]["source_timestamp_utc"] == "2026-01-01T00:00:00+00:00"


@pytest.mark.skipif(
    not Path("docs/specs/brainlink-raw-capture.jsonl").is_file(),
    reason="fixture capture not present",
)
def test_extract_all_eeg_frames_smoke() -> None:
    rows = extract_all_eeg_frames(Path("docs/specs/brainlink-raw-capture.jsonl"))
    assert isinstance(rows, list)
    if rows:
        assert "attention" in rows[0]
        assert "raw_hex" in rows[0]
