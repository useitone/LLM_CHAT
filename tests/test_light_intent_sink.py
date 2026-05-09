"""Tests for ``light.intent`` subscriber (log / BLE dry-run)."""

from __future__ import annotations

import time

import pytest

from neurosync_pro.bus import EventBus
from neurosync_pro.light.ble_solid_worker import BleSolidRgbWorker
from neurosync_pro.light.intent_sink import try_attach_light_intent_sink


def test_try_attach_send_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NSP_LIGHT_SEND_ENABLED", raising=False)
    bus = EventBus()
    detach = try_attach_light_intent_sink(bus)
    detach()


def test_log_mode_logs_when_debug(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("NSP_LIGHT_SEND_ENABLED", "1")
    monkeypatch.setenv("NSP_LIGHT_SEND_MODE", "log")
    monkeypatch.setenv("NSP_LIGHT_SEND_DEBUG", "1")
    bus = EventBus()
    detach = try_attach_light_intent_sink(bus)
    try:
        bus.publish("light.intent", {"kind": "rgb", "rgb": [10, 20, 30]})
    finally:
        detach()
    err = capsys.readouterr().err
    assert "rgb=(10, 20, 30)" in err


def test_dedup_skips_second_publish(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("NSP_LIGHT_SEND_ENABLED", "1")
    monkeypatch.setenv("NSP_LIGHT_SEND_MODE", "log")
    monkeypatch.setenv("NSP_LIGHT_SEND_DEBUG", "1")
    bus = EventBus()
    detach = try_attach_light_intent_sink(bus)
    try:
        bus.publish("light.intent", {"kind": "rgb", "rgb": [1, 2, 3]})
        bus.publish("light.intent", {"kind": "rgb", "rgb": [1, 2, 3]})
    finally:
        detach()
    err = capsys.readouterr().err
    assert err.count("rgb=(1, 2, 3)") == 1


def test_ble_worker_dry_run(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NSP_LIGHT_BLE_DRY_RUN", "1")
    w = BleSolidRgbWorker()
    w.start()
    try:
        w.enqueue((4, 5, 6))
        time.sleep(0.6)
    finally:
        w.stop()
    err = capsys.readouterr().err
    assert "dry_run" in err
    assert "4, 5, 6" in err


def test_sink_ble_mode_forwards_to_dry_worker(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NSP_LIGHT_SEND_ENABLED", "1")
    monkeypatch.setenv("NSP_LIGHT_SEND_MODE", "ble")
    monkeypatch.setenv("NSP_LIGHT_BLE_DRY_RUN", "1")
    bus = EventBus()
    detach = try_attach_light_intent_sink(bus)
    try:
        bus.publish("light.intent", {"kind": "rgb", "rgb": [100, 150, 255]})
        time.sleep(0.6)
    finally:
        detach()
    err = capsys.readouterr().err
    assert "dry_run" in err
    assert "100, 150, 255" in err
