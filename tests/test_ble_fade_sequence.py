"""Tests for BLE worker RGB fade helper."""

from neurosync_pro.light.ble_solid_worker import _fade_rgb_sequence


def test_fade_disabled_when_ms_zero() -> None:
    assert _fade_rgb_sequence((0, 0, 0), (255, 0, 0), 0.0) == [(255, 0, 0)]


def test_fade_single_step_when_no_source() -> None:
    assert _fade_rgb_sequence(None, (10, 20, 30), 200.0) == [(10, 20, 30)]


def test_fade_multi_steps_endpoints() -> None:
    seq = _fade_rgb_sequence((0, 0, 0), (100, 0, 0), 500.0)
    assert len(seq) >= 2
    assert seq[-1] == (100, 0, 0)


def test_fade_max_steps_caps_sequence() -> None:
    long = _fade_rgb_sequence((0, 0, 0), (255, 255, 255), 2000.0, max_steps=4)
    assert len(long) <= 4
    assert long[-1] == (255, 255, 255)


def test_fade_same_color_one_step() -> None:
    assert _fade_rgb_sequence((50, 50, 50), (50, 50, 50), 300.0) == [(50, 50, 50)]
