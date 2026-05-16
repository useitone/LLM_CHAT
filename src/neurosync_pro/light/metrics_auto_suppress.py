"""When EEG→Tone (Volume+Light), EEG→Binaural matrix pulse, or manual RGB hold is active,
metrics bridge must not publish auto ``light.intent`` on the same ``eeg.metrics`` frame.

The UI wraps ``eeg.metrics`` publish with ``NSP_LIGHT_SKIP_AUTO_LIGHT=1``; this module holds
the same boolean policy in one place for tests."""

from __future__ import annotations


def should_skip_metrics_auto_light_for_aux_sources(
    *,
    eeg_tone_enabled: bool,
    eeg_tone_mode: str,
    eeg_tone_vol_src: str,
    eeg_bin_enabled: bool,
    bin_matrix_pulse_checked: bool,
    light_manual_hold_checked: bool,
) -> bool:
    if light_manual_hold_checked:
        return True
    if (
        eeg_tone_enabled
        and str(eeg_tone_mode).strip().lower() == "mono"
        and str(eeg_tone_vol_src).strip().lower() in ("meditation_light", "attention_light")
    ):
        return True
    if eeg_bin_enabled and bin_matrix_pulse_checked:
        return True
    return False
