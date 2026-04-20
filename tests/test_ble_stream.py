"""BLE stream helpers (no hardware)."""

from __future__ import annotations

import asyncio

import pytest

from neurosync_pro.eeg.ble_stream import normalize_ble_address, run_ble_notify_session


def test_normalize_ble_address_hyphens() -> None:
    assert normalize_ble_address("8c-68-ab-e0-37-f7") == "8C:68:AB:E0:37:F7"


def test_run_ble_notify_session_requires_stop_or_duration() -> None:
    async def run() -> None:
        await run_ble_notify_session(
            "00:00:00:00:00:00",
            lambda _b: None,
            duration_s=None,
            stop_event=None,
        )

    with pytest.raises(ValueError, match="stop_event"):
        asyncio.run(run())
