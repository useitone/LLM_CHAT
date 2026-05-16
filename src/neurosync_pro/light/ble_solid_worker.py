"""Background BLE writer: solid RGB payload built from env (vendor framing).

Modes (``NSP_LIGHT_BLE_PROTOCOL``):

- ``raw`` — ``NSP_LIGHT_BLE_RGB_PREFIX_HEX`` + 3 RGB bytes (legacy / unknown devices).
- ``ipixel_png`` — solid PNG sized to matrix WxH, framed like ``pypixelcolor`` ``send_image``
  (see ``ipixel_windows.py``). Default write UUID ``0000fa02-...`` if unset.

For ``ipixel_png`` and real BLE (not dry-run), optional **notify + per-window ACK** on
``0000fa03-...`` matches ``pypixelcolor`` behaviour (see ``ipixel_ack.py``). Disable with
``NSP_LIGHT_BLE_IPX_WAIT_ACK=0`` if a firmware build does not notify.

Safe default: ``NSP_LIGHT_BLE_DRY_RUN=1`` logs instead of writing.

Optional **fade** (linear RGB between last target and new): ``NSP_LIGHT_BLE_FADE_MS`` > 0.
Cap steps with ``NSP_LIGHT_BLE_FADE_MAX_STEPS``; optional floor between steps
``NSP_LIGHT_BLE_FADE_MIN_STEP_S`` and ``NSP_LIGHT_BLE_FADE_RESPECT_MIN_INTERVAL`` (see docs).

Optional **pulse** (brightness factor on last target until a new RGB is queued): ``NSP_LIGHT_BLE_PULSE_HZ`` > 0.

On transfer failure (timeout, disconnect mid-frame, etc.), **full frame** is retried up to ``NSP_LIGHT_BLE_FRAME_RETRIES`` times with ``NSP_LIGHT_BLE_FRAME_RETRY_DELAY_S`` pause between attempts.
Set ``NSP_LIGHT_BLE_RETRY_DEBUG=1`` for stderr lines on retry attempts / recovery.

Requires ``bleak`` (already a project dependency).
"""

from __future__ import annotations

import asyncio
import math
import os
import queue
import sys
import threading
import time
from typing import Any

from neurosync_pro.light.ble_connect_retry import bleak_connect_with_retries
from neurosync_pro.light.ipixel_ack import IpixelAckManager
from neurosync_pro.light.ipixel_constants import IPixel_NOTIFY_UUID, IPixel_WRITE_UUID
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


def _fade_rgb_sequence(
    src: tuple[int, int, int] | None,
    dst: tuple[int, int, int],
    fade_ms: float,
    *,
    max_steps: int = 32,
) -> list[tuple[int, int, int]]:
    """Linear RGB steps from ``src`` to ``dst``; single ``dst`` if no fade."""

    if fade_ms <= 0 or src is None or src == dst:
        return [dst]
    cap = max(2, min(64, int(max_steps)))
    steps = max(2, min(cap, int(fade_ms / 35.0)))
    out: list[tuple[int, int, int]] = []
    for i in range(1, steps + 1):
        t = i / steps
        out.append(
            tuple(
                max(0, min(255, int(round(src[j] * (1.0 - t) + dst[j] * t)))) for j in range(3)
            )
        )
    return out


class BleSolidRgbWorker:
    """One consumer thread with asyncio + optional persistent ``BleakClient``."""

    def __init__(self) -> None:
        self._q: queue.Queue[tuple[int, int, int]] = queue.Queue(maxsize=8)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._min_interval_s = max(0.02, min(0.5, _float_env("NSP_LIGHT_BLE_MIN_INTERVAL_S", 0.05)))
        self._last_target_rgb: tuple[int, int, int] | None = None

    def start(self) -> None:
        if self._thread is not None and not self._thread.is_alive():
            self._thread = None
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

    async def _ipixel_stop_notify(self, client: Any, notify_uuid: str, started: bool) -> None:
        if not started:
            return
        try:
            await client.stop_notify(notify_uuid)
        except Exception:
            pass

    async def _send_ipixel_init(
        self,
        client: Any,
        wuuid: str,
        *,
        ack_mgr: IpixelAckManager | None,
        notify_ok: bool,
        wait_ack: bool,
        ack_timeout: float,
        chunk_response: bool,
    ) -> None:
        if not _truthy(os.environ.get("NSP_LIGHT_BLE_IPX_INIT"), default=True):
            return
        blevel = _int_env("NSP_LIGHT_BLE_IPX_BRIGHTNESS", 80)
        blevel = max(1, min(100, blevel))
        power = bytes([5, 0, 7, 1, 1])
        bright = bytes([5, 0, 4, 0x80, blevel])
        init_timeout = min(float(ack_timeout), 4.0)
        for pkt in (power, bright):
            if ack_mgr is not None and notify_ok and wait_ack:
                ack_mgr.reset()
            await client.write_gatt_char(wuuid, pkt, response=chunk_response)
            if ack_mgr is not None and notify_ok and wait_ack:
                try:
                    await asyncio.wait_for(ack_mgr.window_event.wait(), timeout=init_timeout)
                except asyncio.TimeoutError:
                    print("[light][ble] init ack timeout (continuing)", file=sys.stderr)
            else:
                await asyncio.sleep(0.05)

    async def _write_ipixel_window(
        self,
        client: Any,
        wuuid: str,
        window: bytes,
        *,
        chunk_size: int,
        chunk_response: bool,
        ack_mgr: IpixelAckManager | None,
        notify_ok: bool,
        wait_ack: bool,
        ack_timeout: float,
    ) -> None:
        step = max(8, min(244, chunk_size))
        if ack_mgr is not None and wait_ack and notify_ok:
            ack_mgr.reset()
        for i in range(0, len(window), step):
            await client.write_gatt_char(wuuid, window[i : i + step], response=chunk_response)
        if ack_mgr is not None and wait_ack and notify_ok:
            await asyncio.wait_for(ack_mgr.window_event.wait(), timeout=ack_timeout)
        elif not (notify_ok and wait_ack):
            await asyncio.sleep(0.02)

    async def _run_ipixel_png(
        self,
        client: Any,
        *,
        addr: str,
        wuuid: str,
        notify_uuid: str,
        rgb: tuple[int, int, int],
        r0: int,
        g0: int,
        b0: int,
        dry: bool,
        connect_timeout: float,
        chunk_size: int,
        save_slot: int,
    ) -> Any:
        """Return possibly updated ``BleakClient``."""
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
            return client

        wait_ack = _truthy(os.environ.get("NSP_LIGHT_BLE_IPX_WAIT_ACK"), default=True)
        chunk_response = _truthy(os.environ.get("NSP_LIGHT_BLE_WRITE_CHUNK_RESPONSE"), default=True)
        ack_timeout = max(0.5, _float_env("NSP_LIGHT_BLE_ACK_TIMEOUT_S", 8.0))

        if client is None or not client.is_connected:
            client = await bleak_connect_with_retries(addr, connect_timeout)

        ack_mgr: IpixelAckManager | None = None
        notify_started = False
        if wait_ack:
            ack_mgr = IpixelAckManager()
            try:
                await client.start_notify(notify_uuid, ack_mgr.make_notify_handler())
                notify_started = True
            except Exception as exc:
                print(f"[light][ble] start_notify failed, ACK wait disabled: {exc}", file=sys.stderr)
                ack_mgr = None

        notify_ok = notify_started and ack_mgr is not None

        try:
            await self._send_ipixel_init(
                client,
                wuuid,
                ack_mgr=ack_mgr,
                notify_ok=notify_ok,
                wait_ack=wait_ack,
                ack_timeout=ack_timeout,
                chunk_response=chunk_response,
            )
            for win in windows:
                await self._write_ipixel_window(
                    client,
                    wuuid,
                    win,
                    chunk_size=chunk_size,
                    chunk_response=chunk_response,
                    ack_mgr=ack_mgr,
                    notify_ok=notify_ok,
                    wait_ack=wait_ack,
                    ack_timeout=ack_timeout,
                )
        finally:
            await self._ipixel_stop_notify(client, notify_uuid, notify_started)

        return client

    async def _disconnect_ble_client(self, client: Any) -> None:
        if client is None:
            return
        try:
            if getattr(client, "is_connected", False):
                await client.disconnect()
        except Exception:
            pass

    async def _run_ipixel_png_with_frame_retries(
        self,
        client: Any,
        *,
        addr: str,
        wuuid: str,
        notify_uuid: str,
        rgb: tuple[int, int, int],
        r0: int,
        g0: int,
        b0: int,
        dry: bool,
        connect_timeout: float,
        chunk_size: int,
        save_slot: int,
    ) -> Any:
        retries = max(1, _int_env("NSP_LIGHT_BLE_FRAME_RETRIES", 2))
        delay = max(0.0, _float_env("NSP_LIGHT_BLE_FRAME_RETRY_DELAY_S", 0.15))
        last_exc: Exception | None = None
        cur = client
        retry_dbg = _truthy(os.environ.get("NSP_LIGHT_BLE_RETRY_DEBUG"), default=False)
        for attempt in range(retries):
            try:
                out = await self._run_ipixel_png(
                    cur,
                    addr=addr,
                    wuuid=wuuid,
                    notify_uuid=notify_uuid,
                    rgb=rgb,
                    r0=r0,
                    g0=g0,
                    b0=b0,
                    dry=dry,
                    connect_timeout=connect_timeout,
                    chunk_size=chunk_size,
                    save_slot=save_slot,
                )
                if attempt > 0 and retry_dbg:
                    print(
                        f"[light][ble] frame ok after {attempt + 1} attempt(s) rgb={rgb} ipixel_png",
                        file=sys.stderr,
                    )
                return out
            except Exception as exc:
                last_exc = exc
                if retry_dbg:
                    print(
                        f"[light][ble] frame attempt {attempt + 1}/{retries} failed rgb={rgb}: {exc!r}",
                        file=sys.stderr,
                    )
                await self._disconnect_ble_client(cur)
                cur = None
                if attempt + 1 < retries and delay > 0:
                    await asyncio.sleep(delay)
        if last_exc is not None:
            raise last_exc
        return cur

    async def _send_raw_payload_with_frame_retries(
        self,
        client: Any,
        *,
        addr: str,
        wuuid: str,
        payload: bytes,
        dry: bool,
        connect_timeout: float,
    ) -> Any:
        if dry:
            return client
        retries = max(1, _int_env("NSP_LIGHT_BLE_FRAME_RETRIES", 2))
        delay = max(0.0, _float_env("NSP_LIGHT_BLE_FRAME_RETRY_DELAY_S", 0.15))
        last_exc: Exception | None = None
        cur = client
        retry_dbg = _truthy(os.environ.get("NSP_LIGHT_BLE_RETRY_DEBUG"), default=False)
        for attempt in range(retries):
            try:
                if cur is None or not getattr(cur, "is_connected", False):
                    cur = await bleak_connect_with_retries(addr, connect_timeout)
                await cur.write_gatt_char(wuuid, payload, response=False)
                if attempt > 0 and retry_dbg:
                    print(
                        f"[light][ble] frame ok after {attempt + 1} attempt(s) raw payload_len={len(payload)}",
                        file=sys.stderr,
                    )
                return cur
            except Exception as exc:
                last_exc = exc
                if retry_dbg:
                    print(
                        f"[light][ble] frame attempt {attempt + 1}/{retries} failed raw: {exc!r}",
                        file=sys.stderr,
                    )
                await self._disconnect_ble_client(cur)
                cur = None
                if attempt + 1 < retries and delay > 0:
                    await asyncio.sleep(delay)
        if last_exc is not None:
            raise last_exc
        return cur

    async def _async_main(self) -> None:
        try:
            import bleak  # noqa: F401
        except ImportError:
            print("[light][ble] bleak is not installed", file=sys.stderr)
            return

        dry = _truthy(os.environ.get("NSP_LIGHT_BLE_DRY_RUN"), default=True)
        addr = (os.environ.get("NSP_LIGHT_BLE_ADDRESS") or "").strip()
        proto = (os.environ.get("NSP_LIGHT_BLE_PROTOCOL") or "raw").strip().lower()
        wuuid = (os.environ.get("NSP_LIGHT_BLE_WRITE_UUID") or "").strip()
        if proto == "ipixel_png" and not wuuid:
            wuuid = IPixel_WRITE_UUID
        notify_uuid = (os.environ.get("NSP_LIGHT_BLE_NOTIFY_UUID") or "").strip() or IPixel_NOTIFY_UUID
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

        def _get_rgb() -> tuple[int, int, int] | None:
            try:
                return self._q.get(timeout=0.35)
            except queue.Empty:
                return None

        pending_rgb: tuple[int, int, int] | None = None
        while not self._stop.is_set():
            rgb = pending_rgb
            pending_rgb = None
            if rgb is None:
                rgb = await asyncio.to_thread(_get_rgb)
            if rgb is None:
                continue

            now = time.monotonic()
            if now < next_ok:
                await asyncio.sleep(next_ok - now)
            next_ok = time.monotonic() + self._min_interval_s

            r, g, b = rgb
            r0, g0, b0 = max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b))
            target = (r0, g0, b0)
            fade_ms = max(0.0, _float_env("NSP_LIGHT_BLE_FADE_MS", 0.0))
            fade_max_steps = max(2, min(64, _int_env("NSP_LIGHT_BLE_FADE_MAX_STEPS", 32)))
            fade_seq = _fade_rgb_sequence(
                self._last_target_rgb, target, fade_ms, max_steps=fade_max_steps
            )
            post_sleep = (
                (fade_ms / 1000.0 / max(len(fade_seq), 1)) if fade_ms > 0 and len(fade_seq) > 1 else 0.0
            )
            fade_floor_s = max(0.0, _float_env("NSP_LIGHT_BLE_FADE_MIN_STEP_S", 0.0))
            fade_respect_min_iv = _truthy(
                os.environ.get("NSP_LIGHT_BLE_FADE_RESPECT_MIN_INTERVAL"), default=False
            )

            try:
                if proto == "ipixel_png":
                    for i, (sr, sg, sb) in enumerate(fade_seq):
                        if self._stop.is_set():
                            break
                        client = await self._run_ipixel_png_with_frame_retries(
                            client,
                            addr=addr,
                            wuuid=wuuid,
                            notify_uuid=notify_uuid,
                            rgb=(sr, sg, sb),
                            r0=sr,
                            g0=sg,
                            b0=sb,
                            dry=dry,
                            connect_timeout=connect_timeout,
                            chunk_size=chunk_size,
                            save_slot=save_slot,
                        )
                        if i + 1 < len(fade_seq):
                            gap = post_sleep
                            if fade_floor_s > 0:
                                gap = max(gap, fade_floor_s)
                            if fade_respect_min_iv:
                                gap = max(gap, self._min_interval_s)
                            if gap > 0:
                                await asyncio.sleep(gap)
                else:
                    for i, (sr, sg, sb) in enumerate(fade_seq):
                        if self._stop.is_set():
                            break
                        payload = prefix + bytes((sr, sg, sb))
                        if dry:
                            print(
                                f"[light][ble][dry_run] rgb={(sr, sg, sb)} payload={payload.hex()}",
                                file=sys.stderr,
                            )
                        else:
                            client = await self._send_raw_payload_with_frame_retries(
                                client,
                                addr=addr,
                                wuuid=wuuid,
                                payload=payload,
                                dry=dry,
                                connect_timeout=connect_timeout,
                            )
                        if i + 1 < len(fade_seq):
                            gap = post_sleep
                            if fade_floor_s > 0:
                                gap = max(gap, fade_floor_s)
                            if fade_respect_min_iv:
                                gap = max(gap, self._min_interval_s)
                            if gap > 0:
                                await asyncio.sleep(gap)
            except Exception as exc:
                if proto == "ipixel_png":
                    print(f"[light][ble] ipixel_png failed: {exc}", file=sys.stderr)
                else:
                    print(f"[light][ble] write/connect failed: {exc}", file=sys.stderr)
                await self._disconnect_ble_client(client)
                client = None
                continue

            if self._stop.is_set():
                continue
            self._last_target_rgb = target

            pulse_hz = _float_env("NSP_LIGHT_BLE_PULSE_HZ", 0.0)
            if pulse_hz <= 0:
                continue

            period = 1.0 / max(0.05, min(pulse_hz, 25.0))
            step_sleep = max(self._min_interval_s * 0.4, min(0.18, period / 14.0))
            t0 = time.monotonic()
            while not self._stop.is_set():
                try:
                    pending_rgb = self._q.get_nowait()
                    break
                except queue.Empty:
                    pass
                ph = (time.monotonic() - t0) * (2 * math.pi / period)
                fac = 0.25 + 0.75 * (0.5 + 0.5 * math.sin(ph))
                pr = max(0, min(255, int(round(r0 * fac))))
                pg = max(0, min(255, int(round(g0 * fac))))
                pb = max(0, min(255, int(round(b0 * fac))))
                try:
                    if proto == "ipixel_png":
                        client = await self._run_ipixel_png_with_frame_retries(
                            client,
                            addr=addr,
                            wuuid=wuuid,
                            notify_uuid=notify_uuid,
                            rgb=(pr, pg, pb),
                            r0=pr,
                            g0=pg,
                            b0=pb,
                            dry=dry,
                            connect_timeout=connect_timeout,
                            chunk_size=chunk_size,
                            save_slot=save_slot,
                        )
                    else:
                        payload = prefix + bytes((pr, pg, pb))
                        if dry:
                            print(
                                f"[light][ble][dry_run] rgb={(pr, pg, pb)} payload={payload.hex()}",
                                file=sys.stderr,
                            )
                        else:
                            client = await self._send_raw_payload_with_frame_retries(
                                client,
                                addr=addr,
                                wuuid=wuuid,
                                payload=payload,
                                dry=dry,
                                connect_timeout=connect_timeout,
                            )
                except Exception as exc:
                    if proto == "ipixel_png":
                        print(f"[light][ble] ipixel_png failed: {exc}", file=sys.stderr)
                    else:
                        print(f"[light][ble] write/connect failed: {exc}", file=sys.stderr)
                    await self._disconnect_ble_client(client)
                    client = None
                    break
                await asyncio.sleep(step_sleep)

        if client is not None and not dry:
            try:
                if client.is_connected:
                    await client.disconnect()
            except Exception:
                pass
