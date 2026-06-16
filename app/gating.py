"""Shared helpers for "gated" sections — containers that start disabled with
an explanatory tooltip until an earlier choice unlocks them, and the event
filter that lets tooltips show on disabled widgets (Qt suppresses these by
default)."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QToolTip, QVBoxLayout, QWidget


class TooltipOnDisabled(QObject):
    """Event filter that shows a widget's tooltip even when the widget is disabled.

    Qt normally suppresses tooltip events for disabled widgets; this shim
    intercepts the QEvent.Type.ToolTip event and calls QToolTip.showText()
    directly so the tooltip still appears.
    """

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # type: ignore[override]
        if isinstance(obj, QWidget) and not obj.isEnabled() and event.type() == QEvent.Type.ToolTip:
            tip = obj.toolTip()
            if tip:
                QToolTip.showText(event.globalPos(), tip, obj)  # type: ignore[attr-defined]
            return True
        return super().eventFilter(obj, event)


def apply_gating(
    section: QWidget, tooltip_filter: TooltipOnDisabled, disabled_tooltip: str
) -> None:
    """Turn an existing widget into a "gated" section that starts disabled
    with an explanatory tooltip — used for sections that only make sense once
    an earlier choice has been made (e.g. Groups need a Source, Comparisons
    need ≥ 2 groups). Qt suppresses tooltips on disabled widgets by default,
    hence the `TooltipOnDisabled` filter. The tooltip is stored rather than
    left permanently set — `set_gated_enabled` clears it once the section
    becomes relevant, so it doesn't linger and contradict what's now an
    active, self-explanatory part of the UI."""
    section.setObjectName("gatedSection")
    section._disabled_tip = disabled_tooltip
    section.installEventFilter(tooltip_filter)
    set_gated_enabled(section, False)


def build_gated_section(tooltip_filter: TooltipOnDisabled, disabled_tooltip: str) -> QWidget:
    """A plain gated container (see `apply_gating`) with its own layout —
    used where a fresh `QWidget` can be parked directly into a parent layout."""
    section = QWidget()
    col = QVBoxLayout(section)
    col.setContentsMargins(0, 0, 0, 0)
    apply_gating(section, tooltip_filter, disabled_tooltip)
    return section


def set_gated_enabled(section: QWidget, enabled: bool) -> None:
    section.setEnabled(enabled)
    section.setToolTip("" if enabled else section._disabled_tip)
