"""Qt widgets with safer defaults for embedded forms (e.g. scroll areas)."""

from __future__ import annotations

from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QComboBox


class NoWheelComboBox(QComboBox):
    """Ignore mouse wheel so scrolling the parent does not change the selection."""

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        event.ignore()
