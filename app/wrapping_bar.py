"""A two-part bar whose right-hand group drops onto its own line when the
window is too narrow for both — used for the run panel's bottom bar (tracking
status on the left, Update/Help/Settings/Problem on the right)."""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QGridLayout, QWidget


class WrappingBar(QWidget):
    def __init__(self, left: QWidget, right: QWidget, spacing: int = 12) -> None:
        super().__init__()
        self.setObjectName("bottomBar")  # transparent, see styles.py
        self._left = left
        self._right = right
        self._spacing = spacing
        self._wrapped: bool | None = None

        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(spacing)
        self._grid.setVerticalSpacing(4)
        self._place(False)

    def _fits(self) -> bool:
        needed = self._left.sizeHint().width() + self._spacing + self._right.sizeHint().width()
        return self.width() >= needed

    def _place(self, wrapped: bool) -> None:
        if wrapped == self._wrapped:
            return
        self._wrapped = wrapped
        self._grid.removeWidget(self._left)
        self._grid.removeWidget(self._right)
        right_align = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        self._grid.addWidget(self._left, 0, 0)
        if wrapped:
            self._grid.addWidget(self._right, 1, 0, alignment=right_align)
            self._grid.setColumnStretch(1, 0)
        else:
            self._grid.addWidget(self._right, 0, 1, alignment=right_align)
        self._grid.setColumnStretch(0, 1)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._place(not self._fits())

    def event(self, event: QEvent) -> bool:
        # A child changing size (e.g. the Update button appearing, or a longer
        # status text) arrives as a LayoutRequest; re-check whether it still fits.
        if event.type() == QEvent.Type.LayoutRequest and self._wrapped is not None:
            self._place(not self._fits())
        return super().event(event)
