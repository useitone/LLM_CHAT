"""COM port reader using ThinkGear-style TGAM frames → Attention / Meditation (research path).

Does not use Macrotellect ``BrainLinkParser.pyd``. Optional complement to BLE EEG.
"""

from __future__ import annotations

import os
import time

from PySide6.QtCore import QThread, Signal

from neurosync_pro.eeg.tgam_serial_parser import ThinkgearSerialParser


class TgamComMetricsThread(QThread):
    """Read COM at TGAM baud (default 57600), emit metrics like BLE/vendor COM."""

    metricsReady = Signal(int, int)  # attention, meditation
    signalQualityReady = Signal(int)  # poor_signal 0…200 (0 = good, per NeuroSky-style)
    workerFinished = Signal()
    connectionFailed = Signal(str)

    def __init__(
        self,
        *,
        port: str = "COM3",
        baud: int | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._port = (port or "COM3").strip()
        raw_baud = os.environ.get("NSP_TGAM_COM_BAUD", "")
        if baud is not None:
            self._baud = int(baud)
        else:
            try:
                self._baud = int(str(raw_baud).strip() or "57600", 0)
            except ValueError:
                self._baud = 57600
        self._stop = False

    def request_stop(self) -> None:
        self._stop = True

    def run(self) -> None:  # noqa: D102
        try:
            import serial  # type: ignore[import-not-found]
        except Exception as e:
            self.connectionFailed.emit(
                "Нет pyserial для TGAM COM. Установите: pip install pyserial"
            )
            self.workerFinished.emit()
            return

        port = (os.environ.get("NSP_TGAM_COM_PORT") or "").strip() or self._port
        parser = ThinkgearSerialParser()

        open_delay_s = float(os.environ.get("NSP_TGAM_COM_OPEN_DELAY_S", "0") or "0")
        open_delay_s = max(0.0, min(open_delay_s, 5.0))
        if open_delay_s > 0:
            time.sleep(open_delay_s)

        tries = int(os.environ.get("NSP_TGAM_COM_OPEN_RETRIES", "6") or "6")
        tries = max(1, min(tries, 20))
        pause_s = float(os.environ.get("NSP_TGAM_COM_OPEN_RETRY_PAUSE_S", "0.35") or "0.35")
        pause_s = max(0.05, min(pause_s, 3.0))

        ser = None
        last_exc: Exception | None = None
        for attempt in range(tries):
            if self._stop:
                self.workerFinished.emit()
                return
            try:
                ser = serial.Serial(
                    port=port,
                    baudrate=self._baud,
                    bytesize=serial.EIGHTBITS,
                    parity=serial.PARITY_NONE,
                    stopbits=serial.STOPBITS_ONE,
                    timeout=0.25,
                )
                break
            except Exception as exc:
                last_exc = exc
                if attempt + 1 >= tries:
                    self.connectionFailed.emit(f"TGAM COM {port}: {exc}")
                    self.workerFinished.emit()
                    return
                time.sleep(pause_s)

        if ser is None:
            self.connectionFailed.emit(f"TGAM COM {port}: {last_exc!r}")
            self.workerFinished.emit()
            return

        try:
            read_size = int(os.environ.get("NSP_TGAM_COM_READ_SIZE", "512") or "512")
            read_size = max(64, read_size)
            while not self._stop:
                chunk = ser.read(read_size)
                if not chunk:
                    continue
                for byte in chunk:
                    frame = parser.feed_byte(byte)
                    if frame is not None:
                        self.metricsReady.emit(
                            max(0, min(100, int(frame.attention))),
                            max(0, min(100, int(frame.meditation))),
                        )
                        self.signalQualityReady.emit(
                            max(0, min(255, int(frame.poor_signal)))
                        )
        except Exception as exc:
            if not self._stop:
                self.connectionFailed.emit(f"TGAM COM read: {exc}")
        finally:
            try:
                ser.close()
            except Exception:
                pass
            self.workerFinished.emit()
