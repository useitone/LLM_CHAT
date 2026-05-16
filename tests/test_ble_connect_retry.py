from __future__ import annotations

import asyncio
import unittest.mock

import pytest

from neurosync_pro.light.ble_connect_retry import bleak_connect_with_retries


def test_bleak_connect_retries_then_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NSP_LIGHT_BLE_CONNECT_RETRIES", "4")
    monkeypatch.setenv("NSP_LIGHT_BLE_CONNECT_RETRY_DELAY_S", "0")
    n = {"v": 0}

    class FakeClient:
        def __init__(self, addr: str, timeout: float = 8.0) -> None:
            self.addr = addr

        async def connect(self) -> None:
            n["v"] += 1
            if n["v"] < 3:
                raise RuntimeError("Device with address X was not found")

    async def _run() -> FakeClient:
        with unittest.mock.patch("bleak.BleakClient", FakeClient):
            c = await bleak_connect_with_retries("AA:BB:CC:DD:EE:FF", 2.0)
            assert isinstance(c, FakeClient)
            return c

    asyncio.run(_run())
    assert n["v"] == 3
