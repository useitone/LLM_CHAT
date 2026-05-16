"""Subscribe to ``light.intent`` and optionally log or forward RGB to BLE (see LedMatrix.md).

Disabled unless ``NSP_LIGHT_SEND_ENABLED=1``. BLE safe-by-default: ``NSP_LIGHT_BLE_DRY_RUN``
defaults to true — set ``NSP_LIGHT_BLE_DRY_RUN=0`` only after UUID/prefix are validated.

For iPIXEL matrices use ``NSP_LIGHT_BLE_PROTOCOL=ipixel_png`` (solid PNG transfer); optional
``NSP_LIGHT_BLE_MATRIX_W`` / ``NSP_LIGHT_BLE_MATRIX_H`` (defaults 96×16).
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from typing import Any, Callable

from neurosync_pro.bus import EventBus

from neurosync_pro.light.ble_solid_worker import BleSolidRgbWorker, _truthy

_intent_log_lock = threading.Lock()


def _ipixel_brightness_clamped() -> int:
    """Same 1…100 clamp as :func:`BleSolidRgbWorker._send_ipixel_init` (via ``_int_env``)."""

    raw = os.environ.get("NSP_LIGHT_BLE_IPX_BRIGHTNESS")
    if raw is None or str(raw).strip() == "":
        return 80
    try:
        v = int(str(raw).strip(), 0)
    except ValueError:
        return 80
    return max(1, min(100, v))


def _append_intent_log_line(payload: dict[str, Any], rgb: tuple[int, int, int]) -> None:
    path = (os.environ.get("NSP_LIGHT_INTENT_LOG") or "").strip()
    if not path:
        return
    rec = {
        "t_unix": time.time(),
        "rgb": [int(rgb[0]), int(rgb[1]), int(rgb[2])],
        "source": payload.get("source"),
        "attention": payload.get("attention"),
        "meditation": payload.get("meditation"),
    }
    line = json.dumps(rec, ensure_ascii=False) + "\n"
    try:
        with _intent_log_lock:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
    except OSError:
        pass


class LightIntentSink:
    """Maps validated ``kind: rgb`` intents to log lines or a BLE worker."""

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._mode = (os.environ.get("NSP_LIGHT_SEND_MODE") or "log").strip().lower()
        self._worker: BleSolidRgbWorker | None = None
        self._unsub: Callable[[], None] | None = None
        self._unsub_env: Callable[[], None] | None = None
        self._last_rgb: tuple[int, int, int] | None = None
        self._last_ipx_brightness: int | None = None

    def attach(self) -> Callable[[], None]:
        if self._mode == "ble":
            self._worker = BleSolidRgbWorker()
            self._worker.start()

        self._unsub = self._bus.subscribe("light.intent", self._on_intent)
        self._unsub_env = self._bus.subscribe("light.env_updated", self._on_light_env_updated)

        def detach() -> None:
            if self._unsub is not None:
                try:
                    self._unsub()
                except Exception:
                    pass
                self._unsub = None
            if self._unsub_env is not None:
                try:
                    self._unsub_env()
                except Exception:
                    pass
                self._unsub_env = None
            if self._worker is not None:
                self._worker.stop()
                self._worker = None

        return detach

    def _on_light_env_updated(self, _payload: Any = None) -> None:
        """После смены `NSP_LIGHT_BLE_IPX_BRIGHTNESS` в окружении — повторить последний RGB в BLE."""

        b = _ipixel_brightness_clamped()
        self._last_ipx_brightness = b
        if self._mode != "ble" or self._worker is None:
            return
        if self._last_rgb is None:
            return
        self._worker.enqueue(self._last_rgb)

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
        blevel = _ipixel_brightness_clamped()
        if tup == self._last_rgb and blevel == self._last_ipx_brightness:
            return
        self._last_rgb = tup
        self._last_ipx_brightness = blevel
        _append_intent_log_line(payload, tup)

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
