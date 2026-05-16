"""BLE connect with retries (transient ``device not found`` / scan races)."""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return int(str(raw).strip(), 0)
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return float(str(raw).strip())
    except ValueError:
        return default


async def bleak_connect_with_retries(addr: str, connect_timeout: float) -> Any:
    """Connect to ``addr``; retry on transient failures."""

    from bleak import BleakClient

    max_attempts = max(1, min(10, _int_env("NSP_LIGHT_BLE_CONNECT_RETRIES", 3)))
    delay_s = max(0.0, min(5.0, _float_env("NSP_LIGHT_BLE_CONNECT_RETRY_DELAY_S", 0.4)))
    last_exc: BaseException | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            c = BleakClient(addr, timeout=connect_timeout)
            await c.connect()
            if attempt > 1:
                print(f"[light][ble] connected after {attempt} attempt(s)", file=sys.stderr)
            return c
        except BaseException as exc:
            last_exc = exc
            print(
                f"[light][ble] connect attempt {attempt}/{max_attempts} failed: {exc}",
                file=sys.stderr,
            )
            if attempt < max_attempts and delay_s > 0:
                await asyncio.sleep(delay_s)
    assert last_exc is not None
    raise last_exc
