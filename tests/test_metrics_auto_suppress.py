from __future__ import annotations

import pytest

from neurosync_pro.light.metrics_auto_suppress import should_skip_metrics_auto_light_for_aux_sources


def _call(
    *,
    tone_on: bool = False,
    tone_mode: str = "mono",
    vol_src: str = "meditation",
    bin_on: bool = False,
    bin_pulse: bool = False,
    manual_hold: bool = False,
) -> bool:
    return should_skip_metrics_auto_light_for_aux_sources(
        eeg_tone_enabled=tone_on,
        eeg_tone_mode=tone_mode,
        eeg_tone_vol_src=vol_src,
        eeg_bin_enabled=bin_on,
        bin_matrix_pulse_checked=bin_pulse,
        light_manual_hold_checked=manual_hold,
    )


def test_skip_false_baseline() -> None:
    assert _call() is False


def test_skip_manual_hold() -> None:
    assert _call(manual_hold=True) is True


def test_skip_volume_light_meditation_mono() -> None:
    assert _call(tone_on=True, tone_mode="mono", vol_src="meditation_light") is True


def test_skip_volume_light_attention_mono() -> None:
    assert _call(tone_on=True, tone_mode="mono", vol_src="attention_light") is True


def test_no_skip_volume_light_stereo() -> None:
    assert _call(tone_on=True, tone_mode="stereo", vol_src="meditation_light") is False


def test_no_skip_tone_off() -> None:
    assert _call(tone_on=False, tone_mode="mono", vol_src="meditation_light") is False


def test_skip_bin_pulse_when_both_on() -> None:
    assert _call(bin_on=True, bin_pulse=True) is True


def test_no_skip_bin_without_pulse_checkbox() -> None:
    assert _call(bin_on=True, bin_pulse=False) is False


@pytest.mark.parametrize(
    "mode,src,expect",
    [
        ("Mono", "MEDITATION_LIGHT", True),
        ("MONO", "attention_light", True),
    ],
)
def test_case_insensitive_tone(mode: str, src: str, expect: bool) -> None:
    assert _call(tone_on=True, tone_mode=mode, vol_src=src) is expect
