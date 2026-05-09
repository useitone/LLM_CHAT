"""
PoC: meditation / concentration — phased hints, EEG from JSONL or live BLE, agent API + bus.
"""

from __future__ import annotations

import os
import math
import json
import random
import sys
import tempfile
import threading
import time
import urllib.request
import uuid
from collections import deque
from datetime import UTC, datetime
from io import TextIOBase
from pathlib import Path
from typing import Any, Callable, Iterator

from html import escape as html_escape

from PySide6.QtCore import QEvent, QObject, QPointF, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QKeyEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QPlainTextEdit,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

try:
    from PySide6.QtCharts import QChart, QChartView, QLineSeries, QValueAxis
except Exception:  # pragma: no cover - optional addon
    QChart = QChartView = QLineSeries = QValueAxis = None  # type: ignore[misc,assignment]

from neurosync_pro.agent.server import start_agent_api, stop_agent_api
from neurosync_pro.agent_runtime.chat_reply_parse import strip_md_json_fence, try_parse_ready_program_decision
from neurosync_pro.agent_runtime.contracts import Decision, validate_and_normalize
from neurosync_pro.agent_runtime.loop import (
    SYSTEM_PROMPT as CHAT_STRUCTURED_SYSTEM_PROMPT,
    RuntimeState,
    apply_decision_policy,
    commit_decision_state,
    reset_llm_debounce,
)
from neurosync_pro.agent_runtime.spec_validate import validate_prog_spec, validate_timeline_body
from neurosync_pro.agent_runtime.user_profile import load_ui_profile, resolved_profile_path, save_ui_profile
from neurosync_pro.bus import EventBus
from neurosync_pro.light.metrics_hook import try_attach_metrics_light_bridge
from neurosync_pro.light.intent_sink import try_attach_light_intent_sink
from neurosync_pro.eeg.ble_stream import normalize_ble_address
from neurosync_pro.eeg.hr_filter import HrMedianSmoother
from neurosync_pro.eeg.rr_extract import pick_rr_ms, rr_ms_to_bpm, try_extract_rr_ms_candidates
from neurosync_pro.ui.ble_thread import BleNotifyThread, BleScanThread
from neurosync_pro.ui.com_hr_thread import MacrotellectComHrThread
try:  # optional audio extras
    from neurosync_pro.audio.stream import StreamConfig, ToneSweepStream
except Exception:  # pragma: no cover
    StreamConfig = None  # type: ignore[misc,assignment]
    ToneSweepStream = None  # type: ignore[misc,assignment]


_LIAISON_REASON_CODES = frozenset(
    {"greeting", "confirmation", "liaison", "check_in", "ping", "radio", "ack"}
)
_CHAT_LIAISON_FALLBACK_RU = (
    "На связи, канал открыт. Готов к командам программатора "
    "(JSON: set_spec, set_timeline, stop)."
)


def _chat_strip_context_prefix(full_user: str) -> str:
    """Strip '[контекст интерфейса: …]\\n' prefix from the outgoing user line."""
    s = (full_user or "").strip()
    prefix = "[контекст интерфейса:"
    if not s.startswith(prefix):
        return s
    bracket = s.find("]")
    if bracket < 0:
        return s
    rest = s[bracket + 1 :].lstrip("\n").strip()
    return rest if rest else s


def _chat_is_liaison_ping(body: str) -> bool:
    b = (body or "").lower()
    if len(b) < 2:
        return False
    needles = (
        "оператор",
        "на связи",
        "на связь",
        "приём",
        "прием",
        "слышите",
        "слышишь",
        "алло",
        "тест связи",
        "вы здесь",
        "вы тут",
        "проверк",
        "приём.",
        "прием.",
    )
    return any(n in b for n in needles)


CHAT_CAPABILITY_GROUNDING_RU = """
Границы и честность (обязательно соблюдай):
- Ты отвечаешь из чата. Звук по твоей команде меняется только после того, как оператор применил JSON («Применить команду» или автоприменение). Пока этого не было — не говори «я включил», «уже звучит», «активировал генератор»; говори «предлагаю команду ниже» / «если примените, будет …».
- Через программатор доступны только действия в JSON: set_spec / set_timeline / stop (и hold без смены звука). Spec задаёт бинауральную составляющую (carrier+beat/amp), шум white|pink|brown/amp, sweep:…, off; таймлайн — по строкам mm:ss. Ты не переключаешь галочки EEG→тон, отдельный шум в другой панели, BLE и т.д. Если просят вне этого — прямо: «через этот чат только программатор; для … нужен элемент в интерфейсе».
- Частоты и громкость формулируй как ориентир («примерно», «в рамках spec»), не как гарантию железа.
- Не выдавай за реальность то, чего нет: нет прав — скажи, что не можешь; не придумывай «включение», если команда не прошла или не применена.
""".strip()


CHAT_FREE_FLIGHT_SYSTEM_PROMPT = (
    """Ты со-пилот NeuroSync Pro: медитация, бинауральные биения, цветной шум, программатор звука.
Отвечай по-русски живым языком — оператор хочет общения и общего направления.
Можно образно и развёрнуто.

"""
    + CHAT_CAPABILITY_GROUNDING_RU
    + """

Если нужно реально изменить звук — в конце добавь РОВНО один fenced-блок ```json с одним JSON-объектом (без комментариев и текста внутри блока).
Допустимые action: set_spec, set_timeline, stop, hold.

КРИТИЧНО: поле spec — не описание словами и не английский псевдоязык. Только поддерживаемые токены (можно несколько через пробел в одной строке):
- бинаураль: число+число/амплитуда   пример: 200+2/0.45  (несущая Гц + биение Гц / громкость 0..1)
- шум: white ИЛИ pink ИЛИ brown с амплитудой   пример: brown/0.08
- sweep: начало->конец/длительность_с/амплитуда   пример: sweep:200->120/45/0.5
- выключить: off

Примеры ВАЛИДНЫХ значений spec (копируй стиль, подставляй свои числа):
  "200+6/0.50 pink/0.06"
  "brown/0.10"
  "sweep:180->90/60/0.45"
  "off"

ЗАПРЕЩЕНО в JSON внутри spec/timeline: фразы вида "brown noise", "binaural:2Hz delta", произвольный текст — это сломает проверку и команда не применится.
Для таймлайна — строки вида 0:00 <spec> и 0:30 off (spec только из правил выше); в JSON можно одну строку с \\n или массив строк ["0:00 …", "0:30 …"] — оба формата поддерживаются.

Не делай экстремальной громкости без запроса; если не уверен в цифрах — спроси словами и опусти JSON.

Векторная память — позже; опирайся на переписку и метрики в сообщении пользователя.
"""
).strip()


class _OllamaChatWorker(QThread):
    finished_ok = Signal(str)
    finished_err = Signal(str)

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        messages: list[dict[str, str]],
        timeout_s: float,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._base_url = base_url
        self._model = model
        self._messages = messages
        self._timeout_s = timeout_s

    def run(self) -> None:
        try:
            url = self._base_url.rstrip("/") + "/api/chat"
            payload = {"model": self._model, "messages": self._messages, "stream": False}
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=body,
                headers={"Content-Type": "application/json; charset=utf-8"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self._timeout_s) as resp:
                raw = resp.read().decode("utf-8")
            data = json.loads(raw)
            msg = data.get("message") if isinstance(data, dict) else None
            content = msg.get("content") if isinstance(msg, dict) else None
            if not isinstance(content, str):
                raise RuntimeError("Нет поля message.content в ответе Ollama")
            self.finished_ok.emit(content)
        except Exception as exc:
            self.finished_err.emit(str(exc))


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
        hr_source: str = "",
        hr_com_port: str = "",
        hr_pyd_dir: str = "",
        session_log_path: Path | None = None,
        auto_start_ble: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("NeuroSync Pro — медитация / концентрация (PoC)")
        self._bus = EventBus()
        self._light_bridge_detach: Callable[[], None] | None = None
        self._light_send_detach: Callable[[], None] | None = None
        self._api_server = None
        self._ble_address = (ble_address or "").strip() or None
        self._ble_init_hex = ble_init_hex
        self._ble_duration_s = ble_duration_s
        self._ble_thread: BleNotifyThread | None = None
        self._ble_scan_thread: BleScanThread | None = None
        self._hr_com_thread: MacrotellectComHrThread | None = None
        self._session_log_path = session_log_path
        self._session_log_file: TextIOBase | None = None
        self._session_log_active = session_log_path is not None
        self._session_id = str(uuid.uuid4())
        self._session_t0_mono: float | None = None
        self._last_att = 0
        self._last_med = 0
        self._ble_selected_rssi: int | None = None
        self._last_signal_quality: int | None = None
        self._last_bands: dict[str, int] | None = None
        self._bands_full = False
        self._bands_last_ui_at = 0.0
        self._bands_min_ui_s = 0.25
        self._bands_max_log: dict[str, float] = {}
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

        # Manual white noise (separate stream, can run alongside EEG→Tone).
        self._noise_available = ToneSweepStream is not None
        self._noise_enabled = False
        self._noise_stream = None
        self._noise_vol = 0.08
        self._noise_color = "white"  # white|pink|brown
        self._noise_fade_in_s = 0.02
        self._noise_fade_out_s = 0.08
        self._noise_stop_gen = 0

        # Cache the base text for the tone monitor line.
        # We must not build strings cumulatively from QLabel.text(), otherwise it grows unbounded.
        self._tone_base_text = "—"

        # Programmer (agent-driven) — minimal spec executor (binaural + noise).
        self._prog_available = ToneSweepStream is not None
        self._prog_running = False
        self._prog_spec = "100+7/0.60 pink/0.08"
        self._prog_tone_stream = None
        self._prog_noise_stream = None
        self._prog_timers: list[QTimer] = []
        self._prog_timeline_running = False
        self._prog_noise_color = "pink"
        self._prog_noise_vol = 0.08
        self._prog_tone_left_hz = 96.5
        self._prog_tone_right_hz = 103.5
        self._prog_tone_vol = 0.60
        self._prog_last_status_at = 0.0
        self._prog_status_min_s = 0.25
        self._prog_sink_url = ""

        # Ollama chat (under Programmer): free-form vs structured JSON for program.set_spec.
        self._chat_messages: list[dict[str, str]] = []
        self._chat_last_decision: Decision | None = None
        self._chat_worker: _OllamaChatWorker | None = None
        self._chat_policy_state = RuntimeState()
        self._chat_metric_loop_goal: str | None = None  # attention | meditation | am
        self._chat_metric_loop_tick_index: int = 0
        self._chat_metric_loop_timer = QTimer(self)
        self._chat_metric_loop_timer.setSingleShot(False)
        self._chat_metric_loop_interval_ms = self._chat_metric_loop_interval_ms_default()
        self._chat_metric_loop_timer.setInterval(self._chat_metric_loop_interval_ms)
        self._chat_metric_loop_timer.timeout.connect(self._chat_metric_loop_tick)

        # Generator monitor (UI-only; mirrors what we send to audio engine).
        self._genmon_text = ""

        # Link/quality stats (session time, Hz, last sample age).
        self._session_started_at: float | None = None
        self._last_metric_at: float | None = None
        self._metric_times = deque(maxlen=5000)
        self._bands_change_times = deque(maxlen=5000)
        self._metrics_change_times = deque(maxlen=5000)
        self._prev_metrics: tuple[int, int] | None = None
        self._prev_bands_compact: tuple[int, int, int, int, int] | None = None
        self._stats_timer = QTimer(self)
        self._stats_timer.setInterval(1000)
        self._stats_timer.timeout.connect(self._update_stats_line)

        # Observation logger (windowed aggregates for diary/training).
        self._obs_window_s = 10.0
        self._obs_timer = QTimer(self)
        self._obs_timer.setInterval(int(self._obs_window_s * 1000))
        self._obs_timer.timeout.connect(self._emit_observation)
        self._obs_points: deque[dict[str, Any]] = deque(maxlen=5000)

        # Vendor HR (soft headband / aabb0c — experimental).
        self._last_hr_bpm: int | None = None
        self._last_hr_at: float | None = None
        # Etalon HR diagnostics (COM thread).
        self._last_etalon_event_at: float | None = None
        self._etalon_rr_events: int = 0
        self._etalon_extend_events: int = 0
        self._etalon_errors: int = 0
        self._etalon_last_error: str = ""
        self._etalon_started_at: float | None = None
        self._etalon_restart_delay_ms = self._etalon_restart_delay_ms_default()
        env_hr_source = (os.environ.get("NSP_HR_SOURCE", "") or "").strip().lower()
        self._hr_source = (hr_source or env_hr_source or "open").strip().lower()
        self._hr_com_port = (hr_com_port or os.environ.get("NSP_HR_COM_PORT", "") or "COM3").strip()
        self._hr_pyd_dir = (hr_pyd_dir or os.environ.get("NSP_HR_PYD_DIR", "") or "").strip()
        # Defaults tuned for noisy vendor frames; can be adjusted via env.
        self._hr_smoother = HrMedianSmoother(
            window=int(os.environ.get("NSP_HR_SMOOTH_WINDOW", "5") or "5"),
            max_delta_per_s=float(os.environ.get("NSP_HR_MAX_DELTA_PER_S", "12") or "12"),
            jump_confirm=int(os.environ.get("NSP_HR_JUMP_CONFIRM", "2") or "2"),
        )
        # Separate smoother for etalon COM HR (more responsive by default).
        self._hr_etalon_smoother = HrMedianSmoother(
            window=int(os.environ.get("NSP_HR_ETALON_SMOOTH_WINDOW", "3") or "3"),
            max_delta_per_s=float(os.environ.get("NSP_HR_ETALON_MAX_DELTA_PER_S", "60") or "60"),
            jump_confirm=int(os.environ.get("NSP_HR_ETALON_JUMP_CONFIRM", "1") or "1"),
        )

        # Simple rolling metrics plot (optional, requires PySide6.QtCharts).
        self._plot_available = QChart is not None
        self._plot_enabled = False
        self._plot_window_s = 120.0
        self._t0 = time.monotonic()
        self._t = deque(maxlen=2000)  # seconds since start
        self._att_hist = deque(maxlen=2000)
        self._med_hist = deque(maxlen=2000)
        self._plot_dirty = False
        # Bands plot (uses QtCharts too).
        self._bands_plot_enabled = False
        self._bands_plot_window_s = 120.0
        self._bands_t0 = time.monotonic()
        self._bands_t = deque(maxlen=2000)
        self._bands_hist: dict[str, deque] = {}
        self._bands_plot_dirty = False
        self._bands_chart_view = None
        self._bands_axis_x = None
        self._bands_axis_y = None
        self._bands_series: dict[str, "QLineSeries"] = {}
        self._bands_plot_last_redraw = 0.0
        self._bands_plot_min_redraw_s = 0.25
        self._plot_last_redraw = 0.0
        self._plot_min_redraw_s = 0.15  # ~6-7 FPS max
        self._series_att = None
        self._series_med = None
        self._axis_x = None
        self._axis_y = None
        self._chart_view = None
        # HR (vendor) line chart
        self._hr_plot_enabled = False
        self._hr_plot_window_s = 120.0
        self._hr_t0 = time.monotonic()
        self._hr_t = deque(maxlen=2000)
        self._hr_bpm_hist = deque(maxlen=2000)
        self._hr_plot_dirty = False
        self._hr_plot_last_redraw = 0.0
        self._hr_plot_min_redraw_s = 0.2
        self._series_hr = None
        self._hr_axis_x = None
        self._hr_axis_y = None
        self._hr_chart_view = None
        self._hr_plot_timer: QTimer | None = None

        # EEG→Tone graph: mono (f, v) and stereo (fL, fR, vL, vR); dual Y: Hz, vol.
        self._tone_plot_enabled = False
        self._tone_plot_window_s = 120.0
        self._tone_t0 = time.monotonic()
        self._tone_m_t: deque = deque(maxlen=2000)
        self._tone_m_f: deque = deque(maxlen=2000)
        self._tone_m_v: deque = deque(maxlen=2000)
        self._tone_s_t: deque = deque(maxlen=2000)
        self._tone_s_f_l: deque = deque(maxlen=2000)
        self._tone_s_f_r: deque = deque(maxlen=2000)
        self._tone_s_v_l: deque = deque(maxlen=2000)
        self._tone_s_v_r: deque = deque(maxlen=2000)
        self._tone_plot_dirty = False
        self._tone_plot_last_redraw = 0.0
        self._tone_plot_min_redraw_s = 0.2
        self._series_tone_f = None
        self._series_tone_v = None
        self._series_tone_f_l = None
        self._series_tone_f_r = None
        self._series_tone_v_l = None
        self._series_tone_v_r = None
        self._tone_axis_x = None
        self._tone_axis_y_hz = None
        self._tone_axis_y_vol = None
        self._tone_chart_view = None
        self._tone_plot_timer: QTimer | None = None

        self._eeg_it: Iterator[tuple[int, int]] | None = None
        if self._ble_address is None and jsonl_path and jsonl_path.is_file():
            self._eeg_it = iter(_iter_eeg(jsonl_path))

        if session_log_path is not None:
            session_log_path.parent.mkdir(parents=True, exist_ok=True)
            # CLI-provided log path: keep append semantics (explicit user choice).
            self._session_log_file = session_log_path.open("a", encoding="utf-8")
            self._write_session_start()

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
        self._prog_box = QGroupBox("Программатор")
        pb = QVBoxLayout(self._prog_box)
        pb.setContentsMargins(8, 8, 8, 8)
        pb.setSpacing(6)

        self._prog_tabs = QTabWidget()
        self._prog_tabs.setEnabled(self._prog_available)

        spec_tab = QWidget()
        spec_lay = QVBoxLayout(spec_tab)
        spec_lay.setContentsMargins(0, 0, 0, 0)
        self._prog_spec_edit = QLineEdit()
        self._prog_spec_edit.setPlaceholderText("spec, напр: 100+7/0.60 pink/0.08  или  sweep:1000->100/30/0.6")
        self._prog_spec_edit.setText(self._prog_spec)
        self._prog_spec_edit.setEnabled(self._prog_available)
        spec_lay.addWidget(self._prog_spec_edit)
        self._prog_tabs.addTab(spec_tab, "Spec")

        tl_tab = QWidget()
        tl_lay = QVBoxLayout(tl_tab)
        tl_lay.setContentsMargins(0, 0, 0, 0)
        self._prog_tl_edit = QPlainTextEdit()
        self._prog_tl_edit.setPlaceholderText("Timeline (mm:ss spec), напр:\n0:00 sweep:1000->100/30/0.6 pink/0.08\n0:25 100+7/0.6 pink/0.08\n0:30 off")
        self._prog_tl_edit.setPlainText(
            "0:00 sweep:1000->100/30/0.6 pink/0.08\n0:25 100+7/0.60 pink/0.08\n0:30 off\n"
        )
        tl_lay.addWidget(self._prog_tl_edit)
        self._prog_tabs.addTab(tl_tab, "Timeline")

        pb.addWidget(self._prog_tabs)

        prog_btn_row = QWidget()
        prog_btn_lay = QHBoxLayout(prog_btn_row)
        prog_btn_lay.setContentsMargins(0, 0, 0, 0)
        self._prog_run_btn = QPushButton("Run")
        self._prog_stop_btn = QPushButton("Stop")
        self._prog_run_btn.setEnabled(self._prog_available)
        self._prog_stop_btn.setEnabled(False)
        self._prog_run_btn.clicked.connect(self._prog_run_clicked)
        self._prog_stop_btn.clicked.connect(self._prog_stop_clicked)
        prog_btn_lay.addWidget(self._prog_run_btn)
        prog_btn_lay.addWidget(self._prog_stop_btn)
        prog_btn_lay.addStretch(1)
        pb.addWidget(prog_btn_row)

        def _tab_changed(_i: int) -> None:
            # Keep button text consistent with current state.
            if not hasattr(self, "_prog_run_btn"):
                return
            if self._prog_running and hasattr(self, "_prog_tabs") and int(self._prog_tabs.currentIndex()) == 0:
                self._prog_run_btn.setText("Apply")
            else:
                self._prog_run_btn.setText("Run")

        self._prog_tabs.currentChanged.connect(_tab_changed)

        self._prog_status_lbl = QLabel("idle")
        self._prog_status_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        pb.addWidget(self._prog_status_lbl)

        marker_row = QWidget()
        marker_lay = QHBoxLayout(marker_row)
        marker_lay.setContentsMargins(0, 0, 0, 0)
        marker_lay.addWidget(QLabel("Marker"))
        self._marker_edit = QLineEdit()
        self._marker_edit.setPlaceholderText("например: focus↑ / distracted / artifact_motion")
        marker_lay.addWidget(self._marker_edit, 1)
        marker_lay.addWidget(QLabel("R"))
        self._marker_rating = QDoubleSpinBox()
        self._marker_rating.setRange(-2.0, 2.0)
        self._marker_rating.setSingleStep(1.0)
        self._marker_rating.setDecimals(0)
        self._marker_rating.setValue(0.0)
        self._marker_rating.setToolTip("Быстрая оценка: -2..+2")
        self._marker_rating.setFixedWidth(60)
        marker_lay.addWidget(self._marker_rating)
        self._marker_note = QLineEdit()
        self._marker_note.setPlaceholderText("note (опц.)")
        marker_lay.addWidget(self._marker_note, 1)
        self._marker_btn = QPushButton("Add")
        self._marker_btn.clicked.connect(self._add_marker_clicked)
        marker_lay.addWidget(self._marker_btn)
        pb.addWidget(marker_row)

        sink_row = QWidget()
        sink_lay = QHBoxLayout(sink_row)
        sink_lay.setContentsMargins(0, 0, 0, 0)
        sink_lay.addWidget(QLabel("Sink URL"))
        self._prog_sink_edit = QLineEdit()
        self._prog_sink_edit.setPlaceholderText("http://127.0.0.1:8766/v1/ui_event (опц.)")
        self._prog_sink_edit.setText(self._prog_sink_url)
        self._prog_sink_edit.setEnabled(True)
        self._prog_sink_edit.textChanged.connect(self._prog_sink_changed)
        sink_lay.addWidget(self._prog_sink_edit, 1)
        pb.addWidget(sink_row)

        if not self._prog_available:
            self._prog_box.setToolTip("Установите audio extras: pip install -e \".[audio]\"")

        self._chat_box = QGroupBox("Чат с моделью (Ollama)")
        cb = QVBoxLayout(self._chat_box)
        cb.setContentsMargins(8, 8, 8, 8)
        cb.setSpacing(6)

        chat_top = QWidget()
        chat_top_lay = QHBoxLayout(chat_top)
        chat_top_lay.setContentsMargins(0, 0, 0, 0)
        chat_top_lay.addWidget(QLabel("Ollama URL"))
        self._chat_base_url = QLineEdit()
        self._chat_base_url.setText(os.environ.get("NSP_LOCAL_BASE_URL", "http://127.0.0.1:11434"))
        chat_top_lay.addWidget(self._chat_base_url, 2)
        chat_top_lay.addWidget(QLabel("Модель"))
        self._chat_model = QLineEdit()
        self._chat_model.setText(os.environ.get("NSP_LOCAL_MODEL", "deepseek-v3.1:671b-cloud"))
        self._chat_model.setPlaceholderText("имя модели из ollama list")
        chat_top_lay.addWidget(self._chat_model, 1)
        cb.addWidget(chat_top)

        mode_row = QWidget()
        mode_lay = QHBoxLayout(mode_row)
        mode_lay.setContentsMargins(0, 0, 0, 0)
        self._chat_free_cb = QCheckBox("Свободное общение")
        self._chat_free_cb.setChecked(True)
        self._chat_free_cb.setToolTip(
            "Включено: обычный диалог.\n"
            "Выключено: режим промпта — ответ только JSON-команда программатора "
            "(action set_spec / set_timeline / stop / hold), см. docs/specs/programmer_commands.md"
        )
        self._chat_free_cb.toggled.connect(self._chat_free_mode_changed)
        mode_lay.addWidget(self._chat_free_cb)
        self._chat_auto_apply_cb = QCheckBox("Автоприменение JSON")
        self._chat_auto_apply_cb.setChecked(False)
        self._chat_auto_apply_cb.setEnabled(False)
        self._chat_auto_apply_cb.setToolTip(
            "После ответа модели сразу выполнить распознанную команду (set_spec / set_timeline / stop), если она есть: "
            "в режиме промпта (весь ответ — JSON) или в «Свободном полёте» (JSON в блоке ```json). "
            "Ограничения agent_runtime — только если включены при режиме промпта."
        )
        mode_lay.addWidget(self._chat_auto_apply_cb)
        self._chat_agent_runtime_policy_cb = QCheckBox("Ограничения как у agent_runtime")
        self._chat_agent_runtime_policy_cb.setChecked(False)
        self._chat_agent_runtime_policy_cb.setEnabled(False)
        self._chat_agent_runtime_policy_cb.setToolTip(
            "Выключено (по умолчанию): команда применяется сразу после проверки JSON — как в раннем PoC.\n"
            "Включено: перед применением те же шаги, что у tools/agent_runtime.py "
            "(debounce, cooldown, лимит частоты; переменные NSP_*)."
        )
        mode_lay.addWidget(self._chat_agent_runtime_policy_cb)
        mode_lay.addStretch(1)
        cb.addWidget(mode_row)

        flight_row = QWidget()
        flight_lay = QHBoxLayout(flight_row)
        flight_lay.setContentsMargins(0, 0, 0, 0)
        self._chat_freeflight_cb = QCheckBox("Свободный полёт")
        self._chat_freeflight_cb.setChecked(False)
        self._chat_freeflight_cb.setEnabled(False)
        self._chat_freeflight_cb.setToolTip(
            "Только при включённом «Свободном общении»: живой диалог по-русски и при желании команды программатора "
            "в виде блока ```json в конце ответа. Можно включить «Автоприменение JSON», если команды должны "
            "применяться сами после распознавания."
        )
        self._chat_freeflight_cb.toggled.connect(self._chat_freeflight_toggled)
        flight_lay.addWidget(self._chat_freeflight_cb)
        flight_lay.addStretch(1)
        cb.addWidget(flight_row)

        tpl_row = QWidget()
        tpl_lay = QHBoxLayout(tpl_row)
        tpl_lay.setContentsMargins(0, 0, 0, 0)
        tpl_lay.addWidget(QLabel("Автотакт"))
        btn_stop_tpl = QPushButton("Стоп")
        btn_stop_tpl.setToolTip("Остановить автотакт метрик и подставить JSON stop в поле ввода.")
        btn_stop_tpl.clicked.connect(self._chat_tpl_stop_clicked)
        tpl_lay.addWidget(btn_stop_tpl)
        for cap, goal, tip in (
            (
                "Attention",
                "attention",
                "Свободный полёт: как фраза «даю управление — влияйте на показатели», но запрос уходит сам каждые NSP_CHAT_METRIC_LOOP_S с; "
                "модель может экспериментировать с JSON программатора, приоритет Attention.",
            ),
            (
                "Meditation",
                "meditation",
                "То же для свободного полёта, приоритет Meditation.",
            ),
            (
                "A/M",
                "am",
                "То же, фокус на балансе Attention/Meditation.",
            ),
        ):
            tb = QPushButton(cap)
            tb.setToolTip(tip)
            tb.clicked.connect(lambda checked=False, g=goal: self._chat_start_metric_loop(g))
            tpl_lay.addWidget(tb)
        tpl_lay.addStretch(1)
        cb.addWidget(tpl_row)

        self._chat_view = QTextBrowser()
        self._chat_view.setReadOnly(True)
        self._chat_view.setMinimumHeight(120)
        self._chat_view.setPlaceholderText("Здесь будет переписка…")
        cb.addWidget(self._chat_view, 1)

        self._chat_input = QPlainTextEdit()
        self._chat_input.setPlaceholderText("Сообщение… (Enter — отправить, Shift+Enter — новая строка)")
        self._chat_input.setFixedHeight(72)
        self._chat_input.installEventFilter(self)
        cb.addWidget(self._chat_input)

        chat_btns = QWidget()
        chat_btns_lay = QHBoxLayout(chat_btns)
        chat_btns_lay.setContentsMargins(0, 0, 0, 0)
        self._chat_send_btn = QPushButton("Отправить")
        self._chat_send_btn.clicked.connect(self._chat_send_clicked)
        self._chat_apply_btn = QPushButton("Применить команду")
        self._chat_apply_btn.setEnabled(False)
        self._chat_apply_btn.setToolTip(
            "Применить последнюю распознанную команду (режим промпта или «Свободный полёт» с JSON в ответе). "
            "Ограничения agent_runtime — только в режиме промпта и если включён соответствующий чекбокс."
        )
        self._chat_apply_btn.clicked.connect(self._chat_apply_clicked)
        chat_btns_lay.addWidget(self._chat_send_btn)
        chat_btns_lay.addWidget(self._chat_apply_btn)
        chat_btns_lay.addStretch(1)
        cb.addWidget(chat_btns)

        self._chat_box.setToolTip(
            "Настройки чата сохраняются при закрытии окна в JSON-профиль "
            "(по умолчанию ~/.neurosync_pro/ui_profile.json или NSP_PROFILE_PATH).\n"
            "По умолчанию JSON-команды применяются сразу после валидации. "
            "«Свободный полёт»: диалог + опциональный ```json; контур agent_runtime к чату не подмешивается. "
            "В режиме промпта ограничения debounce/cooldown/лимита — только если включён соответствующий чекбокс."
        )
        self._chat_load_profile()

        right_lay.addWidget(self._prog_box)
        right_lay.addWidget(self._chat_box, 1)
        right_lay.addStretch(1)

        splitter.addWidget(left_scroll)
        splitter.addWidget(mid_widget)
        splitter.addWidget(right_widget)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 2)
        splitter.setStretchFactor(2, 2)
        # Live resize repaints QtCharts + QTextBrowser + nested layouts every pixel → laggy handle.
        # Rubber-band resize keeps dragging smooth; panels apply size on mouse release.
        splitter.setOpaqueResize(False)
        splitter.setHandleWidth(7)
        # Center metrics: HR value sits next to its graph; EEG Hz only in the stats line above.
        self._hr_val = QLabel("—")
        self._hr_val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._hr_val.setToolTip(
            "Число BPM по vendor-кадрам AA BB 0C (экспериментально). Это не ЭКГ и не медицинский прибор; "
            "без хвоста 23 23 после полезной нагрузки кадр отбрасывается. "
            "Отображаемое значение — медиана последних 5 кадров (для подавления одиночных выбросов)."
        )
        self._att_val = QLabel("Attention —")
        self._att_val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._med_val = QLabel("Meditation —")
        self._med_val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

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

        # Programmer bus bridge (agent → UI).
        self._bus.subscribe("program.set_spec", self._on_program_set_spec)
        self._bus.subscribe("program.set_timeline", self._on_program_set_timeline)
        self._bus.subscribe("program.stop", lambda _p: self._prog_stop())
        # Optional EEG→light bridge (LedMatrix.md); off unless NSP_LIGHT_ENABLED=1.
        self._light_bridge_detach = try_attach_metrics_light_bridge(self._bus)
        # Optional light.intent → log/BLE sink; off unless NSP_LIGHT_SEND_ENABLED=1.
        self._light_send_detach = try_attach_light_intent_sink(self._bus)

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

        # Center: source, stats, then plot blocks (HR first — swapped with A/M), then bands.
        mid_lay.addWidget(self._src_label)
        mid_lay.addWidget(self._stats)

        # Left panel content (controls-only).
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

        # Manual noise (background) — independent from EEG stream.
        noise_row = QWidget()
        noise_lay = QHBoxLayout(noise_row)
        noise_lay.setContentsMargins(0, 0, 0, 0)
        self._noise_cb = QCheckBox("Noise (фон)")
        self._noise_cb.setEnabled(self._noise_available)
        if not self._noise_available:
            self._noise_cb.setToolTip("Установите audio extras: pip install -e \".[audio]\"")
        self._noise_cb.toggled.connect(self._toggle_noise)
        self._noise_vol_spin = QDoubleSpinBox()
        self._noise_vol_spin.setRange(0.0, 1.0)
        self._noise_vol_spin.setSingleStep(0.01)
        self._noise_vol_spin.setValue(float(self._noise_vol))
        self._noise_vol_spin.setSuffix(" vol")
        self._noise_vol_spin.setEnabled(self._noise_available)
        self._noise_vol_spin.valueChanged.connect(self._noise_vol_changed)
        self._noise_color_cb = QComboBox()
        self._noise_color_cb.setEnabled(self._noise_available)
        self._noise_color_cb.addItem("White", userData="white")
        self._noise_color_cb.addItem("Pink", userData="pink")
        self._noise_color_cb.addItem("Brown", userData="brown")
        self._noise_color_cb.setCurrentIndex(0)
        self._noise_color_cb.currentIndexChanged.connect(self._noise_color_changed)
        noise_lay.addWidget(self._noise_cb)
        noise_lay.addStretch(1)
        noise_lay.addWidget(QLabel("Color"))
        noise_lay.addWidget(self._noise_color_cb)
        noise_lay.addWidget(QLabel("Vol"))
        noise_lay.addWidget(self._noise_vol_spin)
        left_lay.addWidget(noise_row)

        # Middle panel: HR block (above A/M — user-requested swap), then Attention/Meditation.
        self._hr_group = QGroupBox("Пульс (exp)")
        self._hr_group.setToolTip(
            "Открытый HR — эвристика по BLE. Эталон — Macrotellect COM (RR/extend); при открытом COM многие гарнитуры "
            "перестают слать EEG по BLE — тогда Attention/Meditation/Bands подпитываются из того же COM-потока "
            "(NSP_HR_ETALON_FEED_EEG=1 по умолчанию)."
        )
        hr_block_lay = QVBoxLayout(self._hr_group)
        hr_block_lay.setContentsMargins(8, 4, 8, 4)
        hr_block_lay.setSpacing(4)

        hr_src_row = QWidget()
        hr_src_row_lay = QHBoxLayout(hr_src_row)
        hr_src_row_lay.setContentsMargins(0, 0, 0, 0)
        hr_src_row_lay.addWidget(QLabel("Источник:"))
        self._hr_source_cb = QComboBox()
        self._hr_source_cb.addItem("Открытый (BLE, exp)", userData="open")
        self._hr_source_cb.addItem("Эталон (Macrotellect COM)", userData="etalon")
        hr_src_row_lay.addWidget(self._hr_source_cb, 2)
        hr_src_row_lay.addWidget(QLabel("COM:"))
        self._hr_com_port_le = QLineEdit()
        self._hr_com_port_le.setPlaceholderText("COM3")
        hr_src_row_lay.addWidget(self._hr_com_port_le, 1)
        self._hr_source_apply_btn = QPushButton("Применить")
        self._hr_source_apply_btn.clicked.connect(self._apply_hr_source_ui)
        hr_src_row_lay.addWidget(self._hr_source_apply_btn, 0)
        hr_block_lay.addWidget(hr_src_row)
        self._hr_etalon_status = QLabel("")
        self._hr_etalon_status.setStyleSheet("color: palette(mid);")
        hr_block_lay.addWidget(self._hr_etalon_status)

        hr_plot_row = QWidget()
        hr_plot_row_lay = QHBoxLayout(hr_plot_row)
        hr_plot_row_lay.setContentsMargins(0, 0, 0, 0)
        self._hr_plot_cb = QCheckBox("График (HR, exp)")
        self._hr_plot_cb.setEnabled(self._plot_available)
        if not self._plot_available:
            self._hr_plot_cb.setToolTip("PySide6.QtCharts недоступен в текущей установке.")
        else:
            self._hr_plot_cb.setToolTip(
                "Линия оценки BPM из тех же кадров, что и HR (exp); не кривая ЭКГ."
            )
        self._hr_plot_cb.toggled.connect(self._toggle_hr_plot)
        self._hr_plot_clear_btn = QPushButton("Очистить график HR")
        self._hr_plot_clear_btn.setEnabled(self._plot_available)
        self._hr_plot_clear_btn.clicked.connect(self._clear_hr_plot)
        self._hr_plot_clear_btn.setVisible(False)
        self._hr_now_lbl = QLabel("BPM")
        self._hr_now_lbl.setStyleSheet("color: palette(mid);")
        hr_plot_row_lay.addWidget(self._hr_plot_cb)
        hr_plot_row_lay.addWidget(self._hr_now_lbl)
        hr_plot_row_lay.addWidget(self._hr_val)
        hr_plot_row_lay.addStretch(1)
        hr_plot_row_lay.addWidget(self._hr_plot_clear_btn)
        hr_block_lay.addWidget(hr_plot_row)
        if self._plot_available:
            self._init_hr_plot_widgets(hr_block_lay)
        mid_lay.addWidget(self._hr_group)

        # Bands chart: compact by default; optional Full toggle.
        self._bands_box = QGroupBox("Bands")
        mid_lay.addWidget(self._bands_box)
        blay = QVBoxLayout(self._bands_box)

        bands_row = QWidget()
        bands_row_lay = QHBoxLayout(bands_row)
        bands_row_lay.setContentsMargins(0, 0, 0, 0)

        self._bands_plot_cb = QCheckBox("График (Bands)")
        self._bands_plot_cb.setEnabled(self._plot_available)
        if not self._plot_available:
            self._bands_plot_cb.setToolTip("PySide6.QtCharts недоступен в текущей установке.")
        self._bands_plot_cb.toggled.connect(self._toggle_bands_plot)

        self._bands_full_cb = QCheckBox("Full (8 линий)")
        self._bands_full_cb.setChecked(False)
        self._bands_full_cb.setEnabled(False)  # enabled when chart is enabled
        self._bands_full_cb.toggled.connect(self._toggle_bands_full)

        self._bands_plot_clear_btn = QPushButton("Очистить график")
        self._bands_plot_clear_btn.setEnabled(self._plot_available)
        self._bands_plot_clear_btn.clicked.connect(self._clear_bands_plot)
        self._bands_plot_clear_btn.setVisible(False)

        bands_row_lay.addWidget(self._bands_plot_cb)
        bands_row_lay.addWidget(self._bands_full_cb)
        bands_row_lay.addStretch(1)
        bands_row_lay.addWidget(self._bands_plot_clear_btn)
        blay.addWidget(bands_row)

        self._bands_line = QLabel("нет данных")
        self._bands_line.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        blay.addWidget(self._bands_line)

        if self._plot_available:
            self._init_bands_plot_widgets(blay)

        self._am_group = QGroupBox("Attention / Meditation")
        self._am_group.setToolTip("Сигнальные индикаторы с шлема; кривые 0…100.")
        am_block_lay = QVBoxLayout(self._am_group)
        am_block_lay.setContentsMargins(8, 4, 8, 4)
        am_block_lay.setSpacing(4)
        plot_row = QWidget()
        plot_row_lay = QHBoxLayout(plot_row)
        plot_row_lay.setContentsMargins(0, 0, 0, 0)
        plot_row_lay.addWidget(self._plot_cb)
        plot_row_lay.addSpacing(10)
        plot_row_lay.addWidget(self._att_val)
        plot_row_lay.addSpacing(6)
        plot_row_lay.addWidget(self._med_val)
        plot_row_lay.addStretch(1)
        plot_row_lay.addWidget(self._plot_clear_btn)
        am_block_lay.addWidget(plot_row)
        if self._plot_available:
            self._init_plot_widgets(am_block_lay)
        mid_lay.addWidget(self._am_group)

        self._tone_group = QGroupBox("EEG → Tone (монитор)")
        self._tone_group.setToolTip(
            "Сглаженные f и громкость, которые уходят в движок тона. Mono: f + vol; stereo: fL, fR, vL, vR."
        )
        tone_lay = QVBoxLayout(self._tone_group)
        tone_lay.setContentsMargins(8, 4, 8, 4)
        tone_lay.setSpacing(4)

        tone_row = QWidget()
        tone_row_lay = QHBoxLayout(tone_row)
        tone_row_lay.setContentsMargins(0, 0, 0, 0)
        self._tone_plot_cb = QCheckBox("График (f / vol)")
        self._tone_plot_cb.setEnabled(self._plot_available)
        if not self._plot_available:
            self._tone_plot_cb.setToolTip("PySide6.QtCharts недоступен в текущей установке.")
        else:
            self._tone_plot_cb.setToolTip(
                "Переключение Mono/Stereo в настройках EEG→Tone очищает кривую: разные сетки осей (Hz / vol)."
            )
        self._tone_plot_cb.toggled.connect(self._toggle_tone_plot)
        self._tone_vals_lbl = QLabel("—")
        self._tone_vals_lbl.setStyleSheet("color: palette(mid);")
        self._tone_vals_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._tone_plot_clear_btn = QPushButton("Очистить")
        self._tone_plot_clear_btn.setEnabled(self._plot_available)
        self._tone_plot_clear_btn.setVisible(False)
        self._tone_plot_clear_btn.clicked.connect(self._clear_tone_plot)
        tone_row_lay.addWidget(self._tone_plot_cb)
        tone_row_lay.addSpacing(10)
        tone_row_lay.addWidget(self._tone_vals_lbl, 1)
        tone_row_lay.addWidget(self._tone_plot_clear_btn)
        tone_lay.addWidget(tone_row)

        # Numeric monitor line (raised up into the block, not at the bottom).
        self._genmon_line = QLabel("idle")
        self._genmon_line.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        tone_lay.addWidget(self._genmon_line)

        if self._plot_available:
            self._init_tone_plot_widgets(tone_lay)
        mid_lay.addWidget(self._tone_group)
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

        # HR source UI initial state.
        if getattr(self, "_hr_source", "open") == "etalon":
            self._hr_source_cb.setCurrentIndex(1)
        else:
            self._hr_source_cb.setCurrentIndex(0)
        self._hr_com_port_le.setText(self._hr_com_port or "COM3")
        self._refresh_etalon_status()

        self.setCentralWidget(cw)
        self.resize(1180, 700)

        self._eeg_timer = QTimer(self)
        self._eeg_timer.timeout.connect(self._eeg_tick)
        if self._eeg_it is not None:
            self._eeg_timer.start(200)

        if auto_start_ble and self._ble_address:
            QTimer.singleShot(300, self._start_ble)
        QTimer.singleShot(400, self._maybe_start_etalon_hr)
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
        if self._plot_available:
            self._clear_tone_plot()

    def _append_log(self, text: str) -> None:
        # Lightweight UI log: use chat view (already scrollable) + stderr.
        # Keep it robust: never raise from logging paths.
        try:
            msg = str(text)
        except Exception:
            msg = "<log>"
        try:
            if hasattr(self, "_chat_view"):
                self._chat_append_html(f"<span style='color:#999'><i>{html_escape(msg)}</i></span>")
        except Exception:
            pass
        try:
            print(msg, file=sys.stderr)
        except Exception:
            pass

    def _etalon_restart_delay_ms_default(self) -> int:
        raw = os.environ.get("NSP_HR_COM_RESTART_DELAY_MS", "900")
        try:
            v = int(str(raw).strip())
        except ValueError:
            v = 900
        return max(200, min(v, 8000))

    def _etalon_com_stop_wait_s(self) -> float:
        raw = os.environ.get("NSP_HR_COM_STOP_WAIT_S", "6")
        try:
            v = float(str(raw).strip())
        except ValueError:
            v = 6.0
        return max(1.0, min(v, 30.0))

    def _stop_etalon_com_thread_blocking(self) -> None:
        """Stop COM HR reader and wait until the port is released (Windows COM quirk)."""
        th = self._hr_com_thread
        if th is None:
            return
        try:
            th.request_stop()
        except Exception:
            pass
        deadline = time.monotonic() + float(self._etalon_com_stop_wait_s())
        while th.isRunning() and time.monotonic() < deadline:
            try:
                th.wait(150)
            except Exception:
                break
        # Detach UI slots so stray signals never fire after restart.
        for sig_name in (
            "started",
            "heartRateReady",
            "metricsReady",
            "signalQualityReady",
            "bandsReady",
            "debugEvent",
            "connectionFailed",
            "workerFinished",
        ):
            sig = getattr(th, sig_name, None)
            if sig is None:
                continue
            try:
                sig.disconnect()
            except TypeError:
                pass
        try:
            QApplication.processEvents()
        except Exception:
            pass
        if self._hr_com_thread is th:
            self._hr_com_thread = None

    def _apply_hr_source_ui(self) -> None:
        try:
            mode = str(self._hr_source_cb.currentData() or "open").strip().lower()
            self._hr_source = mode if mode in {"open", "etalon"} else "open"
            self._hr_com_port = (self._hr_com_port_le.text() or "COM3").strip()
            # Persist into env-style fields for logs/debug (no global os.environ mutation).
            self._append_log(f"HR источник: {self._hr_source} (COM={self._hr_com_port})")
            if self._hr_source == "etalon":
                # Avoid showing stale value from open HR while waiting for COM to deliver first sample.
                self._last_hr_bpm = None
                self._last_hr_at = None
                self._last_etalon_event_at = None
                self._etalon_started_at = None
                self._refresh_etalon_status()
            self._maybe_start_etalon_hr(force_restart=True)
        except Exception as exc:
            # Never let UI handler crash: it can orphan running threads.
            self._etalon_errors += 1
            self._etalon_last_error = f"ui_apply_failed: {exc}"
            try:
                self._refresh_etalon_status()
            except Exception:
                pass
            try:
                self._append_log(f"HR UI ошибка: {exc}")
            except Exception:
                pass

    def _maybe_start_etalon_hr(self, *, force_restart: bool = False) -> None:
        want = getattr(self, "_hr_source", "open") == "etalon"
        th = self._hr_com_thread
        if th is not None and (force_restart or not want):
            self._stop_etalon_com_thread_blocking()
            self._refresh_etalon_status()
            if want:
                # Restart after delay so Windows releases COM handle (semaphore timeout otherwise).
                delay_ms = int(self._etalon_restart_delay_ms)
                QTimer.singleShot(delay_ms, lambda: self._maybe_start_etalon_hr(force_restart=False))
                return

        if not want or self._hr_com_thread is not None:
            return

        # Preflight checks to avoid "silent zeros" when the COM parser can't start.
        pre_err = self._etalon_preflight_error()
        if pre_err:
            self._etalon_errors += 1
            self._etalon_last_error = str(pre_err)
            self._append_log(f"HR эталон (COM) не запущен: {pre_err}")
            self._refresh_etalon_status()
            return

        th = MacrotellectComHrThread(
            port=self._hr_com_port or "COM3",
            pyd_dir=self._hr_pyd_dir or None,
            parent=self,
        )
        qc = Qt.ConnectionType.QueuedConnection
        th.started.connect(self._on_etalon_thread_started, qc)
        th.heartRateReady.connect(self._on_etalon_hr, qc)
        th.debugEvent.connect(self._on_etalon_hr_debug, qc)
        th.connectionFailed.connect(self._on_etalon_hr_failed, qc)
        th.workerFinished.connect(self._on_etalon_hr_finished, qc)
        # BrainLink often stops BLE EEG while COM stream is active — reuse COM EEG for UI.
        feed_com_eeg = str(os.environ.get("NSP_HR_ETALON_FEED_EEG", "1") or "1").strip().lower()
        if feed_com_eeg not in {"0", "false", "no", "off"}:
            th.metricsReady.connect(self._on_ble_metrics, qc)
            th.signalQualityReady.connect(self._on_ble_signal_quality, qc)
            th.bandsReady.connect(self._on_ble_bands, qc)
        self._hr_com_thread = th
        th.start()
        self._refresh_etalon_status()
        # If thread fails to start (or dies immediately), surface it quickly.
        QTimer.singleShot(500, self._check_etalon_thread_alive)

    def _on_etalon_hr_failed(self, msg: str) -> None:
        self._etalon_errors += 1
        self._etalon_last_error = str(msg)
        self._append_log(f"HR эталон (COM) ошибка: {msg}")
        self._refresh_etalon_status()

    def _on_etalon_thread_started(self) -> None:
        self._etalon_started_at = time.monotonic()
        self._refresh_etalon_status()

    def _on_etalon_hr_finished(self) -> None:
        self._hr_com_thread = None
        # User switched to open HR — normal shutdown, not an error.
        if getattr(self, "_hr_source", "open") != "etalon":
            self._refresh_etalon_status()
            return
        # If the thread stops immediately without events, surface it.
        if self._last_etalon_event_at is None:
            self._etalon_errors += 1
            if not self._etalon_last_error:
                self._etalon_last_error = "поток завершился без событий (порт/драйвер/pyD?)"
        self._refresh_etalon_status()

    def _check_etalon_thread_alive(self) -> None:
        if getattr(self, "_hr_source", "open") != "etalon":
            return
        th = self._hr_com_thread
        if th is None:
            return
        if not th.isRunning():
            self._etalon_errors += 1
            if not self._etalon_last_error:
                self._etalon_last_error = "поток не запустился (см. лог, проверьте COM/pyD)"
            self._refresh_etalon_status()

    def _on_etalon_hr(self, bpm: int) -> None:
        self._etalon_last_error = ""
        self._last_etalon_event_at = time.monotonic()
        self._refresh_etalon_status()
        self._on_ble_heart_rate(int(bpm), source="macrotellect_com")

    def _on_etalon_hr_debug(self, payload: dict) -> None:
        # Optional diagnostics into session log (opt-in in com thread).
        self._last_etalon_event_at = time.monotonic()
        try:
            k = str((payload or {}).get("kind") or "")
            if k == "rr":
                self._etalon_rr_events += 1
            elif k == "extend":
                self._etalon_extend_events += 1
        except Exception:
            pass
        self._refresh_etalon_status()
        try:
            self._write_event("hr_etalon_debug", {"etalon": dict(payload or {})})
        except Exception:
            pass

    def _refresh_etalon_status(self) -> None:
        if not hasattr(self, "_hr_etalon_status"):
            return
        if getattr(self, "_hr_source", "open") != "etalon":
            self._hr_etalon_status.setText("")
            return
        alive = self._hr_com_thread is not None and self._hr_com_thread.isRunning()
        age = None
        if self._last_etalon_event_at is not None:
            age = max(0.0, time.monotonic() - float(self._last_etalon_event_at))
        parts = []
        parts.append("COM поток: " + ("OK" if alive else "нет"))
        parts.append(f"rr={int(self._etalon_rr_events)}")
        parts.append(f"extend={int(self._etalon_extend_events)}")
        parts.append(f"errors={int(self._etalon_errors)}")
        if self._etalon_started_at is None:
            parts.append("started=—")
        else:
            parts.append(f"started={max(0.0, time.monotonic() - float(self._etalon_started_at)):.1f}s")
        if age is None:
            parts.append("last=—")
        else:
            parts.append(f"last={age:.1f}s")
        if (self._etalon_last_error or "").strip():
            parts.append(f"err={self._etalon_last_error}")
        self._hr_etalon_status.setText(" | ".join(parts))

    def _etalon_preflight_error(self) -> str | None:
        # 1) pyserial import
        try:
            import serial  # type: ignore[import-not-found]
        except Exception:
            return "нет зависимости pyserial (установите: pip install -e . или pip install pyserial)"

        # 2) BrainLinkParser.pyd presence (if pyd_dir is the default or explicitly set)
        candidates: list[Path] = []
        try:
            if (self._hr_pyd_dir or "").strip():
                candidates.append(Path(self._hr_pyd_dir))
        except Exception:
            pass
        # Common case: run from repo root (cwd points at LLM_CHAT).
        try:
            candidates.append(Path.cwd() / "docs/specs/vendor/macrotellect_brainlink_parser")
        except Exception:
            pass
        # Source tree layout: src/neurosync_pro/ui/meditation_poc.py → repo root is parents[3].
        try:
            candidates.append(Path(__file__).resolve().parents[3] / "docs/specs/vendor/macrotellect_brainlink_parser")
        except Exception:
            pass
        # Installed layout fallback: if neurosync_pro lives under site-packages, parents[3] isn't repo.
        # Try one more level up (harmless if it doesn't exist).
        try:
            candidates.append(Path(__file__).resolve().parents[4] / "docs/specs/vendor/macrotellect_brainlink_parser")
        except Exception:
            pass

        tried: list[str] = []
        for d in candidates:
            d2 = d.expanduser()
            tried.append(str(d2))
            pyd_file = d2 / "BrainLinkParser.pyd"
            if pyd_file.is_file():
                break
        else:
            tried_s = "; ".join(tried) if tried else "<none>"
            return (
                "не найден BrainLinkParser.pyd. "
                f"Проверили: {tried_s}. "
                "Задайте NSP_HR_PYD_DIR или --hr-pyd-dir"
            )

        return None

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

    def _ensure_noise_stream(self) -> bool:
        if not self._noise_available:
            return False
        if self._noise_stream is None:
            try:
                self._noise_stream = ToneSweepStream(StreamConfig(sample_rate=48000, channels=2))
                self._noise_stream.set_fades(float(self._noise_fade_in_s), float(self._noise_fade_out_s))
                self._noise_stream.start()
            except Exception as exc:
                self._status.setText(f"Audio ошибка (noise): {exc}")
                self._noise_stream = None
                if hasattr(self, "_noise_cb"):
                    self._noise_cb.setChecked(False)
                return False
        return True

    def _stop_noise_stream(self) -> None:
        st = self._noise_stream
        self._noise_stream = None
        if st is not None:
            try:
                st.stop()
            except Exception:
                pass

    def _toggle_noise(self, on: bool) -> None:
        self._noise_enabled = bool(on)
        if not self._noise_enabled:
            self._request_noise_fadeout_and_close()
            self._refresh_tone_monitor_labels()
            return
        if not self._ensure_noise_stream():
            return
        try:
            self._noise_stream.set_volume(float(self._noise_vol))
            self._noise_stream.play_noise(color=str(self._noise_color))
        except Exception as exc:
            self._status.setText(f"Audio ошибка (noise): {exc}")
            self._stop_noise_stream()
            if hasattr(self, "_noise_cb"):
                self._noise_cb.setChecked(False)
            return
        self._refresh_tone_monitor_labels()

    def _noise_vol_changed(self, v: float) -> None:
        self._noise_vol = float(v)
        if self._noise_enabled and self._noise_stream is not None:
            try:
                self._noise_stream.set_volume(float(self._noise_vol))
            except Exception:
                pass
        self._refresh_tone_monitor_labels()

    def _noise_color_changed(self, _idx: int) -> None:
        data = None
        if hasattr(self, "_noise_color_cb"):
            data = self._noise_color_cb.currentData()
        c = str(data or "white")
        if c not in ("white", "pink", "brown"):
            c = "white"
        self._noise_color = c
        if self._noise_enabled and self._noise_stream is not None:
            try:
                # Restart noise to reset filter state deterministically.
                self._noise_stream.play_noise(color=str(self._noise_color))
            except Exception:
                pass
        self._refresh_tone_monitor_labels()

    def _request_noise_fadeout_and_close(self) -> None:
        st = self._noise_stream
        if st is None:
            return
        try:
            st.idle()
        except Exception:
            self._stop_noise_stream()
            return
        self._noise_stop_gen += 1
        gen = int(self._noise_stop_gen)
        ms = int((float(self._noise_fade_out_s) + 0.10) * 1000.0)
        QTimer.singleShot(ms, lambda: self._maybe_close_noise_stream(gen))

    def _maybe_close_noise_stream(self, gen: int) -> None:
        if int(gen) != int(self._noise_stop_gen):
            return
        if self._noise_enabled:
            return
        self._stop_noise_stream()

    def _set_tone_base_text(self, text: str) -> None:
        self._tone_base_text = str(text or "—")
        self._refresh_tone_monitor_labels()

    def _refresh_tone_monitor_labels(self) -> None:
        # Show tone values and optionally noise volume in the same compact line.
        if hasattr(self, "_tone_vals_lbl"):
            base = str(getattr(self, "_tone_base_text", "—"))
            if base.strip() in ("", "—"):
                base = "—"
            suffix = (
                f" | noise {str(self._noise_color)} v={float(self._noise_vol):.2f}"
                if self._noise_enabled
                else ""
            )
            self._tone_vals_lbl.setText(f"{base}{suffix}" if base != "—" else (f"—{suffix}"))

    # --- Programmer (agent-driven) ---
    def _prog_sink_changed(self, text: str) -> None:
        self._prog_sink_url = str(text or "").strip()

    def _chat_sync_auto_apply_enabled_state(self) -> None:
        free = self._chat_free_cb.isChecked()
        ff = free and self._chat_freeflight_cb.isChecked()
        structured = not free
        self._chat_auto_apply_cb.setEnabled(structured or ff)

    def _chat_freeflight_toggled(self, checked: bool) -> None:
        if not self._chat_free_cb.isChecked():
            return
        self._chat_sync_auto_apply_enabled_state()
        if not checked:
            self._chat_stop_metric_loop()
            self._chat_auto_apply_cb.setChecked(False)
            self._chat_apply_btn.setEnabled(False)

    def _chat_free_mode_changed(self, checked: bool) -> None:
        free = checked
        structured = not free
        self._chat_agent_runtime_policy_cb.setEnabled(structured)
        self._chat_freeflight_cb.setEnabled(free)
        if structured:
            self._chat_stop_metric_loop()
            self._chat_freeflight_cb.setChecked(False)
        self._chat_sync_auto_apply_enabled_state()
        if free:
            if not self._chat_freeflight_cb.isChecked():
                self._chat_auto_apply_cb.setChecked(False)
            self._chat_apply_btn.setEnabled(False)
            self._chat_last_decision = None

    def _chat_cooldown_s(self) -> float:
        raw = os.environ.get("NSP_COOLDOWN_S", "12")
        try:
            return max(0.0, float(str(raw).strip()))
        except ValueError:
            return 12.0

    def _chat_command_ready(self, d: Decision) -> bool:
        if d.action == "hold":
            return False
        if d.action == "stop":
            return True
        if not self._prog_available:
            return False
        if d.action == "set_spec" and bool(d.spec and str(d.spec).strip()):
            return True
        if d.action == "set_timeline" and bool(d.timeline and str(d.timeline).strip()):
            return True
        return False

    def _chat_apply_decision(self, d: Decision) -> None:
        structured_chat = not self._chat_free_cb.isChecked()
        if structured_chat and self._chat_agent_runtime_policy_cb.isChecked():
            filtered = apply_decision_policy(
                d,
                self._chat_policy_state,
                mode="local",
                cooldown_s=self._chat_cooldown_s(),
            )
            if filtered.action == "hold":
                self._chat_append_html(
                    "<span style='color:#fa0'><i>Не применено: "
                    f"{html_escape(filtered.reason_code)} "
                    "(политика как у agent_runtime: debounce → cooldown → rate_limit).</i></span>"
                )
                return
        else:
            filtered = d
        if not self._chat_command_ready(filtered):
            return
        if filtered.action == "set_spec" and filtered.spec:
            self._on_program_set_spec({"spec": filtered.spec})
        elif filtered.action == "set_timeline" and filtered.timeline:
            self._on_program_set_timeline({"timeline": filtered.timeline})
        elif filtered.action == "stop":
            self._prog_stop()
        else:
            return
        commit_decision_state(filtered, self._chat_policy_state)

    def _chat_metrics_hint(self) -> str:
        parts: list[str] = []
        if hasattr(self, "_att_val"):
            parts.append(str(self._att_val.text()))
        if hasattr(self, "_med_val"):
            parts.append(str(self._med_val.text()))
        if hasattr(self, "_hr_val"):
            parts.append(str(self._hr_val.text()))
        return " | ".join(p for p in parts if p.strip())

    def _chat_tpl_insert(self, text: str) -> None:
        if hasattr(self, "_chat_input"):
            self._chat_input.setPlainText(text)
            self._chat_input.setFocus()

    def _chat_load_profile(self) -> None:
        try:
            data = load_ui_profile(resolved_profile_path())
            url = str(data.get("ollama_base_url") or "").strip()
            if url:
                self._chat_base_url.setText(url)
            model = str(data.get("ollama_model") or "").strip()
            if model:
                self._chat_model.setText(model)
            self._chat_free_cb.setChecked(bool(data.get("chat_free_form", True)))
            self._chat_freeflight_cb.setChecked(bool(data.get("chat_freeflight", False)))
            self._chat_auto_apply_cb.setChecked(bool(data.get("chat_auto_apply", False)))
            self._chat_agent_runtime_policy_cb.setChecked(
                bool(data.get("chat_agent_runtime_policy", False))
            )
            self._chat_free_mode_changed(self._chat_free_cb.isChecked())
        except Exception:
            pass

    def _chat_save_profile(self) -> None:
        if not hasattr(self, "_chat_base_url"):
            return
        save_ui_profile(
            {
                "ollama_base_url": self._chat_base_url.text().strip(),
                "ollama_model": self._chat_model.text().strip(),
                "chat_free_form": self._chat_free_cb.isChecked(),
                "chat_auto_apply": self._chat_auto_apply_cb.isChecked(),
                "chat_agent_runtime_policy": self._chat_agent_runtime_policy_cb.isChecked(),
                "chat_freeflight": self._chat_freeflight_cb.isChecked(),
            },
            resolved_profile_path(),
        )

    def _chat_metric_loop_interval_ms_default(self) -> int:
        raw = os.environ.get("NSP_CHAT_METRIC_LOOP_S", "30")
        try:
            sec = float(str(raw).strip())
        except ValueError:
            sec = 30.0
        sec = max(5.0, min(sec, 600.0))
        return int(sec * 1000)

    def _hr_stale_s(self) -> float:
        raw = os.environ.get("NSP_HR_STALE_S", "30")
        try:
            v = float(str(raw).strip())
        except ValueError:
            v = 30.0
        return max(5.0, min(v, 600.0))

    def _chat_build_system_prompt(self) -> str:
        free = self._chat_free_cb.isChecked()
        ff = free and self._chat_freeflight_cb.isChecked()
        if free and ff:
            return CHAT_FREE_FLIGHT_SYSTEM_PROMPT
        if free:
            return (
                "Ты помощник в приложении NeuroSync Pro (медитация, бинауральные биения, цветной шум). "
                "Отвечай по-русски, кратко и по делу.\n\n"
                + CHAT_CAPABILITY_GROUNDING_RU
            )
        return (
            CHAT_STRUCTURED_SYSTEM_PROMPT.strip()
            + "\n\n"
            + CHAT_CAPABILITY_GROUNDING_RU
            + "\n\n"
            + "Язык программатора (spec):\n"
            + '- "<carrier>+<beat>/<amp>" (пример: "200+7/0.60")\n'
            + '- "<color>/<amp>", где color=white|pink|brown (пример: "white/0.70")\n'
            + '- "sweep:<f0>-><f1>/<dur>/<amp>" (пример: "sweep:1000->100/30/0.6")\n'
            + '- "off"\n'
            + "\n"
            + "Тайминг: action set_timeline, поле timeline — одна строка с переносами \\n ИЛИ JSON-массив строк "
            + "(каждая «mm:ss spec»). "
            + "Пример белый шум 30 с:\n"
            + '{"action":"set_timeline","timeline":"0:00 white/0.70\\n0:30 off","confidence":0.9,"reason_code":"user"}\n'
            + 'Остановка: {"action":"stop","confidence":1,"reason_code":"user"}\n'
            + "Проверка связи без смены программы — всё равно JSON с action hold, но обязательно добавь короткий текст в "
            + 'assistant_reply по-русски, например: {"action":"hold","confidence":1,"reason_code":"liaison",'
            + '"assistant_reply":"На связи, приём. Готов к командам set_spec / set_timeline / stop."}\n'
            + "\n"
            + "НЕ смешивай liaison с творческими задачами оператора:\n"
            + "- «Оператор / на связи / приём» без запроса звука → hold + reason_code liaison + assistant_reply.\n"
            + "- «Включите белый шум на N секунд» → только set_timeline: 0:00 white/… и 0:05 off для 5 с и т.п.\n"
            + "- «Свободный полёт», «свободное управление», «пробуйте звуком повлиять», «настройте под медитацию» — это полномочие "
            + "автономии: верни set_spec или короткий set_timeline по числам Attention/Meditation из контекста, "
            + "reason_code operator_autopilot или creative_patch; короткий assistant_reply одним предложением что сделали. "
            + "Не отвечай hold с отговоркой «нужна спецификация», если оператор сам разрешил свободу.\n"
            + "Верни только JSON. Не используй псевдоформаты типа white_noise, duration=30s."
        )

    def _chat_full_user_from_body(self, user_text: str) -> str:
        free = self._chat_free_cb.isChecked()
        ff = free and self._chat_freeflight_cb.isChecked()
        hint = self._chat_metrics_hint()
        if hint and (not free or ff):
            return f"[контекст интерфейса: {hint}]\n{user_text}"
        return user_text

    def _chat_dispatch_message(self, full_user: str) -> None:
        if self._chat_worker is not None and self._chat_worker.isRunning():
            return
        text = (full_user or "").strip()
        if not text:
            return
        base_url = self._chat_base_url.text().strip()
        model = self._chat_model.text().strip()
        if not base_url or not model:
            self._chat_append_html("<span style='color:#f66'><b>Ошибка:</b> задайте Ollama URL и имя модели.</span>")
            return

        system_prompt = self._chat_build_system_prompt()
        messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
        messages.extend(self._chat_messages)
        messages.append({"role": "user", "content": text})

        timeout_s = float(os.environ.get("NSP_MODEL_TIMEOUT_S", "120"))

        self._chat_pending_user = text
        self._chat_append_html(f"<b>Вы:</b> {html_escape(text)}")
        self._chat_send_btn.setEnabled(False)

        self._chat_worker = _OllamaChatWorker(
            base_url=base_url,
            model=model,
            messages=messages,
            timeout_s=timeout_s,
            parent=self,
        )
        self._chat_worker.finished_ok.connect(self._chat_on_reply_ok)
        self._chat_worker.finished_err.connect(self._chat_on_reply_err)
        self._chat_worker.finished.connect(self._chat_on_worker_finished)
        self._chat_worker.start()

    def _chat_metric_loop_body_for_goal(self, goal: str) -> str:
        sec = max(self._chat_metric_loop_interval_ms, 1) / 1000.0
        n = self._chat_metric_loop_tick_index
        tact = "Первый такт цикла." if n <= 1 else f"Очередной такт цикла (сообщение №{n})."
        mandate = (
            f"{tact} Оператор передаёт управление, как в фразе: «хочу дать вам управление — попробуйте влиять на показатели»; "
            f"это **автоматический опрос каждые ~{sec:g} с** только для режима свободного полёта. "
            "Ты можешь **экспериментировать**: пробовать новые сочетания параметров в пределах языка программатора, смотреть на метрики и корректировать курс.\n"
        )
        rules = (
            "Честность: не утверждай, что звук уже изменён, пока оператор не применил JSON (или автоприменение); "
            "если предлагаешь правку — один блок ```json с валидным set_spec или set_timeline "
            "(spec только допустимые токены; timeline одной строкой с \\n или JSON-массивом строк).\n"
        )
        if goal == "attention":
            focus = (
                "Приоритет эксперимента: **Attention** — подстраивай звук так, чтобы поддержать или мягко поднять внимание по числу Attention, "
                "не жертвуя Meditation без нужды."
            )
        elif goal == "meditation":
            focus = (
                "Приоритет эксперимента: **Meditation** — подстраивай программу под показатель Meditation (глубина/стабильность), "
                "избегая резких скачков."
            )
        else:
            focus = (
                "Приоритет эксперимента: **A/M** — ищи баланс Attention и Meditation; не дави на одну метрику ценой резкого обвала другой."
            )
        tail = (
            "\nКратко: что видишь по цифрам по сравнению с прошлым ответом в этой сессии; "
            "если хочешь сменить гипотезу — выдай команду программатора или коротко объясни, почему держишь hold без JSON."
        )
        return mandate + rules + focus + tail

    def _chat_start_metric_loop(self, goal: str) -> None:
        if not (self._chat_free_cb.isChecked() and self._chat_freeflight_cb.isChecked()):
            self._chat_append_html(
                "<span style='color:#fa0'><b>Автотакт:</b> включите «Свободное общение» и «Свободный полёт». "
                "Для самого применения команд удобно также «Автоприменение JSON».</span>"
            )
            return
        self._chat_metric_loop_goal = goal
        self._chat_metric_loop_tick_index = 0
        self._chat_metric_loop_interval_ms = self._chat_metric_loop_interval_ms_default()
        self._chat_metric_loop_timer.setInterval(self._chat_metric_loop_interval_ms)
        if not self._chat_metric_loop_timer.isActive():
            self._chat_metric_loop_timer.start()
        sec = self._chat_metric_loop_interval_ms / 1000.0
        self._chat_append_html(
            f"<span style='color:#8cf'><i>Свободный полёт: автотакт каждые {sec:g} с (эксперименты модели по метрикам), цель "
            f"<b>{html_escape(goal)}</b>. Остановка — «Стоп». Интервал: <code>NSP_CHAT_METRIC_LOOP_S</code>.</i></span>"
        )
        self._chat_metric_loop_tick()

    def _chat_stop_metric_loop(self) -> None:
        self._chat_metric_loop_goal = None
        self._chat_metric_loop_tick_index = 0
        self._chat_metric_loop_timer.stop()

    def _chat_metric_loop_tick(self) -> None:
        if not self._chat_metric_loop_goal:
            self._chat_metric_loop_timer.stop()
            return
        if self._chat_worker is not None and self._chat_worker.isRunning():
            return
        goal = self._chat_metric_loop_goal
        assert goal is not None
        self._chat_metric_loop_tick_index += 1
        hint = self._chat_metrics_hint()
        ctx = f"[контекст интерфейса: {hint}]\n" if hint else ""
        body = self._chat_metric_loop_body_for_goal(goal)
        full_user = ctx + body
        self._chat_dispatch_message(full_user)

    def _chat_tpl_stop_clicked(self) -> None:
        self._chat_stop_metric_loop()
        self._chat_tpl_insert('{"action":"stop","confidence":1,"reason_code":"tpl"}')

    def _chat_send_clicked(self) -> None:
        if self._chat_worker is not None and self._chat_worker.isRunning():
            return
        user_text = self._chat_input.toPlainText().strip()
        if not user_text:
            return
        full_user = self._chat_full_user_from_body(user_text)
        self._chat_dispatch_message(full_user)
        self._chat_input.clear()

    def _chat_append_html(self, html: str) -> None:
        self._chat_view.append(html)
        sb = self._chat_view.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _chat_on_reply_ok(self, content: str) -> None:
        self._chat_append_html(f"<b>Модель:</b> {html_escape(content)}")
        u = str(getattr(self, "_chat_pending_user", "") or "")
        if u:
            self._chat_messages.append({"role": "user", "content": u})
            self._chat_messages.append({"role": "assistant", "content": content})
            while len(self._chat_messages) > 24:
                self._chat_messages.pop(0)

        free = self._chat_free_cb.isChecked()
        self._chat_last_decision = None
        if hasattr(self, "_chat_pending_user"):
            del self._chat_pending_user

        if free:
            if self._chat_freeflight_cb.isChecked():
                parsed_ff = try_parse_ready_program_decision(
                    content,
                    command_ready=self._chat_command_ready,
                )
                self._chat_last_decision = parsed_ff
                can_ff = parsed_ff is not None
                self._chat_apply_btn.setEnabled(can_ff)
                if can_ff and not self._prog_available:
                    self._chat_append_html(
                        "<span style='color:#f66'><i>Программатор недоступен без аудио (extras). "
                        "Команда в ответе есть, но не может быть применена.</i></span>"
                    )
                elif can_ff:
                    self._chat_append_html(
                        "<span style='color:#7b9'><i>Распознана команда программатора — «Применить команду» "
                        "или включите автоприменение.</i></span>"
                    )
                elif "```" in content and not can_ff:
                    self._chat_append_html(
                        "<span style='color:#fa0'><i>В ответе есть блок ```…```, но команда не прошла проверку "
                        "(часто модель вставляет описательный текст вместо языка spec). "
                        "Нужны только токены: carrier+beat/amp, white|pink|brown/amp, sweep:…, off — см. подсказку режима "
                        "«Свободный полёт» или docs/specs/programmer_commands.md.</i></span>"
                    )
                if self._chat_auto_apply_cb.isChecked() and can_ff and parsed_ff is not None:
                    self._chat_apply_decision(parsed_ff)
            else:
                self._chat_apply_btn.setEnabled(False)
            return

        parsed = validate_and_normalize(strip_md_json_fence(content), source="chat_ui")
        self._chat_last_decision = parsed
        can_apply = self._chat_command_ready(parsed)
        self._chat_apply_btn.setEnabled(can_apply)

        if parsed.action == "hold":
            reset_llm_debounce(self._chat_policy_state)
            ar = (parsed.assistant_reply or "").strip()
            body = _chat_strip_context_prefix(u)
            rc_l = (parsed.reason_code or "").strip().lower()
            liaison_ctx = _chat_is_liaison_ping(body) or rc_l in _LIAISON_REASON_CODES
            used_ui_fallback = False
            if not ar and liaison_ctx:
                ar = _CHAT_LIAISON_FALLBACK_RU
                used_ui_fallback = True
            if ar:
                self._chat_append_html(
                    "<span style='color:#9cf'><b>Оператор:</b> "
                    f"{html_escape(ar)}</span>"
                )
            if used_ui_fallback:
                self._chat_append_html(
                    "<span style='color:#777'><i>Программа без изменений (hold). "
                    "Ниже — стандартное подтверждение связи в интерфейсе.</i></span>"
                )
            else:
                self._chat_append_html(
                    "<span style='color:#888'><i>Программа не менялась (hold"
                    f"{', ' + html_escape(parsed.reason_code) if parsed.reason_code else ''}"
                    "). Действия программатора: set_spec, set_timeline, stop.</i></span>"
                )
        elif parsed.action in ("set_spec", "set_timeline") and not self._prog_available:
            self._chat_append_html(
                "<span style='color:#f66'><i>Программатор недоступен без аудио (extras). "
                "Команда распознана, но не может быть применена.</i></span>"
            )
        elif parsed.action == "stop" or can_apply:
            pass
        else:
            self._chat_append_html(
                "<span style='color:#aaa'><i>Команда неполная. Примеры spec: "
                "200+7/0.60 | white/0.70 | sweep:1000->100/30/0.6 | off. "
                "Для таймера: set_timeline с строками mm:ss spec.</i></span>"
            )

        if self._chat_auto_apply_cb.isChecked() and can_apply:
            self._chat_apply_decision(parsed)

    def _chat_on_reply_err(self, err: str) -> None:
        self._chat_append_html(f"<span style='color:#f66'><b>Ollama:</b> {html_escape(err)}</span>")
        if hasattr(self, "_chat_pending_user"):
            del self._chat_pending_user

    def _chat_on_worker_finished(self) -> None:
        self._chat_send_btn.setEnabled(True)
        if self._chat_worker is not None:
            self._chat_worker.deleteLater()
            self._chat_worker = None

    def _chat_apply_clicked(self) -> None:
        d = self._chat_last_decision
        if d is None:
            return
        self._chat_apply_decision(d)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if hasattr(self, "_chat_input") and obj is self._chat_input and event.type() == QEvent.Type.KeyPress:
            ke = event
            if isinstance(ke, QKeyEvent) and ke.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if ke.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                    return False
                self._chat_send_clicked()
                return True
        return super().eventFilter(obj, event)

    def _prog_run_clicked(self) -> None:
        if hasattr(self, "_prog_tabs") and int(self._prog_tabs.currentIndex()) == 1:
            self._prog_start_timeline(str(self._prog_tl_edit.toPlainText()))
            return
        spec = str(self._prog_spec_edit.text() if hasattr(self, "_prog_spec_edit") else self._prog_spec)
        self._prog_start(spec)

    def _prog_stop_clicked(self) -> None:
        self._prog_stop()

    def _on_program_set_spec(self, payload: Any) -> None:
        # payload can be {"spec": "..."} or raw string.
        spec = ""
        if isinstance(payload, dict):
            spec = str(payload.get("spec") or "")
        else:
            spec = str(payload or "")
        spec = spec.strip()
        if spec:
            if hasattr(self, "_prog_spec_edit"):
                self._prog_spec_edit.setText(spec)
            self._prog_start(spec)

    def _on_program_set_timeline(self, payload: Any) -> None:
        text = ""
        if isinstance(payload, dict):
            text = str(payload.get("timeline") or payload.get("text") or "")
        else:
            text = str(payload or "")
        text = text.strip()
        if not text:
            return
        if hasattr(self, "_prog_tabs"):
            self._prog_tabs.setCurrentIndex(1)
        if hasattr(self, "_prog_tl_edit"):
            self._prog_tl_edit.setPlainText(text)
        self._prog_start_timeline(text)

    def _ensure_prog_tone_stream(self) -> bool:
        if not self._prog_available:
            return False
        if self._prog_tone_stream is None:
            try:
                self._prog_tone_stream = ToneSweepStream(StreamConfig(sample_rate=48000, channels=2))
                self._prog_tone_stream.set_fades(0.02, 0.08)
                self._prog_tone_stream.start()
            except Exception as exc:
                self._status.setText(f"Audio ошибка (prog tone): {exc}")
                self._prog_tone_stream = None
                return False
        return True

    def _ensure_prog_noise_stream(self) -> bool:
        if not self._prog_available:
            return False
        if self._prog_noise_stream is None:
            try:
                self._prog_noise_stream = ToneSweepStream(StreamConfig(sample_rate=48000, channels=2))
                self._prog_noise_stream.set_fades(0.02, 0.08)
                self._prog_noise_stream.start()
            except Exception as exc:
                self._status.setText(f"Audio ошибка (prog noise): {exc}")
                self._prog_noise_stream = None
                return False
        return True

    def _prog_parse_spec(self, spec: str) -> dict[str, Any]:
        # Supported: "<carrier>+<beat>/<amp>" and "<color>/<amp>" and "off|-"
        s = (spec or "").strip()
        if not s or s in ("-", "off", "idle"):
            return {"off": True}
        out: dict[str, Any] = {"off": False, "tone": None, "noise": None, "sweep": None}
        for part in s.split():
            p = part.strip()
            if not p:
                continue
            if p in ("-", "off", "idle"):
                out["off"] = True
                continue
            if p.startswith("sweep:"):
                # sweep:f0->f1/dur/amp
                try:
                    rhs = p[len("sweep:") :]
                    arrow = rhs.split("->", 1)
                    f0 = float(arrow[0])
                    tail = arrow[1]
                    bits = tail.split("/")
                    f1 = float(bits[0])
                    dur = float(bits[1]) if len(bits) > 1 else 10.0
                    amp = float(bits[2]) if len(bits) > 2 else 0.6
                    if amp > 1.0:
                        amp = amp / 100.0
                    amp = 0.0 if amp < 0.0 else 1.0 if amp > 1.0 else amp
                    out["sweep"] = {"f0": f0, "f1": f1, "dur": dur, "vol": amp}
                except Exception:
                    continue
                continue
            if "+" in p and "/" in p:
                # binaural: carrier+beat/amp
                try:
                    left = p.split("/", 1)[0]
                    amp_raw = p.split("/", 1)[1]
                    carrier_raw, beat_raw = left.split("+", 1)
                    carrier = float(carrier_raw)
                    beat = float(beat_raw)
                    amp = float(amp_raw)
                    if amp > 1.0:
                        amp = amp / 100.0
                    amp = 0.0 if amp < 0.0 else 1.0 if amp > 1.0 else amp
                    l = float(carrier) - float(beat) * 0.5
                    r = float(carrier) + float(beat) * 0.5
                    out["tone"] = {"l_hz": l, "r_hz": r, "vol": amp}
                except Exception:
                    continue
                continue
            if "/" in p:
                # noise color/amp
                try:
                    c_raw, a_raw = p.split("/", 1)
                    color = str(c_raw).lower().strip()
                    if color not in ("white", "pink", "brown"):
                        continue
                    vol = float(a_raw)
                    if vol > 1.0:
                        vol = vol / 100.0
                    vol = 0.0 if vol < 0.0 else 1.0 if vol > 1.0 else vol
                    out["noise"] = {"color": color, "vol": vol}
                except Exception:
                    continue
        return out

    def _prog_start(self, spec: str) -> None:
        self._prog_spec = str(spec or "").strip()
        ok_spec, spec_reason = validate_prog_spec(self._prog_spec)
        if not ok_spec:
            self._status.setText(
                f"Неверный spec ({spec_reason}). См. docs/specs/programmer_commands.md"
            )
            return
        parsed = self._prog_parse_spec(self._prog_spec)
        if parsed.get("off"):
            self._prog_stop()
            return
        sweep = parsed.get("sweep")
        tone = parsed.get("tone")
        noise = parsed.get("noise")
        if sweep and self._ensure_prog_tone_stream():
            self._prog_tone_vol = float(sweep["vol"])
            try:
                self._prog_tone_stream.set_volume_lr(self._prog_tone_vol, self._prog_tone_vol)
                self._prog_tone_stream.play_sweep(
                    f0_hz=float(sweep["f0"]),
                    f1_hz=float(sweep["f1"]),
                    duration_s=float(sweep["dur"]),
                    log=False,
                    loop=False,
                )
            except Exception as exc:
                self._status.setText(f"Audio ошибка (prog sweep): {exc}")
        elif tone and self._ensure_prog_tone_stream():
            self._prog_tone_left_hz = float(tone["l_hz"])
            self._prog_tone_right_hz = float(tone["r_hz"])
            self._prog_tone_vol = float(tone["vol"])
            try:
                self._prog_tone_stream.set_volume_lr(self._prog_tone_vol, self._prog_tone_vol)
                self._prog_tone_stream.play_binaural(self._prog_tone_left_hz, self._prog_tone_right_hz)
            except Exception as exc:
                self._status.setText(f"Audio ошибка (prog tone): {exc}")
        if noise and self._ensure_prog_noise_stream():
            self._prog_noise_color = str(noise["color"])
            self._prog_noise_vol = float(noise["vol"])
            try:
                self._prog_noise_stream.set_volume(float(self._prog_noise_vol))
                self._prog_noise_stream.play_noise(color=self._prog_noise_color)
            except Exception as exc:
                self._status.setText(f"Audio ошибка (prog noise): {exc}")
        self._prog_running = True
        if hasattr(self, "_prog_run_btn"):
            # Allow re-apply without stop in Spec mode.
            self._prog_run_btn.setEnabled(self._prog_available)
            if hasattr(self, "_prog_tabs") and int(self._prog_tabs.currentIndex()) == 0:
                self._prog_run_btn.setText("Apply")
        if hasattr(self, "_prog_stop_btn"):
            self._prog_stop_btn.setEnabled(True)
        self._prog_status_lbl.setText(f"running: {self._prog_spec}")
        self._write_event(
            "program.action",
            {"action": {"command": "set_spec", "by": "ui" if self.sender() is not None else "agent", "spec": str(self._prog_spec)}},
        )
        self._prog_emit_status()

    def _prog_clear_timers(self) -> None:
        if not getattr(self, "_prog_timers", None):
            return
        for t in list(self._prog_timers):
            try:
                t.stop()
            except Exception:
                pass
            try:
                t.deleteLater()
            except Exception:
                pass
        self._prog_timers.clear()

    def _prog_start_timeline(self, text: str) -> None:
        ok_tl, tl_reason = validate_timeline_body(text)
        if not ok_tl:
            self._status.setText(
                f"Неверный timeline ({tl_reason}). См. docs/specs/programmer_commands.md"
            )
            self._prog_status_lbl.setText("timeline: ошибка разбора")
            self._prog_timeline_running = False
            return
        self._prog_clear_timers()
        self._prog_timeline_running = True
        items: list[tuple[float, str]] = []
        for raw in (text or "").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "#" in line:
                line = line.split("#", 1)[0].strip()
            if not line:
                continue
            parts = line.split(None, 1)
            if len(parts) < 2:
                continue
            ts, spec = parts[0].strip(), parts[1].strip()
            tsec = self._parse_mmss(ts)
            if tsec is None:
                continue
            items.append((float(tsec), spec))
        items.sort(key=lambda x: x[0])
        if not items:
            self._prog_status_lbl.setText("timeline: пусто")
            self._prog_timeline_running = False
            return
        t0 = time.monotonic()
        for at_s, spec in items:
            delay_ms = max(0, int((float(at_s) - 0.0) * 1000.0))
            tm = QTimer(self)
            tm.setSingleShot(True)
            tm.timeout.connect(lambda s=spec: self._prog_start(s))
            tm.start(delay_ms)
            self._prog_timers.append(tm)
        self._prog_status_lbl.setText(f"timeline: {len(items)} шаг(ов)")
        self._write_event(
            "program.action",
            {"action": {"command": "set_timeline", "by": "ui", "timeline": str(text)}},
        )
        self._prog_emit_status()

    @staticmethod
    def _parse_mmss(ts: str) -> float | None:
        # accepts m:s, mm:ss, hh:mm:ss
        try:
            bits = [int(b) for b in ts.strip().split(":")]
        except Exception:
            return None
        if len(bits) == 2:
            m, s = bits
            return float(m * 60 + s)
        if len(bits) == 3:
            h, m, s = bits
            return float(h * 3600 + m * 60 + s)
        return None

    def _prog_stop(self) -> None:
        self._prog_clear_timers()
        self._prog_timeline_running = False
        self._prog_running = False
        # tone
        if self._prog_tone_stream is not None:
            try:
                self._prog_tone_stream.idle()
            except Exception:
                pass
            try:
                self._prog_tone_stream.stop()
            except Exception:
                pass
            self._prog_tone_stream = None
        # noise
        if self._prog_noise_stream is not None:
            try:
                self._prog_noise_stream.idle()
            except Exception:
                pass
            try:
                self._prog_noise_stream.stop()
            except Exception:
                pass
            self._prog_noise_stream = None
        if hasattr(self, "_prog_run_btn"):
            self._prog_run_btn.setEnabled(self._prog_available)
            self._prog_run_btn.setText("Run")
        if hasattr(self, "_prog_stop_btn"):
            self._prog_stop_btn.setEnabled(False)
        if hasattr(self, "_prog_status_lbl"):
            self._prog_status_lbl.setText("idle")
        self._write_event("program.action", {"action": {"command": "stop", "by": "ui"}})
        self._prog_emit_status()

    def _prog_emit_status(self) -> None:
        now = time.monotonic()
        if (now - float(self._prog_last_status_at)) < float(self._prog_status_min_s):
            return
        self._prog_last_status_at = now
        status = {
            "running": bool(self._prog_running),
            "spec": str(self._prog_spec),
            "tone": {"l_hz": float(self._prog_tone_left_hz), "r_hz": float(self._prog_tone_right_hz), "vol": float(self._prog_tone_vol)}
            if self._prog_running
            else None,
            "noise": {"color": str(self._prog_noise_color), "vol": float(self._prog_noise_vol)} if self._prog_running else None,
        }
        self._bus.publish("program.status", status)
        if str(self._prog_sink_url).strip():
            self._post_sink_event("program.status", status)

    def _post_sink_event(self, topic: str, payload: Any) -> None:
        url = str(self._prog_sink_url).strip()
        if not url:
            return
        body = json.dumps({"topic": str(topic), "payload": payload}, ensure_ascii=False).encode("utf-8")

        def _do() -> None:
            try:
                req = urllib.request.Request(
                    url,
                    data=body,
                    headers={"Content-Type": "application/json; charset=utf-8"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=1.5) as _resp:
                    pass
            except Exception:
                pass

        threading.Thread(target=_do, daemon=True).start()

    def _add_marker_clicked(self) -> None:
        label = str(self._marker_edit.text() if hasattr(self, "_marker_edit") else "").strip()
        if not label:
            return
        try:
            rating = int(self._marker_rating.value()) if hasattr(self, "_marker_rating") else 0
        except Exception:
            rating = 0
        note = str(self._marker_note.text() if hasattr(self, "_marker_note") else "").strip()
        self._write_event("marker", {"marker": {"label": label, "rating": rating, "note": note or None}})
        try:
            self._marker_edit.clear()
            if hasattr(self, "_marker_note"):
                self._marker_note.clear()
        except Exception:
            pass

    def _emit_observation(self) -> None:
        # Aggregate last N seconds into a single observation record.
        if not self._session_log_active:
            return
        now = time.monotonic()
        cutoff = now - float(self._obs_window_s)
        pts = [p for p in list(self._obs_points) if float(p.get("t", 0.0)) >= cutoff]
        if not pts:
            return

        def _stats(vals: list[int]) -> dict[str, float] | None:
            if not vals:
                return None
            n = float(len(vals))
            mean = float(sum(vals)) / n
            mn = float(min(vals))
            mx = float(max(vals))
            var = float(sum((float(v) - mean) ** 2 for v in vals)) / max(1.0, n)
            return {"mean": mean, "min": mn, "max": mx, "std": float(math.sqrt(var))}

        att_s = _stats([int(p["att"]) for p in pts if p.get("att") is not None])
        med_s = _stats([int(p["med"]) for p in pts if p.get("med") is not None])
        hr_s = _stats([int(p["hr"]) for p in pts if p.get("hr") is not None])
        sq_vals = [int(p["sq"]) for p in pts if p.get("sq") is not None]
        rssi_vals = [int(p["rssi"]) for p in pts if p.get("rssi") is not None]
        bands_last = None
        for p in reversed(pts):
            b = p.get("bands")
            if isinstance(b, dict):
                bands_last = b
                break

        prog = {
            "running": bool(self._prog_running),
            "spec": str(self._prog_spec),
            "tone": {"l_hz": float(self._prog_tone_left_hz), "r_hz": float(self._prog_tone_right_hz), "vol": float(self._prog_tone_vol)}
            if self._prog_running
            else None,
            "noise": {"color": str(self._prog_noise_color), "vol": float(self._prog_noise_vol)} if self._prog_running else None,
        }

        self._write_event(
            "observation",
            {
                "window_s": float(self._obs_window_s),
                "eeg": {"attention": att_s, "meditation": med_s},
                "hr": hr_s,
                "quality": {"sq_last": self._last_signal_quality, "sq": _stats(sq_vals), "rssi": _stats(rssi_vals)},
                "bands_last": bands_last,
                "program": prog,
            },
        )

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
            if self._obs_timer.isActive():
                self._obs_timer.stop()
        else:
            if self._ble_thread is not None:
                self._obs_timer.start()

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

    def _toggle_bands_plot(self, on: bool) -> None:
        if not self._plot_available:
            return
        self._bands_plot_enabled = bool(on)
        if self._bands_chart_view is not None:
            self._bands_chart_view.setVisible(self._bands_plot_enabled)
        self._bands_plot_clear_btn.setVisible(self._bands_plot_enabled)
        self._bands_full_cb.setEnabled(self._bands_plot_enabled)
        if self._bands_plot_enabled:
            self._bands_plot_timer.start()
        else:
            self._bands_plot_timer.stop()
        if self._bands_plot_enabled:
            self._refresh_bands_plot(force=True)

    def _bands_plot_tick(self) -> None:
        if not self._bands_plot_enabled or not self._bands_plot_dirty:
            return
        now = time.monotonic()
        if now - self._bands_plot_last_redraw < self._bands_plot_min_redraw_s:
            return
        self._bands_plot_last_redraw = now
        self._bands_plot_dirty = False
        self._refresh_bands_plot()

    def _init_bands_plot_widgets(self, lay: QVBoxLayout) -> None:
        if not self._plot_available:
            return

        keys_compact = ("delta", "theta", "alpha", "beta", "gamma")
        keys_full = (
            "delta",
            "theta",
            "low_alpha",
            "high_alpha",
            "low_beta",
            "high_beta",
            "low_gamma",
            "high_gamma",
        )
        for k in set(keys_compact) | set(keys_full):
            self._bands_hist[k] = deque(maxlen=2000)

        chart = QChart()
        chart.legend().setVisible(True)
        chart.setBackgroundRoundness(0)

        def _mk_series(name: str) -> QLineSeries:
            s = QLineSeries()
            s.setName(name)
            chart.addSeries(s)
            return s

        # Always create all series; toggle visibility by Compact/Full.
        self._bands_series = {
            "delta": _mk_series("δ"),
            "theta": _mk_series("θ"),
            "alpha": _mk_series("α"),
            "beta": _mk_series("β"),
            "gamma": _mk_series("γ"),
            "low_alpha": _mk_series("αL"),
            "high_alpha": _mk_series("αH"),
            "low_beta": _mk_series("βL"),
            "high_beta": _mk_series("βH"),
            "low_gamma": _mk_series("γL"),
            "high_gamma": _mk_series("γH"),
        }

        axis_x = QValueAxis()
        axis_x.setTitleText("t, s")
        axis_x.setRange(0, self._bands_plot_window_s)
        axis_x.setLabelFormat("%.0f")

        axis_y = QValueAxis()
        axis_y.setTitleText("log10(1+x)")
        axis_y.setRange(0.0, 6.0)
        axis_y.setLabelFormat("%.1f")

        chart.addAxis(axis_x, Qt.AlignmentFlag.AlignBottom)
        chart.addAxis(axis_y, Qt.AlignmentFlag.AlignLeft)

        for s in self._bands_series.values():
            s.attachAxis(axis_x)
            s.attachAxis(axis_y)

        view = QChartView(chart)
        view.setMinimumHeight(200)
        view.setVisible(False)
        lay.addWidget(view)

        self._bands_axis_x = axis_x
        self._bands_axis_y = axis_y
        self._bands_chart_view = view

        self._bands_plot_timer = QTimer(self)
        self._bands_plot_timer.setInterval(120)
        self._bands_plot_timer.timeout.connect(self._bands_plot_tick)

        # Default: compact series visible.
        self._apply_bands_series_visibility()

    def _apply_bands_series_visibility(self) -> None:
        compact = {"delta", "theta", "alpha", "beta", "gamma"}
        full = {
            "delta",
            "theta",
            "low_alpha",
            "high_alpha",
            "low_beta",
            "high_beta",
            "low_gamma",
            "high_gamma",
        }
        show = full if self._bands_full else compact
        for k, s in self._bands_series.items():
            s.setVisible(k in show)

    def _append_bands_plot_point(self, b: dict[str, int]) -> None:
        if not self._plot_available:
            return
        t = time.monotonic() - self._bands_t0
        self._bands_t.append(t)
        # compact aggregates
        alpha = int(b["low_alpha"]) + int(b["high_alpha"])
        beta = int(b["low_beta"]) + int(b["high_beta"])
        gamma = int(b["low_gamma"]) + int(b["high_gamma"])
        vals = {
            "delta": int(b["delta"]),
            "theta": int(b["theta"]),
            "alpha": int(alpha),
            "beta": int(beta),
            "gamma": int(gamma),
            "low_alpha": int(b["low_alpha"]),
            "high_alpha": int(b["high_alpha"]),
            "low_beta": int(b["low_beta"]),
            "high_beta": int(b["high_beta"]),
            "low_gamma": int(b["low_gamma"]),
            "high_gamma": int(b["high_gamma"]),
        }
        for k, v in vals.items():
            if k in self._bands_hist:
                self._bands_hist[k].append(int(v))
        self._bands_plot_dirty = True

    def _clear_bands_plot(self) -> None:
        if not self._plot_available:
            return
        self._bands_t0 = time.monotonic()
        self._bands_t.clear()
        for dq in self._bands_hist.values():
            dq.clear()
        self._bands_plot_dirty = True
        for s in self._bands_series.values():
            s.clear()
        if self._bands_axis_x is not None:
            self._bands_axis_x.setRange(0, self._bands_plot_window_s)

    def _refresh_bands_plot(self, *, force: bool = False) -> None:
        if not self._plot_available or not self._bands_plot_enabled:
            return
        if self._bands_axis_x is None or not self._bands_series:
            return
        n = len(self._bands_t)
        if n < 2 and not force:
            return

        t_end = self._bands_t[-1] if n else 0.0
        t_start = max(0.0, t_end - self._bands_plot_window_s)
        self._bands_axis_x.setRange(t_start, max(t_start + 1.0, t_end))

        def _log1p(x: int) -> float:
            return math.log10(1.0 + max(0, int(x)))

        # Build visible window points.
        idxs = [i for i, t in enumerate(self._bands_t) if t >= t_start]
        if len(idxs) > 600:
            step = int(math.ceil(len(idxs) / 600))
            idxs = idxs[::step]

        for key, series in self._bands_series.items():
            if not series.isVisible():
                continue
            h = self._bands_hist.get(key)
            if not h:
                continue
            pts = [(self._bands_t[i], _log1p(h[i])) for i in idxs if i < len(h)]
            series.replace([QPointF(x, y) for x, y in pts])

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

    def _toggle_hr_plot(self, on: bool) -> None:
        if not self._plot_available:
            return
        self._hr_plot_enabled = bool(on)
        if self._hr_chart_view is not None:
            self._hr_chart_view.setVisible(self._hr_plot_enabled)
        self._hr_plot_clear_btn.setVisible(self._hr_plot_enabled)
        if self._hr_plot_enabled:
            self._hr_plot_timer.start()
        else:
            self._hr_plot_timer.stop()
        if self._hr_plot_enabled:
            self._refresh_hr_plot(force=True)

    def _hr_plot_tick(self) -> None:
        if not self._hr_plot_enabled or not self._hr_plot_dirty:
            return
        now = time.monotonic()
        if now - self._hr_plot_last_redraw < self._hr_plot_min_redraw_s:
            return
        self._hr_plot_last_redraw = now
        self._hr_plot_dirty = False
        self._refresh_hr_plot()

    def _init_hr_plot_widgets(self, lay: QVBoxLayout) -> None:
        if not self._plot_available:
            return
        series_hr = QLineSeries()
        series_hr.setName("HR оценка (BPM), не ЭКГ")
        chart = QChart()
        chart.addSeries(series_hr)
        chart.legend().setVisible(True)
        chart.setBackgroundRoundness(0)

        axis_x = QValueAxis()
        axis_x.setTitleText("t, s")
        axis_x.setRange(0, self._hr_plot_window_s)
        axis_x.setLabelFormat("%.0f")
        axis_y = QValueAxis()
        axis_y.setRange(40, 200)
        axis_y.setTitleText("BPM")
        axis_y.setLabelFormat("%.0f")

        chart.addAxis(axis_x, Qt.AlignmentFlag.AlignBottom)
        chart.addAxis(axis_y, Qt.AlignmentFlag.AlignLeft)
        series_hr.attachAxis(axis_x)
        series_hr.attachAxis(axis_y)

        view = QChartView(chart)
        view.setMinimumHeight(160)
        view.setVisible(False)
        lay.addWidget(view)

        self._series_hr = series_hr
        self._hr_axis_x = axis_x
        self._hr_axis_y = axis_y
        self._hr_chart_view = view

        self._hr_plot_timer = QTimer(self)
        self._hr_plot_timer.setInterval(120)
        self._hr_plot_timer.timeout.connect(self._hr_plot_tick)

    def _append_hr_plot_point(self, bpm: int) -> None:
        if not self._plot_available:
            return
        t = time.monotonic() - self._hr_t0
        self._hr_t.append(t)
        self._hr_bpm_hist.append(int(bpm))
        self._hr_plot_dirty = True

    def _clear_hr_plot(self) -> None:
        if not self._plot_available:
            return
        self._hr_t0 = time.monotonic()
        self._hr_t.clear()
        self._hr_bpm_hist.clear()
        self._hr_plot_dirty = True
        if self._series_hr is not None:
            self._series_hr.clear()
        if self._hr_axis_x is not None:
            self._hr_axis_x.setRange(0, self._hr_plot_window_s)
        if self._hr_axis_y is not None:
            self._hr_axis_y.setRange(40, 200)

    def _refresh_hr_plot(self, *, force: bool = False) -> None:
        if not self._plot_available or not self._hr_plot_enabled:
            return
        if self._series_hr is None or self._hr_axis_x is None:
            return
        n = len(self._hr_t)
        if n < 1 and not force:
            return
        t_end = self._hr_t[-1] if n else 0.0
        t_start = max(0.0, t_end - self._hr_plot_window_s)
        self._hr_axis_x.setRange(t_start, max(t_start + 1.0, t_end))
        pts = []
        for t, b in zip(self._hr_t, self._hr_bpm_hist):
            if t < t_start:
                continue
            pts.append((t, float(b)))
        if len(pts) > 600:
            step = int(math.ceil(len(pts) / 600))
            pts = pts[::step]
        if pts and self._hr_axis_y is not None:
            ys = [p[1] for p in pts]
            lo = max(30.0, min(ys) - 5.0)
            hi = min(240.0, max(ys) + 5.0)
            if hi - lo < 10.0:
                mid = (lo + hi) * 0.5
                lo, hi = mid - 10.0, mid + 10.0
            self._hr_axis_y.setRange(lo, hi)
        self._series_hr.replace([QPointF(x, y) for x, y in pts])

    def _toggle_tone_plot(self, on: bool) -> None:
        if not self._plot_available:
            return
        self._tone_plot_enabled = bool(on)
        if self._tone_chart_view is not None:
            self._tone_chart_view.setVisible(self._tone_plot_enabled)
        self._tone_plot_clear_btn.setVisible(self._tone_plot_enabled)
        if self._tone_plot_enabled:
            self._tone_plot_timer.start()
        else:
            if self._tone_plot_timer is not None:
                self._tone_plot_timer.stop()
        self._apply_tone_plot_series_visibility()
        if self._tone_plot_enabled:
            self._refresh_tone_plot(force=True)

    def _tone_plot_tick(self) -> None:
        if not self._tone_plot_enabled or not self._tone_plot_dirty:
            return
        now = time.monotonic()
        if now - self._tone_plot_last_redraw < self._tone_plot_min_redraw_s:
            return
        self._tone_plot_last_redraw = now
        self._tone_plot_dirty = False
        self._refresh_tone_plot()

    def _init_tone_plot_widgets(self, lay: QVBoxLayout) -> None:
        if not self._plot_available:
            return
        s_f = QLineSeries()
        s_f.setName("f, Hz (mono)")
        s_v = QLineSeries()
        s_v.setName("vol (mono)")
        s_fl = QLineSeries()
        s_fl.setName("fL, Hz")
        s_fr = QLineSeries()
        s_fr.setName("fR, Hz")
        s_vl = QLineSeries()
        s_vl.setName("vL")
        s_vr = QLineSeries()
        s_vr.setName("vR")
        chart = QChart()
        for s in (s_f, s_v, s_fl, s_fr, s_vl, s_vr):
            chart.addSeries(s)
        chart.legend().setVisible(True)
        chart.setBackgroundRoundness(0)
        ax = QValueAxis()
        ax.setTitleText("t, s")
        ax.setRange(0, self._tone_plot_window_s)
        ax.setLabelFormat("%.0f")
        ay_h = QValueAxis()
        ay_h.setRange(0, 2000)
        ay_h.setTitleText("Hz")
        ay_h.setLabelFormat("%.0f")
        ay_v = QValueAxis()
        ay_v.setRange(0, 0.25)
        ay_v.setTitleText("vol")
        ay_v.setLabelFormat("%.2f")
        chart.addAxis(ax, Qt.AlignmentFlag.AlignBottom)
        chart.addAxis(ay_h, Qt.AlignmentFlag.AlignLeft)
        chart.addAxis(ay_v, Qt.AlignmentFlag.AlignRight)
        s_f.attachAxis(ax)
        s_f.attachAxis(ay_h)
        s_v.attachAxis(ax)
        s_v.attachAxis(ay_v)
        s_fl.attachAxis(ax)
        s_fl.attachAxis(ay_h)
        s_fr.attachAxis(ax)
        s_fr.attachAxis(ay_h)
        s_vl.attachAxis(ax)
        s_vl.attachAxis(ay_v)
        s_vr.attachAxis(ax)
        s_vr.attachAxis(ay_v)
        view = QChartView(chart)
        view.setMinimumHeight(150)
        view.setVisible(False)
        lay.addWidget(view)
        self._series_tone_f = s_f
        self._series_tone_v = s_v
        self._series_tone_f_l = s_fl
        self._series_tone_f_r = s_fr
        self._series_tone_v_l = s_vl
        self._series_tone_v_r = s_vr
        self._tone_axis_x = ax
        self._tone_axis_y_hz = ay_h
        self._tone_axis_y_vol = ay_v
        self._tone_chart_view = view
        self._apply_tone_plot_series_visibility()
        self._tone_plot_timer = QTimer(self)
        self._tone_plot_timer.setInterval(120)
        self._tone_plot_timer.timeout.connect(self._tone_plot_tick)

    def _apply_tone_plot_series_visibility(self) -> None:
        m = self._eeg_tone_mode == "stereo"
        for s, vis in (
            (self._series_tone_f, not m),
            (self._series_tone_v, not m),
            (self._series_tone_f_l, m),
            (self._series_tone_f_r, m),
            (self._series_tone_v_l, m),
            (self._series_tone_v_r, m),
        ):
            if s is not None:
                s.setVisible(vis)
        if self._tone_chart_view is not None and self._tone_chart_view.chart() is not None:
            self._tone_chart_view.chart().legend().setVisible(True)

    def _append_tone_plot_sample(self) -> None:
        if not self._plot_available or not self._tone_plot_enabled:
            return
        t = time.monotonic() - self._tone_t0
        if self._eeg_tone_mode == "stereo":
            self._tone_s_t.append(t)
            self._tone_s_f_l.append(float(self._eeg_tone_f_l))
            self._tone_s_f_r.append(float(self._eeg_tone_f_r))
            self._tone_s_v_l.append(float(self._eeg_tone_v_l))
            self._tone_s_v_r.append(float(self._eeg_tone_v_r))
        else:
            self._tone_m_t.append(t)
            self._tone_m_f.append(float(self._eeg_tone_f_hz))
            self._tone_m_v.append(float(self._eeg_tone_vol))
        self._tone_plot_dirty = True

    def _clear_tone_plot(self) -> None:
        if not self._plot_available:
            return
        self._tone_t0 = time.monotonic()
        for d in (
            self._tone_m_t,
            self._tone_m_f,
            self._tone_m_v,
            self._tone_s_t,
            self._tone_s_f_l,
            self._tone_s_f_r,
            self._tone_s_v_l,
            self._tone_s_v_r,
        ):
            d.clear()
        self._tone_plot_dirty = True
        for s in (
            self._series_tone_f,
            self._series_tone_v,
            self._series_tone_f_l,
            self._series_tone_f_r,
            self._series_tone_v_l,
            self._series_tone_v_r,
        ):
            if s is not None:
                s.clear()
        if self._tone_axis_x is not None:
            self._tone_axis_x.setRange(0, self._tone_plot_window_s)
        if self._tone_axis_y_hz is not None:
            self._tone_axis_y_hz.setRange(0, 2000)
        if self._tone_axis_y_vol is not None:
            self._tone_axis_y_vol.setRange(0, 0.25)

    def _refresh_tone_plot(self, *, force: bool = False) -> None:
        if not self._plot_available or not self._tone_plot_enabled:
            return
        if (
            self._series_tone_f is None
            or self._tone_axis_x is None
            or self._tone_axis_y_hz is None
            or self._tone_axis_y_vol is None
        ):
            return
        is_stereo = self._eeg_tone_mode == "stereo"
        t_deq = self._tone_s_t if is_stereo else self._tone_m_t
        n = len(t_deq)
        if n < 1 and not force:
            return
        t_end = t_deq[-1] if n else 0.0
        t_start = max(0.0, t_end - self._tone_plot_window_s)
        self._tone_axis_x.setRange(t_start, max(t_start + 1.0, t_end))

        def _decimate(pts: list) -> list:
            if len(pts) > 600:
                step = int(math.ceil(len(pts) / 600))
                return pts[::step]
            return pts

        if is_stereo:
            row = list(
                zip(
                    self._tone_s_t,
                    self._tone_s_f_l,
                    self._tone_s_f_r,
                    self._tone_s_v_l,
                    self._tone_s_v_r,
                )
            )
            pts_l, pts_r, pts_vl, pts_vr = [], [], [], []
            for t, fl, fr, vl, vr in row:
                if float(t) < t_start:
                    continue
                tt = float(t)
                pts_l.append((tt, float(fl)))
                pts_r.append((tt, float(fr)))
                pts_vl.append((tt, float(vl)))
                pts_vr.append((tt, float(vr)))
            pts_l = _decimate(pts_l)
            pts_r = _decimate(pts_r)
            pts_vl = _decimate(pts_vl)
            pts_vr = _decimate(pts_vr)
            self._series_tone_f_l.replace([QPointF(x, y) for x, y in pts_l])
            self._series_tone_f_r.replace([QPointF(x, y) for x, y in pts_r])
            self._series_tone_v_l.replace([QPointF(x, y) for x, y in pts_vl])
            self._series_tone_v_r.replace([QPointF(x, y) for x, y in pts_vr])
            hz_vals = [p[1] for p in pts_l + pts_r] or [0.0, 200.0]
            v_vals = [p[1] for p in pts_vl + pts_vr] or [0.0, 0.25]
        else:
            row = list(zip(self._tone_m_t, self._tone_m_f, self._tone_m_v))
            pts_f, pts_vv = [], []
            for t, f, v in row:
                if float(t) < t_start:
                    continue
                tt = float(t)
                pts_f.append((tt, float(f)))
                pts_vv.append((tt, float(v)))
            pts_f = _decimate(pts_f)
            pts_vv = _decimate(pts_vv)
            self._series_tone_f.replace([QPointF(x, y) for x, y in pts_f])
            self._series_tone_v.replace([QPointF(x, y) for x, y in pts_vv])
            hz_vals = [p[1] for p in pts_f] or [0.0, 1000.0]
            v_vals = [p[1] for p in pts_vv] or [0.0, 0.1]

        lo_h = min(hz_vals)
        hi_h = max(hz_vals)
        if hi_h > lo_h:
            pad = max(5.0, (hi_h - lo_h) * 0.08)
            y0, y1 = max(0.0, lo_h - pad), min(8000.0, hi_h + pad)
        else:
            y0, y1 = max(0.0, lo_h - 10.0), lo_h + 10.0
        self._tone_axis_y_hz.setRange(y0, y1)

        lo_v = min(v_vals)
        hi_v = max(v_vals)
        if hi_v < 1.0e-3:
            self._tone_axis_y_vol.setRange(0, 0.3)
        else:
            self._tone_axis_y_vol.setRange(0, min(1.0, max(hi_v * 1.1, 0.02)))

    def _append_session_log(self, att: int, med: int) -> None:
        self._write_event(
            "eeg",
            {
                "eeg": {"attention": int(att), "meditation": int(med)},
                "quality": {"sq": self._last_signal_quality, "rssi": self._ble_selected_rssi},
            },
        )

    def _append_hr_session_log(self, bpm: int, *, source: str = "aabb0c_exp") -> None:
        self._write_event("hr", {"hr": {"bpm": int(bpm), "source": str(source)}})

    def _write_event(self, event_type: str, payload: dict[str, Any]) -> None:
        if not self._session_log_active:
            return
        fp = self._session_log_file
        if fp is None and self._session_log_path is not None:
            self._session_log_path.parent.mkdir(parents=True, exist_ok=True)
            self._session_log_file = self._session_log_path.open("a", encoding="utf-8")
            fp = self._session_log_file
            self._write_session_start()
        if fp is None and self._session_log_path is None:
            self._new_session_log()
            fp = self._session_log_file
            if fp is None:
                return
        if self._session_t0_mono is None:
            self._session_t0_mono = time.monotonic()
        rec: dict[str, Any] = {
            "type": str(event_type),
            "session_id": str(self._session_id),
            "timestamp_utc": datetime.now(UTC).isoformat(),
            "t_monotonic_s": float(time.monotonic() - float(self._session_t0_mono)),
            "source": "ble" if self._ble_thread is not None else ("jsonl" if self._eeg_it is not None else "ui"),
        }
        rec.update(payload or {})
        fp.write(json.dumps(rec, ensure_ascii=False) + "\n")
        fp.flush()

    def _write_session_start(self) -> None:
        # Idempotent-ish: safe to call multiple times; writes only when log is active and file exists.
        if not self._session_log_active:
            return
        if self._session_t0_mono is None:
            self._session_t0_mono = time.monotonic()
        self._write_event(
            "session_start",
            {
                "app": {"window": "meditation_poc"},
                "device": {"ble_address": self._ble_address},
                "programmer": {"spec": str(self._prog_spec)},
            },
        )

    def _write_session_end(self) -> None:
        if not self._session_log_active:
            return
        self._write_event(
            "session_end",
            {
                "summary": {
                    "duration_s": float(time.monotonic() - float(self._session_t0_mono or time.monotonic())),
                }
            },
        )

    def _start_ble(self) -> None:
        if not self._ble_address or self._ble_thread is not None:
            return
        self._status.setText("BLE: подключение…")
        self._ble_start.setEnabled(False)
        self._ble_stop.setEnabled(True)
        self._session_started_at = time.monotonic()
        if self._session_t0_mono is None:
            self._session_t0_mono = time.monotonic()
        self._write_session_start()
        if self._session_log_active:
            self._obs_timer.start()
        self._last_metric_at = None
        self._last_hr_bpm = None
        self._last_hr_at = None
        self._hr_smoother.reset()
        self._hr_etalon_smoother.reset()
        self._hr_val.setText("—")
        if self._plot_available:
            self._clear_hr_plot()
            self._clear_tone_plot()
        self._metric_times.clear()
        th = BleNotifyThread(
            self._ble_address,
            init_hex=self._ble_init_hex,
            duration_s=self._ble_duration_s,
            parent=self,
        )
        qc_ble = Qt.ConnectionType.QueuedConnection
        th.metricsReady.connect(self._on_ble_metrics, qc_ble)
        th.signalQualityReady.connect(self._on_ble_signal_quality, qc_ble)
        th.bandsReady.connect(self._on_ble_bands, qc_ble)
        th.heartRateReady.connect(self._on_ble_heart_rate, qc_ble)
        th.extendRawReady.connect(self._on_ble_extend_raw, qc_ble)
        th.connectionFailed.connect(self._on_ble_failed, qc_ble)
        th.workerFinished.connect(self._on_ble_worker_finished, qc_ble)
        self._ble_thread = th
        th.start()
        self._rssi_timer.start()

    def _stop_ble(self) -> None:
        if self._ble_thread is not None:
            self._ble_thread.request_stop()
            self._status.setText("BLE: остановка…")
        self._rssi_timer.stop()
        if self._obs_timer.isActive():
            self._obs_timer.stop()

    def _on_ble_metrics(self, att: int, med: int) -> None:
        now = time.monotonic()
        self._last_metric_at = now
        self._metric_times.append(now)
        prev = self._prev_metrics
        cur = (int(att), int(med))
        if prev is None or cur != prev:
            self._metrics_change_times.append(now)
            self._prev_metrics = cur
        self._last_att = att
        self._last_med = med
        self._att_val.setText(f"Attention {int(att)}")
        self._med_val.setText(f"Meditation {int(med)}")
        self._bus.publish("eeg.metrics", {"attention": att, "meditation": med})
        self._append_session_log(att, med)
        self._obs_points.append(
            {
                "t": float(now),
                "att": int(att),
                "med": int(med),
                "sq": self._last_signal_quality,
                "rssi": self._ble_selected_rssi,
                "bands": self._last_bands,
                "hr": self._last_hr_bpm,
            }
        )
        self._append_plot_point(att, med)
        self._apply_eeg_tone()
        self._apply_eeg_binaural()
        if self._status.text().startswith("BLE: подключение"):
            self._status.setText("BLE: поток активен")

    def _on_ble_heart_rate(self, bpm: int, *, source: str = "aabb0c_exp") -> None:
        # If etalon HR is selected, ignore open (noisy) HR events.
        if getattr(self, "_hr_source", "open") == "etalon" and source != "macrotellect_com":
            return
        now = time.monotonic()
        # Etalon HR is expected to be less noisy; keep it responsive even if the
        # open-HR smoother is configured aggressively.
        if source == "macrotellect_com":
            sm = self._hr_etalon_smoother.feed(int(bpm), t=now)
        else:
            sm = self._hr_smoother.feed(int(bpm), t=now)
        if sm is None:
            return
        self._last_hr_bpm = int(sm)
        self._last_hr_at = now
        self._append_hr_plot_point(int(sm))
        self._bus.publish("vendor.heart_rate", {"bpm": int(sm), "source": str(source)})
        self._append_hr_session_log(int(sm), source=str(source))

    def _on_ble_extend_raw(self, raw: bytes) -> None:
        # Debug hook: log extend payloads + RR candidates for offline analysis.
        try:
            hx = bytes(raw).hex()
        except Exception:
            hx = ""
        rrs = try_extract_rr_ms_candidates(bytes(raw))
        rr = pick_rr_ms(rrs)
        bpm = int(round(rr_ms_to_bpm(float(rr)))) if rr is not None else None
        self._write_event(
            "hr_extend_debug",
            {
                "extend_raw_hex": hx,
                "rr_candidates_ms": [int(x) for x in rrs],
                "rr_pick_ms": int(rr) if rr is not None else None,
                "bpm_from_rr": int(bpm) if bpm is not None else None,
            },
        )

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
        # Track compact changes (δ/θ/α/β/γ) for rate diagnostics.
        alpha = int(low_alpha) + int(high_alpha)
        beta = int(low_beta) + int(high_beta)
        gamma = int(low_gamma) + int(high_gamma)
        cur_c = (int(delta), int(theta), int(alpha), int(beta), int(gamma))
        prev_c = self._prev_bands_compact
        if prev_c is None or cur_c != prev_c:
            self._bands_change_times.append(time.monotonic())
            self._prev_bands_compact = cur_c
        self._refresh_bands_ui()
        if self._bands_plot_enabled and self._plot_available:
            self._append_bands_plot_point(self._last_bands)
        self._write_event("bands", {"bands": dict(self._last_bands)})

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
        self._att_val.setText(f"Attention {int(att)}")
        self._med_val.setText(f"Meditation {int(med)}")
        self._bus.publish("eeg.metrics", {"attention": att, "meditation": med})
        self._append_session_log(att, med)
        self._append_plot_point(att, med)
        self._apply_eeg_tone()
        self._apply_eeg_binaural()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self._light_send_detach is not None:
            try:
                self._light_send_detach()
            except Exception:
                pass
            self._light_send_detach = None
        if self._light_bridge_detach is not None:
            try:
                self._light_bridge_detach()
            except Exception:
                pass
            self._light_bridge_detach = None
        self._stats_timer.stop()
        if self._obs_timer.isActive():
            self._obs_timer.stop()
        if self._hr_plot_timer is not None:
            self._hr_plot_timer.stop()
        if self._tone_plot_timer is not None:
            self._tone_plot_timer.stop()
        # Noise is independent from EEG→Tone; stop it explicitly.
        if getattr(self, "_noise_enabled", False):
            self._noise_enabled = False
        try:
            self._stop_noise_stream()
        except Exception:
            pass
        self._stop_eeg_tone()
        self._stop_eeg_binaural()
        if self._ble_scan_thread is not None and self._ble_scan_thread.isRunning():
            self._ble_scan_thread.wait(2000)
            self._ble_scan_thread = None
        if self._ble_thread is not None:
            self._ble_thread.request_stop()
            self._ble_thread.wait(8000)
            self._ble_thread = None
        self._stop_etalon_com_thread_blocking()
        if self._api_server is not None:
            stop_agent_api(self._api_server)
            self._api_server = None
        if getattr(self, "_chat_metric_loop_timer", None) is not None and self._chat_metric_loop_timer.isActive():
            self._chat_metric_loop_timer.stop()
        self._chat_metric_loop_goal = None
        if getattr(self, "_chat_worker", None) is not None:
            if self._chat_worker.isRunning():
                self._chat_worker.wait(5000)
            self._chat_worker.deleteLater()
            self._chat_worker = None
        try:
            self._chat_save_profile()
        except OSError:
            pass
        if self._session_log_file is not None:
            try:
                self._write_session_end()
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

        if self._last_hr_bpm is not None and self._last_hr_at is not None:
            age_hr = now - self._last_hr_at
            if age_hr > self._hr_stale_s():
                self._hr_val.setText(f"{self._last_hr_bpm} (нет обновл.)")
            else:
                self._hr_val.setText(str(self._last_hr_bpm))
        else:
            self._hr_val.setText("—")
        self._refresh_etalon_status()

        # Change rates over last 10 seconds (how often values change).
        if self._metrics_change_times:
            cutoff = now - 10.0
            n = 0
            for t in reversed(self._metrics_change_times):
                if t < cutoff:
                    break
                n += 1
            parts.append(f"ΔA/M {n/10.0:.1f} Hz")
        if self._bands_change_times:
            cutoff = now - 10.0
            n = 0
            for t in reversed(self._bands_change_times):
                if t < cutoff:
                    break
                n += 1
            parts.append(f"ΔBands {n/10.0:.1f} Hz")

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
        self._apply_bands_series_visibility()
        self._refresh_bands_ui(force=True)
        self._refresh_bands_plot(force=True)

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
                        self._set_tone_base_text(
                            f"stereo  fL={self._eeg_tone_f_l:.1f}Hz fR={self._eeg_tone_f_r:.1f}Hz  vL={self._eeg_tone_v_l:.3f} vR={self._eeg_tone_v_r:.3f}"
                        )
                        self._append_tone_plot_sample()
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
                    self._set_tone_base_text(f"mono  v={self._eeg_tone_vol:.3f}")
                    self._append_tone_plot_sample()
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
            self._set_tone_base_text(
                f"stereo  fL={self._eeg_tone_f_l:.1f}Hz fR={self._eeg_tone_f_r:.1f}Hz  vL={self._eeg_tone_v_l:.3f} vR={self._eeg_tone_v_r:.3f}"
            )
            self._append_tone_plot_sample()
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
        self._set_tone_base_text(f"mono  f={self._eeg_tone_f_hz:.1f}Hz  v={self._eeg_tone_vol:.3f}")
        self._append_tone_plot_sample()

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
    hr_source: str = "",
    hr_com_port: str = "",
    hr_pyd_dir: str = "",
    session_log_path: Path | None = None,
    auto_start_ble: bool = False,
) -> int:
    app = QApplication.instance() or QApplication([])
    w = MeditationMainWindow(
        jsonl_path,
        ble_address=ble_address,
        ble_init_hex=ble_init_hex,
        ble_duration_s=ble_duration_s,
        hr_source=hr_source,
        hr_com_port=hr_com_port,
        hr_pyd_dir=hr_pyd_dir,
        session_log_path=session_log_path,
        auto_start_ble=auto_start_ble,
    )
    w.show()
    return int(app.exec())
