"""Master-detail group/file-manifest editor.

Self-contained widget: owns its own data (group name -> list of file paths)
and UI, exposes a narrow read/write API. The rest of the app doesn't know or
care how groups are built — it just reads `groups()` when it needs them.

Layout: groups on top (name, file-count badge, ✕ remove), selected group's
file manifest below as a sortable Folder | Filename table — filenames get a
wide column with right-elision, paths show only the parent directory with
left-elision (so the directory closest to the file stays visible) and sort
by full path (directory, then filename). Select a group, see and edit its
files directly in place. No dialogs.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QModelIndex, Qt, pyqtSignal
from PyQt6.QtGui import QMouseEvent, QPalette
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

# --- REVIEWED 2026-06-08: kept deliberately, not oversights -----------------
# `_ElideLeftDelegate`, `_PathSortItem` and `_PlainTextSortItem` below are the
# most "custom Qt internals"-heavy code in this file. Considered ripping them
# out for simplicity; kept because the UX they buy (resize the Folder column
# and watch paths reveal live, sort by path *or* by filename independently)
# has real value for users whose files come from messy/inconsistent folder
# layouts, and no simpler Qt mechanism achieves either — `textElideMode` is
# view-global (not per-column) and `QTableWidgetItem` sorting is by display
# text unless you override `__lt__`. If the table ever needs to be ripped out
# or simplified, these three classes plus their wiring in `_build_manifest_row`
# / `_refresh_manifest_table` are the self-contained unit to remove.
# -----------------------------------------------------------------------------


class _DoubleClickLabel(QLabel):
    """A QLabel that emits `doubleClicked` — used so a single click on a
    group name passes through to row selection, and only an explicit
    double-click opens the rename field."""

    doubleClicked = pyqtSignal()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        self.doubleClicked.emit()
        super().mouseDoubleClickEvent(event)


class _ElideLeftDelegate(QStyledItemDelegate):
    """Elides long text from the left ("…/closest/dir/") instead of the
    right, so the directory nearest the file — the most distinguishing part
    of a path — stays visible rather than the drive root.

    `option.textElideMode` is a per-*view* setting, not per-column, so the
    only way to get left-eliding on just the Folder column is to compute and
    paint the elided text by hand here (the style still paints the
    background/selection/focus chrome via CE_ItemViewItem as normal)."""

    def paint(self, painter, option: QStyleOptionViewItem, index: QModelIndex) -> None:  # type: ignore[override]
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        text = opt.text
        opt.text = ""

        style = opt.widget.style() if opt.widget is not None else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)

        text_rect = style.subElementRect(QStyle.SubElement.SE_ItemViewItemText, opt, opt.widget)
        text_rect.adjust(4, 0, -4, 0)
        elided = opt.fontMetrics.elidedText(text, Qt.TextElideMode.ElideLeft, text_rect.width())

        role = (
            QPalette.ColorRole.HighlightedText
            if opt.state & QStyle.StateFlag.State_Selected
            else QPalette.ColorRole.Text
        )
        painter.save()
        painter.setPen(opt.palette.color(QPalette.ColorGroup.Active, role))
        painter.drawText(text_rect, int(opt.displayAlignment), elided)
        painter.restore()


class _PathSortItem(QTableWidgetItem):
    """A table item whose sort order follows a separate full-path key rather
    than its displayed text — so sorting the Folder column (which displays only
    the parent directory) groups by directory *and* then by filename within
    it, matching what a user scanning the column would expect."""

    def __init__(self, display_text: str, sort_key: str) -> None:
        super().__init__(display_text)
        self._sort_key = sort_key

    def __lt__(self, other: object) -> bool:  # type: ignore[override]
        if isinstance(other, _PathSortItem):
            return self._sort_key < other._sort_key
        return super().__lt__(other)


class _PlainTextSortItem(QTableWidgetItem):
    """Compares with plain Python string ordering — guarantees the Filename
    column sorts by exactly the same rule as the Folder column's filename
    tie-break (Qt's built-in comparison may use locale-aware/case-folding
    collation that subtly disagrees with a plain string comparison)."""

    def __lt__(self, other: object) -> bool:  # type: ignore[override]
        if isinstance(other, QTableWidgetItem):
            return self.text() < other.text()
        return super().__lt__(other)


VIDEO_EXTS = {".mp4", ".avi", ".mov", ".mkv", ".m4v", ".wmv"}
CSV_EXTS = {".csv"}

_COL_TEXT = "#cdd6f4"
_COL_MUTED = "#6c7086"
_COL_ERROR = "#f38ba8"
_COL_WARN = "#fab387"
_COL_SUCCESS = "#a6e3a1"

_BADGE_WIDTH = 44
_REMOVE_BTN_WIDTH = 28


class GroupManifestPanel(QWidget):
    """Define named groups, each holding an explicit list of file paths."""

    group_added = pyqtSignal(str)
    group_removed = pyqtSignal(str)
    group_renamed = pyqtSignal(str, str)  # old_name, new_name
    files_changed = pyqtSignal()  # a group's manifest changed — refresh counts/badges

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
        """Set which file types are expected (switches with Video/CSV mode);
        drives both the file picker's extension filter and the badge label."""
        self._file_exts = exts
        self._refresh_all_badges()

    def clear_all_files(self) -> None:
        """Empty every group's file list, keeping the group names/structure intact."""
        for name in self._manifests:
            self._manifests[name] = []
        self._refresh_manifest_table()
        self._refresh_all_badges()
        self.files_changed.emit()

    # ── UI construction ─────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._build_group_row(), stretch=1)
        layout.addWidget(self._build_manifest_row(), stretch=2)

    def _build_group_row(self) -> QWidget:
        panel = QWidget()
        col = QVBoxLayout(panel)
        col.setContentsMargins(0, 0, 0, 0)

        col.addWidget(QLabel("Groups"))

        self._group_list = QListWidget()
        self._group_list.currentItemChanged.connect(self._on_group_selected)
        col.addWidget(self._group_list)

        add_btn = QPushButton("+ Add Group")
        add_btn.clicked.connect(self._add_group)
        col.addWidget(add_btn)

        return panel

    def _build_manifest_row(self) -> QWidget:
        # A titled box framing the table + buttons as belonging to the
        # selected group — the title *is* the group's name, so the
        # connection is structural rather than something to read and infer.
        self._detail_box = QGroupBox("Select a group to manage its files")
        col = QVBoxLayout(self._detail_box)

        # Two columns: Filename (wide, elide-right — the part users scan
        # first) and Path (parent directory only, elide-left — keeps the
        # directory closest to the file, the most distinguishing part,
        # visible). Sorting the Folder column follows the *full* path so
        # entries group by directory and then by filename within it.
        self._manifest_table = QTableWidget(0, 2)
        self._manifest_table.setHorizontalHeaderLabels(["Folder", "Filename"])
        self._manifest_table.setSortingEnabled(True)
        self._manifest_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._manifest_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._manifest_table.verticalHeader().setVisible(False)
        self._manifest_table.setItemDelegateForColumn(0, _ElideLeftDelegate(self._manifest_table))
        self._manifest_table.itemSelectionChanged.connect(self._refresh_remove_files_enabled)
        header = self._manifest_table.horizontalHeader()
        # Path is the resizable column (its right edge — the boundary between
        # the two columns — is where the drag handle naturally sits);
        # Filename stretches to fill whatever's left, so there's no dangling
        # resize handle floating at the table's far-right edge.
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setStretchLastSection(False)
        header.resizeSection(0, 260)
        col.addWidget(self._manifest_table)

        btn_row = QHBoxLayout()
        self._add_files_btn = QPushButton("Add files…")
        self._add_files_btn.setToolTip(
            "Select multiple files at once, or press Ctrl+A / Cmd+A\n"
            "in the dialog to grab everything in a folder."
        )
        self._add_files_btn.clicked.connect(self._add_files)
        self._remove_files_btn = QPushButton("Remove selected")
        self._remove_files_btn.clicked.connect(self._remove_selected_files)
        btn_row.addWidget(self._add_files_btn)
        btn_row.addWidget(self._remove_files_btn)
        col.addLayout(btn_row)

        self._detail_box.setEnabled(False)
        self._remove_files_btn.setEnabled(False)
        return self._detail_box

    # ── Group list ───────────────────────────────────────────────────────────

    def _add_group(self) -> None:
        base = "Group"
        name = base
        suffix = 2
        while name in self._manifests:
            name = f"{base} {suffix}"
            suffix += 1

        self._manifests[name] = []
        item, item_widget = self._build_group_item(name)
        self._group_list.setCurrentItem(item)
        self.group_added.emit(name)

        # New group goes straight into naming, text pre-selected so typing replaces it.
        self._begin_rename(item_widget)

    def _build_group_item(self, name: str) -> tuple[QListWidgetItem, QWidget]:
        """Build a group row: name (label, switches to an edit field on
        double-click — never a live text field, so single clicks
        unambiguously select the row), file-count badge, ✕ remove."""
        item_widget = QWidget()
        row = QHBoxLayout(item_widget)
        row.setContentsMargins(4, 2, 4, 2)
        row.setSpacing(6)

        name_lbl = _DoubleClickLabel(name)
        name_lbl.setStyleSheet(f"color: {_COL_TEXT}; padding: 3px 4px;")
        name_lbl.doubleClicked.connect(lambda: self._begin_rename(item_widget))

        name_edit = QLineEdit(name)
        name_edit.setFrame(False)
        name_edit.setStyleSheet(
            f"background: transparent; color: {_COL_TEXT}; padding: 3px 4px; border: none;"
        )
        name_edit.editingFinished.connect(lambda: self._commit_rename(item_widget))
        name_edit.hide()

        name_stack = QStackedWidget()
        name_stack.addWidget(name_lbl)  # index 0 — display
        name_stack.addWidget(name_edit)  # index 1 — edit
        row.addWidget(name_stack, stretch=1)

        badge_lbl = QLabel("…")
        badge_lbl.setFixedWidth(_BADGE_WIDTH)
        badge_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge_lbl.setStyleSheet(f"color: {_COL_MUTED}; font-size: 11px; font-weight: bold;")
        row.addWidget(badge_lbl)

        remove_btn = QPushButton("✕")
        remove_btn.setFixedWidth(_REMOVE_BTN_WIDTH)
        remove_btn.clicked.connect(lambda: self._remove_group(item_widget))
        row.addWidget(remove_btn)

        item_widget._name = name  # tracks current name across renames
        item_widget._badge_lbl = badge_lbl
        item_widget._name_lbl = name_lbl
        item_widget._name_edit = name_edit
        item_widget._name_stack = name_stack

        item = QListWidgetItem()
        item.setSizeHint(item_widget.sizeHint())
        self._group_list.addItem(item)
        self._group_list.setItemWidget(item, item_widget)
        self._update_group_badge(item_widget)
        return item, item_widget

    def _begin_rename(self, item_widget: QWidget) -> None:
        idx = self._list_index(item_widget)
        if idx is not None:
            self._group_list.setCurrentRow(idx)
        item_widget._name_edit.setText(item_widget._name)
        item_widget._name_stack.setCurrentWidget(item_widget._name_edit)
        item_widget._name_edit.setFocus()
        item_widget._name_edit.selectAll()

    def _commit_rename(self, item_widget: QWidget) -> None:
        if item_widget._name_stack.currentWidget() is not item_widget._name_edit:
            return  # already committed (e.g. focus-out fired after Enter)

        old_name = item_widget._name
        new_name = item_widget._name_edit.text().strip()
        item_widget._name_stack.setCurrentWidget(item_widget._name_lbl)

        if new_name == old_name:
            return
        if not new_name or new_name in self._manifests:
            QMessageBox.warning(
                self,
                "Invalid name",
                "Group names must be non-empty and unique."
                if not new_name
                else f'A group named "{new_name}" already exists.',
            )
            return

        self._manifests[new_name] = self._manifests.pop(old_name)
        item_widget._name = new_name
        item_widget._name_lbl.setText(new_name)
        self.group_renamed.emit(old_name, new_name)
        if self._selected_group_name() == new_name:
            self._detail_box.setTitle(new_name)

    def _remove_group(self, item_widget: QWidget) -> None:
        name = item_widget._name
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

        idx = self._list_index(item_widget)
        if idx is None:
            return
        del self._manifests[name]
        self._group_list.takeItem(idx)
        self.group_removed.emit(name)

    def _list_index(self, item_widget: QWidget) -> int | None:
        for i in range(self._group_list.count()):
            if self._group_list.itemWidget(self._group_list.item(i)) is item_widget:
                return i
        return None

    def _on_group_selected(self, current: QListWidgetItem | None, _previous) -> None:
        self._refresh_manifest_table()

        name = self._selected_group_name()
        self._detail_box.setEnabled(name is not None)
        self._detail_box.setTitle(name if name else "Select a group to manage its files")
        self._refresh_remove_files_enabled()

    def _refresh_remove_files_enabled(self) -> None:
        """'Remove selected' is only meaningful once rows are actually selected."""
        self._remove_files_btn.setEnabled(bool(self._manifest_table.selectedIndexes()))

    def _selected_group_name(self) -> str | None:
        item = self._group_list.currentItem()
        if item is None:
            return None
        widget = self._group_list.itemWidget(item)
        return widget._name if widget else None

    # ── File-count badges ────────────────────────────────────────────────────

    def _update_group_badge(self, item_widget: QWidget) -> None:
        count = len(self._manifests[item_widget._name])
        ext_label = "CSV" if self._file_exts == CSV_EXTS else "video"

        if count == 0:
            colour, text = _COL_ERROR, "0 ⚠"
            tip = f"No {ext_label} files added yet."
        elif count < 5:
            colour, text = _COL_WARN, f"{count} ⚠"
            tip = f"Only {count} {ext_label} file(s) — results may be underpowered (expected ≥ 5)."
        else:
            colour, text = _COL_SUCCESS, str(count)
            tip = f"{count} {ext_label} file(s)."

        item_widget._badge_lbl.setText(text)
        item_widget._badge_lbl.setStyleSheet(
            f"color: {colour}; font-size: 11px; font-weight: bold;"
        )
        item_widget._badge_lbl.setToolTip(tip)

    def _refresh_all_badges(self) -> None:
        for i in range(self._group_list.count()):
            widget = self._group_list.itemWidget(self._group_list.item(i))
            if widget is not None:
                self._update_group_badge(widget)

    # ── Manifest table (detail view) ─────────────────────────────────────────

    def _refresh_manifest_table(self) -> None:
        name = self._selected_group_name()
        paths = self._manifests.get(name, []) if name else []

        self._manifest_table.setSortingEnabled(False)
        self._manifest_table.setRowCount(len(paths))
        for row, path in enumerate(paths):
            full = str(path)
            parent_text = str(path.parent) + "/"  # trailing slash marks it as a directory

            # Display only the parent dir (filename lives in its own column);
            # sort by the full path so directory-then-filename ordering holds.
            # Right-aligned so the elided ("…/closest/dir/") tail consistently
            # hugs the same edge the eliding cuts toward — strictly left-elide,
            # no left/right mixing as the column is resized.
            path_item = _PathSortItem(parent_text, sort_key=full)
            path_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            path_item.setToolTip(str(path.parent))
            name_item = _PlainTextSortItem(path.name)
            name_item.setToolTip(path.name)
            self._manifest_table.setItem(row, 0, path_item)
            self._manifest_table.setItem(row, 1, name_item)
        self._manifest_table.setSortingEnabled(True)

    # ── Adding files ─────────────────────────────────────────────────────────

    def _add_files(self) -> None:
        name = self._selected_group_name()
        if name is None:
            return
        ext_label = "CSV" if self._file_exts == CSV_EXTS else "Video"
        pattern = " ".join(f"*{ext}" for ext in sorted(self._file_exts))
        files, _ = QFileDialog.getOpenFileNames(
            self, "Select files to add", filter=f"{ext_label} files ({pattern})"
        )
        self._add_paths(name, [Path(f) for f in files])

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
            if (w := self._badge_widget_for(group_name)) is not None:
                self._update_group_badge(w)
            self.files_changed.emit()

    def _badge_widget_for(self, name: str) -> QWidget | None:
        for i in range(self._group_list.count()):
            widget = self._group_list.itemWidget(self._group_list.item(i))
            if widget is not None and widget._name == name:
                return widget
        return None

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
        if (w := self._badge_widget_for(name)) is not None:
            self._update_group_badge(w)
        self.files_changed.emit()
