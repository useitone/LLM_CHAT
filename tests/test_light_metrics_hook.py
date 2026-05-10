import pytest

from neurosync_pro.bus import EventBus
from neurosync_pro.light.metrics_hook import MetricsLightBridge, try_attach_metrics_light_bridge


def test_try_attach_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NSP_LIGHT_ENABLED", raising=False)
    bus = EventBus()
    detach = try_attach_metrics_light_bridge(bus)
    detach()
    assert detach is not None


def test_try_attach_noop_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NSP_LIGHT_ENABLED", "0")
    bus = EventBus()
    seen: list[object] = []

    def cap(p: object) -> None:
        seen.append(p)

    bus.subscribe("light.intent", cap)
    detach = try_attach_metrics_light_bridge(bus)
    bus.publish("eeg.metrics", {"attention": 50, "meditation": 60})
    detach()
    assert seen == []


def test_auto_publishes_light_intent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NSP_LIGHT_ENABLED", "1")
    monkeypatch.setenv("NSP_LIGHT_MODE", "auto")
    monkeypatch.setenv("NSP_LIGHT_METRICS_MIN_INTERVAL_MS", "0")
    bus = EventBus()
    intents: list[dict] = []

    def cap(p: object) -> None:
        if isinstance(p, dict):
            intents.append(dict(p))

    bus.subscribe("light.intent", cap)
    detach = try_attach_metrics_light_bridge(bus)
    try:
        bus.publish("eeg.metrics", {"attention": 10, "meditation": 85})
    finally:
        detach()
    assert len(intents) == 1
    assert intents[0].get("kind") == "rgb"
    assert intents[0].get("rgb") == [100, 150, 255]


def test_metrics_bridge_detach_stops_events(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NSP_LIGHT_MODE", "auto")
    monkeypatch.setenv("NSP_LIGHT_METRICS_MIN_INTERVAL_MS", "0")
    bus = EventBus()
    intents: list[dict] = []

    def cap(p: object) -> None:
        if isinstance(p, dict):
            intents.append(p)

    bus.subscribe("light.intent", cap)
    br = MetricsLightBridge(bus)
    unsub = br.attach()
    bus.publish("eeg.metrics", {"attention": 80, "meditation": 10})
    assert len(intents) == 1
    unsub()
    bus.publish("eeg.metrics", {"attention": 80, "meditation": 10})
    assert len(intents) == 1
