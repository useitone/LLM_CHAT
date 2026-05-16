from __future__ import annotations

import pytest

from neurosync_pro.light.bin_pulse_map import half_period_ms_from_pulse_hz, pulse_hz_from_carrier


def test_pulse_hz_from_carrier_endpoints() -> None:
    assert pulse_hz_from_carrier(200.0, 200.0, 500.0, 0.5, 8.0) == pytest.approx(0.5)
    assert pulse_hz_from_carrier(500.0, 200.0, 500.0, 0.5, 8.0) == pytest.approx(8.0)
    assert pulse_hz_from_carrier(350.0, 200.0, 500.0, 0.5, 8.0) == pytest.approx(4.25)


def test_half_period_ms_caps_by_ble_interval() -> None:
    ms = half_period_ms_from_pulse_hz(100.0, ble_min_interval_s=0.05)
    assert ms >= 20.0
    assert ms <= 20000.0
