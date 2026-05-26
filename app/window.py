"""Main application window.

Two-panel layout:
  Left  — groups (top) + comparisons (bottom)
  Right — arena selector, output folder, run controls, log
"""

from __future__ import annotations

import datetime
import itertools
import os
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app import arenas as arena_pkg
from app.runner import PipelineRunner

# ── Colour tokens ─────────────────────────────────────────────────────────────
_COL_BG      = "#1e1e2e"
_COL_PANEL   = "#2a2a3e"
_COL_ACCENT  = "#7c6af7"
_COL_TEXT    = "#cdd6f4"
_COL_MUTED   = "#6c7086"
_COL_SEP     = "#3a3a4e"
_COL_ERROR   = "#f38ba8"
_COL_SUCCESS = "#a6e3a1"
_GROUP_COLOURS = ["#7c6af7", "#f38ba8", "#a6e3a1", "#fab387", "#89dceb", "#f9e2af"]

_NAME_EDIT_WIDTH = 130
_COMP_PLACEHOLDER = "— select —"


class MainWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("py3r Analysis")
        self.setMinimumSize(1020, 700)
        self._apply_stylesheet()

        self._arenas = arena_pkg.discover()
        self._runner: PipelineRunner | None = None
        # Each group: (name, path, colour)
        self._groups: list[tuple[str, Path, str]] = []

        root = QHBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)
        root.addWidget(self._build_left_panel(), stretch=2)
        root.addWidget(self._build_run_panel(), stretch=3)

    # ── Left panel ────────────────────────────────────────────────────────────
    def _build_left_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # Groups section
        t1 = QLabel("Groups")
        t1.setObjectName("sectionTitle")
        layout.addWidget(t1)

        self._group_list = QListWidget()
        self._group_list.setObjectName("groupList")
        layout.addWidget(self._group_list, stretch=3)

        add_btn = QPushButton("+ Add Group")
        add_btn.setObjectName("secondaryButton")
        add_btn.clicked.connect(self._add_group)
        layout.addWidget(add_btn)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {_COL_SEP}; margin: 4px 0;")
        layout.addWidget(sep)

        # Comparisons section
        t2 = QLabel("Comparisons")
        t2.setObjectName("sectionTitle")
        layout.addWidget(t2)

        self._comp_list = QListWidget()
        self._comp_list.setObjectName("groupList")
        layout.addWidget(self._comp_list, stretch=2)

        comp_btns = QHBoxLayout()
        all_pairs_btn = QPushButton("All pairs")
        all_pairs_btn.setObjectName("secondaryButton")
        all_pairs_btn.clicked.connect(self._all_pairs)
        comp_btns.addWidget(all_pairs_btn)
        add_comp_btn = QPushButton("+ Add")
        add_comp_btn.setObjectName("secondaryButton")
        add_comp_btn.clicked.connect(self._add_blank_comparison)
        comp_btns.addWidget(add_comp_btn)
        layout.addLayout(comp_btns)

        return panel

    # ── Run panel ─────────────────────────────────────────────────────────────
    def _build_run_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        arena_label = QLabel("Arena")
        arena_label.setObjectName("sectionTitle")
        layout.addWidget(arena_label)

        self._arena_combo = QComboBox()
        self._arena_combo.addItem("— select arena —", userData=None)
        for mod in self._arenas:
            self._arena_combo.addItem(mod.NAME, userData=mod)
        self._arena_combo.currentIndexChanged.connect(self._refresh_run_button)
        layout.addWidget(self._arena_combo)

        layout.addSpacing(6)

        out_label = QLabel("Output folder")
        out_label.setObjectName("sectionTitle")
        layout.addWidget(out_label)

        out_row = QHBoxLayout()
        self._out_edit = QLineEdit()
        self._out_edit.setPlaceholderText("Choose output folder…")
        self._out_edit.textChanged.connect(self._refresh_run_button)
        out_row.addWidget(self._out_edit)
        out_browse = QPushButton("📁")
        out_browse.setFixedWidth(36)
        out_browse.clicked.connect(self._browse_output)
        out_row.addWidget(out_browse)
        layout.addLayout(out_row)

        layout.addSpacing(4)

        self._run_btn = QPushButton("▶  Analyse")
        self._run_btn.setObjectName("primaryButton")
        self._run_btn.setEnabled(False)
        self._run_btn.clicked.connect(self._toggle_run)
        layout.addWidget(self._run_btn)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setTextVisible(False)
        layout.addWidget(self._progress)

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setObjectName("logBox")
        layout.addWidget(self._log, stretch=1)

        self._open_btn = QPushButton("Open Results Folder")
        self._open_btn.setObjectName("secondaryButton")
        self._open_btn.setVisible(False)
        self._open_btn.clicked.connect(self._open_results)
        layout.addWidget(self._open_btn)
        self._last_output: str | None = None

        return panel

    # ── Group management ──────────────────────────────────────────────────────
    def _add_group(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select group folder")
        if not folder:
            return
        path = Path(folder)

        if path in [p for _, p, _ in self._groups]:
            QMessageBox.warning(self, "Duplicate folder",
                                f"The folder\n{path}\nis already in the list.")
            return

        # Unique name: append _2, _3, … until no collision
        base = path.name
        name = base
        existing = {n for n, _, _ in self._groups}
        suffix = 2
        while name in existing:
            name = f"{base}_{suffix}"
            suffix += 1

        colour = _GROUP_COLOURS[len(self._groups) % len(_GROUP_COLOURS)]
        self._groups.append((name, path, colour))
        self._add_group_row(name, path, colour)
        self._sync_comp_add(name)
        self._refresh_run_button()

    def _add_group_row(self, name: str, path: Path, colour: str) -> None:
        item_widget = QWidget()
        row = QHBoxLayout(item_widget)
        row.setContentsMargins(4, 2, 4, 2)
        row.setSpacing(6)

        dot = QLabel("●")
        dot.setStyleSheet(f"color: {colour}; font-size: 14px;")
        dot.setFixedWidth(16)
        row.addWidget(dot)

        name_edit = QLineEdit(name)
        name_edit.setFrame(False)
        name_edit.setFixedWidth(_NAME_EDIT_WIDTH)
        name_edit.setStyleSheet(
            f"background: transparent; color: {_COL_TEXT}; padding: 3px 4px; border: none;"
        )
        name_edit.editingFinished.connect(
            lambda: self._rename_group(item_widget, name_edit, name_edit.text())
        )
        row.addWidget(name_edit)

        path_str = str(path)
        path_lbl = QLabel(path_str)
        path_lbl.setStyleSheet(f"color: {_COL_MUTED}; font-size: 11px;")
        path_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        path_lbl.setToolTip(path_str)
        row.addWidget(path_lbl, stretch=1)

        remove_btn = QPushButton("✕")
        remove_btn.setFixedWidth(28)
        remove_btn.setObjectName("removeButton")
        remove_btn.clicked.connect(lambda: self._remove_group(item_widget))
        row.addWidget(remove_btn)

        item = QListWidgetItem()
        item.setSizeHint(item_widget.sizeHint())
        self._group_list.addItem(item)
        self._group_list.setItemWidget(item, item_widget)

    def _rename_group(self, item_widget: QWidget, name_edit: QLineEdit, new_name: str) -> None:
        new_name = new_name.strip()
        idx = self._list_index(self._group_list, item_widget)
        if idx is None:
            return
        old_name, path, colour = self._groups[idx]
        if new_name == old_name:
            return
        if not new_name:
            name_edit.setText(old_name)
            return
        existing = {n for i, (n, _, _) in enumerate(self._groups) if i != idx}
        if new_name in existing:
            QMessageBox.warning(self, "Duplicate name",
                                f'A group named "{new_name}" already exists.')
            name_edit.setText(old_name)
            return
        self._groups[idx] = (new_name, path, colour)
        self._sync_comp_rename(old_name, new_name)

    def _remove_group(self, item_widget: QWidget) -> None:
        idx = self._list_index(self._group_list, item_widget)
        if idx is not None:
            name, _, _ = self._groups[idx]
            self._groups.pop(idx)
            self._group_list.takeItem(idx)
            self._sync_comp_remove(name)
            self._refresh_run_button()

    # ── Comparison management ─────────────────────────────────────────────────
    def _all_pairs(self) -> None:
        while self._comp_list.count():
            self._comp_list.takeItem(0)
        for a, b in itertools.combinations([n for n, _, _ in self._groups], 2):
            self._add_comp_row(a, b)

    def _add_blank_comparison(self) -> None:
        if not self._groups:
            return
        self._add_comp_row()   # both combos start on placeholder

    def _add_comp_row(self, name_a: str | None = None, name_b: str | None = None) -> None:
        group_names = [n for n, _, _ in self._groups]
        if not group_names:
            return

        row_widget = QWidget()
        layout = QHBoxLayout(row_widget)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(4)

        dot_a = QLabel("●")
        dot_a.setFixedWidth(14)
        layout.addWidget(dot_a)

        combo_a = QComboBox()
        combo_a.setObjectName("compCombo")
        combo_a.addItem(_COMP_PLACEHOLDER)          # item 0 — placeholder
        for n in group_names:
            combo_a.addItem(n)
        if name_a in group_names:
            combo_a.setCurrentText(name_a)
        layout.addWidget(combo_a, stretch=1)

        vs_lbl = QLabel("vs")
        vs_lbl.setStyleSheet(f"color: {_COL_MUTED}; font-size: 11px;")
        vs_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vs_lbl.setFixedWidth(20)
        layout.addWidget(vs_lbl)

        dot_b = QLabel("●")
        dot_b.setFixedWidth(14)
        layout.addWidget(dot_b)

        combo_b = QComboBox()
        combo_b.setObjectName("compCombo")
        combo_b.addItem(_COMP_PLACEHOLDER)          # item 0 — placeholder
        for n in group_names:
            combo_b.addItem(n)
        if name_b in group_names:
            combo_b.setCurrentText(name_b)
        layout.addWidget(combo_b, stretch=1)

        remove_btn = QPushButton("✕")
        remove_btn.setFixedWidth(28)
        remove_btn.setObjectName("removeButton")
        remove_btn.clicked.connect(lambda: self._remove_comp_row(row_widget))
        layout.addWidget(remove_btn)

        # Store refs for sync
        row_widget._combo_a = combo_a
        row_widget._combo_b = combo_b
        row_widget._dot_a = dot_a
        row_widget._dot_b = dot_b

        def _refresh_dots() -> None:
            dot_a.setStyleSheet(
                f"color: {self._group_colour(combo_a.currentText())}; font-size: 14px;"
            )
            dot_b.setStyleSheet(
                f"color: {self._group_colour(combo_b.currentText())}; font-size: 14px;"
            )

        combo_a.currentTextChanged.connect(lambda _: (_refresh_dots(), self._check_comp_duplicate(row_widget, combo_a)))
        combo_b.currentTextChanged.connect(lambda _: (_refresh_dots(), self._check_comp_duplicate(row_widget, combo_b)))
        _refresh_dots()

        item = QListWidgetItem()
        item.setSizeHint(row_widget.sizeHint())
        self._comp_list.addItem(item)
        self._comp_list.setItemWidget(item, row_widget)

    def _remove_comp_row(self, row_widget: QWidget) -> None:
        idx = self._list_index(self._comp_list, row_widget)
        if idx is not None:
            self._comp_list.takeItem(idx)

    def _sync_comp_add(self, new_name: str) -> None:
        for w in self._comp_rows():
            w._combo_a.addItem(new_name)
            w._combo_b.addItem(new_name)
        for existing_name in [n for n, _, _ in self._groups if n != new_name]:
            self._add_comp_row(existing_name, new_name)

    def _sync_comp_remove(self, name: str) -> None:
        to_remove = [
            i for i in range(self._comp_list.count())
            if (w := self._comp_list.itemWidget(self._comp_list.item(i))) is not None
            and (w._combo_a.currentText() == name or w._combo_b.currentText() == name)
        ]
        for i in reversed(to_remove):
            self._comp_list.takeItem(i)
        for w in self._comp_rows():
            for combo in (w._combo_a, w._combo_b):
                idx = combo.findText(name)
                if idx >= 0:
                    # If the removed name was selected, revert to placeholder
                    if combo.currentIndex() == idx:
                        combo.removeItem(idx)
                        combo.setCurrentText(_COMP_PLACEHOLDER)
                    else:
                        combo.removeItem(idx)

    def _sync_comp_rename(self, old_name: str, new_name: str) -> None:
        for w in self._comp_rows():
            for combo, dot in ((w._combo_a, w._dot_a), (w._combo_b, w._dot_b)):
                item_idx = combo.findText(old_name)
                if item_idx >= 0:
                    was_selected = combo.currentIndex() == item_idx
                    combo.setItemText(item_idx, new_name)
                    if was_selected:
                        dot.setStyleSheet(
                            f"color: {self._group_colour(new_name)}; font-size: 14px;"
                        )

    def _comp_rows(self) -> list[QWidget]:
        return [
            w for i in range(self._comp_list.count())
            if (w := self._comp_list.itemWidget(self._comp_list.item(i))) is not None
        ]

    def _check_comp_duplicate(self, row_widget: QWidget, changed_combo: QComboBox) -> None:
        """Warn and revert if this row now duplicates an existing pair."""
        a = row_widget._combo_a.currentText()
        b = row_widget._combo_b.currentText()
        if a == _COMP_PLACEHOLDER or b == _COMP_PLACEHOLDER or a == b:
            return
        for w in self._comp_rows():
            if w is row_widget:
                continue
            ea = w._combo_a.currentText()
            eb = w._combo_b.currentText()
            if (a == ea and b == eb) or (a == eb and b == ea):
                QMessageBox.warning(
                    self, "Duplicate comparison",
                    f'"{a} vs {b}" is already in the list.',
                )
                changed_combo.setCurrentText(_COMP_PLACEHOLDER)
                return

    def _get_comparisons(self) -> list[tuple[str, str]]:
        seen: set[tuple[str, str]] = set()
        pairs: list[tuple[str, str]] = []
        for w in self._comp_rows():
            a, b = w._combo_a.currentText(), w._combo_b.currentText()
            if (
                a and b
                and a != _COMP_PLACEHOLDER and b != _COMP_PLACEHOLDER
                and a != b
                and (a, b) not in seen
            ):
                seen.add((a, b))
                pairs.append((a, b))
        return pairs

    def _group_colour(self, name: str) -> str:
        for n, _, colour in self._groups:
            if n == name:
                return colour
        return _COL_MUTED

    # ── Run controls ──────────────────────────────────────────────────────────
    def _browse_output(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select output folder")
        if folder:
            self._out_edit.setText(folder)

    def _refresh_run_button(self) -> None:
        self._run_btn.setEnabled(
            self._arena_combo.currentData() is not None
            and len(self._groups) > 0
            and bool(self._out_edit.text().strip())
            and self._runner is None
        )

    def _toggle_run(self) -> None:
        if self._runner is not None:
            self._cancel_run()
        else:
            self._start_run()

    def _start_run(self) -> None:
        arena_mod = self._arena_combo.currentData()
        if arena_mod is None:
            return
        output_dir = Path(self._out_edit.text().strip())
        groups = {name: path for name, path, _ in self._groups}
        comparisons = self._get_comparisons()

        if not comparisons:
            answer = QMessageBox.question(
                self, "No comparisons defined",
                "No group comparisons are set.\n\n"
                "The analysis will run but no pairwise statistics or\n"
                "BFA comparison plots will be produced.\n\n"
                "Proceed anyway?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        self._log.clear()
        self._progress.setValue(0)
        self._open_btn.setVisible(False)
        self._run_btn.setText("■  Cancel")
        self._arena_combo.setEnabled(False)
        self._out_edit.setEnabled(False)
        self._comp_list.setEnabled(False)

        self._runner = PipelineRunner(arena_mod, groups, output_dir, comparisons)
        self._runner.log.connect(self._on_log)
        self._runner.progress.connect(self._on_progress)
        self._runner.finished.connect(self._on_finished)
        self._runner.error.connect(self._on_error)
        self._runner.start()

    def _cancel_run(self) -> None:
        if self._runner:
            self._runner.terminate()
            self._runner.wait()
            self._runner = None
        self._log_line("Cancelled.", colour=_COL_ERROR)
        self._reset_controls()

    def _reset_controls(self) -> None:
        self._run_btn.setText("▶  Analyse")
        self._arena_combo.setEnabled(True)
        self._out_edit.setEnabled(True)
        self._comp_list.setEnabled(True)
        self._refresh_run_button()

    # ── Runner signal handlers ─────────────────────────────────────────────────
    def _on_log(self, msg: str) -> None:
        self._log_line(msg)

    def _on_progress(self, pct: int) -> None:
        if pct < 0:
            self._progress.setRange(0, 0)
        else:
            self._progress.setRange(0, 100)
            self._progress.setValue(pct)

    def _on_finished(self, output_path: str) -> None:
        self._runner = None
        self._last_output = output_path
        self._log_line(f"✅  Complete — results in {output_path}", colour=_COL_SUCCESS)
        self._progress.setValue(100)
        self._open_btn.setVisible(True)
        self._reset_controls()

    def _on_error(self, tb: str) -> None:
        self._runner = None
        self._log_line("❌  Pipeline error:", colour=_COL_ERROR)
        for line in tb.splitlines():
            self._log_line(line, colour=_COL_ERROR)
        self._reset_controls()

    def _open_results(self) -> None:
        if self._last_output:
            os.startfile(self._last_output)

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _list_index(self, list_widget: QListWidget, widget: QWidget) -> int | None:
        for i in range(list_widget.count()):
            if list_widget.itemWidget(list_widget.item(i)) is widget:
                return i
        return None

    def _log_line(self, message: str, colour: str = _COL_TEXT) -> None:
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self._log.setTextColor(QColor(_COL_MUTED))
        self._log.insertPlainText(f"[{ts}] ")
        self._log.setTextColor(QColor(colour))
        self._log.insertPlainText(message + "\n")
        self._log.ensureCursorVisible()

    def _apply_stylesheet(self) -> None:
        self.setStyleSheet(f"""
            QWidget {{
                background-color: {_COL_BG};
                color: {_COL_TEXT};
                font-family: "Segoe UI", sans-serif;
                font-size: 13px;
            }}
            QFrame#panel {{
                background-color: {_COL_PANEL};
                border-radius: 8px;
            }}
            QLabel#sectionTitle {{
                color: {_COL_TEXT};
                font-weight: bold;
                font-size: 12px;
                letter-spacing: 1px;
                text-transform: uppercase;
            }}
            QPushButton#primaryButton {{
                background-color: {_COL_ACCENT};
                color: white;
                border: none;
                border-radius: 6px;
                padding: 10px 0;
                font-size: 14px;
                font-weight: bold;
            }}
            QPushButton#primaryButton:hover {{ background-color: #9580ff; }}
            QPushButton#primaryButton:disabled {{ background-color: {_COL_MUTED}; }}
            QPushButton#secondaryButton {{
                background-color: transparent;
                color: {_COL_ACCENT};
                border: 1px solid {_COL_ACCENT};
                border-radius: 5px;
                padding: 6px 10px;
            }}
            QPushButton#secondaryButton:hover {{ background-color: {_COL_ACCENT}22; }}
            QPushButton#removeButton {{
                background: transparent;
                color: {_COL_ERROR};
                border: none;
                font-size: 12px;
            }}
            QPushButton#removeButton:hover {{ color: white; }}
            QComboBox {{
                background-color: {_COL_BG};
                border: 1px solid {_COL_MUTED};
                border-radius: 5px;
                padding: 6px 10px;
            }}
            QComboBox#compCombo {{
                padding: 3px 6px;
                font-size: 12px;
            }}
            QComboBox::drop-down {{ border: none; width: 24px; }}
            QLineEdit {{
                background-color: {_COL_BG};
                border: 1px solid {_COL_MUTED};
                border-radius: 5px;
                padding: 6px 10px;
            }}
            QListWidget#groupList {{
                background-color: {_COL_BG};
                border: 1px solid {_COL_MUTED};
                border-radius: 5px;
            }}
            QListWidget#groupList::item:selected {{ background: transparent; }}
            QTextEdit#logBox {{
                background-color: {_COL_BG};
                border: 1px solid {_COL_MUTED};
                border-radius: 5px;
                font-family: "Consolas", monospace;
                font-size: 12px;
                padding: 4px;
            }}
            QProgressBar {{
                background-color: {_COL_BG};
                border: 1px solid {_COL_MUTED};
                border-radius: 4px;
                height: 8px;
            }}
            QProgressBar::chunk {{
                background-color: {_COL_ACCENT};
                border-radius: 4px;
            }}
            QScrollBar:vertical {{
                background: {_COL_BG};
                width: 8px;
            }}
            QScrollBar::handle:vertical {{
                background: {_COL_MUTED};
                border-radius: 4px;
            }}
            QToolTip {{
                background-color: {_COL_PANEL};
                color: {_COL_TEXT};
                border: 1px solid {_COL_MUTED};
                padding: 4px;
            }}
        """)
