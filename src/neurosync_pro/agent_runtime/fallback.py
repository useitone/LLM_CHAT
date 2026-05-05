from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .contracts import Decision


@dataclass
class FallbackState:
    last_spec: str = ""
    hold_streak: int = 0


def _get_mean(obs: dict[str, Any], path: list[str]) -> float | None:
    cur: Any = obs
    for k in path:
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    if isinstance(cur, dict) and "mean" in cur:
        try:
            return float(cur["mean"])
        except (TypeError, ValueError):
            return None
    return None


def _spec_from_obs(obs: dict[str, Any]) -> str:
    att = _get_mean(obs, ["eeg", "attention"])
    med = _get_mean(obs, ["eeg", "meditation"])
    hr = _get_mean(obs, ["hr", "bpm"])
    carrier = 200.0
    beat = 10.0
    noise_color = "pink"
    noise_vol = 0.06
    tone_amp = 0.55

    if att is not None and att < 35:
        beat = 15.0
        noise_color = "brown"
        noise_vol = 0.10
        tone_amp = 0.60
    elif med is not None and med > 65:
        beat = 6.0
        noise_color = "pink"
        noise_vol = 0.04
        tone_amp = 0.50
    elif hr is not None and hr > 95:
        beat = 8.0
        noise_color = "brown"
        noise_vol = 0.08
        tone_amp = 0.50

    return f"{carrier:.0f}+{beat:.0f}/{tone_amp:.2f} {noise_color}/{noise_vol:.2f}"


def decide_fallback(
    obs: dict[str, Any],
    state: FallbackState,
    *,
    min_confidence: float = 0.5,
) -> Decision:
    """
    Heuristic fallback with tiny hysteresis:
    - hold when spec did not change
    - escalate confidence when same decision repeats.
    """
    spec = _spec_from_obs(obs)
    if spec == state.last_spec:
        state.hold_streak += 1
        return Decision(
            action="hold",
            spec=None,
            confidence=min(1.0, 0.3 + state.hold_streak * 0.1),
            reason_code="fallback_no_change",
            source="heuristic",
            timeline=None,
        )

    state.last_spec = spec
    state.hold_streak = 0
    return Decision(
        action="set_spec",
        spec=spec,
        confidence=min_confidence,
        reason_code="fallback_spec",
        source="heuristic",
        timeline=None,
    )
