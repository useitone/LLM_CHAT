"""Optional HR source: Macrotellect BrainLinkParser.pyd over virtual COM (Windows).

This is treated as an "etalon" (reference) HR path because it comes from the
vendor parser that also exposes RR intervals (HRV). It is independent of BLE:
you can keep BLE for EEG while taking HR from COM in parallel.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import QThread, Signal


def _truthy_env(raw: str | None, *, default: bool = True) -> bool:
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _int_attr(obj: Any, name: str, default: int = 0) -> int:
    try:
        return int(getattr(obj, name, default))
    except Exception:
        return default


def _default_pyd_dir() -> Path:
    # Mirrors scripts/brainlink_com_macrotellect.py default.
    # Prefer cwd (repo root when launched from checkout), then fallback to source-tree relative.
    cwd = Path.cwd()
    d1 = cwd / "docs/specs/vendor/macrotellect_brainlink_parser"
    if d1.is_dir():
        return d1
    return Path(__file__).resolve().parents[3] / "docs/specs/vendor/macrotellect_brainlink_parser"


def _load_brainlink_parser_class(pyd_dir: Path) -> type:
    pyd_file = pyd_dir / "BrainLinkParser.pyd"
    if not pyd_file.is_file():
        raise RuntimeError(
            "BrainLinkParser.pyd не найден: "
            f"{pyd_file}. См. docs/specs/vendor/macrotellect_brainlink_parser/README.md"
        )
    if str(pyd_dir.resolve()) not in sys.path:
        sys.path.insert(0, str(pyd_dir.resolve()))
    try:
        from BrainLinkParser import BrainLinkParser  # type: ignore[import-not-found]
    except ImportError as e:
        raise RuntimeError(
            "Не удалось импортировать BrainLinkParser.pyd. "
            "Проверьте совпадение версии Python/архитектуры (обычно Python 3.11 x64). "
            f"Ошибка: {e}"
        ) from e
    return BrainLinkParser


def _rr_triplet_to_bpm(rr1: int, rr2: int, rr3: int) -> int | None:
    xs = [int(rr1), int(rr2), int(rr3)]
    xs = [x for x in xs if x > 0]
    if not xs:
        return None
    xs.sort()
    rr_ms = float(xs[len(xs) // 2])
    bpm = int(round(60000.0 / rr_ms)) if rr_ms > 1 else 0
    if 30 <= bpm <= 220:
        return bpm
    return None


class MacrotellectComHrThread(QThread):
    """Read COM port and emit HR estimates from RR triplets."""

    heartRateReady = Signal(int)  # bpm
    # Same shapes as BleNotifyThread — when COM is open, many headsets stop BLE EEG.
    metricsReady = Signal(int, int)
    signalQualityReady = Signal(int)
    bandsReady = Signal(int, int, int, int, int, int, int, int, int, int)
    debugEvent = Signal(dict)  # optional: rr/extend snapshots for logging
    connectionFailed = Signal(str)
    workerFinished = Signal()

    def __init__(
        self,
        *,
        port: str = "COM3",
        baud: int = 115200,
        pyd_dir: str | Path | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._port = (port or "COM3").strip()
        self._baud = int(baud)
        # Treat empty strings / "." as "use default".
        if pyd_dir is None:
            self._pyd_dir = _default_pyd_dir()
        else:
            s = str(pyd_dir).strip()
            if not s or s in {".", ".\\"}:
                self._pyd_dir = _default_pyd_dir()
            else:
                self._pyd_dir = Path(pyd_dir)
        self._stop = False

    def request_stop(self) -> None:
        self._stop = True

    def run(self) -> None:  # noqa: D102
        try:
            try:
                import serial  # type: ignore[import-not-found]
            except Exception as e:
                raise RuntimeError(
                    "Нет зависимости pyserial для эталонного HR. "
                    "Установите: pip install -e . или pip install pyserial"
                ) from e

            BrainLinkParser = _load_brainlink_parser_class(self._pyd_dir)

            feed_eeg = _truthy_env(os.environ.get("NSP_HR_ETALON_FEED_EEG"), default=True)

            def on_eeg(data: Any) -> None:
                if not feed_eeg:
                    return
                att = _int_attr(data, "attention")
                med = _int_attr(data, "meditation")
                self.metricsReady.emit(att, med)
                if hasattr(data, "signal"):
                    try:
                        self.signalQualityReady.emit(_int_attr(data, "signal"))
                    except Exception:
                        pass
                self.bandsReady.emit(
                    _int_attr(data, "delta"),
                    _int_attr(data, "theta"),
                    _int_attr(data, "lowAlpha"),
                    _int_attr(data, "highAlpha"),
                    _int_attr(data, "lowBeta"),
                    _int_attr(data, "highBeta"),
                    _int_attr(data, "lowGamma"),
                    _int_attr(data, "highGamma"),
                    att,
                    med,
                )

            def on_extend(data: Any) -> None:
                # Some firmwares surface BPM directly in "extend.heart".
                heart = None
                try:
                    if hasattr(data, "heart"):
                        heart = int(getattr(data, "heart"))
                except Exception:
                    heart = None
                if heart is not None and 30 <= int(heart) <= 220:
                    self.heartRateReady.emit(int(heart))
                if str(os.environ.get("NSP_HR_ETALON_DEBUG", "0") or "0").strip().lower() in {"1", "true", "yes", "on"}:
                    try:
                        self.debugEvent.emit({"kind": "extend", "heart": heart})
                    except Exception:
                        pass

            def on_gyro(_x: int, _y: int, _z: int) -> None:
                return

            def on_rr(rr1: int, rr2: int, rr3: int) -> None:
                bpm = _rr_triplet_to_bpm(rr1, rr2, rr3)
                if bpm is not None:
                    self.heartRateReady.emit(int(bpm))
                if str(os.environ.get("NSP_HR_ETALON_DEBUG", "0") or "0").strip().lower() in {"1", "true", "yes", "on"}:
                    try:
                        self.debugEvent.emit({"kind": "rr", "rr1": int(rr1), "rr2": int(rr2), "rr3": int(rr3), "bpm": bpm})
                    except Exception:
                        pass

            def on_raw(_raw: int) -> None:
                return

            parser = BrainLinkParser(on_eeg, on_extend, on_gyro, on_rr, on_raw)

            open_delay_s = float(os.environ.get("NSP_HR_COM_OPEN_DELAY_S", "0") or "0")
            open_delay_s = max(0.0, min(open_delay_s, 5.0))
            if open_delay_s > 0:
                time.sleep(open_delay_s)

            tries = int(os.environ.get("NSP_HR_COM_OPEN_RETRIES", "6") or "6")
            tries = max(1, min(tries, 20))
            pause_s = float(os.environ.get("NSP_HR_COM_OPEN_RETRY_PAUSE_S", "0.35") or "0.35")
            pause_s = max(0.05, min(pause_s, 3.0))

            ser = None
            last_exc: Exception | None = None
            for attempt in range(tries):
                if self._stop:
                    raise RuntimeError("остановка до открытия COM")
                try:
                    ser = serial.Serial(
                        port=self._port,
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
                        raise
                    time.sleep(pause_s)

            if ser is None:
                raise RuntimeError("не удалось открыть COM") from last_exc
            try:
                read_size = int(os.environ.get("NSP_HR_COM_READ_SIZE", "4096") or "4096")
                read_size = max(64, read_size)
                while not self._stop:
                    chunk = ser.read(read_size)
                    if chunk:
                        parser.parse(bytes(chunk))
                    else:
                        time.sleep(0.01)
            finally:
                try:
                    ser.close()
                except Exception:
                    pass
        except Exception as exc:  # pragma: no cover - hardware
            self.connectionFailed.emit(str(exc))
        finally:
            self.workerFinished.emit()

