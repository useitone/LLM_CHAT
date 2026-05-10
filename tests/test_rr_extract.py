from neurosync_pro.eeg.rr_extract import pick_rr_ms, rr_ms_to_bpm, try_extract_rr_ms_candidates


def test_rr_ms_to_bpm() -> None:
    assert round(rr_ms_to_bpm(1000.0), 2) == 60.0


def test_pick_rr_ms_median() -> None:
    assert pick_rr_ms([900, 890, 910]) == 900


def test_try_extract_rr_ms_candidates_prefers_plausible_range() -> None:
    # Build a buffer that contains little-endian 900ms (0x0384) near the end.
    # Keep it long enough to pass the conservative min_payload_len guard.
    raw = bytes([0x00, 0x01, 0x02, 0x10, 0x20, 0x30, 0x84, 0x03, 0x55])
    out = try_extract_rr_ms_candidates(raw)
    assert 900 in out

