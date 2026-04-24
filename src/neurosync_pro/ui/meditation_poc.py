"""
PoC: meditation / concentration — phased hints, EEG from JSONL or live BLE, agent API + bus.
"""

from __future__ import annotations

import math
import json
import random
import sys
import tempfile
import time
from collections import deque
from datetime import UTC, datetime
from io import TextIOBase
from pathlib import Path
from typing import Any, Iterator

from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

try:
    from PySide6.QtCharts import QChart, QChartView, QLineSeries, QValueAxis
except Exception:  # pragma: no cover - optional addon
    QChart = QChartView = QLineSeries = QValueAxis = None  # type: ignore[misc,assignment]

from neurosync_pro.agent.server import start_agent_api, stop_agent_api
from neurosync_pro.bus import EventBus
from neurosync_pro.eeg.ble_stream import normalize_ble_address
from neurosync_pro.ui.ble_thread import BleNotifyThread, BleScanThread
try:  # optional audio extras
    from neurosync_pro.audio.stream import StreamConfig, ToneSweepStream
except Exception:  # pragma: no cover
    StreamConfig = None  # type: ignore[misc,assignment]
    ToneSweepStream = None  # type: ignore[misc,assignment]


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


class MeditationMainWindow(QMainWindow):
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
        self._ble_address = (ble_address or "").strip() or None
        self._ble_init_hex = ble_init_hex
        self._ble_duration_s = ble_duration_s
        self._ble_thread: BleNotifyThread | None = None
        self._ble_scan_thread: BleScanThread | None = None
        self._session_log_path = session_log_path
        self._session_log_file: TextIOBase | None = None
        self._session_log_active = session_log_path is not None
        self._last_att = 0
        self._last_med = 0
        self._ble_selected_rssi: int | None = None
        self._last_signal_quality: int | None = None
        self._last_bands: dict[str, int] | None = None
        self._bands_full = False
        self._bands_last_ui_at = 0.0
        self._bands_min_ui_s = 0.25
        self._rssi_scan_thread: BleScanThread | None = None
        self._rssi_timer = QTimer(self)
        self._rssi_timer.setInterval(6000)
        self._rssi_timer.timeout.connect(self._tick_rssi_scan)

        # EEG → Tone (audio biofeedback)
        self._eeg_tone_available = ToneSweepStream is not None
        self._eeg_tone_enabled = False
        self._eeg_tone_stream = None
        self._eeg_tone_min_hz = 100.0
        self._eeg_tone_max_hz = 1000.0
        self._eeg_tone_min_vol = 0.02
        self._eeg_tone_max_vol = 0.20
        self._eeg_tone_alpha = 0.18  # EMA smoothing
        self._eeg_tone_f_hz = 440.0
        self._eeg_tone_vol = 0.0
        self._eeg_tone_last_apply = 0.0
        self._eeg_tone_apply_min_s = 0.10  # 10 Hz
        self._eeg_tone_freq_src = "attention"  # attention|meditation
        self._eeg_tone_vol_src = "meditation"  # off|attention|meditation
        self._eeg_tone_fixed_vol = 0.08
        self._eeg_tone_mode = "mono"  # mono|stereo

        # EEG → Tone stereo mapping (L/R independent)
        self._eeg_tone_l_min_hz = 100.0
        self._eeg_tone_l_max_hz = 1000.0
        self._eeg_tone_l_freq_src = "attention"  # attention|meditation|off
        self._eeg_tone_l_fixed_hz = 440.0
        self._eeg_tone_l_min_vol = 0.02
        self._eeg_tone_l_max_vol = 0.20
        self._eeg_tone_l_vol_src = "off"  # off|attention|meditation|freq_inv
        self._eeg_tone_l_fixed_vol = 0.08
        self._eeg_tone_r_min_hz = 100.0
        self._eeg_tone_r_max_hz = 1000.0
        self._eeg_tone_r_freq_src = "meditation"  # attention|meditation|off
        self._eeg_tone_r_fixed_hz = 440.0
        self._eeg_tone_r_min_vol = 0.02
        self._eeg_tone_r_max_vol = 0.20
        self._eeg_tone_r_vol_src = "off"  # off|attention|meditation|freq_inv
        self._eeg_tone_r_fixed_vol = 0.08
        self._eeg_tone_f_l = 440.0
        self._eeg_tone_f_r = 440.0
        self._eeg_tone_v_l = 0.0
        self._eeg_tone_v_r = 0.0

        # EEG → Binaural (stereo, random delta)
        self._eeg_bin_available = ToneSweepStream is not None
        self._eeg_bin_enabled = False
        self._eeg_bin_stream = None
        self._eeg_bin_base_min_hz = 200.0
        self._eeg_bin_base_max_hz = 500.0
        self._eeg_bin_base_src = "attention"  # attention|meditation
        self._eeg_bin_delta_min_hz = 2.0
        self._eeg_bin_delta_max_hz = 20.0
        self._eeg_bin_delta_update_s = 5.0
        self._eeg_bin_fixed_vol = 0.08
        self._eeg_bin_alpha = 0.18
        self._eeg_bin_base_hz = 300.0
        self._eeg_bin_delta_hz = 8.0
        self._eeg_bin_last_delta_at = 0.0

        # Generator monitor (UI-only; mirrors what we send to audio engine).
        self._genmon_text = ""

        # Link/quality stats (session time, Hz, last sample age).
        self._session_started_at: float | None = None
        self._last_metric_at: float | None = None
        self._metric_times = deque(maxlen=5000)
        self._stats_timer = QTimer(self)
        self._stats_timer.setInterval(1000)
        self._stats_timer.timeout.connect(self._update_stats_line)

        # Simple rolling metrics plot (optional, requires PySide6.QtCharts).
        self._plot_available = QChart is not None
        self._plot_enabled = False
        self._plot_window_s = 120.0
        self._t0 = time.monotonic()
        self._t = deque(maxlen=2000)  # seconds since start
        self._att_hist = deque(maxlen=2000)
        self._med_hist = deque(maxlen=2000)
        self._plot_dirty = False
        self._plot_last_redraw = 0.0
        self._plot_min_redraw_s = 0.15  # ~6-7 FPS max
        self._series_att = None
        self._series_med = None
        self._axis_x = None
        self._axis_y = None
        self._chart_view = None

        self._eeg_it: Iterator[tuple[int, int]] | None = None
        if self._ble_address is None and jsonl_path and jsonl_path.is_file():
            self._eeg_it = iter(_iter_eeg(jsonl_path))

        if session_log_path is not None:
            session_log_path.parent.mkdir(parents=True, exist_ok=True)
            # CLI-provided log path: keep append semantics (explicit user choice).
            self._session_log_file = session_log_path.open("a", encoding="utf-8")

        cw = QWidget()
        root = QHBoxLayout(cw)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter, 1)

        # Left: controls (scrollable).
        left_widget = QWidget()
        left_lay = QVBoxLayout(left_widget)
        left_lay.setContentsMargins(8, 8, 8, 8)
        left_lay.setSpacing(6)
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setWidget(left_widget)

        # Middle: indicators/plots (non-scroll; uses splitters/tabs later).
        mid_widget = QWidget()
        mid_lay = QVBoxLayout(mid_widget)
        mid_lay.setContentsMargins(8, 8, 8, 8)
        mid_lay.setSpacing(6)

        # Right: programmer placeholder.
        right_widget = QWidget()
        right_lay = QVBoxLayout(right_widget)
        right_lay.setContentsMargins(8, 8, 8, 8)
        right_lay.setSpacing(6)
        right_lay.addWidget(QLabel("Программатор (скоро)"))
        right_lay.addStretch(1)

        splitter.addWidget(left_scroll)
        splitter.addWidget(mid_widget)
        splitter.addWidget(right_widget)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 2)
        splitter.setStretchFactor(2, 2)
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

        self._plot_cb = QCheckBox("График (Attention / Meditation)")
        self._plot_cb.setEnabled(self._plot_available)
        if not self._plot_available:
            self._plot_cb.setToolTip("PySide6.QtCharts недоступен в текущей установке.")
        self._plot_cb.toggled.connect(self._toggle_plot)
        self._plot_clear_btn = QPushButton("Очистить график")
        self._plot_clear_btn.setEnabled(self._plot_available)
        self._plot_clear_btn.clicked.connect(self._clear_plot)
        self._plot_clear_btn.setVisible(False)
        self._plot_timer = QTimer(self)
        self._plot_timer.setInterval(120)
        self._plot_timer.timeout.connect(self._plot_tick)

        # Session logging controls (new session file per run; avoids append confusion).
        self._session_btn = QPushButton("Новая сессия (лог)")
        self._session_btn.clicked.connect(self._new_session_log)
        self._record_btn = QPushButton("Запись: Вкл")
        self._record_btn.setCheckable(True)
        self._record_btn.setChecked(self._session_log_active)
        self._record_btn.toggled.connect(self._toggle_recording)
        if not self._session_log_active:
            self._record_btn.setText("Запись: Выкл")
        if self._session_log_path is not None:
            self._session_btn.setEnabled(False)
            self._session_btn.setToolTip("Для фиксированного --session-log новая сессия не создаётся (append).")

        self._eeg_tone_cb = QCheckBox("EEG → Tone (A=частота, M=громкость)")
        self._eeg_tone_cb.setEnabled(self._eeg_tone_available)
        if not self._eeg_tone_available:
            self._eeg_tone_cb.setToolTip("Установите audio extras: pip install -e \".[audio]\"")
        self._eeg_tone_cb.toggled.connect(self._toggle_eeg_tone)

        self._eeg_tone_box = QGroupBox("EEG → Tone настройки")
        self._eeg_tone_box.setEnabled(self._eeg_tone_available)
        self._eeg_tone_box.setVisible(False)
        form = QFormLayout(self._eeg_tone_box)
        self._tone_mode = QComboBox()
        self._tone_mode.addItem("Mono", userData="mono")
        self._tone_mode.addItem("Stereo (L/R)", userData="stereo")
        self._tone_mode.setCurrentIndex(0)
        self._tone_mode.currentIndexChanged.connect(self._tone_mode_changed)
        self._tone_min_hz = QDoubleSpinBox()
        self._tone_min_hz.setRange(1.0, 20000.0)
        self._tone_min_hz.setValue(self._eeg_tone_min_hz)
        self._tone_min_hz.setSuffix(" Hz")
        self._tone_min_hz.valueChanged.connect(lambda v: setattr(self, "_eeg_tone_min_hz", float(v)))
        self._tone_max_hz = QDoubleSpinBox()
        self._tone_max_hz.setRange(1.0, 20000.0)
        self._tone_max_hz.setValue(self._eeg_tone_max_hz)
        self._tone_max_hz.setSuffix(" Hz")
        self._tone_max_hz.valueChanged.connect(lambda v: setattr(self, "_eeg_tone_max_hz", float(v)))
        self._tone_min_vol = QDoubleSpinBox()
        self._tone_min_vol.setRange(0.0, 1.0)
        self._tone_min_vol.setSingleStep(0.01)
        self._tone_min_vol.setValue(self._eeg_tone_min_vol)
        self._tone_min_vol.valueChanged.connect(lambda v: setattr(self, "_eeg_tone_min_vol", float(v)))
        self._tone_max_vol = QDoubleSpinBox()
        self._tone_max_vol.setRange(0.0, 1.0)
        self._tone_max_vol.setSingleStep(0.01)
        self._tone_max_vol.setValue(self._eeg_tone_max_vol)
        self._tone_max_vol.valueChanged.connect(lambda v: setattr(self, "_eeg_tone_max_vol", float(v)))

        self._tone_freq_src = QComboBox()
        self._tone_freq_src.addItem("Attention → Hz", userData="attention")
        self._tone_freq_src.addItem("Meditation → Hz", userData="meditation")
        self._tone_freq_src.currentIndexChanged.connect(self._tone_freq_src_changed)

        self._tone_vol_src = QComboBox()
        self._tone_vol_src.addItem("Volume: Off (fixed)", userData="off")
        self._tone_vol_src.addItem("Meditation → Volume", userData="meditation")
        self._tone_vol_src.addItem("Attention → Volume", userData="attention")
        self._tone_vol_src.setCurrentIndex(1)
        self._tone_vol_src.currentIndexChanged.connect(self._tone_vol_src_changed)

        self._tone_fixed_vol = QDoubleSpinBox()
        self._tone_fixed_vol.setRange(0.0, 1.0)
        self._tone_fixed_vol.setSingleStep(0.01)
        self._tone_fixed_vol.setValue(self._eeg_tone_fixed_vol)
        self._tone_fixed_vol.valueChanged.connect(lambda v: setattr(self, "_eeg_tone_fixed_vol", float(v)))
        self._tone_fixed_vol.setEnabled(False)

        self._eeg_tone_stereo_box = QGroupBox("Stereo (L/R) mapping")
        self._eeg_tone_stereo_box.setVisible(False)
        stereo_lay = QHBoxLayout(self._eeg_tone_stereo_box)
        stereo_lay.setContentsMargins(0, 0, 0, 0)

        self._tone_l_box = QGroupBox("Left (L)")
        lf = QFormLayout(self._tone_l_box)
        self._tone_l_min_hz = QDoubleSpinBox()
        self._tone_l_min_hz.setRange(1.0, 20000.0)
        self._tone_l_min_hz.setValue(self._eeg_tone_l_min_hz)
        self._tone_l_min_hz.setSuffix(" Hz")
        self._tone_l_min_hz.valueChanged.connect(lambda v: setattr(self, "_eeg_tone_l_min_hz", float(v)))
        self._tone_l_max_hz = QDoubleSpinBox()
        self._tone_l_max_hz.setRange(1.0, 20000.0)
        self._tone_l_max_hz.setValue(self._eeg_tone_l_max_hz)
        self._tone_l_max_hz.setSuffix(" Hz")
        self._tone_l_max_hz.valueChanged.connect(lambda v: setattr(self, "_eeg_tone_l_max_hz", float(v)))
        self._tone_l_freq_src = QComboBox()
        self._tone_l_freq_src.addItem("Attention → Hz", userData="attention")
        self._tone_l_freq_src.addItem("Meditation → Hz", userData="meditation")
        self._tone_l_freq_src.addItem("Off (fixed Hz)", userData="off")
        self._tone_l_freq_src.setCurrentIndex(0)
        self._tone_l_freq_src.currentIndexChanged.connect(self._tone_l_freq_src_changed)
        self._tone_l_fixed_hz = QDoubleSpinBox()
        self._tone_l_fixed_hz.setRange(1.0, 20000.0)
        self._tone_l_fixed_hz.setValue(self._eeg_tone_l_fixed_hz)
        self._tone_l_fixed_hz.setSuffix(" Hz")
        self._tone_l_fixed_hz.valueChanged.connect(lambda v: setattr(self, "_eeg_tone_l_fixed_hz", float(v)))
        self._tone_l_fixed_hz.setEnabled(False)
        self._tone_l_min_vol = QDoubleSpinBox()
        self._tone_l_min_vol.setRange(0.0, 1.0)
        self._tone_l_min_vol.setSingleStep(0.01)
        self._tone_l_min_vol.setValue(self._eeg_tone_l_min_vol)
        self._tone_l_min_vol.valueChanged.connect(lambda v: setattr(self, "_eeg_tone_l_min_vol", float(v)))
        self._tone_l_max_vol = QDoubleSpinBox()
        self._tone_l_max_vol.setRange(0.0, 1.0)
        self._tone_l_max_vol.setSingleStep(0.01)
        self._tone_l_max_vol.setValue(self._eeg_tone_l_max_vol)
        self._tone_l_max_vol.valueChanged.connect(lambda v: setattr(self, "_eeg_tone_l_max_vol", float(v)))
        self._tone_l_vol_src = QComboBox()
        self._tone_l_vol_src.addItem("Volume: Off (fixed)", userData="off")
        self._tone_l_vol_src.addItem("Freq → Volume (inverted, log)", userData="freq_inv")
        self._tone_l_vol_src.addItem("Meditation → Volume", userData="meditation")
        self._tone_l_vol_src.addItem("Attention → Volume", userData="attention")
        self._tone_l_vol_src.setCurrentIndex(0)
        self._tone_l_vol_src.currentIndexChanged.connect(self._tone_l_vol_src_changed)
        self._tone_l_fixed_vol = QDoubleSpinBox()
        self._tone_l_fixed_vol.setRange(0.0, 1.0)
        self._tone_l_fixed_vol.setSingleStep(0.01)
        self._tone_l_fixed_vol.setValue(self._eeg_tone_l_fixed_vol)
        self._tone_l_fixed_vol.valueChanged.connect(lambda v: setattr(self, "_eeg_tone_l_fixed_vol", float(v)))
        self._tone_l_fixed_vol.setEnabled(True)
        lf.addRow("Hz min", self._tone_l_min_hz)
        lf.addRow("Hz max", self._tone_l_max_hz)
        lf.addRow("Freq source", self._tone_l_freq_src)
        lf.addRow("Fixed Hz", self._tone_l_fixed_hz)
        lf.addRow("Vol min", self._tone_l_min_vol)
        lf.addRow("Vol max", self._tone_l_max_vol)
        lf.addRow("Vol source", self._tone_l_vol_src)
        lf.addRow("Fixed vol", self._tone_l_fixed_vol)

        self._tone_r_box = QGroupBox("Right (R)")
        rf = QFormLayout(self._tone_r_box)
        self._tone_r_min_hz = QDoubleSpinBox()
        self._tone_r_min_hz.setRange(1.0, 20000.0)
        self._tone_r_min_hz.setValue(self._eeg_tone_r_min_hz)
        self._tone_r_min_hz.setSuffix(" Hz")
        self._tone_r_min_hz.valueChanged.connect(lambda v: setattr(self, "_eeg_tone_r_min_hz", float(v)))
        self._tone_r_max_hz = QDoubleSpinBox()
        self._tone_r_max_hz.setRange(1.0, 20000.0)
        self._tone_r_max_hz.setValue(self._eeg_tone_r_max_hz)
        self._tone_r_max_hz.setSuffix(" Hz")
        self._tone_r_max_hz.valueChanged.connect(lambda v: setattr(self, "_eeg_tone_r_max_hz", float(v)))
        self._tone_r_freq_src = QComboBox()
        self._tone_r_freq_src.addItem("Attention → Hz", userData="attention")
        self._tone_r_freq_src.addItem("Meditation → Hz", userData="meditation")
        self._tone_r_freq_src.addItem("Off (fixed Hz)", userData="off")
        self._tone_r_freq_src.setCurrentIndex(1)
        self._tone_r_freq_src.currentIndexChanged.connect(self._tone_r_freq_src_changed)
        self._tone_r_fixed_hz = QDoubleSpinBox()
        self._tone_r_fixed_hz.setRange(1.0, 20000.0)
        self._tone_r_fixed_hz.setValue(self._eeg_tone_r_fixed_hz)
        self._tone_r_fixed_hz.setSuffix(" Hz")
        self._tone_r_fixed_hz.valueChanged.connect(lambda v: setattr(self, "_eeg_tone_r_fixed_hz", float(v)))
        self._tone_r_fixed_hz.setEnabled(False)
        self._tone_r_min_vol = QDoubleSpinBox()
        self._tone_r_min_vol.setRange(0.0, 1.0)
        self._tone_r_min_vol.setSingleStep(0.01)
        self._tone_r_min_vol.setValue(self._eeg_tone_r_min_vol)
        self._tone_r_min_vol.valueChanged.connect(lambda v: setattr(self, "_eeg_tone_r_min_vol", float(v)))
        self._tone_r_max_vol = QDoubleSpinBox()
        self._tone_r_max_vol.setRange(0.0, 1.0)
        self._tone_r_max_vol.setSingleStep(0.01)
        self._tone_r_max_vol.setValue(self._eeg_tone_r_max_vol)
        self._tone_r_max_vol.valueChanged.connect(lambda v: setattr(self, "_eeg_tone_r_max_vol", float(v)))
        self._tone_r_vol_src = QComboBox()
        self._tone_r_vol_src.addItem("Volume: Off (fixed)", userData="off")
        self._tone_r_vol_src.addItem("Freq → Volume (inverted, log)", userData="freq_inv")
        self._tone_r_vol_src.addItem("Meditation → Volume", userData="meditation")
        self._tone_r_vol_src.addItem("Attention → Volume", userData="attention")
        self._tone_r_vol_src.setCurrentIndex(0)
        self._tone_r_vol_src.currentIndexChanged.connect(self._tone_r_vol_src_changed)
        self._tone_r_fixed_vol = QDoubleSpinBox()
        self._tone_r_fixed_vol.setRange(0.0, 1.0)
        self._tone_r_fixed_vol.setSingleStep(0.01)
        self._tone_r_fixed_vol.setValue(self._eeg_tone_r_fixed_vol)
        self._tone_r_fixed_vol.valueChanged.connect(lambda v: setattr(self, "_eeg_tone_r_fixed_vol", float(v)))
        self._tone_r_fixed_vol.setEnabled(True)
        rf.addRow("Hz min", self._tone_r_min_hz)
        rf.addRow("Hz max", self._tone_r_max_hz)
        rf.addRow("Freq source", self._tone_r_freq_src)
        rf.addRow("Fixed Hz", self._tone_r_fixed_hz)
        rf.addRow("Vol min", self._tone_r_min_vol)
        rf.addRow("Vol max", self._tone_r_max_vol)
        rf.addRow("Vol source", self._tone_r_vol_src)
        rf.addRow("Fixed vol", self._tone_r_fixed_vol)

        stereo_lay.addWidget(self._tone_l_box, 1)
        stereo_lay.addWidget(self._tone_r_box, 1)

        form.addRow("Mode", self._tone_mode)
        form.addRow("Hz min", self._tone_min_hz)
        form.addRow("Hz max", self._tone_max_hz)
        form.addRow("Freq source", self._tone_freq_src)
        form.addRow("Vol min", self._tone_min_vol)
        form.addRow("Vol max", self._tone_max_vol)
        form.addRow("Vol source", self._tone_vol_src)
        form.addRow("Fixed vol", self._tone_fixed_vol)
        form.addRow(self._eeg_tone_stereo_box)

        self._eeg_bin_cb = QCheckBox("EEG → Binaural (stereo, random Δf)")
        self._eeg_bin_cb.setEnabled(self._eeg_bin_available)
        if not self._eeg_bin_available:
            self._eeg_bin_cb.setToolTip("Установите audio extras: pip install -e \".[audio]\"")
        self._eeg_bin_cb.toggled.connect(self._toggle_eeg_binaural)

        self._eeg_bin_box = QGroupBox("EEG → Binaural настройки")
        self._eeg_bin_box.setEnabled(self._eeg_bin_available)
        self._eeg_bin_box.setVisible(False)
        bform = QFormLayout(self._eeg_bin_box)
        self._bin_base_min = QDoubleSpinBox()
        self._bin_base_min.setRange(1.0, 20000.0)
        self._bin_base_min.setValue(self._eeg_bin_base_min_hz)
        self._bin_base_min.setSuffix(" Hz")
        self._bin_base_min.valueChanged.connect(lambda v: setattr(self, "_eeg_bin_base_min_hz", float(v)))
        self._bin_base_max = QDoubleSpinBox()
        self._bin_base_max.setRange(1.0, 20000.0)
        self._bin_base_max.setValue(self._eeg_bin_base_max_hz)
        self._bin_base_max.setSuffix(" Hz")
        self._bin_base_max.valueChanged.connect(lambda v: setattr(self, "_eeg_bin_base_max_hz", float(v)))
        self._bin_base_src = QComboBox()
        self._bin_base_src.addItem("Attention → base Hz", userData="attention")
        self._bin_base_src.addItem("Meditation → base Hz", userData="meditation")
        self._bin_base_src.currentIndexChanged.connect(self._bin_base_src_changed)
        self._bin_delta_min = QDoubleSpinBox()
        self._bin_delta_min.setRange(0.1, 200.0)
        self._bin_delta_min.setValue(self._eeg_bin_delta_min_hz)
        self._bin_delta_min.setSuffix(" Hz")
        self._bin_delta_min.valueChanged.connect(lambda v: setattr(self, "_eeg_bin_delta_min_hz", float(v)))
        self._bin_delta_max = QDoubleSpinBox()
        self._bin_delta_max.setRange(0.1, 200.0)
        self._bin_delta_max.setValue(self._eeg_bin_delta_max_hz)
        self._bin_delta_max.setSuffix(" Hz")
        self._bin_delta_max.valueChanged.connect(lambda v: setattr(self, "_eeg_bin_delta_max_hz", float(v)))
        self._bin_delta_update = QDoubleSpinBox()
        self._bin_delta_update.setRange(0.5, 60.0)
        self._bin_delta_update.setValue(self._eeg_bin_delta_update_s)
        self._bin_delta_update.setSuffix(" s")
        self._bin_delta_update.valueChanged.connect(lambda v: setattr(self, "_eeg_bin_delta_update_s", float(v)))
        self._bin_vol = QDoubleSpinBox()
        self._bin_vol.setRange(0.0, 1.0)
        self._bin_vol.setSingleStep(0.01)
        self._bin_vol.setValue(self._eeg_bin_fixed_vol)
        self._bin_vol.valueChanged.connect(lambda v: setattr(self, "_eeg_bin_fixed_vol", float(v)))

        bform.addRow("Base Hz min", self._bin_base_min)
        bform.addRow("Base Hz max", self._bin_base_max)
        bform.addRow("Base source", self._bin_base_src)
        bform.addRow("Δf min", self._bin_delta_min)
        bform.addRow("Δf max", self._bin_delta_max)
        bform.addRow("Δf update", self._bin_delta_update)
        bform.addRow("Volume", self._bin_vol)

        # BLE scan controls (when address not passed explicitly).
        self._ble_scan_btn = QPushButton("Сканировать BrainLink")
        self._ble_scan_btn.clicked.connect(self._scan_ble)
        self._ble_devices = QComboBox()
        self._ble_devices.setEnabled(False)
        self._ble_devices.currentIndexChanged.connect(self._select_ble_device)

        self._ble_start = QPushButton("Старт BLE")
        self._ble_stop = QPushButton("Стоп BLE")
        self._ble_stop.setEnabled(False)
        self._ble_start.clicked.connect(self._start_ble)
        self._ble_stop.clicked.connect(self._stop_ble)
        self._ble_start.setEnabled(bool(self._ble_address))

        self._status = QLabel("")
        self._stats = QLabel("")

        # Left panel content.
        left_lay.addWidget(self._src_label)
        left_lay.addWidget(self._stats)
        left_lay.addWidget(QLabel("Метрики:"))
        left_lay.addWidget(self._att)
        left_lay.addWidget(self._med)
        sess_row = QWidget()
        sess_lay = QHBoxLayout(sess_row)
        sess_lay.setContentsMargins(0, 0, 0, 0)
        sess_lay.addWidget(self._session_btn)
        sess_lay.addWidget(self._record_btn)
        sess_lay.addStretch(1)
        left_lay.addWidget(sess_row)
        left_lay.addWidget(self._eeg_tone_cb)
        left_lay.addWidget(self._eeg_tone_box)
        left_lay.addWidget(self._eeg_bin_cb)
        left_lay.addWidget(self._eeg_bin_box)

        # Middle panel: plot controls + plots.
        plot_row = QWidget()
        plot_row_lay = QHBoxLayout(plot_row)
        plot_row_lay.setContentsMargins(0, 0, 0, 0)
        plot_row_lay.addWidget(self._plot_cb)
        plot_row_lay.addStretch(1)
        plot_row_lay.addWidget(self._plot_clear_btn)
        mid_lay.addWidget(plot_row)
        if self._plot_available:
            self._init_plot_widgets(mid_lay)

        # Bands: compact by default (cheap UI/CPU) with optional Full toggle.
        self._bands_box = QGroupBox("Bands (Compact)")
        mid_lay.addWidget(self._bands_box)
        bform = QFormLayout(self._bands_box)
        self._bands_full_cb = QCheckBox("Full (8 линий)")
        self._bands_full_cb.setChecked(False)
        self._bands_full_cb.toggled.connect(self._toggle_bands_full)
        self._bands_line = QLabel("нет данных")
        self._bands_line.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        bform.addRow("", self._bands_full_cb)
        bform.addRow("Bands", self._bands_line)

        self._genmon_box = QGroupBox("Generator monitor")
        mid_lay.addWidget(self._genmon_box)
        gm_form = QFormLayout(self._genmon_box)
        self._genmon_line = QLabel("idle")
        self._genmon_line.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        gm_form.addRow("State", self._genmon_line)
        mid_lay.addStretch(1)
        if self._ble_address:
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            h.addWidget(self._ble_start)
            h.addWidget(self._ble_stop)
            left_lay.addWidget(row)
        else:
            scan_row = QWidget()
            sh = QHBoxLayout(scan_row)
            sh.setContentsMargins(0, 0, 0, 0)
            sh.addWidget(self._ble_scan_btn)
            sh.addWidget(self._ble_devices, 1)
            left_lay.addWidget(scan_row)
            btn_row = QWidget()
            bh = QHBoxLayout(btn_row)
            bh.setContentsMargins(0, 0, 0, 0)
            bh.addWidget(self._ble_start)
            bh.addWidget(self._ble_stop)
            left_lay.addWidget(btn_row)
        left_lay.addWidget(self._api_cb)
        left_lay.addWidget(self._status)
        left_lay.addStretch(1)
        self.setCentralWidget(cw)
        self.resize(1180, 700)

        self._eeg_timer = QTimer(self)
        self._eeg_timer.timeout.connect(self._eeg_tick)
        if self._eeg_it is not None:
            self._eeg_timer.start(200)

        if auto_start_ble and self._ble_address:
            QTimer.singleShot(300, self._start_ble)
        self._stats_timer.start()

    def _toggle_eeg_tone(self, on: bool) -> None:
        self._eeg_tone_enabled = bool(on)
        self._eeg_tone_box.setVisible(self._eeg_tone_enabled)
        self._eeg_tone_stereo_box.setVisible(self._eeg_tone_enabled and self._eeg_tone_mode == "stereo")
        if self._eeg_tone_enabled and self._eeg_bin_enabled:
            self._eeg_bin_cb.setChecked(False)
        if not self._eeg_tone_enabled:
            self._stop_eeg_tone()

    def _tone_freq_src_changed(self, _idx: int) -> None:
        data = self._tone_freq_src.currentData()
        if data in ("attention", "meditation"):
            self._eeg_tone_freq_src = str(data)

    def _tone_vol_src_changed(self, _idx: int) -> None:
        data = self._tone_vol_src.currentData()
        if data in ("off", "attention", "meditation"):
            self._eeg_tone_vol_src = str(data)
        self._tone_fixed_vol.setEnabled(self._eeg_tone_vol_src == "off")

    def _tone_mode_changed(self, _idx: int) -> None:
        data = self._tone_mode.currentData()
        if data in ("mono", "stereo"):
            self._eeg_tone_mode = str(data)
        self._eeg_tone_stereo_box.setVisible(self._eeg_tone_enabled and self._eeg_tone_mode == "stereo")
        self._stop_eeg_tone()

    def _tone_l_freq_src_changed(self, _idx: int) -> None:
        data = self._tone_l_freq_src.currentData()
        if data in ("attention", "meditation", "off"):
            self._eeg_tone_l_freq_src = str(data)
        self._tone_l_fixed_hz.setEnabled(self._eeg_tone_l_freq_src == "off")

    def _tone_r_freq_src_changed(self, _idx: int) -> None:
        data = self._tone_r_freq_src.currentData()
        if data in ("attention", "meditation", "off"):
            self._eeg_tone_r_freq_src = str(data)
        self._tone_r_fixed_hz.setEnabled(self._eeg_tone_r_freq_src == "off")

    def _tone_l_vol_src_changed(self, _idx: int) -> None:
        data = self._tone_l_vol_src.currentData()
        if data in ("off", "attention", "meditation", "freq_inv"):
            self._eeg_tone_l_vol_src = str(data)
        self._tone_l_fixed_vol.setEnabled(self._eeg_tone_l_vol_src == "off")

    def _tone_r_vol_src_changed(self, _idx: int) -> None:
        data = self._tone_r_vol_src.currentData()
        if data in ("off", "attention", "meditation", "freq_inv"):
            self._eeg_tone_r_vol_src = str(data)
        self._tone_r_fixed_vol.setEnabled(self._eeg_tone_r_vol_src == "off")

    def _toggle_eeg_binaural(self, on: bool) -> None:
        self._eeg_bin_enabled = bool(on)
        self._eeg_bin_box.setVisible(self._eeg_bin_enabled)
        if self._eeg_bin_enabled and self._eeg_tone_enabled:
            self._eeg_tone_cb.setChecked(False)
        if not self._eeg_bin_enabled:
            self._stop_eeg_binaural()

    def _bin_base_src_changed(self, _idx: int) -> None:
        data = self._bin_base_src.currentData()
        if data in ("attention", "meditation"):
            self._eeg_bin_base_src = str(data)

    def _ensure_eeg_binaural_stream(self) -> bool:
        if not self._eeg_bin_available:
            return False
        if self._eeg_bin_stream is None:
            try:
                self._eeg_bin_stream = ToneSweepStream(StreamConfig(sample_rate=48000, channels=2))
                self._eeg_bin_stream.set_fades(0.02, 0.08)
                self._eeg_bin_stream.start()
            except Exception as exc:
                self._status.setText(f"Audio ошибка: {exc}")
                self._eeg_bin_stream = None
                self._eeg_bin_cb.setChecked(False)
                return False
        return True

    def _stop_eeg_binaural(self) -> None:
        st = self._eeg_bin_stream
        self._eeg_bin_stream = None
        if st is not None:
            try:
                st.stop()
            except Exception:
                pass

    def _ensure_eeg_tone_stream(self, *, channels: int) -> bool:
        if not self._eeg_tone_available:
            return False
        if self._eeg_tone_stream is None or int(getattr(self._eeg_tone_stream, "cfg").channels) != int(channels):
            self._stop_eeg_tone()
            try:
                self._eeg_tone_stream = ToneSweepStream(StreamConfig(sample_rate=48000, channels=int(channels)))
                self._eeg_tone_stream.set_fades(0.02, 0.08)
                self._eeg_tone_stream.start()
            except Exception as exc:
                self._status.setText(f"Audio ошибка: {exc}")
                self._eeg_tone_stream = None
                self._eeg_tone_cb.setChecked(False)
                return False
        return True

    def _stop_eeg_tone(self) -> None:
        st = self._eeg_tone_stream
        self._eeg_tone_stream = None
        if st is not None:
            try:
                st.stop()
            except Exception:
                pass

    def _toggle_recording(self, on: bool) -> None:
        self._session_log_active = bool(on)
        self._record_btn.setText("Запись: Вкл" if self._session_log_active else "Запись: Выкл")
        if not self._session_log_active:
            if self._session_log_file is not None:
                try:
                    self._session_log_file.close()
                except OSError:
                    pass
                self._session_log_file = None

    def _default_session_log_path(self) -> Path:
        ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        root = Path.cwd()
        return root / "docs" / "specs" / "sessions" / f"meditation_{ts}.jsonl"

    def _new_session_log(self) -> None:
        # Rotate to a new timestamped file. Enable recording automatically.
        if self._session_log_path is not None:
            self._status.setText(f"Лог фиксирован CLI: {self._session_log_path}")
            return

        if self._session_log_file is not None:
            try:
                self._session_log_file.close()
            except OSError:
                pass
            self._session_log_file = None

        path = self._default_session_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._session_log_file = path.open("w", encoding="utf-8")
        self._status.setText(f"Лог сессии: {path}")

        if not self._session_log_active:
            self._record_btn.setChecked(True)

    def _scan_ble(self) -> None:
        if self._ble_scan_thread is not None and self._ble_scan_thread.isRunning():
            return
        self._status.setText("BLE: сканирование…")
        self._ble_scan_btn.setEnabled(False)
        self._ble_devices.clear()
        self._ble_devices.setEnabled(False)
        th = BleScanThread(scan_time_s=12.0, name_filter="BrainLink", parent=self)
        th.scanResult.connect(self._on_scan_result)
        th.scanFailed.connect(self._on_scan_failed)
        self._ble_scan_thread = th
        th.start()

    def _on_scan_result(self, rows: list) -> None:
        self._ble_scan_btn.setEnabled(True)
        self._ble_devices.clear()
        if not rows:
            self._status.setText("BLE: ничего не найдено (включите гарнитуру/видимость и повторите)")
            self._ble_devices.setEnabled(False)
            return
        for r in rows:
            name = r.get("name") or "Unknown"
            addr = normalize_ble_address(str(r.get("address") or ""))
            rssi = r.get("rssi")
            extra = f"  RSSI={rssi}" if rssi is not None else ""
            self._ble_devices.addItem(
                f"{name} ({addr}){extra}",
                userData={"address": addr, "rssi": rssi, "name": name},
            )
        self._ble_devices.setEnabled(True)
        self._status.setText("BLE: выберите устройство и нажмите «Старт BLE»")
        self._select_ble_device(self._ble_devices.currentIndex())

    def _on_scan_failed(self, msg: str) -> None:
        self._ble_scan_btn.setEnabled(True)
        self._ble_devices.setEnabled(False)
        self._status.setText(f"BLE scan ошибка: {msg}")

    def _select_ble_device(self, idx: int) -> None:
        if idx < 0:
            self._ble_address = None
            self._ble_start.setEnabled(False)
            self._src_label.setText("ЭЭГ: нет (только фазы дыхания)")
            return
        data = self._ble_devices.itemData(idx)
        if isinstance(data, dict):
            addr_s = normalize_ble_address(str(data.get("address") or ""))
            rssi = data.get("rssi")
            try:
                self._ble_selected_rssi = int(rssi) if rssi is not None else None
            except (TypeError, ValueError):
                self._ble_selected_rssi = None
        else:
            addr_s = normalize_ble_address(str(data or ""))
            self._ble_selected_rssi = None
        self._ble_address = addr_s or None
        if self._ble_address:
            self._src_label.setText(f"BLE: {self._ble_address}")
            self._ble_start.setEnabled(True)

    def _toggle_plot(self, on: bool) -> None:
        if not self._plot_available:
            return
        self._plot_enabled = bool(on)
        if self._chart_view is not None:
            self._chart_view.setVisible(self._plot_enabled)
        self._plot_clear_btn.setVisible(self._plot_enabled)
        if self._plot_enabled:
            self._plot_timer.start()
        else:
            self._plot_timer.stop()
        if self._plot_enabled:
            self._refresh_plot(force=True)

    def _plot_tick(self) -> None:
        if not self._plot_enabled or not self._plot_dirty:
            return
        now = time.monotonic()
        if now - self._plot_last_redraw < self._plot_min_redraw_s:
            return
        self._plot_last_redraw = now
        self._plot_dirty = False
        self._refresh_plot()

    def _init_plot_widgets(self, lay: QVBoxLayout) -> None:
        if not self._plot_available:
            return
        # Lazily create chart stack.
        series_att = QLineSeries()
        series_att.setName("Attention")
        series_med = QLineSeries()
        series_med.setName("Meditation")
        chart = QChart()
        chart.addSeries(series_att)
        chart.addSeries(series_med)
        chart.legend().setVisible(True)
        chart.setBackgroundRoundness(0)

        axis_x = QValueAxis()
        axis_x.setTitleText("t, s")
        axis_x.setRange(0, self._plot_window_s)
        axis_x.setLabelFormat("%.0f")
        axis_y = QValueAxis()
        axis_y.setRange(0, 100)
        axis_y.setTitleText("value")
        axis_y.setLabelFormat("%.0f")

        chart.addAxis(axis_x, Qt.AlignmentFlag.AlignBottom)
        chart.addAxis(axis_y, Qt.AlignmentFlag.AlignLeft)
        series_att.attachAxis(axis_x)
        series_att.attachAxis(axis_y)
        series_med.attachAxis(axis_x)
        series_med.attachAxis(axis_y)

        view = QChartView(chart)
        view.setMinimumHeight(180)
        view.setVisible(False)  # controlled by checkbox
        lay.addWidget(view)

        self._series_att = series_att
        self._series_med = series_med
        self._axis_x = axis_x
        self._axis_y = axis_y
        self._chart_view = view

    def _append_plot_point(self, att: int, med: int) -> None:
        if not self._plot_available:
            return
        t = time.monotonic() - self._t0
        self._t.append(t)
        self._att_hist.append(att)
        self._med_hist.append(med)
        self._plot_dirty = True

    def _clear_plot(self) -> None:
        if not self._plot_available:
            return
        self._t0 = time.monotonic()
        self._t.clear()
        self._att_hist.clear()
        self._med_hist.clear()
        self._plot_dirty = True
        if self._series_att is not None:
            self._series_att.clear()
        if self._series_med is not None:
            self._series_med.clear()
        if self._axis_x is not None:
            self._axis_x.setRange(0, self._plot_window_s)

    def _refresh_plot(self, *, force: bool = False) -> None:
        if not self._plot_available or not self._plot_enabled:
            return
        if self._series_att is None or self._series_med is None or self._axis_x is None:
            return
        n = len(self._t)
        if n < 2 and not force:
            return

        t_end = self._t[-1] if n else 0.0
        t_start = max(0.0, t_end - self._plot_window_s)
        self._axis_x.setRange(t_start, max(t_start + 1.0, t_end))

        # Build visible window points.
        pts_att = []
        pts_med = []
        for t, a, m in zip(self._t, self._att_hist, self._med_hist):
            if t < t_start:
                continue
            pts_att.append((t, a))
            pts_med.append((t, m))
        # Throttle extreme point counts.
        if len(pts_att) > 600:
            step = int(math.ceil(len(pts_att) / 600))
            pts_att = pts_att[::step]
            pts_med = pts_med[::step]

        self._series_att.replace([QPointF(x, y) for x, y in pts_att])
        self._series_med.replace([QPointF(x, y) for x, y in pts_med])

    def _append_session_log(self, att: int, med: int) -> None:
        if not self._session_log_active:
            return
        fp = self._session_log_file
        if fp is None and self._session_log_path is not None:
            self._session_log_path.parent.mkdir(parents=True, exist_ok=True)
            self._session_log_file = self._session_log_path.open("a", encoding="utf-8")
            fp = self._session_log_file
        if fp is None and self._session_log_path is None:
            # Lazy-create first session log on demand.
            self._new_session_log()
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
        self._session_started_at = time.monotonic()
        self._last_metric_at = None
        self._metric_times.clear()
        th = BleNotifyThread(
            self._ble_address,
            init_hex=self._ble_init_hex,
            duration_s=self._ble_duration_s,
            parent=self,
        )
        th.metricsReady.connect(self._on_ble_metrics)
        th.signalQualityReady.connect(self._on_ble_signal_quality)
        th.bandsReady.connect(self._on_ble_bands)
        th.connectionFailed.connect(self._on_ble_failed)
        th.workerFinished.connect(self._on_ble_worker_finished)
        self._ble_thread = th
        th.start()
        self._rssi_timer.start()

    def _stop_ble(self) -> None:
        if self._ble_thread is not None:
            self._ble_thread.request_stop()
            self._status.setText("BLE: остановка…")
        self._rssi_timer.stop()

    def _on_ble_metrics(self, att: int, med: int) -> None:
        now = time.monotonic()
        self._last_metric_at = now
        self._metric_times.append(now)
        self._last_att = att
        self._last_med = med
        self._att.setValue(att)
        self._med.setValue(med)
        self._bus.publish("eeg.metrics", {"attention": att, "meditation": med})
        self._append_session_log(att, med)
        self._append_plot_point(att, med)
        self._apply_eeg_tone()
        self._apply_eeg_binaural()
        if self._status.text().startswith("BLE: подключение"):
            self._status.setText("BLE: поток активен")

    def _on_ble_signal_quality(self, q: int) -> None:
        try:
            self._last_signal_quality = int(q)
        except (TypeError, ValueError):
            self._last_signal_quality = None

    def _on_ble_bands(
        self,
        delta: int,
        theta: int,
        low_alpha: int,
        high_alpha: int,
        low_beta: int,
        high_beta: int,
        low_gamma: int,
        high_gamma: int,
        _att: int,
        _med: int,
    ) -> None:
        self._last_bands = {
            "delta": int(delta),
            "theta": int(theta),
            "low_alpha": int(low_alpha),
            "high_alpha": int(high_alpha),
            "low_beta": int(low_beta),
            "high_beta": int(high_beta),
            "low_gamma": int(low_gamma),
            "high_gamma": int(high_gamma),
        }
        self._refresh_bands_ui()

    def _on_ble_failed(self, msg: str) -> None:
        self._status.setText(f"BLE ошибка: {msg}")

    def _on_ble_worker_finished(self) -> None:
        self._ble_thread = None
        self._ble_start.setEnabled(True)
        self._ble_stop.setEnabled(False)
        self._rssi_timer.stop()
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

    def _eeg_tick(self) -> None:
        if self._eeg_it is None:
            return
        try:
            att, med = next(self._eeg_it)
        except StopIteration:
            self._eeg_timer.stop()
            return
        now = time.monotonic()
        if self._session_started_at is None:
            self._session_started_at = now
        self._last_metric_at = now
        self._metric_times.append(now)
        self._last_att = att
        self._last_med = med
        self._att.setValue(att)
        self._med.setValue(med)
        self._bus.publish("eeg.metrics", {"attention": att, "meditation": med})
        self._append_session_log(att, med)
        self._append_plot_point(att, med)
        self._apply_eeg_tone()
        self._apply_eeg_binaural()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self._stats_timer.stop()
        self._stop_eeg_tone()
        self._stop_eeg_binaural()
        if self._ble_scan_thread is not None and self._ble_scan_thread.isRunning():
            self._ble_scan_thread.wait(2000)
            self._ble_scan_thread = None
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

    def _update_stats_line(self) -> None:
        # Show: session time, update rate, last sample age, plus RSSI if known.
        now = time.monotonic()
        parts: list[str] = []
        if self._session_started_at is not None:
            elapsed = max(0.0, now - self._session_started_at)
            mm = int(elapsed // 60)
            ss = int(elapsed % 60)
            parts.append(f"⏱ {mm:02d}:{ss:02d}")

        # Hz over last 10 seconds.
        hz = None
        if self._metric_times:
            cutoff = now - 10.0
            n = 0
            for t in reversed(self._metric_times):
                if t < cutoff:
                    break
                n += 1
            hz = n / 10.0
            parts.append(f"{hz:.1f} Hz")

        if self._last_metric_at is not None:
            age = now - self._last_metric_at
            parts.append(f"last {age:.1f}s")
            if age > 3.0 and (self._ble_thread is not None):
                parts.append("нет данных?")

        if self._ble_selected_rssi is not None:
            parts.append(f"RSSI {self._ble_selected_rssi}")
        if self._last_signal_quality is not None:
            parts.append(f"SQ {self._last_signal_quality}")

        self._stats.setText(" · ".join(parts))

    def _toggle_bands_full(self, on: bool) -> None:
        self._bands_full = bool(on)
        self._bands_box.setTitle("Bands (Full)" if self._bands_full else "Bands (Compact)")
        self._refresh_bands_ui(force=True)

    def _refresh_bands_ui(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and (now - self._bands_last_ui_at) < self._bands_min_ui_s:
            return
        self._bands_last_ui_at = now

        b = self._last_bands
        if not b:
            self._bands_line.setText("нет данных")
            return

        if self._bands_full:
            self._bands_line.setText(
                "δ={delta} θ={theta} αL={low_alpha} αH={high_alpha} βL={low_beta} βH={high_beta} γL={low_gamma} γH={high_gamma}".format(
                    **b
                )
            )
            return

        alpha = int(b["low_alpha"]) + int(b["high_alpha"])
        beta = int(b["low_beta"]) + int(b["high_beta"])
        gamma = int(b["low_gamma"]) + int(b["high_gamma"])
        self._bands_line.setText(
            f"δ={b['delta']}  θ={b['theta']}  α={alpha}  β={beta}  γ={gamma}"
        )

    def _tick_rssi_scan(self) -> None:
        # Best-effort RSSI refresh via a short advertisement scan.
        if not self._ble_address:
            return
        if self._rssi_scan_thread is not None and self._rssi_scan_thread.isRunning():
            return
        th = BleScanThread(scan_time_s=2.0, name_filter="", parent=self)
        th.scanResult.connect(self._on_rssi_scan_result)
        th.scanFailed.connect(lambda _msg: None)
        self._rssi_scan_thread = th
        th.start()

    def _on_rssi_scan_result(self, rows: list) -> None:
        self._rssi_scan_thread = None
        addr = normalize_ble_address(self._ble_address or "")
        best = None
        for r in rows or []:
            a = normalize_ble_address(str(r.get("address") or ""))
            if a != addr:
                continue
            best = r.get("rssi")
            break
        try:
            self._ble_selected_rssi = int(best) if best is not None else self._ble_selected_rssi
        except (TypeError, ValueError):
            pass

    def _apply_eeg_tone(self) -> None:
        if not self._eeg_tone_enabled:
            return
        now = time.monotonic()
        if now - self._eeg_tone_last_apply < self._eeg_tone_apply_min_s:
            return
        self._eeg_tone_last_apply = now

        # Failsafe: if data is stale, fade volume down and stop.
        if self._last_metric_at is None or (now - self._last_metric_at) > 3.0:
            if self._eeg_tone_mode == "stereo":
                a = self._eeg_tone_alpha
                self._eeg_tone_v_l = self._eeg_tone_v_l * (1.0 - a)
                self._eeg_tone_v_r = self._eeg_tone_v_r * (1.0 - a)
                if max(self._eeg_tone_v_l, self._eeg_tone_v_r) < 0.005:
                    self._stop_eeg_tone()
                else:
                    if self._ensure_eeg_tone_stream(channels=2):
                        self._eeg_tone_stream.set_volume_lr(float(self._eeg_tone_v_l), float(self._eeg_tone_v_r))
                        self._genmon_line.setText(
                            f"EEG→Tone(stereo) fL={self._eeg_tone_f_l:.1f}Hz fR={self._eeg_tone_f_r:.1f}Hz  vL={self._eeg_tone_v_l:.3f} vR={self._eeg_tone_v_r:.3f}"
                        )
                return
            self._eeg_tone_vol = self._eeg_tone_vol * (1.0 - self._eeg_tone_alpha)
            if self._eeg_tone_vol < 0.005:
                self._stop_eeg_tone()
            else:
                if self._ensure_eeg_tone_stream(channels=1):
                    self._eeg_tone_stream.set_volume(float(self._eeg_tone_vol))
                    self._genmon_line.setText(
                        f"EEG→Tone(mono) v={self._eeg_tone_vol:.3f}"
                    )
            return

        # Map metrics.
        att = float(self._last_att) / 100.0
        med = float(self._last_med) / 100.0
        a = self._eeg_tone_alpha

        def _metric(kind: str) -> float:
            if kind == "meditation":
                return med
            if kind == "attention":
                return att
            return 0.0

        def _map_freq(src: str, *, min_hz: float, max_hz: float, fixed_hz: float) -> float:
            if src == "off":
                return float(fixed_hz)
            x = _metric(src)
            lo = float(min_hz)
            hi = float(max_hz)
            if hi < lo:
                lo, hi = hi, lo
            return lo + (hi - lo) * float(x)

        def _map_vol(
            src: str,
            *,
            min_vol: float,
            max_vol: float,
            fixed_vol: float,
            freq_hz: float,
            fmin_hz: float,
            fmax_hz: float,
        ) -> float:
            if src == "off":
                return float(fixed_vol)
            if src == "freq_inv":
                lo = max(1.0, float(fmin_hz))
                hi = max(lo + 1e-6, float(fmax_hz))
                if hi < lo:
                    lo, hi = hi, lo
                f = max(lo, min(hi, float(freq_hz)))
                x = math.log(f / lo) / max(1e-12, math.log(hi / lo))
                x = 0.0 if x < 0.0 else 1.0 if x > 1.0 else float(x)
                vv_lo = float(min_vol)
                vv_hi = float(max_vol)
                if vv_hi < vv_lo:
                    vv_lo, vv_hi = vv_hi, vv_lo
                return vv_hi - (vv_hi - vv_lo) * x
            x = _metric(src)
            vv_lo = float(min_vol)
            vv_hi = float(max_vol)
            if vv_hi < vv_lo:
                vv_lo, vv_hi = vv_hi, vv_lo
            return vv_lo + (vv_hi - vv_lo) * float(x)

        if self._eeg_tone_mode == "stereo":
            f_l_t = _map_freq(
                self._eeg_tone_l_freq_src,
                min_hz=self._eeg_tone_l_min_hz,
                max_hz=self._eeg_tone_l_max_hz,
                fixed_hz=self._eeg_tone_l_fixed_hz,
            )
            f_r_t = _map_freq(
                self._eeg_tone_r_freq_src,
                min_hz=self._eeg_tone_r_min_hz,
                max_hz=self._eeg_tone_r_max_hz,
                fixed_hz=self._eeg_tone_r_fixed_hz,
            )
            v_l_t = _map_vol(
                self._eeg_tone_l_vol_src,
                min_vol=self._eeg_tone_l_min_vol,
                max_vol=self._eeg_tone_l_max_vol,
                fixed_vol=self._eeg_tone_l_fixed_vol,
                freq_hz=float(f_l_t),
                fmin_hz=self._eeg_tone_l_min_hz,
                fmax_hz=self._eeg_tone_l_max_hz,
            )
            v_r_t = _map_vol(
                self._eeg_tone_r_vol_src,
                min_vol=self._eeg_tone_r_min_vol,
                max_vol=self._eeg_tone_r_max_vol,
                fixed_vol=self._eeg_tone_r_fixed_vol,
                freq_hz=float(f_r_t),
                fmin_hz=self._eeg_tone_r_min_hz,
                fmax_hz=self._eeg_tone_r_max_hz,
            )

            self._eeg_tone_f_l = (1.0 - a) * self._eeg_tone_f_l + a * float(f_l_t)
            self._eeg_tone_f_r = (1.0 - a) * self._eeg_tone_f_r + a * float(f_r_t)
            self._eeg_tone_v_l = (1.0 - a) * self._eeg_tone_v_l + a * float(v_l_t)
            self._eeg_tone_v_r = (1.0 - a) * self._eeg_tone_v_r + a * float(v_r_t)

            if not self._ensure_eeg_tone_stream(channels=2):
                return
            self._eeg_tone_stream.set_volume_lr(float(self._eeg_tone_v_l), float(self._eeg_tone_v_r))
            self._eeg_tone_stream.play_binaural(float(self._eeg_tone_f_l), float(self._eeg_tone_f_r))
            self._genmon_line.setText(
                f"EEG→Tone(stereo) fL={self._eeg_tone_f_l:.1f}Hz fR={self._eeg_tone_f_r:.1f}Hz  vL={self._eeg_tone_v_l:.3f} vR={self._eeg_tone_v_r:.3f}"
            )
            return

        freq_src = med if self._eeg_tone_freq_src == "meditation" else att
        target_f = self._eeg_tone_min_hz + (self._eeg_tone_max_hz - self._eeg_tone_min_hz) * freq_src

        if self._eeg_tone_vol_src == "off":
            target_v = float(self._eeg_tone_fixed_vol)
        else:
            vol_src = med if self._eeg_tone_vol_src == "meditation" else att
            target_v = self._eeg_tone_min_vol + (self._eeg_tone_max_vol - self._eeg_tone_min_vol) * vol_src

        self._eeg_tone_f_hz = (1.0 - a) * self._eeg_tone_f_hz + a * target_f
        self._eeg_tone_vol = (1.0 - a) * self._eeg_tone_vol + a * target_v

        if not self._ensure_eeg_tone_stream(channels=1):
            return
        self._eeg_tone_stream.set_volume(float(self._eeg_tone_vol))
        self._eeg_tone_stream.play_tone(float(self._eeg_tone_f_hz))
        self._genmon_line.setText(
            f"EEG→Tone(mono) f={self._eeg_tone_f_hz:.1f}Hz  v={self._eeg_tone_vol:.3f}"
        )

    def _apply_eeg_binaural(self) -> None:
        if not self._eeg_bin_enabled:
            return
        now = time.monotonic()
        if now - self._eeg_tone_last_apply < self._eeg_tone_apply_min_s:
            return

        # Failsafe: if data is stale, stop audio.
        if self._last_metric_at is None or (now - self._last_metric_at) > 3.0:
            self._stop_eeg_binaural()
            return

        base_src = float(self._last_med) / 100.0 if self._eeg_bin_base_src == "meditation" else float(self._last_att) / 100.0
        base_target = self._eeg_bin_base_min_hz + (self._eeg_bin_base_max_hz - self._eeg_bin_base_min_hz) * base_src

        # Randomize delta periodically.
        if (now - self._eeg_bin_last_delta_at) >= max(0.5, float(self._eeg_bin_delta_update_s)):
            lo = min(float(self._eeg_bin_delta_min_hz), float(self._eeg_bin_delta_max_hz))
            hi = max(float(self._eeg_bin_delta_min_hz), float(self._eeg_bin_delta_max_hz))
            self._eeg_bin_delta_hz = random.uniform(lo, hi)
            self._eeg_bin_last_delta_at = now

        a = self._eeg_bin_alpha
        self._eeg_bin_base_hz = (1.0 - a) * self._eeg_bin_base_hz + a * float(base_target)

        left = float(self._eeg_bin_base_hz)
        right = float(self._eeg_bin_base_hz + self._eeg_bin_delta_hz)

        if not self._ensure_eeg_binaural_stream():
            return
        self._eeg_bin_stream.set_volume(float(self._eeg_bin_fixed_vol))
        self._eeg_bin_stream.play_binaural(left, right)
        self._genmon_line.setText(
            f"EEG→Binaural fL={left:.1f}Hz fR={right:.1f}Hz  Δf={float(self._eeg_bin_delta_hz):.2f}Hz  v={float(self._eeg_bin_fixed_vol):.3f}"
        )


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
