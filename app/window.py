"""Main application window.

Two-panel layout:
  Left   — group management (add / remove / rename groups)
  Right  — arena selector, output folder, run controls, log

Deliberately kept in a single file while the UI is small.  Split into
widgets/ sub-modules if it grows.
"""

from __future__ import annotations

import datetime
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
_COL_ERROR   = "#f38ba8"
_COL_SUCCESS = "#a6e3a1"
_GROUP_COLOURS = ["#7c6af7", "#f38ba8", "#a6e3a1", "#fab387", "#89dceb", "#f9e2af"]

# Fixed pixel width for the group-name inline editor
_NAME_EDIT_WIDTH = 130


class MainWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("py3r Analysis")
        self.setMinimumSize(1020, 700)
        self._apply_stylesheet()

        self._arenas = arena_pkg.discover()
        self._runner: PipelineRunner | None = None
        self._groups: list[tuple[str, Path]] = []   # [(name, path), …]

        root = QHBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)
        root.addWidget(self._build_groups_panel(), stretch=2)
        root.addWidget(self._build_run_panel(), stretch=3)

    # ── Groups panel ──────────────────────────────────────────────────────────
    def _build_groups_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        title = QLabel("Groups")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)

        self._group_list = QListWidget()
        self._group_list.setObjectName("groupList")
        layout.addWidget(self._group_list)

        add_btn = QPushButton("+ Add Group")
        add_btn.setObjectName("secondaryButton")
        add_btn.clicked.connect(self._add_group)
        layout.addWidget(add_btn)

        return panel

    # ── Run panel ─────────────────────────────────────────────────────────────
    def _build_run_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # Arena selector
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

        # Output folder
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

        # Analyse button
        self._run_btn = QPushButton("▶  Analyse")
        self._run_btn.setObjectName("primaryButton")
        self._run_btn.setEnabled(False)
        self._run_btn.clicked.connect(self._toggle_run)
        layout.addWidget(self._run_btn)

        # Progress bar
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setTextVisible(False)
        layout.addWidget(self._progress)

        # Log
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setObjectName("logBox")
        layout.addWidget(self._log, stretch=1)

        # Open results folder (hidden until a run completes successfully)
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

        # Warn if this folder is already in the list
        existing_paths = [p for _, p in self._groups]
        if path in existing_paths:
            QMessageBox.warning(
                self,
                "Duplicate group",
                f"The folder\n{path}\nis already in the group list.",
            )
            return

        name = path.name
        colour = _GROUP_COLOURS[len(self._groups) % len(_GROUP_COLOURS)]
        self._groups.append((name, path))
        self._add_group_row(name, path, colour)
        self._refresh_run_button()

    def _add_group_row(self, name: str, path: Path, colour: str) -> None:
        item_widget = QWidget()
        row = QHBoxLayout(item_widget)
        row.setContentsMargins(4, 2, 4, 2)
        row.setSpacing(6)

        # Colour dot
        dot = QLabel("●")
        dot.setStyleSheet(f"color: {colour}; font-size: 14px;")
        dot.setFixedWidth(16)
        row.addWidget(dot)

        # Group name — fixed width so it doesn't get squeezed by long paths
        name_edit = QLineEdit(name)
        name_edit.setFrame(False)
        name_edit.setFixedWidth(_NAME_EDIT_WIDTH)
        # Explicit padding restores descender room lost when overriding the global stylesheet
        name_edit.setStyleSheet(
            f"background: transparent; color: {_COL_TEXT}; padding: 3px 4px; border: none;"
        )
        name_edit.editingFinished.connect(
            lambda: self._rename_group(item_widget, name_edit.text())
        )
        row.addWidget(name_edit)

        # Path label — elided with full path as tooltip
        path_str = str(path)
        path_lbl = QLabel(path_str)
        path_lbl.setStyleSheet(f"color: {_COL_MUTED}; font-size: 11px;")
        path_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        path_lbl.setToolTip(path_str)   # full path on hover
        row.addWidget(path_lbl, stretch=1)

        # Remove button
        remove_btn = QPushButton("✕")
        remove_btn.setFixedWidth(28)
        remove_btn.setObjectName("removeButton")
        remove_btn.clicked.connect(lambda: self._remove_group(item_widget))
        row.addWidget(remove_btn)

        item = QListWidgetItem()
        item.setSizeHint(item_widget.sizeHint())
        self._group_list.addItem(item)
        self._group_list.setItemWidget(item, item_widget)

    def _rename_group(self, widget: QWidget, new_name: str) -> None:
        idx = self._widget_index(widget)
        if idx is not None:
            _, path = self._groups[idx]
            self._groups[idx] = (new_name, path)

    def _remove_group(self, widget: QWidget) -> None:
        idx = self._widget_index(widget)
        if idx is not None:
            self._groups.pop(idx)
            self._group_list.takeItem(idx)
            self._refresh_run_button()

    def _widget_index(self, widget: QWidget) -> int | None:
        for i in range(self._group_list.count()):
            if self._group_list.itemWidget(self._group_list.item(i)) is widget:
                return i
        return None

    # ── Run controls ──────────────────────────────────────────────────────────
    def _browse_output(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select output folder")
        if folder:
            self._out_edit.setText(folder)

    def _refresh_run_button(self) -> None:
        has_arena = self._arena_combo.currentData() is not None
        has_groups = len(self._groups) > 0
        has_output = bool(self._out_edit.text().strip())
        not_running = self._runner is None
        self._run_btn.setEnabled(has_arena and has_groups and has_output and not_running)

    def _toggle_run(self) -> None:
        if self._runner is not None:
            self._cancel_run()
        else:
            self._start_run()

    def _start_run(self) -> None:
        arena_mod = self._arena_combo.currentData()
        if arena_mod is None:
            QMessageBox.warning(self, "No arena", "Please select an arena.")
            return

        output_dir = Path(self._out_edit.text().strip())
        groups = {name: path for name, path in self._groups}

        self._log.clear()
        self._progress.setValue(0)
        self._open_btn.setVisible(False)
        self._run_btn.setText("■  Cancel")
        self._arena_combo.setEnabled(False)
        self._out_edit.setEnabled(False)

        self._runner = PipelineRunner(arena_mod, groups, output_dir)
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
        self._refresh_run_button()

    # ── Runner signal handlers ─────────────────────────────────────────────────
    def _on_log(self, message: str) -> None:
        self._log_line(message)

    def _on_progress(self, pct: int) -> None:
        if pct < 0:
            self._progress.setRange(0, 0)   # indeterminate spinner
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
            os.startfile(self._last_output)   # Windows only — correct for target OS

    # ── Helpers ───────────────────────────────────────────────────────────────
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
