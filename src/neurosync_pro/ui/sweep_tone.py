"""GUI: realtime tone & sweep generator (Windows MVP)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from neurosync_pro.audio.stream import StreamConfig, ToneSweepStream


class SweepToneMainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("NeuroSync Pro — Tone / Sweep (MVP)")
        self._stream = ToneSweepStream(StreamConfig(sample_rate=48000))

        cw = QWidget()
        root = QVBoxLayout(cw)

        # Tone controls
        tone_box = QGroupBox("Tone")
        tone_form = QFormLayout(tone_box)
        self._tone_hz = QDoubleSpinBox()
        self._tone_hz.setRange(1.0, 20000.0)
        self._tone_hz.setValue(440.0)
        self._tone_hz.setSuffix(" Hz")
        tone_form.addRow("Freq", self._tone_hz)

        # Sweep controls
        sweep_box = QGroupBox("Sweep")
        sweep_form = QFormLayout(sweep_box)
        self._f0 = QDoubleSpinBox()
        self._f0.setRange(1.0, 20000.0)
        self._f0.setValue(200.0)
        self._f0.setSuffix(" Hz")
        self._f1 = QDoubleSpinBox()
        self._f1.setRange(1.0, 20000.0)
        self._f1.setValue(1000.0)
        self._f1.setSuffix(" Hz")
        self._dur = QDoubleSpinBox()
        self._dur.setRange(0.1, 300.0)
        self._dur.setValue(10.0)
        self._dur.setSuffix(" s")
        self._log = QCheckBox("Log sweep")
        self._loop = QCheckBox("Loop")
        sweep_form.addRow("F0", self._f0)
        sweep_form.addRow("F1", self._f1)
        sweep_form.addRow("Duration", self._dur)
        sweep_form.addRow("", self._log)
        sweep_form.addRow("", self._loop)

        # Common
        common_box = QGroupBox("Common")
        common_form = QFormLayout(common_box)
        self._vol = QDoubleSpinBox()
        self._vol.setRange(0.0, 1.0)
        self._vol.setSingleStep(0.01)
        self._vol.setValue(0.15)
        self._fade_in = QDoubleSpinBox()
        self._fade_in.setRange(0.0, 1.0)
        self._fade_in.setSingleStep(0.01)
        self._fade_in.setValue(0.02)
        self._fade_in.setSuffix(" s")
        self._fade_out = QDoubleSpinBox()
        self._fade_out.setRange(0.0, 2.0)
        self._fade_out.setSingleStep(0.01)
        self._fade_out.setValue(0.05)
        self._fade_out.setSuffix(" s")
        common_form.addRow("Volume", self._vol)
        common_form.addRow("Fade in", self._fade_in)
        common_form.addRow("Fade out", self._fade_out)

        # Buttons
        btn_row = QWidget()
        btns = QHBoxLayout(btn_row)
        self._tone_btn = QPushButton("Play tone")
        self._sweep_btn = QPushButton("Play sweep")
        self._stop_btn = QPushButton("Stop")
        self._stop_btn.setEnabled(False)
        btns.addWidget(self._tone_btn)
        btns.addWidget(self._sweep_btn)
        btns.addWidget(self._stop_btn)

        self._status = QLabel("")
        self._status.setAlignment(Qt.AlignmentFlag.AlignLeft)

        root.addWidget(tone_box)
        root.addWidget(sweep_box)
        root.addWidget(common_box)
        root.addWidget(btn_row)
        root.addWidget(self._status)
        self.setCentralWidget(cw)
        self.resize(520, 520)

        self._tone_btn.clicked.connect(self._play_tone)
        self._sweep_btn.clicked.connect(self._play_sweep)
        self._stop_btn.clicked.connect(self._stop)

    def _apply_common(self) -> None:
        self._stream.set_volume(float(self._vol.value()))
        self._stream.set_fades(float(self._fade_in.value()), float(self._fade_out.value()))

    def _play_tone(self) -> None:
        self._apply_common()
        self._stream.start()
        self._stream.play_tone(float(self._tone_hz.value()))
        self._status.setText(f"Tone {self._tone_hz.value():.1f} Hz")
        self._stop_btn.setEnabled(True)

    def _play_sweep(self) -> None:
        self._apply_common()
        self._stream.start()
        self._stream.play_sweep(
            f0_hz=float(self._f0.value()),
            f1_hz=float(self._f1.value()),
            duration_s=float(self._dur.value()),
            log=bool(self._log.isChecked()),
            loop=bool(self._loop.isChecked()),
        )
        mode = "log" if self._log.isChecked() else "linear"
        lp = " loop" if self._loop.isChecked() else ""
        self._status.setText(f"Sweep {mode} {self._f0.value():.0f}->{self._f1.value():.0f} Hz, {self._dur.value():.1f}s{lp}")
        self._stop_btn.setEnabled(True)

    def _stop(self) -> None:
        self._stream.idle()
        self._stream.stop()
        self._status.setText("Stopped.")
        self._stop_btn.setEnabled(False)

    def closeEvent(self, event) -> None:  # noqa: N802, ANN001
        try:
            self._stream.stop()
        finally:
            super().closeEvent(event)


def run_sweep_tone_ui() -> int:
    app = QApplication.instance() or QApplication([])
    w = SweepToneMainWindow()
    w.show()
    return int(app.exec())

