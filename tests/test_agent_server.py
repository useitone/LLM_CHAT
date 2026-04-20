import json
import socket
import urllib.request

from neurosync_pro.agent.server import start_agent_api, stop_agent_api
from neurosync_pro.bus import EventBus


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def test_agent_post_publishes_to_bus() -> None:
    bus = EventBus()
    seen: list = []
    bus.subscribe("eeg.tick", lambda p: seen.append(p))
    port = _free_port()
    srv, _thr = start_agent_api(bus, host="127.0.0.1", port=port)
    try:
        data = json.dumps({"topic": "eeg.tick", "payload": {"x": 2}}).encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/event",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=3)
    finally:
        stop_agent_api(srv)
    assert seen == [{"x": 2}]
