"""Master-detail group/file-manifest editor.

Self-contained widget: owns its own data (group name -> list of file paths)
and UI, exposes a narrow read/write API. The rest of the app doesn't know or
care how groups are built — it just reads `groups()` when it needs them.

Layout: group names on the left, selected group's file manifest (a sortable
Filename | Path table) on the right — select a group, see and edit its files
directly. No dialogs, no per-row buttons.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

VIDEO_EXTS = {".mp4", ".avi", ".mov", ".mkv", ".m4v", ".wmv"}
CSV_EXTS = {".csv"}


class GroupManifestPanel(QWidget):
    """Define named groups, each holding an explicit list of file paths."""

    groups_changed = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self._manifests: dict[str, list[Path]] = {}
        self._file_exts: set[str] = VIDEO_EXTS
        self._build_ui()

    # ── Public API ──────────────────────────────────────────────────────────
    # Everything the rest of the app is allowed to know about this widget.

    def groups(self) -> dict[str, list[Path]]:
        """Return {group_name: [file_path, ...]} for every defined group, in order."""
        return {name: list(paths) for name, paths in self._manifests.items()}

    def set_file_extensions(self, exts: set[str]) -> None:
        """Set which file types 'Add folder…' picks up (switches with Video/CSV mode)."""
        self._file_exts = exts

    def clear_all_files(self) -> None:
        """Empty every group's file list, keeping the group names/structure intact."""
        for name in self._manifests:
            self._manifests[name] = []
        self._refresh_manifest_table()
        self.groups_changed.emit()

    # ── UI construction ─────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._build_group_column(), stretch=1)
        layout.addWidget(self._build_manifest_column(), stretch=2)

    def _build_group_column(self) -> QWidget:
        panel = QWidget()
        col = QVBoxLayout(panel)
        col.setContentsMargins(0, 0, 0, 0)

        col.addWidget(QLabel("Groups"))

        self._group_list = QListWidget()
        self._group_list.currentItemChanged.connect(self._on_group_selected)
        col.addWidget(self._group_list)

        row = QHBoxLayout()
        add_btn = QPushButton("Add group…")
        add_btn.clicked.connect(self._add_group)
        remove_btn = QPushButton("Remove group")
        remove_btn.clicked.connect(self._remove_selected_group)
        row.addWidget(add_btn)
        row.addWidget(remove_btn)
        col.addLayout(row)

        return panel

    def _build_manifest_column(self) -> QWidget:
        panel = QWidget()
        col = QVBoxLayout(panel)
        col.setContentsMargins(0, 0, 0, 0)

        self._detail_header = QLabel("Select a group to manage its files")
        col.addWidget(self._detail_header)

        self._manifest_table = QTableWidget(0, 2)
        self._manifest_table.setHorizontalHeaderLabels(["Filename", "Path"])
        self._manifest_table.setSortingEnabled(True)
        self._manifest_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._manifest_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._manifest_table.verticalHeader().setVisible(False)
        header = self._manifest_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        col.addWidget(self._manifest_table)

        btn_row = QHBoxLayout()
        self._add_files_btn = QPushButton("Add files…")
        self._add_files_btn.clicked.connect(self._add_files)
        self._add_folder_btn = QPushButton("Add folder…")
        self._add_folder_btn.clicked.connect(self._add_folder)
        self._remove_files_btn = QPushButton("Remove selected")
        self._remove_files_btn.clicked.connect(self._remove_selected_files)
        btn_row.addWidget(self._add_files_btn)
        btn_row.addWidget(self._add_folder_btn)
        btn_row.addWidget(self._remove_files_btn)
        col.addLayout(btn_row)

        self._set_detail_controls_enabled(False)
        return panel

    # ── Group list ───────────────────────────────────────────────────────────

    def _add_group(self) -> None:
        base = "Group"
        name = base
        suffix = 2
        while name in self._manifests:
            name = f"{base} {suffix}"
            suffix += 1

        self._manifests[name] = []
        item = QListWidgetItem(name)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
        self._group_list.addItem(item)
        self._group_list.setCurrentItem(item)
        self.groups_changed.emit()

    def _remove_selected_group(self) -> None:
        item = self._group_list.currentItem()
        if item is None:
            return
        name = item.text()
        if (
            self._manifests[name]
            and QMessageBox.question(
                self,
                "Remove group",
                f'"{name}" contains {len(self._manifests[name])} file(s). Remove it anyway?',
            )
            != QMessageBox.StandardButton.Yes
        ):
            return

        del self._manifests[name]
        self._group_list.takeItem(self._group_list.row(item))
        self.groups_changed.emit()

    def _on_group_selected(self, current: QListWidgetItem | None, _previous) -> None:
        self._refresh_manifest_table()
        self._set_detail_controls_enabled(current is not None)
        self._detail_header.setText(
            f"Files in “{current.text()}”" if current else "Select a group to manage its files"
        )

    def _set_detail_controls_enabled(self, enabled: bool) -> None:
        self._add_files_btn.setEnabled(enabled)
        self._add_folder_btn.setEnabled(enabled)
        self._remove_files_btn.setEnabled(enabled)

    def _selected_group_name(self) -> str | None:
        item = self._group_list.currentItem()
        return item.text() if item else None

    # ── Manifest table (detail view) ─────────────────────────────────────────

    def _refresh_manifest_table(self) -> None:
        name = self._selected_group_name()
        paths = self._manifests.get(name, []) if name else []

        self._manifest_table.setSortingEnabled(False)
        self._manifest_table.setRowCount(len(paths))
        for row, path in enumerate(paths):
            name_item = QTableWidgetItem(path.name)
            name_item.setToolTip(str(path))
            path_item = QTableWidgetItem(str(path.parent))
            path_item.setToolTip(str(path))
            self._manifest_table.setItem(row, 0, name_item)
            self._manifest_table.setItem(row, 1, path_item)
        self._manifest_table.setSortingEnabled(True)

    # ── Adding files ─────────────────────────────────────────────────────────

    def _add_files(self) -> None:
        name = self._selected_group_name()
        if name is None:
            return
        files, _ = QFileDialog.getOpenFileNames(self, "Select files to add")
        self._add_paths(name, [Path(f) for f in files])

    def _add_folder(self) -> None:
        name = self._selected_group_name()
        if name is None:
            return
        folder = QFileDialog.getExistingDirectory(self, "Select folder to add files from")
        if not folder:
            return
        top_level = sorted(
            f
            for f in Path(folder).iterdir()
            if f.is_file() and not f.name.startswith(".") and f.suffix.lower() in self._file_exts
        )
        self._add_paths(name, top_level)

    def _add_paths(self, group_name: str, paths: list[Path]) -> None:
        """Shared add routine for both entry points — same dedup, same feedback."""
        if not paths:
            return

        manifest = self._manifests[group_name]
        existing_in_group = set(manifest)
        added = 0

        for path in paths:
            if path in existing_in_group:
                continue  # within-group duplicate: silent skip

            other_group = self._group_containing(path, exclude=group_name)
            if other_group is not None:
                reply = QMessageBox.question(
                    self,
                    "File already in another group",
                    f'"{path.name}" is already in group "{other_group}".\n'
                    f'Add it to "{group_name}" too?',
                )
                if reply != QMessageBox.StandardButton.Yes:
                    continue

            manifest.append(path)
            existing_in_group.add(path)
            added += 1

        if added:
            self._refresh_manifest_table()
            self.groups_changed.emit()

    def _group_containing(self, path: Path, *, exclude: str) -> str | None:
        for name, paths in self._manifests.items():
            if name != exclude and path in paths:
                return name
        return None

    # ── Removing files ───────────────────────────────────────────────────────

    def _remove_selected_files(self) -> None:
        name = self._selected_group_name()
        if name is None:
            return
        rows = sorted({idx.row() for idx in self._manifest_table.selectedIndexes()}, reverse=True)
        if not rows:
            return

        manifest = self._manifests[name]
        for row in rows:
            del manifest[row]

        self._refresh_manifest_table()
        self.groups_changed.emit()
