"""Background BLE writer: solid RGB payload built from env (vendor framing).

iPIXEL / pypixelcolor byte layout is device-specific — prefix hex + ``RGB`` is configurable.
Safe default: ``NSP_LIGHT_BLE_DRY_RUN=1`` logs hex instead of writing.

Requires ``bleak`` (already a project dependency).
"""

from __future__ import annotations

import asyncio
import os
import queue
import sys
import threading
import time
from typing import Any


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


class BleSolidRgbWorker:
    """One consumer thread with asyncio + optional persistent ``BleakClient``."""

    def __init__(self) -> None:
        self._q: queue.Queue[tuple[int, int, int]] = queue.Queue(maxsize=8)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._min_interval_s = max(0.02, min(0.5, _float_env("NSP_LIGHT_BLE_MIN_INTERVAL_S", 0.05)))

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._thread_main, name="BleSolidRgbWorker", daemon=True)
        self._thread.start()

    def stop(self, *, join_timeout_s: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=join_timeout_s)
            self._thread = None

    def enqueue(self, rgb: tuple[int, int, int]) -> None:
        try:
            self._q.put_nowait(rgb)
        except queue.Full:
            try:
                _ = self._q.get_nowait()
            except queue.Empty:
                pass
            try:
                self._q.put_nowait(rgb)
            except queue.Full:
                pass

    def _thread_main(self) -> None:
        asyncio.run(self._async_main())

    async def _async_main(self) -> None:
        try:
            from bleak import BleakClient
        except ImportError:
            print("[light][ble] bleak is not installed", file=sys.stderr)
            return

        dry = _truthy(os.environ.get("NSP_LIGHT_BLE_DRY_RUN"), default=True)
        addr = (os.environ.get("NSP_LIGHT_BLE_ADDRESS") or "").strip()
        wuuid = (os.environ.get("NSP_LIGHT_BLE_WRITE_UUID") or "").strip()
        prefix_hex = (os.environ.get("NSP_LIGHT_BLE_RGB_PREFIX_HEX") or "").strip().replace(" ", "")
        connect_timeout = max(2.0, _float_env("NSP_LIGHT_BLE_CONNECT_TIMEOUT", 8.0))

        prefix = bytes.fromhex(prefix_hex) if prefix_hex else b""

        if not dry and (not addr or not wuuid):
            print(
                "[light][ble] set NSP_LIGHT_BLE_ADDRESS and NSP_LIGHT_BLE_WRITE_UUID "
                "(or NSP_LIGHT_BLE_DRY_RUN=1 for logging only)",
                file=sys.stderr,
            )
            return

        client: Any = None
        next_ok = 0.0

        def _get_rgb() -> tuple[int, int, int] | None:
            try:
                return self._q.get(timeout=0.35)
            except queue.Empty:
                return None

        while not self._stop.is_set():
            rgb = await asyncio.to_thread(_get_rgb)
            if rgb is None:
                continue

            now = time.monotonic()
            if now < next_ok:
                await asyncio.sleep(next_ok - now)
            next_ok = time.monotonic() + self._min_interval_s

            r, g, b = rgb
            r0, g0, b0 = max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b))
            payload = prefix + bytes((r0, g0, b0))

            if dry:
                print(f"[light][ble][dry_run] rgb={rgb} payload={payload.hex()}", file=sys.stderr)
                continue

            try:
                if client is None or not client.is_connected:
                    client = BleakClient(addr, timeout=connect_timeout)
                    await client.connect()
                await client.write_gatt_char(wuuid, payload, response=False)
            except Exception as exc:
                print(f"[light][ble] write/connect failed: {exc}", file=sys.stderr)
                if client is not None:
                    try:
                        await client.disconnect()
                    except Exception:
                        pass
                    client = None

        if client is not None and not dry:
            try:
                if client.is_connected:
                    await client.disconnect()
            except Exception:
                pass
