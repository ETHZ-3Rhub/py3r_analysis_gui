"""Main application window.

Two-panel layout:
  Left  — source (input type + arena), groups, comparisons
  Right — output folder, run controls, log
"""

from __future__ import annotations

import os

from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app import arenas as arena_pkg
from app.comparisons_panel import ComparisonsPanel
from app.confirm_dialog import ask, grumpy_teacher, pipeline_reference_image
from app.gating import TooltipOnDisabled, build_gated_section, set_gated_enabled
from app.group_manifest_panel import CSV_EXTS, VIDEO_EXTS, GroupManifestPanel
from app.options_dialog import AdvancedOptionsDialog
from app.run_controller import RunController
from app.settings_dialog import SettingsDialog, get_version
from app.theme import get_theme as _get_theme
from app.tracking_env_panel import TrackingEnvPanel

_T = _get_theme()  # cached at import for inline widget-creation calls

_BADGE_WIDTH = 44


class MainWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"py3r Analysis  v{get_version()}")
        self.setMinimumSize(1020, 700)
        self._separators: list[QFrame] = []
        self._apply_stylesheet()

        self._arenas = arena_pkg.discover()
        self._last_source_is_csv: bool | None = None  # None until a source is first chosen
        self._current_options: dict = {}

        # Shared filter so tooltips still show on disabled widgets (gated
        # sections, the Analyse button) — Qt suppresses them by default.
        self._btn_tooltip_filter = TooltipOnDisabled(self)

        root = QHBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)
        root.addWidget(self._build_left_panel(), stretch=2)
        root.addWidget(self._build_run_panel(), stretch=3)

        self._run_btn.installEventFilter(self._btn_tooltip_filter)
        self._env_panel.status_changed.connect(self._on_env_status_changed)

        self._run_controller = RunController(
            dialog_parent=self,
            arena_combo=self._arena_combo,
            options_btn=self._options_btn,
            out_edit=self._out_edit,
            run_btn=self._run_btn,
            log=self._log,
            open_btn=self._open_btn,
            group_panel=self._group_panel,
            comp_panel=self._comp_panel,
            video_radio=self._video_radio,
            csv_radio=self._csv_radio,
            env_panel=self._env_panel,
            get_options=lambda: self._current_options,
            on_video_radio_refresh=self._update_video_radio_availability,
            parent=self,
        )

        # Populate initial tooltip (button starts disabled)
        self._run_controller.refresh_run_button()

        # Kick off tracking env status check
        self._env_panel.kick_env_check()

    def closeEvent(self, event: QCloseEvent) -> None:
        """Qt aborts (SIGABRT) if a QThread is destroyed while still running —
        e.g. closing the window while the env check is still "checking". Make
        sure any in-flight worker threads are stopped and joined first."""
        self._run_controller.shutdown()
        # Join any env-check/install worker threads still running (more than
        # one can be in flight if checks were kicked in quick succession).
        self._env_panel.shutdown()
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
        self._groups_section = build_gated_section(self._btn_tooltip_filter, no_source_tip)
        group_col = self._groups_section.layout()

        self._group_panel = GroupManifestPanel()
        group_col.addWidget(self._group_panel)

        layout.addWidget(self._groups_section, stretch=1)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {_T.sep}; margin: 4px 0;")
        self._separators.append(sep2)
        layout.addWidget(sep2)

        # ── Comparisons — locked until there are at least two groups to pair ──
        self._comp_panel = ComparisonsPanel(self._group_panel, self._btn_tooltip_filter)
        layout.addWidget(self._comp_panel)

        return panel

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

        # ── Bottom bar: tracking status (left) + settings button (right) ──────
        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(6)

        self._env_panel = TrackingEnvPanel()
        bottom_row.addWidget(self._env_panel)

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

        if not is_csv and not self._env_panel.offer_tracking_install_if_needed():
            self._revert_source_selection()
            return

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
        set_gated_enabled(self._groups_section, True)
        self._run_controller.refresh_run_button()

    def _revert_source_selection(self) -> None:
        """Undo a source selection the user backed out of (e.g. declined
        tracking setup) — back to whatever was selected before, or nothing
        if this was the first choice."""
        self._source_group.blockSignals(True)
        if self._last_source_is_csv is None:
            # Exclusive QButtonGroups won't let setChecked(False) leave zero
            # buttons checked — temporarily relax exclusivity to allow it.
            self._source_group.setExclusive(False)
            self._video_radio.setChecked(False)
            self._source_group.setExclusive(True)
        elif self._last_source_is_csv:
            self._csv_radio.setChecked(True)
        else:
            self._video_radio.setChecked(True)
        self._source_group.blockSignals(False)

    def _update_video_radio_availability(self) -> None:
        """'Video files' is always selectable — if the tracking environment
        isn't ready yet, picking it offers to set it up in the background
        (see TrackingEnvPanel.offer_tracking_install_if_needed) rather than greying the
        option out and sending the user on a scavenger hunt to Settings."""
        self._video_radio.setEnabled(True)
        self._video_radio.setToolTip("")

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

        self._run_controller.refresh_run_button()

    def _open_options(self) -> None:
        arena_mod = self._arena_combo.currentData()
        options = getattr(arena_mod, "OPTIONS", []) if arena_mod else []
        if not options:
            return
        from PySide6.QtWidgets import QDialog

        dlg = AdvancedOptionsDialog(options, self._current_options, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._current_options = dlg.values()

    def _on_env_status_changed(self) -> None:
        self._update_video_radio_availability()
        self._run_controller.refresh_run_button()

    def _open_settings(self) -> None:
        SettingsDialog(self._env_panel, self).exec()
        self._apply_stylesheet()

    def _open_results(self) -> None:
        if not self._run_controller.last_output:
            return
        import platform
        import subprocess

        path = self._run_controller.last_output
        system = platform.system()
        if system == "Windows":
            os.startfile(path)
        elif system == "Darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _apply_stylesheet(self) -> None:
        _T = _get_theme()
        if hasattr(self, "_group_panel"):
            self._group_panel.refresh_theme()
        for sep in self._separators:
            sep.setStyleSheet(f"color: {_T.sep}; margin: 4px 0;")
        if hasattr(self, "_comp_panel"):
            self._comp_panel.refresh_theme()
        if hasattr(self, "_env_panel"):
            self._env_panel.refresh_theme()
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
            QRadioButton::indicator {{
                width: 14px;
                height: 14px;
                border-radius: 8px;
                border: 2px solid {_T.muted};
                background-color: transparent;
            }}
            QRadioButton::indicator:checked {{
                border: 2px solid {_T.accent};
                background-color: {_T.accent};
            }}
            QRadioButton::indicator:disabled {{
                border: 2px solid {_T.sep};
            }}
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
