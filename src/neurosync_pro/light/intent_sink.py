"""Subscribe to ``light.intent`` and optionally log or forward RGB to BLE (see LedMatrix.md).

Disabled unless ``NSP_LIGHT_SEND_ENABLED=1``. BLE safe-by-default: ``NSP_LIGHT_BLE_DRY_RUN``
defaults to true — set ``NSP_LIGHT_BLE_DRY_RUN=0`` only after UUID/prefix are validated.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Callable

from neurosync_pro.bus import EventBus

from neurosync_pro.light.ble_solid_worker import BleSolidRgbWorker, _truthy


class LightIntentSink:
    """Maps validated ``kind: rgb`` intents to log lines or a BLE worker."""

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._mode = (os.environ.get("NSP_LIGHT_SEND_MODE") or "log").strip().lower()
        self._worker: BleSolidRgbWorker | None = None
        self._unsub: Callable[[], None] | None = None
        self._last_rgb: tuple[int, int, int] | None = None

    def attach(self) -> Callable[[], None]:
        if self._mode == "ble":
            self._worker = BleSolidRgbWorker()
            self._worker.start()

        self._unsub = self._bus.subscribe("light.intent", self._on_intent)

        def detach() -> None:
            if self._unsub is not None:
                try:
                    self._unsub()
                except Exception:
                    pass
                self._unsub = None
            if self._worker is not None:
                self._worker.stop()
                self._worker = None

        return detach

    def _on_intent(self, payload: Any) -> None:
        if not isinstance(payload, dict):
            return
        if payload.get("kind") != "rgb":
            return
        rgb_raw = payload.get("rgb")
        if not isinstance(rgb_raw, (list, tuple)) or len(rgb_raw) != 3:
            return
        try:
            r, g, b = int(rgb_raw[0]), int(rgb_raw[1]), int(rgb_raw[2])
        except (TypeError, ValueError):
            return
        tup = (max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b)))

        if self._mode not in {"", "log", "debug", "ble"}:
            return
        if tup == self._last_rgb:
            return
        self._last_rgb = tup

        if self._mode in {"", "log", "debug"}:
            if _truthy(os.environ.get("NSP_LIGHT_SEND_DEBUG"), default=False):
                print(f"[light][send] rgb={tup}", file=sys.stderr)
            return

        if self._mode == "ble" and self._worker is not None:
            self._worker.enqueue(tup)


def try_attach_light_intent_sink(bus: EventBus) -> Callable[[], None]:
    """Attach sink if ``NSP_LIGHT_SEND_ENABLED`` is truthy; otherwise return no-op detach."""
    if not _truthy(os.environ.get("NSP_LIGHT_SEND_ENABLED"), default=False):
        return lambda: None
    sink = LightIntentSink(bus)
    return sink.attach()
