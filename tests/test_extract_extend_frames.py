from neurosync_pro.eeg.protocol import extract_extend_frames


def test_extract_extend_frames_finds_heart_rate() -> None:
    # Build minimal 15-byte frame expected by decode_extend:
    # AA AA BB 0C 02 + 10 bytes data (heart_rate at data[8]).
    hdr = bytes.fromhex("aaaa")
    typ = bytes([0xBB, 0x0C, 0x02])
    data = bytearray(10)
    data[0] = 0x06  # ap marker (matches state machine AP_CHECK_BYTE)
    data[8] = 72  # heart_rate
    frame = hdr + typ + bytes(data)
    buf = b"xx" + frame + b"yy"
    out = extract_extend_frames(buf)
    assert len(out) == 1
    assert out[0].heart_rate == 72

