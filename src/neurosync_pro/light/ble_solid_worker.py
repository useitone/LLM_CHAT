"""Background BLE writer: solid RGB payload built from env (vendor framing).

Modes (``NSP_LIGHT_BLE_PROTOCOL``):

- ``raw`` — ``NSP_LIGHT_BLE_RGB_PREFIX_HEX`` + 3 RGB bytes (legacy / unknown devices).
- ``ipixel_png`` — solid PNG sized to matrix WxH, framed like ``pypixelcolor`` ``send_image``
  (see ``ipixel_windows.py``). Default write UUID ``0000fa02-...`` if unset.

Safe default: ``NSP_LIGHT_BLE_DRY_RUN=1`` logs instead of writing.

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

from neurosync_pro.light.ipixel_constants import IPixel_WRITE_UUID
from neurosync_pro.light.ipixel_png import solid_rgb_png
from neurosync_pro.light.ipixel_windows import build_png_transfer_windows


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


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return int(str(raw).strip(), 0)
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

    async def _send_ipixel_init(self, client: Any, wuuid: str) -> None:
        if not _truthy(os.environ.get("NSP_LIGHT_BLE_IPX_INIT"), default=True):
            return
        blevel = _int_env("NSP_LIGHT_BLE_IPX_BRIGHTNESS", 80)
        blevel = max(1, min(100, blevel))
        power = bytes([5, 0, 7, 1, 1])
        bright = bytes([5, 0, 4, 0x80, blevel])
        for pkt in (power, bright):
            await client.write_gatt_char(wuuid, pkt, response=False)
            await asyncio.sleep(0.05)

    async def _write_chunks(
        self,
        client: Any,
        wuuid: str,
        payload: bytes,
        *,
        chunk_size: int,
    ) -> None:
        step = max(8, min(244, chunk_size))
        for i in range(0, len(payload), step):
            await client.write_gatt_char(wuuid, payload[i : i + step], response=False)

    async def _async_main(self) -> None:
        try:
            from bleak import BleakClient
        except ImportError:
            print("[light][ble] bleak is not installed", file=sys.stderr)
            return

        dry = _truthy(os.environ.get("NSP_LIGHT_BLE_DRY_RUN"), default=True)
        addr = (os.environ.get("NSP_LIGHT_BLE_ADDRESS") or "").strip()
        proto = (os.environ.get("NSP_LIGHT_BLE_PROTOCOL") or "raw").strip().lower()
        wuuid = (os.environ.get("NSP_LIGHT_BLE_WRITE_UUID") or "").strip()
        if proto == "ipixel_png" and not wuuid:
            wuuid = IPixel_WRITE_UUID
        prefix_hex = (os.environ.get("NSP_LIGHT_BLE_RGB_PREFIX_HEX") or "").strip().replace(" ", "")
        connect_timeout = max(2.0, _float_env("NSP_LIGHT_BLE_CONNECT_TIMEOUT", 8.0))
        chunk_size = _int_env("NSP_LIGHT_BLE_WRITE_CHUNK", 244)
        save_slot = _int_env("NSP_LIGHT_BLE_IPX_SAVE_SLOT", 0)

        prefix = bytes.fromhex(prefix_hex) if prefix_hex else b""

        if not dry and not addr:
            print(
                "[light][ble] set NSP_LIGHT_BLE_ADDRESS "
                "(or NSP_LIGHT_BLE_DRY_RUN=1 for logging only)",
                file=sys.stderr,
            )
            return

        if not dry and not wuuid:
            print("[light][ble] missing write UUID (set NSP_LIGHT_BLE_WRITE_UUID)", file=sys.stderr)
            return

        client: Any = None
        next_ok = 0.0
        sent_ipixel_init = False

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

            if proto == "ipixel_png":
                mw = _int_env("NSP_LIGHT_BLE_MATRIX_W", 96)
                mh = _int_env("NSP_LIGHT_BLE_MATRIX_H", 16)
                png = solid_rgb_png(mw, mh, r0, g0, b0)
                windows = build_png_transfer_windows(png, save_slot=save_slot)
                if dry:
                    total = sum(len(w) for w in windows)
                    head = windows[0][:48].hex() if windows else ""
                    print(
                        f"[light][ble][dry_run][ipixel_png] rgb={rgb} "
                        f"windows={len(windows)} total_bytes={total} first48={head}",
                        file=sys.stderr,
                    )
                    continue

                try:
                    if client is None or not client.is_connected:
                        client = BleakClient(addr, timeout=connect_timeout)
                        await client.connect()
                        sent_ipixel_init = False
                    if not sent_ipixel_init:
                        await self._send_ipixel_init(client, wuuid)
                        sent_ipixel_init = True
                    for win in windows:
                        await self._write_chunks(client, wuuid, win, chunk_size=chunk_size)
                        await asyncio.sleep(0.02)
                except Exception as exc:
                    print(f"[light][ble] ipixel_png failed: {exc}", file=sys.stderr)
                    if client is not None:
                        try:
                            await client.disconnect()
                        except Exception:
                            pass
                        client = None
                        sent_ipixel_init = False
                continue

            # --- raw protocol ---
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
