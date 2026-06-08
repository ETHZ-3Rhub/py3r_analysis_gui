"""Main application window.

Two-panel layout:
  Left  — source (input type + arena), groups, comparisons
  Right — output folder, run controls, log
"""

from __future__ import annotations

import datetime
import itertools
import os
from pathlib import Path

from PyQt6.QtCore import QEvent, QObject, Qt
from PyQt6.QtGui import QCloseEvent, QColor, QTextCursor
from PyQt6.QtWidgets import (
    QButtonGroup,
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
    QRadioButton,
    QSizePolicy,
    QTextEdit,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from app import arenas as arena_pkg
from app.group_manifest_panel import CSV_EXTS, VIDEO_EXTS, GroupManifestPanel
from app.runner import PipelineRunner
from app.settings_dialog import EnvCheckWorker, SettingsDialog, get_version, parse_env_result

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

_BADGE_WIDTH = 44
_REMOVE_BTN_WIDTH = 28
_COMP_PLACEHOLDER = "— select —"


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
        self.setWindowTitle(f"py3r Analysis  v{get_version()}")
        self.setMinimumSize(1020, 700)
        self._apply_stylesheet()

        self._arenas = arena_pkg.discover()
        self._runner: PipelineRunner | None = None
        self._env_status: str = "checking"  # mirrors EnvCheckWorker result strings
        self._last_source_is_csv: bool | None = None  # None until a source is first chosen

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

        # Kick off tracking env status check
        self._env_check_worker = EnvCheckWorker()
        self._env_check_worker.done.connect(self._on_env_status)
        self._env_check_worker.start()

    def closeEvent(self, event: QCloseEvent) -> None:
        """Qt aborts (SIGABRT) if a QThread is destroyed while still running —
        e.g. closing the window while the env check is still "checking". Make
        sure any in-flight worker threads are stopped and joined first."""
        if self._runner is not None:
            self._cancel_run()
        if self._env_check_worker.isRunning():
            self._env_check_worker.done.disconnect(self._on_env_status)
            self._env_check_worker.wait()
        super().closeEvent(event)

    # ── Left panel ────────────────────────────────────────────────────────────
    def _build_left_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # ── Source — decided first: it determines what kind of files
        # belong in groups, so groups can't be built before it's chosen ──────
        src_label = QLabel("Source")
        src_label.setObjectName("sectionTitle")
        layout.addWidget(src_label)

        self._video_radio = QRadioButton("Video files (run tracking)")
        self._csv_radio = QRadioButton("Pre-tracked CSV files")
        self._csv_radio.setToolTip(
            "Each group's files are CSVs already produced by YOLO3R —\n"
            "tracking has already been done, so that step is skipped."
        )
        self._source_group = QButtonGroup(self)
        self._source_group.addButton(self._video_radio)
        self._source_group.addButton(self._csv_radio)
        self._source_group.buttonToggled.connect(self._on_source_changed)
        layout.addWidget(self._video_radio)
        layout.addWidget(self._csv_radio)
        self._update_video_radio_availability()

        sep0 = QFrame()
        sep0.setFrameShape(QFrame.Shape.HLine)
        sep0.setStyleSheet(f"color: {_COL_SEP}; margin: 4px 0;")
        layout.addWidget(sep0)

        # ── Arena — its own peer section, not a Source sub-item ──────────────
        arena_label = QLabel("Arena")
        arena_label.setObjectName("sectionTitle")
        layout.addWidget(arena_label)

        self._arena_combo = QComboBox()
        self._arena_combo.addItem("— select arena —", userData=None)
        for mod in self._arenas:
            self._arena_combo.addItem(mod.NAME, userData=mod)
        self._arena_combo.currentIndexChanged.connect(self._refresh_run_button)
        layout.addWidget(self._arena_combo)

        sep1 = QFrame()
        sep1.setFrameShape(QFrame.Shape.HLine)
        sep1.setStyleSheet(f"color: {_COL_SEP}; margin: 4px 0;")
        layout.addWidget(sep1)

        # ── Groups — locked until a source is chosen above ───────────────────
        self._groups_section = QWidget()
        self._groups_section.setEnabled(False)
        group_col = QVBoxLayout(self._groups_section)
        group_col.setContentsMargins(0, 0, 0, 0)

        self._group_panel = GroupManifestPanel()
        self._group_panel.group_added.connect(self._sync_comp_add)
        self._group_panel.group_added.connect(self._refresh_run_button)
        self._group_panel.group_removed.connect(self._sync_comp_remove)
        self._group_panel.group_removed.connect(self._refresh_run_button)
        self._group_panel.group_renamed.connect(self._sync_comp_rename)
        self._group_panel.files_changed.connect(self._refresh_run_button)
        group_col.addWidget(self._group_panel)

        layout.addWidget(self._groups_section, stretch=1)

        return panel

    # ── Run panel ─────────────────────────────────────────────────────────────
    def _build_run_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # Comparisons section
        comp_label = QLabel("Comparisons")
        comp_label.setObjectName("sectionTitle")
        layout.addWidget(comp_label)

        self._comp_list = QListWidget()
        self._comp_list.setObjectName("groupList")
        layout.addWidget(self._comp_list, stretch=1)

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

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {_COL_SEP}; margin: 4px 0;")
        layout.addWidget(sep)

        out_label = QLabel("Output folder")
        out_label.setObjectName("sectionTitle")
        layout.addWidget(out_label)

        out_row = QHBoxLayout()
        self._out_edit = QLineEdit()
        self._out_edit.setPlaceholderText("Choose output folder…")
        self._out_edit.textChanged.connect(self._refresh_run_button)
        self._out_edit.textChanged.connect(self._update_output_warning)
        out_row.addWidget(self._out_edit)
        self._out_warn_lbl = QLabel("")
        self._out_warn_lbl.setFixedWidth(_BADGE_WIDTH)
        self._out_warn_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._out_warn_lbl.setStyleSheet(f"color: {_COL_WARN}; font-size: 11px; font-weight: bold;")
        out_row.addWidget(self._out_warn_lbl)
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

        # ── Bottom bar: tracking status (left) + settings button (right) ──────
        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(6)

        self._env_dot = QLabel("●")
        self._env_dot.setFixedWidth(14)
        self._env_dot.setStyleSheet(f"color: {_COL_MUTED}; font-size: 13px;")
        self._env_lbl = QLabel("Checking…")
        self._env_lbl.setStyleSheet(f"color: {_COL_MUTED}; font-size: 11px;")
        bottom_row.addWidget(self._env_dot)
        bottom_row.addWidget(self._env_lbl)
        bottom_row.addStretch()

        settings_btn = QPushButton("⚙  Settings")
        settings_btn.setObjectName("settingsButton")
        settings_btn.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        settings_btn.clicked.connect(self._open_settings)
        bottom_row.addWidget(settings_btn)

        layout.addLayout(bottom_row)

        return panel

    # ── Source ────────────────────────────────────────────────────────────────
    def _on_source_changed(self, _button: QRadioButton, checked: bool) -> None:
        """Unlock group-building once the user has committed to a file type —
        groups can't be sensibly built before we know what should be in them.

        Switching afterwards (videos <-> CSVs) invalidates every existing
        manifest, since the files no longer match the expected type — confirm
        before clearing them, and revert the radio if the user backs out."""
        if not checked:
            return
        is_csv = self._csv_radio.isChecked()

        if self._last_source_is_csv is not None and self._last_source_is_csv != is_csv:
            if any(self._group_panel.groups().values()):
                answer = QMessageBox.question(
                    self,
                    "Switch source?",
                    "Switching source clears every group's file list, since the\n"
                    "files no longer match the new type. Continue?",
                )
                if answer != QMessageBox.StandardButton.Yes:
                    self._source_group.blockSignals(True)
                    (self._csv_radio if self._last_source_is_csv else self._video_radio).setChecked(
                        True
                    )
                    self._source_group.blockSignals(False)
                    return
            self._group_panel.clear_all_files()

        self._last_source_is_csv = is_csv
        self._group_panel.set_file_extensions(CSV_EXTS if is_csv else VIDEO_EXTS)
        self._groups_section.setEnabled(True)
        self._refresh_run_button()

    def _update_video_radio_availability(self) -> None:
        """'Video files' requires a working tracking environment — grey the
        option out (with a tooltip explaining why) until one is available,
        rather than letting the user pick it and only then telling them it
        won't work."""
        if self._env_status == "checking":
            self._video_radio.setEnabled(False)
            self._video_radio.setToolTip("Checking tracking environment…")
        elif self._env_status in ("not_installed", "error"):
            self._video_radio.setEnabled(False)
            self._video_radio.setToolTip(
                "Tracking environment is not set up.\n"
                "Open Settings to install it, or choose\n"
                "'Pre-tracked CSV files' if your files are already tracked."
            )
        else:
            self._video_radio.setEnabled(True)
            self._video_radio.setToolTip("")

    # ── Comparison management ─────────────────────────────────────────────────
    def _all_pairs(self) -> None:
        while self._comp_list.count():
            self._comp_list.takeItem(0)
        for a, b in itertools.combinations(self._group_panel.groups(), 2):
            self._add_comp_row(a, b)

    def _remove_all_comparisons(self) -> None:
        while self._comp_list.count():
            self._comp_list.takeItem(0)

    def _add_blank_comparison(self) -> None:
        if not self._group_panel.groups():
            return
        self._add_comp_row()  # both combos start on placeholder

    def _add_comp_row(self, name_a: str | None = None, name_b: str | None = None) -> None:
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
        for existing_name in [n for n in self._group_panel.groups() if n != new_name]:
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

    def _update_output_warning(self) -> None:
        """Show an orange ⚠ badge next to the output folder if it is non-empty."""
        out_text = self._out_edit.text().strip()
        if out_text:
            try:
                p = Path(out_text)
                if p.is_dir() and any(p.iterdir()):
                    self._out_warn_lbl.setText("⚠")
                    self._out_warn_lbl.setToolTip(
                        "Output folder is not empty.\nExisting files may be overwritten."
                    )
                    return
            except (PermissionError, OSError):
                pass
        self._out_warn_lbl.setText("")
        self._out_warn_lbl.setToolTip("")

    def _collect_warnings(self) -> list[str]:
        """Return soft-warning strings shown in the 'proceed anyway?' dialog.

        Hard errors (0 files in a group) already block the run button and are
        never included here — by the time this is called those groups are fixed.
        """
        warnings: list[str] = []

        # Warn if the output folder already contains files
        out_text = self._out_edit.text().strip()
        if out_text:
            out_path = Path(out_text)
            try:
                if out_path.is_dir() and any(out_path.iterdir()):
                    warnings.append(
                        "Output folder is not empty — existing files may be overwritten."
                    )
            except (PermissionError, OSError):
                pass  # can't read the folder; ignore silently

        ext_label = "CSV" if self._csv_radio.isChecked() else "video"
        for name, files in self._group_panel.groups().items():
            # 0 files is a hard error handled by the run button gate; skip here
            if 0 < len(files) < 5:
                warnings.append(
                    f'Group "{name}": only {len(files)} {ext_label} file(s) — '
                    f"results may be underpowered (expected ≥ 5)."
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
        groups = self._group_panel.groups()

        if self._arena_combo.currentData() is None:
            reasons.append("No arena selected.")
        if not groups:
            reasons.append("No groups added.")
        if not self._out_edit.text().strip():
            reasons.append("No output folder set.")

        # Hard block: tracking env not installed (only matters when actually tracking)
        skip = self._csv_radio.isChecked()
        if not skip and self._env_status in ("not_installed", "error"):
            reasons.append(
                "Tracking environment is not set up.\n"
                "     Open Settings to install it, or choose\n"
                "     'Pre-tracked CSV files' as the source to skip tracking."
            )

        # Hard block: any group with zero relevant files
        ext_label = "CSV" if skip else "video"
        for name, files in groups.items():
            if not files:
                reasons.append(f'Group "{name}" contains no {ext_label} files.')

        if self._runner is not None:
            # Pipeline is running — keep Cancel button enabled regardless of validation
            self._run_btn.setEnabled(True)
        else:
            can_run = not reasons
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
        groups = self._group_panel.groups()
        comparisons = self._get_comparisons()
        skip_tracking = self._csv_radio.isChecked()

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
        self._video_radio.setEnabled(False)
        self._csv_radio.setEnabled(False)

        self._runner = PipelineRunner(
            arena_mod, groups, output_dir, comparisons, skip_tracking=skip_tracking
        )
        self._runner.log.connect(self._on_log)
        self._runner.progress.connect(self._on_progress)
        self._runner.warning.connect(self._on_warning)
        self._runner.subprocess_output.connect(self._on_subprocess_output)
        self._runner.finished.connect(self._on_finished)
        self._runner.error.connect(self._on_error)
        self._runner.start()

    def _cancel_run(self) -> None:
        if self._runner:
            # Disconnect our handlers so stray signals from the dying thread
            # don't affect the UI after we've already reset it.
            self._runner.log.disconnect(self._on_log)
            self._runner.progress.disconnect(self._on_progress)
            self._runner.warning.disconnect(self._on_warning)
            self._runner.subprocess_output.disconnect(self._on_subprocess_output)
            self._runner.finished.disconnect(self._on_finished)
            self._runner.error.disconnect(self._on_error)
            # Schedule Qt-side cleanup once the thread actually finishes,
            # without blocking the GUI thread via wait().
            self._runner.finished.connect(self._runner.deleteLater)
            self._runner.cancel()
            self._runner = None
        self._log_line("Cancelled.", colour=_COL_ERROR)
        self._reset_controls()

    def _reset_controls(self) -> None:
        self._run_btn.setText("▶  Analyse")
        self._arena_combo.setEnabled(True)
        self._out_edit.setEnabled(True)
        self._comp_list.setEnabled(True)
        self._update_video_radio_availability()
        self._csv_radio.setEnabled(True)
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

    def _on_subprocess_output(self, chunk: str) -> None:
        self._log.setTextColor(QColor(_COL_MUTED))
        self._log.insertPlainText(chunk)
        self._log.ensureCursorVisible()

    def _on_warning(self, msg: str) -> None:
        self._log_line(f"⚠  {msg}", colour=_COL_WARN)

    def _on_error(self, tb: str) -> None:
        self._runner.wait()  # ensure Qt thread machinery has fully stopped before GC
        self._runner = None
        self._log_line("❌  Pipeline error:", colour=_COL_ERROR)
        for line in tb.splitlines():
            self._log_line(line, colour=_COL_ERROR)
        self._reset_controls()

    def _on_env_status(self, result: str) -> None:
        self._env_status = result
        colour, label, tooltip = parse_env_result(result)
        self._env_dot.setStyleSheet(f"color: {colour}; font-size: 13px;")
        self._env_lbl.setText(label)
        self._env_lbl.setStyleSheet(f"color: {colour}; font-size: 11px;")
        self._env_lbl.setToolTip(tooltip)
        self._env_dot.setToolTip(tooltip)
        self._update_video_radio_availability()
        self._refresh_run_button()

    def _open_settings(self) -> None:
        SettingsDialog(self).exec()
        # Refresh status after settings dialog closes (user may have reinstalled)
        self._env_check_worker = EnvCheckWorker()
        self._env_check_worker.done.connect(self._on_env_status)
        self._env_check_worker.start()

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
        cursor = self._log.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if cursor.columnNumber() > 0:
            self._log.insertPlainText("\n")
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
                font-family: "Helvetica Neue", Arial, sans-serif;
                font-size: 13px;
            }}
            QFrame#panel {{
                background-color: {_COL_PANEL};
                border-radius: 8px;
            }}
            QLabel {{
                background: transparent;
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
            QPushButton#secondaryButton:hover {{ background-color: {_COL_ACCENT}; color: white; }}
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
            QListWidget#manifestGroupList::item {{
                border-radius: 4px;
            }}
            QListWidget#manifestGroupList::item:selected {{
                background-color: rgba(124, 106, 247, 70);
            }}
            QListWidget#manifestGroupList::item:hover:!selected {{
                background-color: rgba(124, 106, 247, 30);
            }}
            QGroupBox#manifestGroupBox {{
                border: 1px solid {_COL_MUTED};
                border-radius: 8px;
                margin-top: 10px;
                padding-top: 12px;
                font-weight: bold;
            }}
            QGroupBox#manifestGroupBox::title {{
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
                color: {_COL_TEXT};
            }}
            QTableWidget#manifestTable {{
                background-color: {_COL_BG};
                border: 1px solid {_COL_MUTED};
                border-radius: 5px;
                gridline-color: transparent;
            }}
            QTableWidget#manifestTable::item {{
                padding: 4px 8px;
                border: none;
            }}
            QTableWidget#manifestTable::item:selected {{
                background-color: rgba(124, 106, 247, 70);
                color: {_COL_TEXT};
            }}
            QHeaderView::section {{
                background-color: transparent;
                color: {_COL_MUTED};
                border: none;
                border-bottom: 1px solid {_COL_SEP};
                padding: 4px 8px;
                font-size: 10px;
                font-weight: bold;
                text-transform: uppercase;
                letter-spacing: 1px;
            }}
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
            QPushButton#settingsButton {{
                background: transparent;
                color: {_COL_MUTED};
                border: none;
                font-size: 11px;
                padding: 2px 0;
                text-align: right;
            }}
            QPushButton#settingsButton:hover {{ color: {_COL_TEXT}; }}
            QToolTip {{
                background-color: {_COL_PANEL};
                color: {_COL_TEXT};
                border: 1px solid {_COL_MUTED};
                padding: 4px;
            }}
        """)
