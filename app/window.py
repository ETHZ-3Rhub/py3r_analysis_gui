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

from PyQt6.QtCore import QEvent, QObject, Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QCheckBox,
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
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from app import arenas as arena_pkg
from app.runner import PipelineRunner

# ── Colour tokens ─────────────────────────────────────────────────────────────
_COL_BG = "#1e1e2e"
_COL_PANEL = "#2a2a3e"
_COL_ACCENT = "#7c6af7"
_COL_TEXT = "#cdd6f4"
_COL_MUTED = "#6c7086"
_COL_SEP = "#3a3a4e"
_COL_ERROR = "#f38ba8"
_COL_WARN = "#fab387"
_COL_SUCCESS = "#a6e3a1"

_NAME_EDIT_WIDTH = 130
_BADGE_WIDTH = 44
_REMOVE_BTN_WIDTH = 28
_COMP_PLACEHOLDER = "— select —"

# ── File extensions counted per mode ──────────────────────────────────────────
_VIDEO_EXTS = {".mp4", ".avi", ".mov", ".mkv", ".m4v", ".wmv"}
_CSV_EXTS = {".csv"}


def _count_files(path: Path, skip_tracking: bool) -> tuple[int, bool]:
    """Return (file_count, has_subdirs) for *path*, counting only relevant extensions."""
    exts = _CSV_EXTS if skip_tracking else _VIDEO_EXTS
    try:
        entries = list(path.iterdir())
        count = sum(1 for e in entries if e.is_file() and e.suffix.lower() in exts)
        has_subdirs = any(e.is_dir() for e in entries)
        return count, has_subdirs
    except (PermissionError, OSError):
        return 0, False


class _TooltipOnDisabled(QObject):
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


class MainWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("py3r Analysis")
        self.setMinimumSize(1020, 700)
        self._apply_stylesheet()

        self._arenas = arena_pkg.discover()
        self._runner: PipelineRunner | None = None
        # Each group: (name, path)
        self._groups: list[tuple[str, Path]] = []

        root = QHBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)
        root.addWidget(self._build_left_panel(), stretch=2)
        root.addWidget(self._build_run_panel(), stretch=3)

        # Show tooltip on the Analyse button even when it is disabled
        self._btn_tooltip_filter = _TooltipOnDisabled(self)
        self._run_btn.installEventFilter(self._btn_tooltip_filter)

        # Populate initial tooltip (button starts disabled)
        self._refresh_run_button()

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

        # Column headers — widths must mirror _add_group_row layout
        headers = QWidget()
        hdr_row = QHBoxLayout(headers)
        hdr_row.setContentsMargins(6, 0, 4, 0)
        hdr_row.setSpacing(6)
        for text, width, stretch, align in [
            ("Name", _NAME_EDIT_WIDTH, 0, Qt.AlignmentFlag.AlignLeft),
            ("Path", 0, 1, Qt.AlignmentFlag.AlignLeft),
            ("Files", _BADGE_WIDTH, 0, Qt.AlignmentFlag.AlignCenter),
            ("", _REMOVE_BTN_WIDTH, 0, Qt.AlignmentFlag.AlignLeft),  # remove-btn column
        ]:
            lbl = QLabel(text)
            lbl.setObjectName("colHeader")
            lbl.setAlignment(align)
            if width:
                lbl.setFixedWidth(width)
            else:
                lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            hdr_row.addWidget(lbl, stretch=stretch)
        layout.addWidget(headers)

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
        comp_btns.setSpacing(6)
        for label, slot in [
            ("All pairs", self._all_pairs),
            ("Clear", self._remove_all_comparisons),
            ("+ Add", self._add_blank_comparison),
        ]:
            btn = QPushButton(label)
            btn.setObjectName("secondaryButton")
            btn.clicked.connect(slot)
            comp_btns.addWidget(btn)
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

        self._skip_tracking_cb = QCheckBox("Groups contain pre-tracked CSV files")
        self._skip_tracking_cb.setToolTip(
            "When checked, each group folder is expected to contain CSV files\n"
            "produced by YOLO3R (tracking already done). The tracking step is\n"
            "skipped and analysis runs directly on those CSVs."
        )
        self._skip_tracking_cb.stateChanged.connect(self._refresh_group_file_counts)
        layout.addWidget(self._skip_tracking_cb)

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

        if path in [p for _, p in self._groups]:
            QMessageBox.warning(
                self, "Duplicate folder", f"The folder\n{path}\nis already in the list."
            )
            return

        # Unique name: append _2, _3, … until no collision
        base = path.name
        name = base
        existing = {n for n, _ in self._groups}
        suffix = 2
        while name in existing:
            name = f"{base}_{suffix}"
            suffix += 1

        self._groups.append((name, path))
        row_widget = self._add_group_row(name, path)
        self._update_group_badge(row_widget, path)
        self._sync_comp_add(name)
        self._refresh_run_button()

    def _add_group_row(self, name: str, path: Path) -> QWidget:
        item_widget = QWidget()
        row = QHBoxLayout(item_widget)
        row.setContentsMargins(4, 2, 4, 2)
        row.setSpacing(6)

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

        # File-count badge — colour/text set by _update_group_badge()
        badge_lbl = QLabel("…")
        badge_lbl.setFixedWidth(_BADGE_WIDTH)
        badge_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge_lbl.setStyleSheet(f"color: {_COL_MUTED}; font-size: 11px; font-weight: bold;")
        row.addWidget(badge_lbl)

        remove_btn = QPushButton("✕")
        remove_btn.setFixedWidth(_REMOVE_BTN_WIDTH)
        remove_btn.setObjectName("removeButton")
        remove_btn.clicked.connect(lambda: self._remove_group(item_widget))
        row.addWidget(remove_btn)

        item_widget._badge_lbl = badge_lbl

        item = QListWidgetItem()
        item.setSizeHint(item_widget.sizeHint())
        self._group_list.addItem(item)
        self._group_list.setItemWidget(item, item_widget)
        return item_widget

    def _rename_group(self, item_widget: QWidget, name_edit: QLineEdit, new_name: str) -> None:
        new_name = new_name.strip()
        idx = self._list_index(self._group_list, item_widget)
        if idx is None:
            return
        old_name, path = self._groups[idx]
        if new_name == old_name:
            return
        if not new_name:
            name_edit.setText(old_name)
            return
        existing = {n for i, (n, _) in enumerate(self._groups) if i != idx}
        if new_name in existing:
            QMessageBox.warning(
                self, "Duplicate name", f'A group named "{new_name}" already exists.'
            )
            name_edit.setText(old_name)
            return
        self._groups[idx] = (new_name, path)
        self._sync_comp_rename(old_name, new_name)
        self._refresh_run_button()

    def _remove_group(self, item_widget: QWidget) -> None:
        idx = self._list_index(self._group_list, item_widget)
        if idx is not None:
            name, _ = self._groups[idx]
            self._groups.pop(idx)
            self._group_list.takeItem(idx)
            self._sync_comp_remove(name)
            self._refresh_run_button()

    # ── Comparison management ─────────────────────────────────────────────────
    def _all_pairs(self) -> None:
        while self._comp_list.count():
            self._comp_list.takeItem(0)
        for a, b in itertools.combinations([n for n, _ in self._groups], 2):
            self._add_comp_row(a, b)

    def _remove_all_comparisons(self) -> None:
        while self._comp_list.count():
            self._comp_list.takeItem(0)

    def _add_blank_comparison(self) -> None:
        if not self._groups:
            return
        self._add_comp_row()  # both combos start on placeholder

    def _add_comp_row(self, name_a: str | None = None, name_b: str | None = None) -> None:
        group_names = [n for n, _ in self._groups]
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
        vs_lbl.setStyleSheet(f"color: {_COL_MUTED}; font-size: 11px;")
        vs_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vs_lbl.setFixedWidth(20)
        layout.addWidget(vs_lbl)

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

    def _sync_comp_add(self, new_name: str) -> None:
        for w in self._comp_rows():
            w._combo_a.addItem(new_name)
            w._combo_b.addItem(new_name)
        for existing_name in [n for n, _ in self._groups if n != new_name]:
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
        if a == _COMP_PLACEHOLDER or b == _COMP_PLACEHOLDER or a == b:
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

    def _get_comparisons(self) -> list[tuple[str, str]]:
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

    # ── File count badges ─────────────────────────────────────────────────────
    def _update_group_badge(self, row_widget: QWidget, path: Path) -> None:
        """Refresh the file-count badge on a single group row."""
        skip = self._skip_tracking_cb.isChecked()
        count, has_subdirs = _count_files(path, skip)
        ext_label = "CSV" if skip else "video"

        if count == 0:
            # Hard error — blocks the run button; same ⚠ symbol as warnings but red
            colour = _COL_ERROR
            text = "0 ⚠"
            tip_parts = [
                f"No {ext_label} files found.",
                "Analysis cannot proceed until this folder contains the right files.",
            ]
        elif count < 5:
            # Soft warning — run is allowed but flagged
            colour = _COL_WARN
            text = f"{count} ⚠"
            tip_parts = [
                f"Only {count} {ext_label} file(s) — results may be underpowered (expected ≥ 5)."
            ]
        else:
            colour = _COL_SUCCESS
            text = str(count)
            tip_parts = [f"{count} {ext_label} file(s) found."]

        if has_subdirs:
            tip_parts.append("Subfolders detected — only top-level files are counted.")

        row_widget._badge_lbl.setText(text)
        row_widget._badge_lbl.setStyleSheet(f"color: {colour}; font-size: 11px; font-weight: bold;")
        row_widget._badge_lbl.setToolTip("\n".join(tip_parts))

    def _refresh_group_file_counts(self) -> None:
        """Re-evaluate badges for every group row (called when skip_tracking changes)."""
        for i in range(self._group_list.count()):
            w = self._group_list.itemWidget(self._group_list.item(i))
            if w is None or not hasattr(w, "_badge_lbl"):
                continue
            _, path = self._groups[i]
            self._update_group_badge(w, path)
        self._refresh_run_button()

    def _collect_warnings(self) -> list[str]:
        """Return soft-warning strings shown in the 'proceed anyway?' dialog.

        Hard errors (0 files in a group) already block the run button and are
        never included here — by the time this is called those groups are fixed.
        """
        warnings: list[str] = []
        skip = self._skip_tracking_cb.isChecked()
        ext_label = "CSV" if skip else "video"

        for i in range(self._group_list.count()):
            w = self._group_list.itemWidget(self._group_list.item(i))
            if w is None:
                continue
            name, path = self._groups[i]
            count, has_subdirs = _count_files(path, skip)
            # count == 0 is a hard error handled by the run button gate; skip here
            if 0 < count < 5:
                warnings.append(
                    f'Group "{name}": only {count} {ext_label} file(s) — '
                    f"results may be underpowered (expected ≥ 5)."
                )
            if has_subdirs:
                warnings.append(
                    f'Group "{name}": subfolders detected — only top-level files are counted.'
                )

        if not self._get_comparisons():
            warnings.append(
                "No group comparisons defined — pairwise statistics and BFA comparison "
                "plots will be skipped."
            )

        return warnings

    # ── Run controls ──────────────────────────────────────────────────────────
    def _browse_output(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select output folder")
        if folder:
            self._out_edit.setText(folder)

    def _refresh_run_button(self) -> None:
        reasons: list[str] = []

        if self._arena_combo.currentData() is None:
            reasons.append("No arena selected.")
        if not self._groups:
            reasons.append("No groups added.")
        if not self._out_edit.text().strip():
            reasons.append("No output folder set.")

        # Hard block: any group with zero relevant files
        if self._groups:
            skip = self._skip_tracking_cb.isChecked()
            ext_label = "CSV" if skip else "video"
            for name, path in self._groups:
                count, _ = _count_files(path, skip)
                if count == 0:
                    reasons.append(f'Group "{name}" contains no {ext_label} files.')

        can_run = not reasons and self._runner is None
        self._run_btn.setEnabled(can_run)

        if reasons:
            self._run_btn.setToolTip("Cannot run:\n" + "\n".join(f"  •  {r}" for r in reasons))
        else:
            self._run_btn.setToolTip("Run the analysis pipeline.")

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
        groups = {name: path for name, path in self._groups}
        comparisons = self._get_comparisons()
        skip_tracking = self._skip_tracking_cb.isChecked()

        warnings = self._collect_warnings()
        if warnings:
            bullet_list = "\n".join(f"  ⚠  {w}" for w in warnings)
            answer = QMessageBox.question(
                self,
                "Warnings — proceed?",
                f"The following issues were detected:\n\n{bullet_list}\n\nProceed anyway?",
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
        self._skip_tracking_cb.setEnabled(False)

        self._runner = PipelineRunner(
            arena_mod, groups, output_dir, comparisons, skip_tracking=skip_tracking
        )
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
        self._skip_tracking_cb.setEnabled(True)
        self._progress.setRange(0, 100)  # un-spin indeterminate bar if active
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
        self._runner.wait()  # ensure Qt thread machinery has fully stopped before GC
        self._runner = None
        self._last_output = output_path
        self._log_line(f"✅  Complete — results in {output_path}", colour=_COL_SUCCESS)
        self._progress.setValue(100)
        self._open_btn.setVisible(True)
        self._reset_controls()

    def _on_error(self, tb: str) -> None:
        self._runner.wait()  # ensure Qt thread machinery has fully stopped before GC
        self._runner = None
        self._log_line("❌  Pipeline error:", colour=_COL_ERROR)
        for line in tb.splitlines():
            self._log_line(line, colour=_COL_ERROR)
        self._reset_controls()

    def _open_results(self) -> None:
        if not self._last_output:
            return
        import platform
        import subprocess

        path = self._last_output
        system = platform.system()
        if system == "Windows":
            os.startfile(path)
        elif system == "Darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])

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
                font-family: "Segoe UI", "Helvetica Neue", Arial, sans-serif;
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
            QLabel#colHeader {{
                color: {_COL_MUTED};
                font-size: 10px;
                text-transform: uppercase;
                letter-spacing: 1px;
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
                color: {_COL_MUTED};
                border: none;
                font-size: 12px;
            }}
            QPushButton#removeButton:hover {{ color: {_COL_ERROR}; }}
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
