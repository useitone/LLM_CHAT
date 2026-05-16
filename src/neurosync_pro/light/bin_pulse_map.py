"""Map EEG→Binaural smoothed carrier (Hz) to LED matrix pulse rate (Hz).

Only **technical** limits (BLE min interval between RGB frames). No perceptual caps.
"""

from __future__ import annotations


def pulse_hz_from_carrier(
    carrier_hz: float,
    carrier_lo: float,
    carrier_hi: float,
    pulse_lo: float,
    pulse_hi: float,
) -> float:
    """Linear map ``carrier`` from ``[carrier_lo, carrier_hi]`` to ``[pulse_lo, pulse_hi]`` (Hz full blink cycles)."""

    lo = min(float(carrier_lo), float(carrier_hi))
    hi = max(float(carrier_lo), float(carrier_hi))
    if hi <= lo:
        hi = lo + 1e-6
    t = (float(carrier_hz) - lo) / (hi - lo)
    t = 0.0 if t < 0.0 else 1.0 if t > 1.0 else float(t)
    p0 = min(float(pulse_lo), float(pulse_hi))
    p1 = max(float(pulse_lo), float(pulse_hi))
    return p0 + (p1 - p0) * t


def half_period_ms_from_pulse_hz(pulse_hz: float, *, ble_min_interval_s: float) -> float:
    """One matrix half-step (ms): limited so two steps fit ``NSP_LIGHT_BLE_MIN_INTERVAL_S`` each."""

    iv = max(0.01, min(1.0, float(ble_min_interval_s)))
    f = max(1e-6, float(pulse_hz))
    f_cap = 0.95 / (2.0 * iv)
    if f > f_cap:
        f = f_cap
    return max(20.0, min(20000.0, 1000.0 / (2.0 * f)))
