"""Lightweight HR smoothing for experimental vendor frames.

Goal: make UI stable even when vendor frames are noisy.
Strategy:
- Reject implausible BPM range.
- Median over a short rolling window.
- Optional step limiter: large jumps require confirmation across multiple samples.
"""

from __future__ import annotations

from collections import deque


class HrMedianSmoother:
    def __init__(
        self,
        *,
        window: int = 5,
        bpm_min: int = 35,
        bpm_max: int = 220,
        max_delta_per_s: float = 12.0,
        jump_confirm: int = 2,
    ) -> None:
        self._win = max(1, int(window))
        self._bpm_min = int(bpm_min)
        self._bpm_max = int(bpm_max)
        self._max_delta_per_s = float(max_delta_per_s)
        self._jump_confirm = max(1, int(jump_confirm))
        self._buf: deque[int] = deque(maxlen=self._win)
        self._last_out: int | None = None
        self._last_out_t: float | None = None
        self._pending_jump_to: int | None = None
        self._pending_jump_n: int = 0

    def reset(self) -> None:
        self._buf.clear()
        self._last_out = None
        self._last_out_t = None
        self._pending_jump_to = None
        self._pending_jump_n = 0

    def feed(self, bpm: int, *, t: float | None = None) -> int | None:
        try:
            v = int(bpm)
        except (TypeError, ValueError):
            return None
        if v < self._bpm_min or v > self._bpm_max:
            return None
        self._buf.append(v)
        cand = self.current()
        if cand is None:
            return None
        return self._apply_step_limit(cand, t=t)

    def current(self) -> int | None:
        if not self._buf:
            return None
        xs = sorted(self._buf)
        return int(xs[len(xs) // 2])

    def _apply_step_limit(self, cand: int, *, t: float | None) -> int:
        """Allow small movement; require repeats for big jumps."""
        if self._last_out is None:
            self._last_out = int(cand)
            self._last_out_t = float(t) if t is not None else None
            return int(cand)

        last = int(self._last_out)
        if self._max_delta_per_s <= 0:
            self._last_out = int(cand)
            self._last_out_t = float(t) if t is not None else self._last_out_t
            return int(cand)

        dt = None
        if t is not None and self._last_out_t is not None:
            dt = max(0.01, float(t) - float(self._last_out_t))
        # Without dt, assume 1s tick (conservative).
        allow = float(self._max_delta_per_s) * (dt if dt is not None else 1.0)
        if abs(int(cand) - last) <= allow:
            self._pending_jump_to = None
            self._pending_jump_n = 0
            self._last_out = int(cand)
            if t is not None:
                self._last_out_t = float(t)
            return int(cand)

        # Big jump: require confirmation.
        if self._pending_jump_to == int(cand):
            self._pending_jump_n += 1
        else:
            self._pending_jump_to = int(cand)
            self._pending_jump_n = 1
        if self._pending_jump_n >= self._jump_confirm:
            self._last_out = int(cand)
            if t is not None:
                self._last_out_t = float(t)
            self._pending_jump_to = None
            self._pending_jump_n = 0
            return int(cand)

        # Hold previous stable output until confirmed.
        if t is not None:
            self._last_out_t = float(t)
        return last

