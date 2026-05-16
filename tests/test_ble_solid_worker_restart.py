from __future__ import annotations

from unittest.mock import patch

from neurosync_pro.light.ble_solid_worker import BleSolidRgbWorker


def test_ble_worker_start_replaces_dead_thread() -> None:
    calls: list[int] = []

    async def _fast_async_main(self: BleSolidRgbWorker) -> None:
        calls.append(1)
        return

    w = BleSolidRgbWorker()
    with patch.object(BleSolidRgbWorker, "_async_main", _fast_async_main):
        w.start()
        th1 = w._thread
        assert th1 is not None
        th1.join(timeout=5.0)
        assert not th1.is_alive()
        w.start()
        th2 = w._thread
        assert th2 is not None
        assert th2 is not th1
        th2.join(timeout=5.0)
    assert calls == [1, 1]
