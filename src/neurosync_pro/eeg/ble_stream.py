"""BrainLink Pro BLE (Nordic UART) notify stream — async API for live sessions."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

try:
    from bleak import BleakClient
except ImportError:  # pragma: no cover
    BleakClient = None  # type: ignore[misc, assignment]

DEFAULT_NOTIFY_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
DEFAULT_WRITE_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"


def normalize_ble_address(address: str) -> str:
    """Windows often shows MAC with hyphens; bleak expects colons."""
    a = address.strip().upper()
    if "-" in a:
        a = a.replace("-", ":")
    return a

OnNotifyChunk = Callable[[bytes], None]


async def run_ble_notify_session(
    address: str,
    on_chunk: OnNotifyChunk,
    *,
    notify_uuid: str = DEFAULT_NOTIFY_UUID,
    write_uuid: str = DEFAULT_WRITE_UUID,
    init_hex: str = "",
    duration_s: float | None = None,
    stop_event: asyncio.Event | None = None,
) -> None:
    """
    Connect, subscribe to notify, forward each payload to ``on_chunk``.

    Stops when ``duration_s`` elapses (if set), ``stop_event`` is set, or on disconnect.
    """
    if BleakClient is None:  # pragma: no cover
        raise RuntimeError("bleak is not installed")

    if duration_s is None and stop_event is None:
        raise ValueError("Provide stop_event and/or duration_s")

    stop_ev = stop_event if stop_event is not None else asyncio.Event()

    async def _wait_stop() -> None:
        await stop_ev.wait()

    async with BleakClient(address) as client:
        if not client.is_connected:
            raise RuntimeError(f"Failed to connect: {address}")

        def _handler(_sender: Any, data: bytearray) -> None:
            on_chunk(bytes(data))

        await client.start_notify(notify_uuid, _handler)

        init = init_hex.strip().replace(" ", "")
        if init:
            await client.write_gatt_char(write_uuid, bytes.fromhex(init), response=False)

        try:
            if duration_s is not None and duration_s > 0:
                await asyncio.wait(
                    [asyncio.create_task(asyncio.sleep(duration_s)), asyncio.create_task(_wait_stop())],
                    return_when=asyncio.FIRST_COMPLETED,
                )
            else:
                await _wait_stop()
        finally:
            try:
                await client.stop_notify(notify_uuid)
            except Exception:
                pass


def schedule_stop(loop: asyncio.AbstractEventLoop, stop_event: asyncio.Event) -> None:
    """Thread-safe: signal ``run_ble_notify_session`` to exit (call from GUI thread)."""
    loop.call_soon_threadsafe(stop_event.set)
