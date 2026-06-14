"""Comparison-pairs editor — lets the user define group-vs-group comparisons,
gated until at least two groups exist."""

from __future__ import annotations

import itertools

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.gating import TooltipOnDisabled, apply_gating, set_gated_enabled
from app.group_manifest_panel import GroupManifestPanel
from app.theme import get_theme as _get_theme

_COMP_PLACEHOLDER = "— select —"
_REMOVE_BTN_WIDTH = 28
_MAX_AUTO_PAIR_GROUPS = 6  # beyond this, "all pairs" stops auto-populating and is disabled


class ComparisonsPanel(QWidget):
    def __init__(
        self,
        group_panel: GroupManifestPanel,
        tooltip_filter: TooltipOnDisabled,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        _T = _get_theme()
        self._group_panel = group_panel
        self._tooltip_filter = tooltip_filter

        comp_col = QVBoxLayout(self)
        comp_col.setContentsMargins(0, 0, 0, 0)
        apply_gating(
            self,
            tooltip_filter,
            "Add at least two groups before defining comparisons between them.",
        )

        comp_title_row = QHBoxLayout()
        comp_title_row.setSpacing(6)
        comp_label = QLabel("Comparisons")
        comp_label.setObjectName("sectionTitle")
        comp_title_row.addWidget(comp_label)
        self._comp_auto_warning = QLabel("⚠ not all pairs added")
        self._comp_auto_warning.setStyleSheet(f"color: {_T.warn}; font-size: 11px;")
        self._comp_auto_warning.setVisible(False)
        comp_title_row.addWidget(self._comp_auto_warning)
        comp_title_row.addStretch()
        comp_col.addLayout(comp_title_row)

        self._comp_list = QListWidget()
        self._comp_list.setObjectName("groupList")
        comp_col.addWidget(self._comp_list, stretch=1)

        comp_btns = QHBoxLayout()
        comp_btns.setSpacing(6)
        for label, slot in [
            ("All pairs", self._all_pairs),
            ("Clear", self._remove_all_comparisons),
            ("+ Add", self._add_blank_comparison),
        ]:
            btn = QPushButton(label)
            btn.setObjectName("secondaryButton")
            btn.clicked.connect(slot)
            if label == "All pairs":
                self._all_pairs_btn = btn
                btn.installEventFilter(self._tooltip_filter)
            comp_btns.addWidget(btn)
        comp_col.addLayout(comp_btns)

        self._group_panel.group_added.connect(self._sync_comp_add)
        self._group_panel.group_added.connect(self._refresh_comparisons_enabled)
        self._group_panel.group_removed.connect(self._sync_comp_remove)
        self._group_panel.group_removed.connect(self._refresh_comparisons_enabled)
        self._group_panel.group_renamed.connect(self._sync_comp_rename)

    # ── Public API ───────────────────────────────────────────────────────────
    def get_comparisons(self) -> list[tuple[str, str]]:
        seen: set[tuple[str, str]] = set()
        pairs: list[tuple[str, str]] = []
        for w in self._comp_rows():
            a, b = w._combo_a.currentText(), w._combo_b.currentText()
            if (
                a
                and b
                and a != _COMP_PLACEHOLDER
                and b != _COMP_PLACEHOLDER
                and a != b
                and (a, b) not in seen
            ):
                seen.add((a, b))
                pairs.append((a, b))
        return pairs

    def set_list_enabled(self, enabled: bool) -> None:
        self._comp_list.setEnabled(enabled)

    def refresh_theme(self) -> None:
        _T = _get_theme()
        self._comp_auto_warning.setStyleSheet(f"color: {_T.warn}; font-size: 11px;")
        for w in self._comp_rows():
            w._vs_lbl.setStyleSheet(f"color: {_T.muted}; font-size: 11px;")

    # ── Comparison management ───────────────────────────────────────────────
    def _all_pairs(self) -> None:
        groups = self._group_panel.groups()
        if len(groups) > _MAX_AUTO_PAIR_GROUPS:
            return  # button should be disabled in this state, but guard anyway

        while self._comp_list.count():
            self._comp_list.takeItem(0)
        for a, b in itertools.combinations(groups, 2):
            self._add_comp_row(a, b)

    def _remove_all_comparisons(self) -> None:
        while self._comp_list.count():
            self._comp_list.takeItem(0)

    def _add_blank_comparison(self) -> None:
        if not self._group_panel.groups():
            return
        self._add_comp_row()  # both combos start on placeholder

    def _add_comp_row(self, name_a: str | None = None, name_b: str | None = None) -> None:
        _T = _get_theme()
        group_names = list(self._group_panel.groups())
        if not group_names:
            return

        row_widget = QWidget()
        layout = QHBoxLayout(row_widget)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(4)

        combo_a = QComboBox()
        combo_a.setObjectName("compCombo")
        combo_a.addItem(_COMP_PLACEHOLDER)
        for n in group_names:
            combo_a.addItem(n)
        if name_a in group_names:
            combo_a.setCurrentText(name_a)
        layout.addWidget(combo_a, stretch=1)

        vs_lbl = QLabel("vs")
        vs_lbl.setStyleSheet(f"color: {_T.muted}; font-size: 11px;")
        vs_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vs_lbl.setFixedWidth(20)
        layout.addWidget(vs_lbl)
        row_widget._vs_lbl = vs_lbl

        combo_b = QComboBox()
        combo_b.setObjectName("compCombo")
        combo_b.addItem(_COMP_PLACEHOLDER)
        for n in group_names:
            combo_b.addItem(n)
        if name_b in group_names:
            combo_b.setCurrentText(name_b)
        layout.addWidget(combo_b, stretch=1)

        remove_btn = QPushButton("✕")
        remove_btn.setFixedWidth(_REMOVE_BTN_WIDTH)
        remove_btn.setObjectName("removeButton")
        remove_btn.clicked.connect(lambda: self._remove_comp_row(row_widget))
        layout.addWidget(remove_btn)

        row_widget._combo_a = combo_a
        row_widget._combo_b = combo_b

        combo_a.currentTextChanged.connect(
            lambda _: self._check_comp_duplicate(row_widget, combo_a)
        )
        combo_b.currentTextChanged.connect(
            lambda _: self._check_comp_duplicate(row_widget, combo_b)
        )

        item = QListWidgetItem()
        item.setSizeHint(row_widget.sizeHint())
        self._comp_list.addItem(item)
        self._comp_list.setItemWidget(item, row_widget)

    def _remove_comp_row(self, row_widget: QWidget) -> None:
        idx = self._list_index(self._comp_list, row_widget)
        if idx is not None:
            self._comp_list.takeItem(idx)

    def _refresh_comparisons_enabled(self) -> None:
        """Comparisons only mean something once there's something to pair —
        grey the whole section out (with an explanatory tooltip) until at
        least two groups exist, rather than showing empty, clickable controls."""
        groups = self._group_panel.groups()
        set_gated_enabled(self, len(groups) >= 2)

        too_many = len(groups) > _MAX_AUTO_PAIR_GROUPS
        self._all_pairs_btn.setEnabled(not too_many)
        self._comp_auto_warning.setVisible(too_many)
        if too_many:
            n_pairs = len(groups) * (len(groups) - 1) // 2
            tooltip = (
                f"{len(groups)} groups → {n_pairs} pairs. Auto-pairing stops above "
                f'{_MAX_AUTO_PAIR_GROUPS} groups — add pairs manually with "+ Add".'
            )
            self._all_pairs_btn.setToolTip(tooltip)
            self._comp_auto_warning.setToolTip(tooltip)
        else:
            self._all_pairs_btn.setToolTip("")

    def _sync_comp_add(self, new_name: str) -> None:
        for w in self._comp_rows():
            w._combo_a.addItem(new_name)
            w._combo_b.addItem(new_name)
        groups = self._group_panel.groups()
        if len(groups) > _MAX_AUTO_PAIR_GROUPS:
            return
        for existing_name in [n for n in groups if n != new_name]:
            self._add_comp_row(existing_name, new_name)

    def _sync_comp_remove(self, name: str) -> None:
        to_remove = [
            i
            for i in range(self._comp_list.count())
            if (w := self._comp_list.itemWidget(self._comp_list.item(i))) is not None
            and (w._combo_a.currentText() == name or w._combo_b.currentText() == name)
        ]
        for i in reversed(to_remove):
            self._comp_list.takeItem(i)
        for w in self._comp_rows():
            for combo in (w._combo_a, w._combo_b):
                idx = combo.findText(name)
                if idx >= 0:
                    if combo.currentIndex() == idx:
                        combo.removeItem(idx)
                        combo.setCurrentText(_COMP_PLACEHOLDER)
                    else:
                        combo.removeItem(idx)

    def _sync_comp_rename(self, old_name: str, new_name: str) -> None:
        for w in self._comp_rows():
            for combo in (w._combo_a, w._combo_b):
                item_idx = combo.findText(old_name)
                if item_idx >= 0:
                    combo.setItemText(item_idx, new_name)

    def _comp_rows(self) -> list[QWidget]:
        return [
            w
            for i in range(self._comp_list.count())
            if (w := self._comp_list.itemWidget(self._comp_list.item(i))) is not None
        ]

    def _check_comp_duplicate(self, row_widget: QWidget, changed_combo: QComboBox) -> None:
        """Warn and revert if this row now duplicates an existing pair."""
        a = row_widget._combo_a.currentText()
        b = row_widget._combo_b.currentText()
        if a == _COMP_PLACEHOLDER or b == _COMP_PLACEHOLDER:
            return
        if a == b:
            QMessageBox.warning(
                self,
                "Invalid comparison",
                "A group can't be compared against itself.",
            )
            changed_combo.setCurrentText(_COMP_PLACEHOLDER)
            return
        for w in self._comp_rows():
            if w is row_widget:
                continue
            ea = w._combo_a.currentText()
            eb = w._combo_b.currentText()
            if (a == ea and b == eb) or (a == eb and b == ea):
                QMessageBox.warning(
                    self,
                    "Duplicate comparison",
                    f'"{a} vs {b}" is already in the list.',
                )
                changed_combo.setCurrentText(_COMP_PLACEHOLDER)
                return

    # ── Helpers ──────────────────────────────────────────────────────────────
    def _list_index(self, list_widget: QListWidget, widget: QWidget) -> int | None:
        for i in range(list_widget.count()):
            if list_widget.itemWidget(list_widget.item(i)) is widget:
                return i
        return None
