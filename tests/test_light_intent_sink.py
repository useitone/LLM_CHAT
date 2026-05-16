"""Tests for ``light.intent`` subscriber (log / BLE dry-run)."""

from __future__ import annotations

import json
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
    monkeypatch.setenv("NSP_LIGHT_BLE_IPX_BRIGHTNESS", "80")
    bus = EventBus()
    detach = try_attach_light_intent_sink(bus)
    try:
        bus.publish("light.intent", {"kind": "rgb", "rgb": [1, 2, 3]})
        bus.publish("light.intent", {"kind": "rgb", "rgb": [1, 2, 3]})
    finally:
        detach()
    err = capsys.readouterr().err
    assert err.count("rgb=(1, 2, 3)") == 1


def test_intent_log_file_append(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    log_path = tmp_path / "intents.jsonl"
    monkeypatch.setenv("NSP_LIGHT_SEND_ENABLED", "1")
    monkeypatch.setenv("NSP_LIGHT_SEND_MODE", "log")
    monkeypatch.setenv("NSP_LIGHT_SEND_DEBUG", "0")
    monkeypatch.setenv("NSP_LIGHT_INTENT_LOG", str(log_path))
    bus = EventBus()
    detach = try_attach_light_intent_sink(bus)
    try:
        bus.publish(
            "light.intent",
            {"kind": "rgb", "rgb": [7, 8, 9], "source": "test", "attention": 1, "meditation": 2},
        )
        bus.publish(
            "light.intent",
            {"kind": "rgb", "rgb": [7, 8, 9], "source": "test", "attention": 1, "meditation": 2},
        )
        bus.publish("light.intent", {"kind": "rgb", "rgb": [10, 11, 12], "source": "test2"})
    finally:
        detach()
        monkeypatch.delenv("NSP_LIGHT_INTENT_LOG", raising=False)
    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    r0 = json.loads(lines[0])
    assert r0["rgb"] == [7, 8, 9]
    assert r0["source"] == "test"
    assert r0["attention"] == 1
    assert r0["meditation"] == 2
    r1 = json.loads(lines[1])
    assert r1["rgb"] == [10, 11, 12]


def test_brightness_change_same_rgb_logs_again(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("NSP_LIGHT_SEND_ENABLED", "1")
    monkeypatch.setenv("NSP_LIGHT_SEND_MODE", "log")
    monkeypatch.setenv("NSP_LIGHT_SEND_DEBUG", "1")
    monkeypatch.setenv("NSP_LIGHT_BLE_IPX_BRIGHTNESS", "40")
    bus = EventBus()
    detach = try_attach_light_intent_sink(bus)
    try:
        bus.publish("light.intent", {"kind": "rgb", "rgb": [10, 20, 30]})
        monkeypatch.setenv("NSP_LIGHT_BLE_IPX_BRIGHTNESS", "90")
        bus.publish("light.intent", {"kind": "rgb", "rgb": [10, 20, 30]})
    finally:
        detach()
    err = capsys.readouterr().err
    assert err.count("rgb=(10, 20, 30)") == 2


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


def test_ble_worker_ipixel_png_dry_run(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NSP_LIGHT_BLE_PROTOCOL", "ipixel_png")
    monkeypatch.setenv("NSP_LIGHT_BLE_DRY_RUN", "1")
    monkeypatch.setenv("NSP_LIGHT_BLE_MATRIX_W", "16")
    monkeypatch.setenv("NSP_LIGHT_BLE_MATRIX_H", "8")
    w = BleSolidRgbWorker()
    w.start()
    try:
        w.enqueue((40, 50, 60))
        time.sleep(0.6)
    finally:
        w.stop()
    err = capsys.readouterr().err
    assert "ipixel_png" in err
    assert "windows=" in err


def test_ble_worker_ipixel_fade_emits_multiple_dry_run_frames(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NSP_LIGHT_BLE_PROTOCOL", "ipixel_png")
    monkeypatch.setenv("NSP_LIGHT_BLE_DRY_RUN", "1")
    monkeypatch.setenv("NSP_LIGHT_BLE_MATRIX_W", "16")
    monkeypatch.setenv("NSP_LIGHT_BLE_MATRIX_H", "8")
    monkeypatch.setenv("NSP_LIGHT_BLE_FADE_MS", "280")
    w = BleSolidRgbWorker()
    w.start()
    try:
        w.enqueue((255, 0, 0))
        time.sleep(0.45)
        w.enqueue((0, 0, 200))
        time.sleep(1.4)
    finally:
        w.stop()
    err = capsys.readouterr().err
    assert err.count("[light][ble][dry_run][ipixel_png]") >= 5


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


def test_light_env_updated_resends_ble_dry_run(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NSP_LIGHT_SEND_ENABLED", "1")
    monkeypatch.setenv("NSP_LIGHT_SEND_MODE", "ble")
    monkeypatch.setenv("NSP_LIGHT_BLE_DRY_RUN", "1")
    monkeypatch.setenv("NSP_LIGHT_BLE_PROTOCOL", "ipixel_png")
    monkeypatch.setenv("NSP_LIGHT_BLE_MATRIX_W", "16")
    monkeypatch.setenv("NSP_LIGHT_BLE_MATRIX_H", "8")
    bus = EventBus()
    detach = try_attach_light_intent_sink(bus)
    try:
        bus.publish("light.intent", {"kind": "rgb", "rgb": [11, 22, 33]})
        time.sleep(0.65)
        capsys.readouterr()
        bus.publish("light.env_updated", {})
        time.sleep(0.65)
    finally:
        detach()
    err = capsys.readouterr().err
    assert err.count("dry_run") >= 1
    assert "11, 22, 33" in err
