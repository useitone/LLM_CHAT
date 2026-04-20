from neurosync_pro.bus import EventBus


def test_bus_publish() -> None:
    bus = EventBus()
    seen: list = []

    def h(p):
        seen.append(p)

    bus.subscribe("t", h)
    bus.publish("t", {"a": 1})
    assert seen == [{"a": 1}]
