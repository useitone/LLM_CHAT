"""Subscribe to EventBus `eeg.metrics` and derive optional light intents.

Default: **disabled** (no subscription). Enable with ``NSP_LIGHT_ENABLED=1``.

Modes (``NSP_LIGHT_MODE``):
- ``log`` — optional stderr lines when ``NSP_LIGHT_DEBUG=1`` (no BLE).
- ``auto`` — simple threshold RGB mapping; publishes ``light.intent`` on the bus for a future sender.

BLE/hardware is intentionally **not** implemented here — see LedMatrix.md roadmap.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any, Callable

from neurosync_pro.bus import EventBus

from neurosync_pro.light.auto_rules import get_auto_rules_from_env, rgb_from_auto_rules


def _truthy(raw: str | None, *, default: bool = False) -> bool:
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return float(str(raw).strip())
    except ValueError:
        return default


class MetricsLightBridge:
    """Thin bridge: EEG metrics → throttled ``light.intent`` (auto) or debug log."""

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._mode = (os.environ.get("NSP_LIGHT_MODE") or "log").strip().lower()
        self._min_interval_s = max(
            0.05,
            min(2.0, _float_env("NSP_LIGHT_METRICS_MIN_INTERVAL_MS", 200.0) / 1000.0),
        )
        self._last_emit_mono: float = 0.0
        self._med_thr = _float_env("NSP_LIGHT_AUTO_MED_THRESHOLD", 70.0)
        self._att_thr = _float_env("NSP_LIGHT_AUTO_ATT_THRESHOLD", 70.0)
        self._unsub: Callable[[], None] | None = None

    def attach(self) -> Callable[[], None]:
        self._unsub = self._bus.subscribe("eeg.metrics", self._on_metrics)

        def detach() -> None:
            if self._unsub is not None:
                try:
                    self._unsub()
                except Exception:
                    pass
                self._unsub = None

        return detach

    def _on_metrics(self, payload: Any) -> None:
        if not isinstance(payload, dict):
            return
        now = time.monotonic()
        if now - self._last_emit_mono < self._min_interval_s:
            return
        self._last_emit_mono = now

        try:
            att = float(payload.get("attention", 0))
            med = float(payload.get("meditation", 0))
        except (TypeError, ValueError):
            return

        if self._mode in {"", "log", "debug"}:
            if _truthy(os.environ.get("NSP_LIGHT_DEBUG"), default=False):
                print(f"[light] attention={att:.0f} meditation={med:.0f}", file=sys.stderr)
            return

        if self._mode == "auto":
            if _truthy(os.environ.get("NSP_LIGHT_SKIP_AUTO_LIGHT"), default=False):
                return
            rgb = self._auto_rgb(att, med)
            try:
                self._bus.publish(
                    "light.intent",
                    {
                        "kind": "rgb",
                        "rgb": [int(rgb[0]), int(rgb[1]), int(rgb[2])],
                        "attention": att,
                        "meditation": med,
                        "t_monotonic_s": now,
                    },
                )
            except Exception:
                pass
            return

        # Unknown mode — ignore quietly.

    def _auto_rgb(self, att: float, med: float) -> tuple[int, int, int]:
        loaded = get_auto_rules_from_env()
        if loaded is not None:
            rules, idle = loaded
            return rgb_from_auto_rules(att, med, rules, idle)
        # Meditation-first tie-break (same spirit as LedMatrix examples).
        calm_blue = (100, 150, 255)
        focus_warm = (255, 255, 200)
        idle = (24, 28, 36)
        if med >= self._med_thr:
            return calm_blue
        if att >= self._att_thr:
            return focus_warm
        return idle


def auto_rgb_from_metrics(attention: float, meditation: float) -> tuple[int, int, int]:
    """Same RGB as авто-режим: JSON-правила (``NSP_LIGHT_AUTO_RULES_PATH``) или пороги ``NSP_LIGHT_AUTO_*``."""

    loaded = get_auto_rules_from_env()
    if loaded is not None:
        rules, idle = loaded
        try:
            att = float(attention)
            med = float(meditation)
        except (TypeError, ValueError):
            att, med = 0.0, 0.0
        return rgb_from_auto_rules(att, med, rules, idle)

    med_thr = _float_env("NSP_LIGHT_AUTO_MED_THRESHOLD", 70.0)
    att_thr = _float_env("NSP_LIGHT_AUTO_ATT_THRESHOLD", 70.0)
    try:
        att = float(attention)
        med = float(meditation)
    except (TypeError, ValueError):
        att, med = 0.0, 0.0
    calm_blue = (100, 150, 255)
    focus_warm = (255, 255, 200)
    idle = (24, 28, 36)
    if med >= med_thr:
        return calm_blue
    if att >= att_thr:
        return focus_warm
    return idle


def try_attach_metrics_light_bridge(bus: EventBus) -> Callable[[], None]:
    """Attach bridge if ``NSP_LIGHT_ENABLED`` is truthy; otherwise return no-op detach."""
    if not _truthy(os.environ.get("NSP_LIGHT_ENABLED"), default=False):
        return lambda: None
    bridge = MetricsLightBridge(bus)
    return bridge.attach()
