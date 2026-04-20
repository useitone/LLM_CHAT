"""
PoC: meditation / concentration — phased hints, EEG from JSONL or live BLE, agent API + bus.
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import UTC, datetime
from io import TextIOBase
from pathlib import Path
from typing import Any, Iterator

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from neurosync_pro.agent.server import start_agent_api, stop_agent_api
from neurosync_pro.audio.engine import sine_pcm16_mono, write_wav_pcm16_mono
from neurosync_pro.bus import EventBus
from neurosync_pro.ui.ble_thread import BleNotifyThread


def _iter_eeg(path: Path) -> Iterator[tuple[int, int]]:
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                o: dict[str, Any] = json.loads(line)
            except json.JSONDecodeError:
                continue
            if o.get("type") != "eeg":
                continue
            e = o.get("eeg")
            if not isinstance(e, dict):
                continue
            yield int(e.get("attention", 0)), int(e.get("meditation", 0))


def _play_chime() -> None:
    if sys.platform == "win32":
        import winsound

        fd, tmp = tempfile.mkstemp(suffix=".wav")
        import os

        os.close(fd)
        p = Path(tmp)
        try:
            pcm = sine_pcm16_mono(880.0, 0.15, sample_rate=22050, volume=0.2)
            write_wav_pcm16_mono(p, pcm, sample_rate=22050)
            winsound.PlaySound(str(p), winsound.SND_FILENAME | winsound.SND_ASYNC)
        finally:
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass


def _play_brief_pitch_hz(freq_hz: float, volume: float) -> None:
    """Short tone for biofeedback (Windows async WAV)."""
    if sys.platform != "win32":
        return
    import winsound

    fd, tmp = tempfile.mkstemp(suffix=".wav")
    import os

    os.close(fd)
    p = Path(tmp)
    try:
        f = max(90.0, min(1200.0, freq_hz))
        v = max(0.02, min(0.35, volume))
        pcm = sine_pcm16_mono(f, 0.08, sample_rate=22050, volume=v)
        write_wav_pcm16_mono(p, pcm, sample_rate=22050)
        winsound.PlaySound(str(p), winsound.SND_FILENAME | winsound.SND_ASYNC)
    finally:
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass


class MeditationMainWindow(QMainWindow):
    PHASES = [
        ("Вдох 4 счёта…", 4000),
        ("Задержка…", 2000),
        ("Выдох 6 счётов…", 6000),
        ("Расслабление…", 2000),
    ]

    def __init__(
        self,
        jsonl_path: Path | None = None,
        *,
        ble_address: str | None = None,
        ble_init_hex: str = "",
        ble_duration_s: float | None = None,
        session_log_path: Path | None = None,
        auto_start_ble: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("NeuroSync Pro — медитация / концентрация (PoC)")
        self._bus = EventBus()
        self._api_server = None
        self._phase_index = 0
        self._ble_address = (ble_address or "").strip() or None
        self._ble_init_hex = ble_init_hex
        self._ble_duration_s = ble_duration_s
        self._ble_thread: BleNotifyThread | None = None
        self._session_log_path = session_log_path
        self._session_log_file: TextIOBase | None = None
        self._last_att = 0
        self._last_med = 0

        self._eeg_it: Iterator[tuple[int, int]] | None = None
        if self._ble_address is None and jsonl_path and jsonl_path.is_file():
            self._eeg_it = iter(_iter_eeg(jsonl_path))

        if session_log_path is not None:
            session_log_path.parent.mkdir(parents=True, exist_ok=True)
            self._session_log_file = session_log_path.open("a", encoding="utf-8")

        cw = QWidget()
        lay = QVBoxLayout(cw)
        self._hint = QLabel(self.PHASES[0][0])
        self._hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        f = self._hint.font()
        f.setPointSize(14)
        self._hint.setFont(f)
        self._att = QProgressBar()
        self._att.setRange(0, 100)
        self._att.setFormat("Attention %v")
        self._med = QProgressBar()
        self._med.setRange(0, 100)
        self._med.setFormat("Meditation %v")

        src_bits: list[str] = []
        if self._ble_address:
            src_bits.append(f"BLE: {self._ble_address}")
        elif self._eeg_it is not None:
            src_bits.append(f"JSONL: {jsonl_path}")
        else:
            src_bits.append("ЭЭГ: нет (только фазы дыхания)")
        self._src_label = QLabel(" / ".join(src_bits))

        self._api_cb = QCheckBox("Agent API :8765 (POST /v1/event JSON {topic, payload})")
        self._api_cb.toggled.connect(self._toggle_api)

        self._bio_cb = QCheckBox("Тон обратной связи (высота ~ Attention, громкость ~ Meditation)")
        self._bio_timer = QTimer(self)
        self._bio_timer.setInterval(700)
        self._bio_timer.timeout.connect(self._biofeedback_tick)

        self._ble_start = QPushButton("Старт BLE")
        self._ble_stop = QPushButton("Стоп BLE")
        self._ble_stop.setEnabled(False)
        self._ble_start.clicked.connect(self._start_ble)
        self._ble_stop.clicked.connect(self._stop_ble)

        self._status = QLabel("")
        lay.addWidget(self._hint)
        lay.addWidget(self._src_label)
        lay.addWidget(QLabel("Метрики:"))
        lay.addWidget(self._att)
        lay.addWidget(self._med)
        if self._ble_address:
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            h.addWidget(self._ble_start)
            h.addWidget(self._ble_stop)
            lay.addWidget(row)
        lay.addWidget(self._bio_cb)
        self._bio_cb.toggled.connect(self._toggle_biofeedback)
        lay.addWidget(self._api_cb)
        btn = QPushButton("Сигнал фазы (звук)")
        btn.clicked.connect(_play_chime)
        lay.addWidget(btn)
        lay.addWidget(self._status)
        self.setCentralWidget(cw)
        self.resize(520, 380)

        self._phase_timer = QTimer(self)
        self._phase_timer.timeout.connect(self._next_phase)
        self._start_phase_duration(self.PHASES[0][1])

        self._eeg_timer = QTimer(self)
        self._eeg_timer.timeout.connect(self._eeg_tick)
        if self._eeg_it is not None:
            self._eeg_timer.start(200)

        if auto_start_ble and self._ble_address:
            QTimer.singleShot(300, self._start_ble)

    def _toggle_biofeedback(self, on: bool) -> None:
        if on:
            self._bio_timer.start()
        else:
            self._bio_timer.stop()

    def _biofeedback_tick(self) -> None:
        att, med = self._last_att, self._last_med
        freq = 220.0 + float(att) * 4.5
        vol = 0.04 + 0.22 * (float(med) / 100.0)
        _play_brief_pitch_hz(freq, vol)

    def _append_session_log(self, att: int, med: int) -> None:
        fp = self._session_log_file
        if fp is None:
            return
        rec = {
            "type": "eeg",
            "timestamp_utc": datetime.now(UTC).isoformat(),
            "eeg": {"attention": att, "meditation": med},
        }
        fp.write(json.dumps(rec, ensure_ascii=False) + "\n")
        fp.flush()

    def _start_ble(self) -> None:
        if not self._ble_address or self._ble_thread is not None:
            return
        self._status.setText("BLE: подключение…")
        self._ble_start.setEnabled(False)
        self._ble_stop.setEnabled(True)
        th = BleNotifyThread(
            self._ble_address,
            init_hex=self._ble_init_hex,
            duration_s=self._ble_duration_s,
            parent=self,
        )
        th.metricsReady.connect(self._on_ble_metrics)
        th.connectionFailed.connect(self._on_ble_failed)
        th.workerFinished.connect(self._on_ble_worker_finished)
        self._ble_thread = th
        th.start()

    def _stop_ble(self) -> None:
        if self._ble_thread is not None:
            self._ble_thread.request_stop()
            self._status.setText("BLE: остановка…")

    def _on_ble_metrics(self, att: int, med: int) -> None:
        self._last_att = att
        self._last_med = med
        self._att.setValue(att)
        self._med.setValue(med)
        self._bus.publish("eeg.metrics", {"attention": att, "meditation": med})
        self._append_session_log(att, med)
        if self._status.text().startswith("BLE: подключение"):
            self._status.setText("BLE: поток активен")

    def _on_ble_failed(self, msg: str) -> None:
        self._status.setText(f"BLE ошибка: {msg}")

    def _on_ble_worker_finished(self) -> None:
        self._ble_thread = None
        self._ble_start.setEnabled(True)
        self._ble_stop.setEnabled(False)
        if not str(self._status.text()).startswith("BLE ошибка"):
            self._status.setText("BLE: отключено")

    def _toggle_api(self, on: bool) -> None:
        if on:
            self._api_server, _ = start_agent_api(self._bus, port=8765)
            self._status.setText("API: http://127.0.0.1:8765/v1/event")
        else:
            if self._api_server is not None:
                stop_agent_api(self._api_server)
                self._api_server = None
            if self._ble_thread and self._ble_thread.isRunning():
                self._status.setText("BLE: поток активен")
            else:
                self._status.setText("API выключен")

    def _start_phase_duration(self, ms: int) -> None:
        self._phase_timer.stop()
        self._phase_timer.start(ms)

    def _next_phase(self) -> None:
        self._phase_timer.stop()
        _play_chime()
        self._phase_index = (self._phase_index + 1) % len(self.PHASES)
        text, dur = self.PHASES[self._phase_index]
        self._hint.setText(text)
        self._bus.publish("meditation.phase", {"phase": self._phase_index, "text": text})
        self._start_phase_duration(dur)

    def _eeg_tick(self) -> None:
        if self._eeg_it is None:
            return
        try:
            att, med = next(self._eeg_it)
        except StopIteration:
            self._eeg_timer.stop()
            return
        self._last_att = att
        self._last_med = med
        self._att.setValue(att)
        self._med.setValue(med)
        self._bus.publish("eeg.metrics", {"attention": att, "meditation": med})
        self._append_session_log(att, med)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self._bio_timer.stop()
        if self._ble_thread is not None:
            self._ble_thread.request_stop()
            self._ble_thread.wait(8000)
            self._ble_thread = None
        if self._api_server is not None:
            stop_agent_api(self._api_server)
            self._api_server = None
        if self._session_log_file is not None:
            try:
                self._session_log_file.close()
            except OSError:
                pass
            self._session_log_file = None
        super().closeEvent(event)


def run_meditation_poc(
    jsonl_path: Path | None = None,
    *,
    ble_address: str | None = None,
    ble_init_hex: str = "",
    ble_duration_s: float | None = None,
    session_log_path: Path | None = None,
    auto_start_ble: bool = False,
) -> int:
    app = QApplication.instance() or QApplication([])
    w = MeditationMainWindow(
        jsonl_path,
        ble_address=ble_address,
        ble_init_hex=ble_init_hex,
        ble_duration_s=ble_duration_s,
        session_log_path=session_log_path,
        auto_start_ble=auto_start_ble,
    )
    w.show()
    return int(app.exec())
