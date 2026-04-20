"""Minimal PySide6 replay: attention/meditation sparklines from Macrotellect JSONL."""

from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import Iterator

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QApplication, QLabel, QMainWindow, QVBoxLayout, QWidget


def _iter_eeg_rows(path: Path) -> Iterator[tuple[int, int]]:
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


class Sparkline(QWidget):
    def __init__(self, title: str, max_points: int = 200, parent: QWidget | None = None):
        super().__init__(parent)
        self._title = title
        self._values: deque[float] = deque(maxlen=max_points)
        self.setMinimumHeight(120)

    def push(self, v: float) -> None:
        self._values.append(float(v))
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(30, 30, 30))
        painter.setPen(QPen(QColor(180, 180, 180)))
        painter.drawText(8, 18, self._title)
        if len(self._values) < 2:
            return
        w = self.width() - 16
        h = self.height() - 32
        lo, hi = 0.0, 100.0
        vals = list(self._values)
        painter.setPen(QPen(QColor(100, 200, 255), 2))
        for i in range(1, len(vals)):
            x0 = 8 + (i - 1) * w / (len(vals) - 1)
            x1 = 8 + i * w / (len(vals) - 1)
            y0 = 24 + h - (vals[i - 1] - lo) / (hi - lo) * h
            y1 = 24 + h - (vals[i] - lo) / (hi - lo) * h
            painter.drawLine(int(x0), int(y0), int(x1), int(y1))


class ReplayMainWindow(QMainWindow):
    def __init__(self, path: Path, interval_ms: float, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("NeuroSync Pro — EEG replay (Macrotellect JSONL)")
        self._rows = _iter_eeg_rows(path)
        self._it = iter(self._rows)
        cw = QWidget()
        layout = QVBoxLayout(cw)
        self._lbl = QLabel(f"File: {path}")
        self._att_plot = Sparkline("Attention (0–100)")
        self._med_plot = Sparkline("Meditation (0–100)")
        layout.addWidget(self._lbl)
        layout.addWidget(self._att_plot)
        layout.addWidget(self._med_plot)
        self.setCentralWidget(cw)
        self.resize(640, 420)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(max(20, int(interval_ms)))

    def _tick(self) -> None:
        try:
            att, med = next(self._it)
        except StopIteration:
            self._timer.stop()
            self._lbl.setText(self._lbl.text() + " — EOF")
            return
        self._att_plot.push(att)
        self._med_plot.push(med)


def run_replay_plot(path: Path, interval_ms: float) -> int:
    app = QApplication.instance() or QApplication([])
    w = ReplayMainWindow(path, interval_ms)
    w.show()
    return int(app.exec())
