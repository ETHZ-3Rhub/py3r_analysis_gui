"""Reusable Qt widget subclasses shared across the GUI."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QListWidget, QStyle, QStyleOptionViewItem


class CheckableListWidget(QListWidget):
    """QListWidget where clicking anywhere on a row toggles its checkbox.

    Normally only clicking the indicator itself toggles the check state.
    This subclass intercepts mouse presses: if the click lands outside the
    indicator rect, it toggles manually and skips the normal press (avoiding
    double-toggle when the indicator IS clicked).
    """

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        item = self.itemAt(event.pos())
        if item is not None and item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
            opt = QStyleOptionViewItem()
            opt.initFrom(self)
            opt.rect = self.visualItemRect(item)
            opt.features = QStyleOptionViewItem.ViewItemFeature.HasCheckIndicator
            check_rect = self.style().subElementRect(
                QStyle.SubElement.SE_ItemViewItemCheckIndicator, opt, self
            )
            if not check_rect.contains(event.pos()):
                new_state = (
                    Qt.CheckState.Unchecked
                    if item.checkState() == Qt.CheckState.Checked
                    else Qt.CheckState.Checked
                )
                item.setCheckState(new_state)
                return
        super().mousePressEvent(event)
