"""Background BLE notify session in a QThread (own asyncio loop)."""

from __future__ import annotations

import asyncio

from PySide6.QtCore import QThread, Signal

from neurosync_pro.eeg.ble_stream import run_ble_notify_session, schedule_stop
from neurosync_pro.eeg.live_decode import LiveEegDecoder


class BleNotifyThread(QThread):
    """Decode BrainLink notify chunks and emit attention/meditation per EEG frame."""

    metricsReady = Signal(int, int)
    connectionFailed = Signal(str)
    workerFinished = Signal()

    def __init__(
        self,
        address: str,
        *,
        init_hex: str = "",
        duration_s: float | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._address = address
        self._init_hex = init_hex
        self._duration_s = duration_s
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_ev: asyncio.Event | None = None

    def request_stop(self) -> None:
        loop = self._loop
        ev = self._stop_ev
        if loop is not None and ev is not None and not ev.is_set():
            schedule_stop(loop, ev)

    def run(self) -> None:  # noqa: D102
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._stop_ev = asyncio.Event()
        decoder = LiveEegDecoder()

        def on_chunk(data: bytes) -> None:
            for frame in decoder.feed_chunk(data):
                self.metricsReady.emit(frame.attention, frame.meditation)

        try:
            self._loop.run_until_complete(
                run_ble_notify_session(
                    self._address,
                    on_chunk,
                    init_hex=self._init_hex,
                    duration_s=self._duration_s,
                    stop_event=self._stop_ev,
                )
            )
        except Exception as exc:  # pragma: no cover - hardware
            self.connectionFailed.emit(str(exc))
        finally:
            if self._loop is not None:
                try:
                    self._loop.close()
                except Exception:
                    pass
                self._loop = None
            self._stop_ev = None
            self.workerFinished.emit()
