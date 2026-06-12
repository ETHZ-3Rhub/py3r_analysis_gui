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
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QTextEdit,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from app import arenas as arena_pkg
from app.confirm_dialog import ask, grumpy_teacher, pipeline_reference_image, warning_face
from app.group_manifest_panel import CSV_EXTS, VIDEO_EXTS, GroupManifestPanel
from app.options_dialog import AdvancedOptionsDialog
from app.runner import PipelineRunner
from app.settings_dialog import EnvCheckWorker, SettingsDialog, get_version, parse_env_result
from app.theme import get_theme as _get_theme

_T = _get_theme()  # cached at import for inline widget-creation calls

_BADGE_WIDTH = 44
_REMOVE_BTN_WIDTH = 28
_COMP_PLACEHOLDER = "— select —"
_SPINNER_CHARS = "|/-\\"
_MAX_AUTO_PAIR_GROUPS = 6  # beyond this, "all pairs" stops auto-populating and is disabled


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
        self._separators: list[QFrame] = []
        self._apply_stylesheet()

        self._arenas = arena_pkg.discover()
        self._runner: PipelineRunner | None = None
        self._env_status: str = "checking"  # mirrors EnvCheckWorker result strings
        self._last_source_is_csv: bool | None = None  # None until a source is first chosen
        self._current_options: dict = {}

        # Shared filter so tooltips still show on disabled widgets (gated
        # sections, the Analyse button) — Qt suppresses them by default.
        self._btn_tooltip_filter = _TooltipOnDisabled(self)

        root = QHBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)
        root.addWidget(self._build_left_panel(), stretch=2)
        root.addWidget(self._build_run_panel(), stretch=3)

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
        sep0.setStyleSheet(f"color: {_T.sep}; margin: 4px 0;")
        self._separators.append(sep0)
        layout.addWidget(sep0)

        no_source_tip = (
            "Choose a source above first — it determines what kind of files belong in groups."
        )

        # ── Groups — locked until a source is chosen above ───────────────────
        self._groups_section = self._build_gated_section(no_source_tip)
        group_col = self._groups_section.layout()

        self._group_panel = GroupManifestPanel()
        self._group_panel.group_added.connect(self._sync_comp_add)
        self._group_panel.group_added.connect(self._refresh_run_button)
        self._group_panel.group_added.connect(self._refresh_comparisons_enabled)
        self._group_panel.group_removed.connect(self._sync_comp_remove)
        self._group_panel.group_removed.connect(self._refresh_run_button)
        self._group_panel.group_removed.connect(self._refresh_comparisons_enabled)
        self._group_panel.group_renamed.connect(self._sync_comp_rename)
        self._group_panel.files_changed.connect(self._refresh_run_button)
        group_col.addWidget(self._group_panel)

        layout.addWidget(self._groups_section, stretch=1)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {_T.sep}; margin: 4px 0;")
        self._separators.append(sep2)
        layout.addWidget(sep2)

        # ── Comparisons — locked until there are at least two groups to pair ──
        self._comp_section = self._build_gated_section(
            "Add at least two groups before defining comparisons between them."
        )
        comp_col = self._comp_section.layout()

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
                btn.installEventFilter(self._btn_tooltip_filter)
            comp_btns.addWidget(btn)
        comp_col.addLayout(comp_btns)

        layout.addWidget(self._comp_section)

        return panel

    def _build_gated_section(self, disabled_tooltip: str) -> QWidget:
        """A plain container that starts disabled with an explanatory tooltip
        — used for sections that only make sense once an earlier choice has
        been made (e.g. Groups need a Source, Comparisons need ≥ 2 groups).
        Qt suppresses tooltips on disabled widgets by default, hence the
        `_TooltipOnDisabled` filter. The tooltip is stored rather than left
        permanently set — `_set_gated_enabled` clears it once the section
        becomes relevant, so it doesn't linger and contradict what's now an
        active, self-explanatory part of the UI."""
        section = QWidget()
        section.setObjectName("gatedSection")
        section._disabled_tip = disabled_tooltip
        section.installEventFilter(self._btn_tooltip_filter)
        col = QVBoxLayout(section)
        col.setContentsMargins(0, 0, 0, 0)
        self._set_gated_enabled(section, False)
        return section

    def _set_gated_enabled(self, section: QWidget, enabled: bool) -> None:
        section.setEnabled(enabled)
        section.setToolTip("" if enabled else section._disabled_tip)

    # ── Run panel ─────────────────────────────────────────────────────────────
    def _build_run_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # ── Arena — a processing-pipeline choice (model + analysis), not
        # something that depends on the source file type, so it lives here
        # among the other run-configuration controls rather than gated on
        # the left ──────────────────────────────────────────────────────────
        arena_label = QLabel("Pipeline")
        arena_label.setObjectName("sectionTitle")
        layout.addWidget(arena_label)

        self._arena_combo = QComboBox()
        self._arena_combo.addItem("— select pipeline —", userData=None)
        for mod in self._arenas:
            self._arena_combo.addItem(mod.NAME, userData=mod)
        self._arena_combo.currentIndexChanged.connect(self._on_arena_changed)
        layout.addWidget(self._arena_combo)

        opts_row = QHBoxLayout()
        opts_row.addStretch()
        self._options_btn = QPushButton("Advanced options")
        self._options_btn.setObjectName("settingsButton")
        self._options_btn.setEnabled(False)
        self._options_btn.setToolTip("No arena selected.")
        self._options_btn.clicked.connect(self._open_options)
        opts_row.addWidget(self._options_btn)
        layout.addLayout(opts_row)

        sep_arena = QFrame()
        sep_arena.setFrameShape(QFrame.Shape.HLine)
        sep_arena.setStyleSheet(f"color: {_T.sep}; margin: 4px 0;")
        self._separators.append(sep_arena)
        layout.addWidget(sep_arena)

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
        self._env_dot.setStyleSheet(f"color: {_T.muted}; font-size: 13px;")
        self._env_lbl = QLabel("Tracking: Checking…")
        self._env_lbl.setStyleSheet(f"color: {_T.muted}; font-size: 11px;")
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
                if not ask(
                    self,
                    "Switch source?",
                    "Switching source clears every group's file list, since the\n"
                    "files no longer match the new type. Continue?",
                    grumpy_teacher(),
                    yes_label="Clear and switch",
                    no_label="Cancel",
                ):
                    self._source_group.blockSignals(True)
                    (self._csv_radio if self._last_source_is_csv else self._video_radio).setChecked(
                        True
                    )
                    self._source_group.blockSignals(False)
                    return
            self._group_panel.clear_all_files()

        self._last_source_is_csv = is_csv
        self._group_panel.set_file_extensions(CSV_EXTS if is_csv else VIDEO_EXTS)
        self._set_gated_enabled(self._groups_section, True)
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
        self._set_gated_enabled(self._comp_section, len(groups) >= 2)

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

    def _collect_warnings(self) -> list[str]:
        """Return soft-warning strings shown in the 'proceed anyway?' dialog.

        Hard errors (0 files in a group) already block the run button and are
        never included here — by the time this is called those groups are fixed.
        """
        warnings: list[str] = []

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

    def _on_arena_changed(self) -> None:
        self._current_options = {}

        arena_mod = self._arena_combo.currentData()
        if arena_mod is not None and not ask(
            self,
            "Confirm pipeline",
            f"Does your arena look like this?\n\n({arena_mod.NAME})",
            pipeline_reference_image(arena_mod),
            yes_label="Yes",
            no_label="No",
        ):
            self._arena_combo.setCurrentIndex(0)
            return

        self._refresh_run_button()

    def _open_options(self) -> None:
        arena_mod = self._arena_combo.currentData()
        options = getattr(arena_mod, "OPTIONS", []) if arena_mod else []
        if not options:
            return
        from PyQt6.QtWidgets import QDialog

        dlg = AdvancedOptionsDialog(options, self._current_options, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._current_options = dlg.values()

    def _refresh_run_button(self) -> None:
        arena_mod = self._arena_combo.currentData()
        arena_options = getattr(arena_mod, "OPTIONS", []) if arena_mod else []
        self._options_btn.setEnabled(bool(arena_options))
        if not arena_mod:
            self._options_btn.setToolTip("No arena selected.")
        elif not arena_options:
            self._options_btn.setToolTip("This arena has no advanced options.")
        else:
            n = len(arena_options)
            self._options_btn.setToolTip(f"{n} advanced option{'s' if n != 1 else ''} available.")

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
        # Nest everything inside a timestamped run folder of our own — if the
        # user points this at somewhere like their Desktop, we don't want to
        # scatter our tracking/figures/etc. folders directly into it.
        run_name = f"py3r_analysis_{datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}"
        out_root = Path(self._out_edit.text().strip())
        output_dir = out_root / run_name
        if output_dir.exists():
            # Timestamp collisions should be near-impossible (one-second
            # resolution), but if the user fires off two runs in the same
            # second — or a folder with this exact name already exists for
            # some other reason — disambiguate rather than silently mixing
            # outputs together in one folder.
            original_name = run_name
            suffix = 2
            while (out_root / f"{original_name}_{suffix}").exists():
                suffix += 1
            run_name = f"{original_name}_{suffix}"
            output_dir = out_root / run_name
            QMessageBox.information(
                self,
                "Output folder already exists",
                f'A folder named "{original_name}" already exists in the '
                f'chosen output location.\n\nResults will be written to "{run_name}" instead.',
            )
        output_dir.mkdir(parents=True, exist_ok=True)
        groups = self._group_panel.groups()
        comparisons = self._get_comparisons()
        skip_tracking = self._csv_radio.isChecked()

        warnings = self._collect_warnings()
        if warnings:
            bullet_list = "\n".join(f"  ⚠  {w}" for w in warnings)
            if not ask(
                self,
                "Warnings — proceed?",
                f"The following issues were detected:\n\n{bullet_list}\n\nProceed anyway?",
                warning_face(),
                yes_label="Proceed",
                no_label="Go back",
            ):
                return

        self._log.clear()
        self._open_btn.setVisible(False)
        self._run_btn.setText("■  Cancel")
        self._arena_combo.setEnabled(False)
        self._out_edit.setEnabled(False)
        self._comp_list.setEnabled(False)
        self._video_radio.setEnabled(False)
        self._csv_radio.setEnabled(False)

        self._runner = PipelineRunner(
            arena_mod,
            groups,
            output_dir,
            comparisons,
            skip_tracking=skip_tracking,
            options=self._current_options,
        )
        self._runner.log.connect(self._on_log)
        self._runner.warning.connect(self._on_warning)
        self._runner.subprocess_output.connect(self._on_subprocess_output)
        self._runner.heartbeat.connect(self._on_heartbeat)
        self._runner.finished.connect(self._on_finished)
        self._runner.error.connect(self._on_error)
        self._spinner_active = False
        self._spinner_idx = 0
        self._runner.start()

    def _cancel_run(self) -> None:
        if self._runner:
            # Disconnect our handlers so stray signals from the dying thread
            # don't affect the UI after we've already reset it.
            self._runner.log.disconnect(self._on_log)
            self._runner.warning.disconnect(self._on_warning)
            self._runner.subprocess_output.disconnect(self._on_subprocess_output)
            self._runner.heartbeat.disconnect(self._on_heartbeat)
            self._runner.finished.disconnect(self._on_finished)
            self._runner.error.disconnect(self._on_error)
            # cancel() kills the running subprocess immediately, so run()
            # returns within milliseconds — safe to wait() on the GUI thread.
            self._runner.cancel()
            self._runner.wait()
            self._runner.deleteLater()
            self._runner = None
        self._log_line("Cancelled.", colour=_T.error)
        self._reset_controls()

    def _reset_controls(self) -> None:
        self._run_btn.setText("▶  Analyse")
        self._arena_combo.setEnabled(True)
        self._out_edit.setEnabled(True)
        self._comp_list.setEnabled(True)
        self._update_video_radio_availability()
        self._csv_radio.setEnabled(True)
        self._refresh_run_button()

    # ── Runner signal handlers ─────────────────────────────────────────────────
    def _on_log(self, msg: str) -> None:
        self._log_line(msg)

    def _on_finished(self, output_path: str) -> None:
        self._runner.wait()  # ensure Qt thread machinery has fully stopped before GC
        self._runner = None
        self._last_output = output_path
        self._log_line(f"✅  Complete — results in {output_path}", colour=_T.success)
        self._open_btn.setVisible(True)
        self._reset_controls()

    def _on_subprocess_output(self, chunk: str) -> None:
        self._clear_spinner()
        self._log.setTextColor(QColor(_T.muted))
        self._log.insertPlainText(chunk)
        self._log.ensureCursorVisible()

    def _on_heartbeat(self) -> None:
        cursor = self._log.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if self._spinner_active:
            cursor.deletePreviousChar()
        else:
            self._spinner_active = True
        self._log.setTextColor(QColor(_T.muted))
        cursor.insertText(_SPINNER_CHARS[self._spinner_idx % len(_SPINNER_CHARS)])
        self._spinner_idx += 1
        self._log.ensureCursorVisible()

    def _clear_spinner(self) -> None:
        if not self._spinner_active:
            return
        self._spinner_active = False
        cursor = self._log.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.deletePreviousChar()

    def _on_warning(self, msg: str) -> None:
        self._log_line(f"⚠  {msg}", colour=_T.warn)

    def _on_error(self, tb: str) -> None:
        self._runner.wait()  # ensure Qt thread machinery has fully stopped before GC
        self._runner = None
        self._log_line("❌  Pipeline error:", colour=_T.error)
        for line in tb.splitlines():
            self._log_line(line, colour=_T.error)
        self._reset_controls()

    def _on_env_status(self, result: str) -> None:
        self._env_status = result
        colour, label, tooltip = parse_env_result(result)
        self._env_dot.setStyleSheet(f"color: {colour}; font-size: 13px;")
        self._env_lbl.setText(f"Tracking: {label}")
        self._env_lbl.setStyleSheet(f"color: {colour}; font-size: 11px;")
        self._env_lbl.setToolTip(tooltip)
        self._env_dot.setToolTip(tooltip)
        self._update_video_radio_availability()
        self._refresh_run_button()

    def _open_settings(self) -> None:
        SettingsDialog(self).exec()
        self._apply_stylesheet()
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

    def _log_line(self, message: str, colour: str = _T.text) -> None:
        self._clear_spinner()
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        cursor = self._log.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if cursor.columnNumber() > 0:
            self._log.insertPlainText("\n")
        self._log.setTextColor(QColor(_T.muted))
        self._log.insertPlainText(f"[{ts}] ")
        self._log.setTextColor(QColor(colour))
        self._log.insertPlainText(message + "\n")
        self._log.ensureCursorVisible()

    def _apply_stylesheet(self) -> None:
        _T = _get_theme()
        if hasattr(self, "_group_panel"):
            self._group_panel.refresh_theme()
        for sep in self._separators:
            sep.setStyleSheet(f"color: {_T.sep}; margin: 4px 0;")
        if hasattr(self, "_comp_auto_warning"):
            self._comp_auto_warning.setStyleSheet(f"color: {_T.warn}; font-size: 11px;")
        if hasattr(self, "_comp_list"):
            for w in self._comp_rows():
                w._vs_lbl.setStyleSheet(f"color: {_T.muted}; font-size: 11px;")
        if hasattr(self, "_env_dot"):
            if self._env_status == "checking":
                self._env_dot.setStyleSheet(f"color: {_T.muted}; font-size: 13px;")
                self._env_lbl.setStyleSheet(f"color: {_T.muted}; font-size: 11px;")
            else:
                self._on_env_status(self._env_status)
        self.setStyleSheet(f"""
            QWidget {{
                background-color: {_T.bg};
                color: {_T.panel_text};
                font-family: "Helvetica Neue", Arial, sans-serif;
                font-size: 13px;
            }}
            QFrame#panel {{
                background-color: {_T.panel};
                border-radius: 8px;
            }}
            QWidget#gatedSection, QWidget#groupManifestPanel {{
                background: transparent;
            }}
            QLabel {{
                background: transparent;
            }}
            QLabel#sectionTitle {{
                color: {_T.title};
                font-weight: bold;
                font-size: 12px;
                letter-spacing: 1px;
                text-transform: uppercase;
            }}
            QLabel#sectionTitle:disabled {{
                color: {_T.muted};
            }}
            QPushButton#primaryButton {{
                background-color: {_T.accent};
                color: white;
                border: none;
                border-radius: 6px;
                padding: 10px 0;
                font-size: 14px;
                font-weight: bold;
            }}
            QPushButton#primaryButton:hover {{ background-color: {_T.accent_hover}; }}
            QPushButton#primaryButton:disabled {{ background-color: {_T.muted}; }}
            QPushButton#secondaryButton {{
                background-color: transparent;
                color: {_T.accent};
                border: 1px solid {_T.accent};
                border-radius: 5px;
                padding: 6px 10px;
            }}
            QPushButton#secondaryButton:hover {{ background-color: {_T.accent}; color: white; }}
            QPushButton#secondaryButton:disabled {{
                color: {_T.muted};
                border-color: {_T.muted};
                background-color: transparent;
            }}
            QPushButton#removeButton {{
                background: transparent;
                color: {_T.muted};
                border: none;
                font-size: 12px;
            }}
            QPushButton#removeButton:hover {{ color: {_T.error}; }}
            QComboBox {{
                background-color: {_T.display};
                color: {_T.text};
                border: 1px solid {_T.muted};
                border-radius: 5px;
                padding: 6px 10px;
            }}
            QComboBox#compCombo {{
                padding: 3px 6px;
                font-size: 12px;
            }}
            QComboBox::drop-down {{ border: none; width: 24px; }}
            QComboBox QAbstractItemView {{
                background-color: {_T.display};
                color: {_T.text};
                selection-background-color: {_T.selection_bg};
            }}
            QLineEdit {{
                background-color: {_T.display};
                color: {_T.text};
                border: 1px solid {_T.muted};
                border-radius: 5px;
                padding: 6px 10px;
            }}
            QListWidget#groupList, QListWidget#manifestGroupList {{
                background-color: {_T.display};
                color: {_T.text};
                border: 1px solid {_T.muted};
                border-radius: 5px;
            }}
            QListWidget#groupList::item:selected,
            QListWidget#manifestGroupList::item:selected {{ background: transparent; }}
            QListWidget#groupList QWidget,
            QListWidget#manifestGroupList QWidget {{ background: transparent; }}
            QListWidget#groupList QComboBox,
            QListWidget#manifestGroupList QComboBox {{
                background-color: {_T.display};
                color: {_T.text};
            }}
            QListWidget#groupList QComboBox QAbstractItemView,
            QListWidget#manifestGroupList QComboBox QAbstractItemView {{
                background-color: {_T.display};
                color: {_T.text};
                selection-background-color: {_T.selection_bg};
            }}
            QTableWidget#manifestTable {{
                background-color: {_T.display};
                color: {_T.text};
                border: 1px solid {_T.muted};
                border-radius: 5px;
                gridline-color: transparent;
            }}
            QTableWidget#manifestTable::item {{
                padding: 4px 8px;
                border: none;
                color: {_T.text};
            }}
            QTableWidget#manifestTable::item:selected {{
                background-color: {_T.selection_bg};
                color: {_T.text};
            }}
            QHeaderView::section {{
                background-color: transparent;
                color: {_T.muted};
                border: none;
                border-bottom: 1px solid {_T.sep};
                padding: 4px 8px;
                font-size: 10px;
                font-weight: bold;
                text-transform: uppercase;
                letter-spacing: 1px;
            }}
            QTextEdit#logBox {{
                background-color: {_T.display};
                border: 1px solid {_T.muted};
                border-radius: 5px;
                font-family: "Menlo", "Consolas", monospace;
                font-size: 11px;
                padding: 4px;
            }}
            QScrollBar:vertical {{
                background: {_T.display};
                width: 8px;
            }}
            QScrollBar::handle:vertical {{
                background: {_T.muted};
                border-radius: 4px;
            }}
            QPushButton#settingsButton {{
                background: transparent;
                color: {_T.muted};
                border: none;
                font-size: 11px;
                padding: 2px 0;
                text-align: right;
            }}
            QPushButton#settingsButton:hover {{ color: {_T.panel_text}; }}
            QToolTip {{
                background-color: {_T.panel};
                color: {_T.panel_text};
                border: 1px solid {_T.muted};
                padding: 4px;
            }}
        """)
