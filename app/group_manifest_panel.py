"""Flat group list + per-group file-manifest editor dialog.

Self-contained widget: owns its own data (group name -> list of file paths)
and UI, exposes a narrow read/write API. The rest of the app doesn't know or
care how groups are built — it just reads `groups()` when it needs them.

Layout: a single flat list of named groups (name, file-count badge, "Files…"
button, ✕ remove). Two creation paths sit below it:
  - "Add from folder…" — picks a folder, the group is built from every
    matching file in it (one click for the common case: organised labs
    where each condition already lives in its own folder)
  - "Add from files…" — opens the file picker directly and builds the group
    from whatever's selected (the escape hatch for messy/scattered layouts)
Either way the group is born already holding files — there's no such thing
as an empty group to fill in later — and its name is immediately offered up
for editing (pre-filled from the folder name, or "Group" for manual picks).

Click a group's name at any time to rename it in place. Click "Files…" to
open a small modal table (Folder | Filename, sortable, add/remove) for that
group — kept out of the main view so the always-visible list stays simple.
"""

from __future__ import annotations

import csv
from pathlib import Path

from PySide6.QtCore import QModelIndex, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QMouseEvent, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QFileDialog,
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

from app.confirm_dialog import ask
from app.styles import base_stylesheet
from app.text_utils import natural_key
from app.theme import get_theme as _get_theme

# --- REVIEWED 2026-06-08: kept deliberately, not oversights -----------------
# `_ElideLeftDelegate`, `_PathSortItem` and `_PlainTextSortItem` below are the
# most "custom Qt internals"-heavy code in this file. Considered ripping them
# out for simplicity; kept because the UX they buy (resize the Folder column
# and watch paths reveal live, sort by path *or* by filename independently)
# has real value for users whose files come from messy/inconsistent folder
# layouts, and no simpler Qt mechanism achieves either — `textElideMode` is
# view-global (not per-column) and `QTableWidgetItem` sorting is by display
# text unless you override `__lt__`. If the manifest dialog ever needs to be
# ripped out or simplified, these three classes plus their wiring in
# `_ManifestDialog` are the self-contained unit to remove.
# -----------------------------------------------------------------------------


class _ClickableLabel(QLabel):
    """A QLabel that emits `clicked` on a single press — used so clicking a
    group's name opens it for renaming immediately (there's no row-selection
    state to protect against accidental clicks anymore)."""

    clicked = Signal()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        self.clicked.emit()
        super().mousePressEvent(event)


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

        if opt.state & QStyle.StateFlag.State_Selected:
            color = opt.palette.color(
                QPalette.ColorGroup.Active, QPalette.ColorRole.HighlightedText
            )
        else:
            fg: QBrush | None = index.data(Qt.ItemDataRole.ForegroundRole)
            color = (
                fg.color()
                if isinstance(fg, QBrush)
                else opt.palette.color(QPalette.ColorGroup.Active, QPalette.ColorRole.Text)
            )

        painter.save()
        painter.setPen(color)
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
            return natural_key(self._sort_key) < natural_key(other._sort_key)
        return super().__lt__(other)


class _PlainTextSortItem(QTableWidgetItem):
    """Compares with plain Python string ordering — guarantees the Filename
    column sorts by exactly the same rule as the Folder column's filename
    tie-break (Qt's built-in comparison may use locale-aware/case-folding
    collation that subtly disagrees with a plain string comparison)."""

    def __lt__(self, other: object) -> bool:  # type: ignore[override]
        if isinstance(other, QTableWidgetItem):
            return natural_key(self.text()) < natural_key(other.text())
        return super().__lt__(other)


VIDEO_EXTS = {".mp4", ".avi", ".mov", ".mkv", ".m4v", ".wmv"}
CSV_EXTS = {".csv"}


_BADGE_WIDTH = 44
_REMOVE_BTN_WIDTH = 28

# Text colours cycled over unique folders in the manifest dialog table so
# rows from the same folder share a colour. All Catppuccin Mocha tones —
# readable against the dark background, distinct from each other, and none
# clash with the app's error/warning/success semantic colours.
_FOLDER_TEXT_COLOURS = [
    "#89b4fa",  # blue
    "#cba6f7",  # mauve
    "#89dceb",  # sky
    "#94e2d5",  # teal
    "#f9e2af",  # yellow
    "#b4befe",  # lavender
    "#74c7ec",  # sapphire
    "#a6adc8",  # overlay (dimmer, 8th+ folders)
]


# Columns present in every YOLO3R tracking CSV produced by a pixel-rescaled
# (i.e. compatible) version of this pipeline — used to filter out unrelated
# CSVs (shopping lists, metadata exports, ...) accidentally added in "skip
# tracking" mode. max_dim.x/y specifically guards against older, non-rescaled
# YOLO3R-shaped CSVs that would otherwise produce an aspect-ratio error.
_YOLO3R_HEADER_MARKERS = {"frame_index", "max_dim.x", "max_dim.y"}


def _looks_like_yolo3r_csv(path: Path) -> bool:
    """Cheap structural check on a CSV's header row only."""
    try:
        with path.open(newline="", encoding="utf-8", errors="ignore") as f:
            header = next(csv.reader(f), None)
    except OSError:
        return False
    return header is not None and _YOLO3R_HEADER_MARKERS.issubset(header)


# DLC CSVs have three header rows whose first cell is the row-type label.
_DLC_HEADER_LABELS = ("scorer", "bodyparts", "coords")


def _looks_like_dlc_csv(path: Path) -> bool:
    """Cheap structural check: first cell of each of the first 3 rows must be
    the expected DLC header label (scorer / bodyparts / coords)."""
    try:
        with path.open(newline="", encoding="utf-8", errors="ignore") as f:
            reader = csv.reader(f)
            rows = [next(reader, None) for _ in range(3)]
    except OSError:
        return False
    return all(
        row is not None and row and row[0] == label
        for row, label in zip(rows, _DLC_HEADER_LABELS, strict=False)
    )


def _is_known_tracking_csv(path: Path) -> bool:
    return _looks_like_yolo3r_csv(path) or _looks_like_dlc_csv(path)


def _ext_label(file_exts: set[str]) -> str:
    return "CSV" if file_exts == CSV_EXTS else "video"


def _file_dialog_filter(file_exts: set[str]) -> str:
    label = "CSV" if file_exts == CSV_EXTS else "Video"
    pattern = " ".join(f"*{ext}" for ext in sorted(file_exts))
    return f"{label} files ({pattern})"


class _ManifestDialog(QDialog):
    """Modal Folder | Filename editor for one group's file list.

    Deliberately separate from the main group list — keeping the table (the
    "busy" part of this UI) out of the always-visible view was the whole
    point of this redesign; it only needs to exist while the user is actively
    curating one group's files."""

    def __init__(self, panel: GroupManifestPanel, group_name: str) -> None:
        super().__init__(panel)
        self._panel = panel
        self._group_name = group_name
        self.setWindowTitle(group_name)
        # Top-level windows (QDialog included) do NOT inherit a parent widget's
        # `setStyleSheet()` — only the QApplication-wide one — so without this,
        # the table/buttons/tooltips inside fall back to plain system (light)
        # styling while everything else in the app is dark. Built from the
        # shared helper rather than copied off the parent window, so the dialog
        # doesn't depend on having a styled `MainWindow` ancestor.
        self.setStyleSheet(base_stylesheet(_get_theme()))
        self.resize(560, 380)
        self._build_ui()
        self._refresh_table()
        self._table.sortItems(0, Qt.SortOrder.AscendingOrder)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self._table.clearSelection()
        super().mousePressEvent(event)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        # Two columns: Folder (parent directory only, elide-left — keeps the
        # directory closest to the file, the most distinguishing part,
        # visible) and Filename (wide, elide-right). Sorting the Folder
        # column follows the *full* path so entries group by directory and
        # then by filename within it.
        self._table = QTableWidget(0, 2)
        self._table.setObjectName("manifestTable")
        self._table.setHorizontalHeaderLabels(["Folder", "Filename"])
        self._table.setShowGrid(False)
        self._table.setSortingEnabled(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.setItemDelegateForColumn(0, _ElideLeftDelegate(self._table))
        self._table.itemSelectionChanged.connect(self._refresh_remove_enabled)
        header = self._table.horizontalHeader()
        # Folder is the resizable column (its right edge — the boundary
        # between the two columns — is where the drag handle naturally sits);
        # Filename stretches to fill whatever's left, so there's no dangling
        # resize handle floating at the table's far-right edge.
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setStretchLastSection(False)
        header.resizeSection(0, 260)
        layout.addWidget(self._table)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("Add files…")
        add_btn.setObjectName("secondaryButton")
        add_btn.setToolTip(
            "Select multiple files at once, or press Ctrl+A / Cmd+A\n"
            "in the dialog to grab everything in a folder."
        )
        add_btn.clicked.connect(self._add_files)
        self._remove_btn = QPushButton("Remove selected")
        self._remove_btn.setObjectName("secondaryButton")
        self._remove_btn.setEnabled(False)
        self._remove_btn.clicked.connect(self._remove_selected)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(self._remove_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

    def _refresh_remove_enabled(self) -> None:
        self._remove_btn.setEnabled(bool(self._table.selectedIndexes()))

    def _refresh_table(self) -> None:
        paths = self._panel._manifests.get(self._group_name, [])

        # Assign a text colour to each unique parent folder in first-seen order.
        folder_colour: dict[Path, QBrush] = {}
        for path in paths:
            if path.parent not in folder_colour:
                idx = len(folder_colour) % len(_FOLDER_TEXT_COLOURS)
                folder_colour[path.parent] = QBrush(QColor(_FOLDER_TEXT_COLOURS[idx]))

        self._table.clearSelection()
        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(paths))
        for row, path in enumerate(paths):
            full = str(path)
            parent_text = str(path.parent) + "/"  # trailing slash marks it as a directory
            bg = folder_colour[path.parent]

            # Right-aligned so the elided ("…/closest/dir/") tail consistently
            # hugs the same edge the eliding cuts toward.
            path_item = _PathSortItem(parent_text, sort_key=full)
            path_item.setData(Qt.ItemDataRole.UserRole, full)
            path_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            path_item.setToolTip(str(path.parent))
            path_item.setForeground(bg)
            name_item = _PlainTextSortItem(path.name)
            name_item.setToolTip(path.name)
            name_item.setForeground(bg)
            self._table.setItem(row, 0, path_item)
            self._table.setItem(row, 1, name_item)
        self._table.setSortingEnabled(True)

    def _add_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self, "Select files to add", filter=_file_dialog_filter(self._panel._file_exts)
        )
        if self._panel._add_paths(self._group_name, [Path(f) for f in files]):
            self._refresh_table()

    def _remove_selected(self) -> None:
        rows = {idx.row() for idx in self._table.selectedIndexes()}
        if not rows:
            return
        to_remove = {Path(self._table.item(row, 0).data(Qt.ItemDataRole.UserRole)) for row in rows}
        manifest = self._panel._manifests[self._group_name]
        self._panel._manifests[self._group_name] = [p for p in manifest if p not in to_remove]
        self._refresh_table()
        self._panel._on_manifest_changed(self._group_name)


class GroupManifestPanel(QWidget):
    """Define named groups, each holding an explicit list of file paths."""

    group_added = Signal(str)
    group_removed = Signal(str)
    group_renamed = Signal(str, str)  # old_name, new_name
    files_changed = Signal()  # a group's manifest changed — refresh counts/badges

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

    def _filter_valid_csvs(self, paths: list[Path]) -> tuple[list[Path], int]:
        """In CSV ("skip tracking") mode, drop files that don't look like
        tracking output from a known tracker (YOLO3R or DLC). Returns
        (valid_paths, n_skipped)."""
        if self._file_exts != CSV_EXTS:
            return paths, 0
        valid = [p for p in paths if _is_known_tracking_csv(p)]
        return valid, len(paths) - len(valid)

    def _warn_skipped_csvs(self, n_skipped: int) -> None:
        noun = "file" if n_skipped == 1 else "files"
        QMessageBox.information(
            self,
            "Some files skipped",
            f"{n_skipped} {noun} don't look like YOLO3R tracking output "
            "(unexpected column headers) and were skipped.",
        )

    def clear_all_files(self) -> None:
        """Empty every group's file list, keeping the group names/structure intact."""
        for name in self._manifests:
            self._manifests[name] = []
        self._refresh_all_badges()
        self.files_changed.emit()

    # ── UI construction ─────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        # Plain QWidgets pick up the app-wide `QWidget { background-color }`
        # rule, which is *darker* than the panel they sit on — without an
        # override the section reads as one big dark box swallowing its own
        # heading rather than sitting flush on the panel like its siblings.
        # NOTE: deliberately an object-name rule in the central stylesheet
        # (see window.py `_apply_stylesheet`, `#groupManifestPanel`), not a
        # local `setStyleSheet()` call here — a widget-instance stylesheet
        # creates its own cascade scope that silently breaks descendants'
        # object-name styling and tooltip theming (cost a few hours to find).
        self.setObjectName("groupManifestPanel")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        groups_label = QLabel("Groups")
        groups_label.setObjectName("sectionTitle")
        layout.addWidget(groups_label)

        self._group_list = QListWidget()
        self._group_list.setObjectName("manifestGroupList")
        layout.addWidget(self._group_list, stretch=1)

        adv_row = QHBoxLayout()
        adv_row.setContentsMargins(0, 0, 0, 0)
        adv_btn = QPushButton("Advanced loader")
        adv_btn.setObjectName("settingsButton")
        adv_btn.setToolTip(
            "Open the advanced loader — import groups from a metadata CSV\n"
            "or (coming soon) by walking a directory tree."
        )
        adv_btn.clicked.connect(self._open_advanced_loader)
        adv_row.addStretch()
        adv_row.addWidget(adv_btn)
        layout.addLayout(adv_row)

        add_row = QHBoxLayout()
        add_row.setSpacing(6)
        from_folder_btn = QPushButton("+ New group from folder…")
        from_folder_btn.setObjectName("secondaryButton")
        from_folder_btn.setToolTip(
            "Build a group from every matching file already sitting\n"
            "in one folder — the one-click option if your files are organised."
        )
        from_folder_btn.clicked.connect(lambda: self._add_from_folder())
        from_files_btn = QPushButton("+ New group from files…")
        from_files_btn.setObjectName("secondaryButton")
        from_files_btn.setToolTip(
            "Pick individual files yourself — for groups whose files\n"
            "are scattered across several folders."
        )
        from_files_btn.clicked.connect(self._add_from_files)
        add_row.addWidget(from_folder_btn)
        add_row.addWidget(from_files_btn)
        layout.addLayout(add_row)

    # ── Group creation ───────────────────────────────────────────────────────

    def _add_from_folder(self, start_dir: str = "") -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select a folder", start_dir)
        if not folder:
            return
        folder_path = Path(folder)

        try:
            contents = list(folder_path.iterdir())
            paths = sorted(
                p
                for p in contents
                if p.is_file()
                and not p.name.startswith(".")
                and p.suffix.lower() in self._file_exts
            )
            has_subdirs = any(p.is_dir() for p in contents)
        except (PermissionError, OSError):
            paths = []
            has_subdirs = False

        paths, n_skipped = self._filter_valid_csvs(paths)

        if not paths:
            if n_skipped:
                QMessageBox.information(
                    self,
                    "No matching files",
                    f'"{folder_path.name}" contains {n_skipped} CSV file(s), but none look '
                    "like YOLO3R tracking output (unexpected column headers). "
                    "Pick a different folder, or add files manually instead.",
                )
            else:
                QMessageBox.information(
                    self,
                    "No matching files",
                    f'"{folder_path.name}" contains no {_ext_label(self._file_exts)} files '
                    "(subfolders are not scanned). "
                    "Pick a different folder, or add files manually instead.",
                )
            return

        if n_skipped:
            self._warn_skipped_csvs(n_skipped)

        if has_subdirs:
            msg = QMessageBox(self)
            msg.setWindowTitle("Subfolders will be ignored")
            msg.setText(
                f'"{folder_path.name}" contains subfolders, which won\'t be scanned.\n\n'
                f"Only {_ext_label(self._file_exts)} files directly inside this folder "
                "will be added.\n\n"
                "Continue, or go back to pick a different folder?"
            )
            continue_btn = msg.addButton("Continue", QMessageBox.ButtonRole.AcceptRole)
            msg.addButton("Go Back", QMessageBox.ButtonRole.RejectRole)
            msg.exec()
            if msg.clickedButton() is not continue_btn:
                self._add_from_folder(start_dir=str(folder_path.parent))
                return

        self._create_group(default_name=folder_path.name, paths=paths)

    def _add_from_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self, "Select files", filter=_file_dialog_filter(self._file_exts)
        )
        if not files:
            return
        paths, n_skipped = self._filter_valid_csvs([Path(f) for f in files])
        if n_skipped:
            self._warn_skipped_csvs(n_skipped)
        if not paths:
            return
        self._create_group(default_name="Group", paths=paths)

    def _open_advanced_loader(self) -> None:
        from app.advanced_loader_dialog import AdvancedLoaderDialog

        dlg = AdvancedLoaderDialog(self._file_exts, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._bulk_add_groups(dlg.result_groups())

    def _bulk_add_groups(self, groups: dict[str, list[Path]]) -> None:
        for name, paths in groups.items():
            self._add_group_direct(name, paths)

    def _add_group_direct(self, name: str, paths: list[Path]) -> None:
        """Like _create_group but skips the rename prompt — used for CSV import."""
        base = name
        suffix = 2
        while name in self._manifests:
            name = f"{base} {suffix}"
            suffix += 1
        self._manifests[name] = []
        self._build_group_item(name)
        self.group_added.emit(name)
        self._add_paths(name, paths)

    def _create_group(self, default_name: str, paths: list[Path]) -> None:
        """Groups are always born holding files — naming comes after, as a
        "rename this thing you just made" step (pre-filled and ready to
        type over), since an empty group never makes sense on its own."""
        name = default_name
        suffix = 2
        while name in self._manifests:
            name = f"{default_name} {suffix}"
            suffix += 1

        self._manifests[name] = []
        item, item_widget = self._build_group_item(name)
        self.group_added.emit(name)
        self._add_paths(name, paths)
        self._begin_rename(item_widget)

    # ── Group list ───────────────────────────────────────────────────────────

    def _build_group_item(self, name: str) -> tuple[QListWidgetItem, QWidget]:
        """Build a group row: name (label, click to rename — there's no
        row-selection state to protect, so a single click is unambiguous),
        file-count badge, "Files…" (opens the manifest dialog), ✕ remove."""
        item_widget = QWidget()
        row = QHBoxLayout(item_widget)
        row.setContentsMargins(4, 2, 4, 2)
        row.setSpacing(6)

        t = _get_theme()

        name_lbl = _ClickableLabel(name)
        name_lbl.setStyleSheet(f"color: {t.text}; padding: 3px 4px;")
        name_lbl.clicked.connect(lambda: self._begin_rename(item_widget))

        name_edit = QLineEdit(name)
        name_edit.setFrame(False)
        name_edit.setStyleSheet(
            f"background: transparent; color: {t.text}; padding: 3px 4px; border: none;"
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
        badge_lbl.setStyleSheet(f"color: {t.muted}; font-size: 11px; font-weight: bold;")
        row.addWidget(badge_lbl)

        files_btn = QPushButton("Edit")
        files_btn.setObjectName("secondaryButton")
        files_btn.clicked.connect(lambda: self._open_manifest_dialog(item_widget._name))
        row.addWidget(files_btn)

        remove_btn = QPushButton("✕")
        remove_btn.setObjectName("removeButton")
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

    def _open_manifest_dialog(self, name: str) -> None:
        _ManifestDialog(self, name).exec()

    def _begin_rename(self, item_widget: QWidget) -> None:
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

    def _remove_group(self, item_widget: QWidget) -> None:
        name = item_widget._name
        if self._manifests[name] and not ask(
            self,
            "Remove group",
            f'"{name}" contains {len(self._manifests[name])} file(s). Remove it anyway?',
            yes_label="Remove",
            no_label="Cancel",
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

    # ── File-count badges ────────────────────────────────────────────────────

    def _update_group_badge(self, item_widget: QWidget) -> None:
        t = _get_theme()
        count = len(self._manifests[item_widget._name])
        ext_label = _ext_label(self._file_exts)

        if count == 0:
            colour, text = t.error, "0 ⚠"
            tip = f"No {ext_label} files added yet."
        elif count < 5:
            colour, text = t.warn, f"{count} ⚠"
            tip = f"Only {count} {ext_label} file(s) — results may be underpowered (expected ≥ 5)."
        else:
            colour, text = t.success, str(count)
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

    def refresh_theme(self) -> None:
        """Re-apply theme colours to existing group rows after a theme
        change — group widgets are built once and styled at creation time,
        so they don't pick up new theme tokens automatically."""
        t = _get_theme()
        for i in range(self._group_list.count()):
            widget = self._group_list.itemWidget(self._group_list.item(i))
            if widget is None:
                continue
            widget._name_lbl.setStyleSheet(f"color: {t.text}; padding: 3px 4px;")
            widget._name_edit.setStyleSheet(
                f"background: transparent; color: {t.text}; padding: 3px 4px; border: none;"
            )
            self._update_group_badge(widget)

    def _badge_widget_for(self, name: str) -> QWidget | None:
        for i in range(self._group_list.count()):
            widget = self._group_list.itemWidget(self._group_list.item(i))
            if widget is not None and widget._name == name:
                return widget
        return None

    # ── Manifest editing (called from _ManifestDialog and group creation) ────

    def _on_manifest_changed(self, group_name: str) -> None:
        if (w := self._badge_widget_for(group_name)) is not None:
            self._update_group_badge(w)
        self.files_changed.emit()

    def _add_paths(self, group_name: str, paths: list[Path]) -> bool:
        """Shared add routine for both entry points — same dedup, same
        feedback. Returns True if anything was actually added."""
        paths, n_skipped = self._filter_valid_csvs(paths)
        if n_skipped:
            self._warn_skipped_csvs(n_skipped)
        if not paths:
            return False

        manifest = self._manifests[group_name]
        existing_in_group = set(manifest)
        added = 0

        # Pre-scan for files already claimed by other groups, so we can ask
        # about all of them in a single dialog instead of one per file.
        new_paths = [p for p in paths if p not in existing_in_group]
        duplicates = {
            p for p in new_paths if self._group_containing(p, exclude=group_name) is not None
        }

        add_duplicates = True
        if duplicates:
            count = len(duplicates)
            noun = "file" if count == 1 else "files"
            add_duplicates = ask(
                self,
                "File already in another group",
                f"{count} {noun} you're adding to \"{group_name}\" "
                f"{'is' if count == 1 else 'are'} already in another group.\n\n"
                f"Add {'it' if count == 1 else 'them'} anyway?",
                yes_label="Add anyway",
                no_label="Skip",
            )

        for path in new_paths:
            if path in duplicates and not add_duplicates:
                continue

            manifest.append(path)
            existing_in_group.add(path)
            added += 1

        if added:
            self._on_manifest_changed(group_name)
        return added > 0

    def _group_containing(self, path: Path, *, exclude: str) -> str | None:
        for name, paths in self._manifests.items():
            if name != exclude and path in paths:
                return name
        return None
