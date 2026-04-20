"""Lightweight in-process event bus (thread-safe publish)."""

from __future__ import annotations

import threading
from collections import defaultdict
from typing import Any, Callable, DefaultDict, List


class EventBus:
    """Subscribe to string topics; publish dict or any payload."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subs: DefaultDict[str, List[Callable[[Any], None]]] = defaultdict(list)

    def subscribe(self, topic: str, fn: Callable[[Any], None]) -> Callable[[], None]:
        with self._lock:
            self._subs[topic].append(fn)

        def unsubscribe() -> None:
            with self._lock:
                if fn in self._subs[topic]:
                    self._subs[topic].remove(fn)

        return unsubscribe

    def publish(self, topic: str, payload: Any = None) -> None:
        with self._lock:
            handlers = list(self._subs.get(topic, ()))
        for h in handlers:
            try:
                h(payload)
            except Exception:
                pass
