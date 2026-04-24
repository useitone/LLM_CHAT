"""Tests for incremental EEG decode from BLE chunks."""

from __future__ import annotations

from neurosync_pro.eeg.live_decode import LiveEegDecoder


def _make_eeg_packet_tlv(attention: int = 11, meditation: int = 22) -> bytes:
    payload = bytearray(32)
    idx = 0
    payload[idx] = 0x02
    payload[idx + 1] = 0
    idx += 2
    payload[idx] = 0x83
    payload[idx + 1] = 0x18
    idx += 2
    powers = [0xABCDEF, 0x010203, 1, 2, 3, 4, 5, 6]
    for v in powers:
        payload[idx : idx + 3] = int(v).to_bytes(3, "big")
        idx += 3
    payload[idx] = 0x04
    payload[idx + 1] = attention & 0xFF
    idx += 2
    payload[idx] = 0x05
    payload[idx + 1] = meditation & 0xFF
    idx += 2
    checksum = (~(sum(payload) & 0xFF)) & 0xFF
    return bytes([0xAA, 0xAA, 0x20]) + bytes(payload) + bytes([checksum])


def test_live_decoder_single_packet() -> None:
    dec = LiveEegDecoder()
    pkt = _make_eeg_packet_tlv(7, 9)
    frames = dec.feed_chunk(pkt)
    assert len(frames) == 1
    assert frames[0].signal_quality == 0
    assert frames[0].attention == 7
    assert frames[0].meditation == 9


def test_live_decoder_split_across_chunks() -> None:
    dec = LiveEegDecoder()
    pkt = _make_eeg_packet_tlv(40, 50)
    mid = len(pkt) // 2
    assert dec.feed_chunk(pkt[:mid]) == []
    frames = dec.feed_chunk(pkt[mid:])
    assert len(frames) == 1
    assert frames[0].signal_quality == 0
    assert frames[0].attention == 40
    assert frames[0].meditation == 50
