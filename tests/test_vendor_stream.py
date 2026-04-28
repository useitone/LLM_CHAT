"""Tests for vendor NUS interleaved frames (aabb0c HR heuristic)."""

from neurosync_pro.eeg.vendor_stream import (
    Aabb0cHeartRateParser,
    try_parse_aabb0c_hr_payload,
)


def test_parse_02_prefix_with_padding() -> None:
    # Observed pattern: 02 .. 02 .. 02 .. 00 00 00 BPM
    ten = bytes.fromhex("02a802bc02bc00000032")
    assert try_parse_aabb0c_hr_payload(ten) == 50


def test_parse_01_prefix_exercise() -> None:
    ten = bytes.fromhex("01e001f401d601ea00a4")
    # 0xA4=164 in 60–200
    assert try_parse_aabb0c_hr_payload(ten) == 164


def test_reject_no_match() -> None:
    ten = bytes.fromhex("02b202a8029e0000000a")
    assert try_parse_aabb0c_hr_payload(ten) is None


def test_incremental_parser_split_across_chunks() -> None:
    p = Aabb0cHeartRateParser()
    sig = bytes.fromhex("aabb0c")
    payload = bytes.fromhex("02a802bc02bc00000046")
    a = p.feed(b"prefix" + sig + payload[:4])
    assert a == []
    b = p.feed(payload[4:] + bytes.fromhex("2323"))
    assert b == [70]


def test_full_frame_in_one_chunk() -> None:
    p = Aabb0cHeartRateParser()
    frame = bytes.fromhex("aabb0c") + bytes.fromhex("02a802bc02bc00000032") + bytes.fromhex("2323")
    assert p.feed(frame) == [50]


def test_rejects_bpm_without_2323_suffix() -> None:
    p = Aabb0cHeartRateParser()
    # Same payload as valid HR but no ``23 23`` → do not count as HR.
    frame = bytes.fromhex("aabb0c") + bytes.fromhex("02a802bc02bc00000032") + b"\x00\x00"
    assert p.feed(frame) == []


def test_rejects_shorter_spoof_2323() -> None:
    # Suffix must be exactly 0x23 0x23 (not 0x23 0x24)
    p = Aabb0cHeartRateParser()
    frame = bytes.fromhex("aabb0c02a802bc02bc00000032") + b"\x23\x24"
    assert p.feed(frame) == []
