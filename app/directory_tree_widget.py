"""Directory-tree group loader widget.

Walks a root directory to depth range [min, max] and collects files into
groups — one group per folder that contains matching files. Group name is
built from selected depth levels joined with '_'.

Embeddable as page 1 of AdvancedLoaderDialog's QStackedWidget.
Same public API as CsvImportWidget: validity_changed signal, is_valid(),
result_groups().
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStyle,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from app.theme import get_theme as _get_theme

# ── Pure walk logic ───────────────────────────────────────────────────────────


def _walk_tree(
    root: Path,
    min_depth: int,
    max_depth: int,
    file_exts: set[str],
) -> list[tuple[tuple[str, ...], list[Path]]]:
    """Return [(parts, [Path, ...]), ...] for all matching folders in [min_depth, max_depth].

    parts = path segments from root to the folder (1-based depth).
    """
    entries: list[tuple[tuple[str, ...], list[Path]]] = []
    try:
        children = sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))
    except (PermissionError, OSError):
        return []
    for child in children:
        _recurse(root, child, 1, min_depth, max_depth, file_exts, entries)
    return entries


def _recurse(
    root: Path,
    current: Path,
    depth: int,
    min_depth: int,
    max_depth: int,
    file_exts: set[str],
    entries: list[tuple[tuple[str, ...], list[Path]]],
) -> None:
    if min_depth <= depth <= max_depth:
        try:
            files = sorted(
                p
                for p in current.iterdir()
                if p.is_file() and not p.name.startswith(".") and p.suffix.lower() in file_exts
            )
        except (PermissionError, OSError):
            files = []
        if files:
            parts = current.relative_to(root).parts
            entries.append((parts, files))

    if depth < max_depth:
        try:
            subdirs = sorted(
                p for p in current.iterdir() if p.is_dir() and not p.name.startswith(".")
            )
        except (PermissionError, OSError):
            subdirs = []
        for subdir in subdirs:
            _recurse(root, subdir, depth + 1, min_depth, max_depth, file_exts, entries)


def _compute_groups(
    entries: list[tuple[tuple[str, ...], list[Path]]],
    selected_levels: set[int],
    default_name: str = "Group",
) -> dict[str, list[Path]]:
    """Compute {group_name: [Path, ...]} using only selected depth levels (1-based).

    Entries that produce the same name after level selection are merged.
    All-deselected (or shallower-than-selected) entries fall under default_name.
    """
    merged: dict[str, list[Path]] = {}
    for parts, files in entries:
        name_parts = [parts[i - 1] for i in sorted(selected_levels) if i <= len(parts)]
        name = "_".join(name_parts) if name_parts else default_name
        if name not in merged:
            merged[name] = []
        merged[name].extend(files)
    return {name: sorted(set(files)) for name, files in merged.items()}


# ── Local _CheckableListWidget (intentionally duplicated from csv_import_dialog) ──


class _CheckableListWidget(QListWidget):
    """QListWidget where clicking anywhere on a row toggles its checkbox."""

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        item = self.itemAt(event.pos())
        if item is not None and item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
            opt = QStyleOptionViewItem()
            opt.initFrom(self)
            opt.rect = self.visualItemRect(item)
            opt.features = QStyleOptionViewItem.ViewItemFeature.HasCheckIndicator
            check_rect = self.style().subElementRect(
                QStyle.SubElement.SE_ItemViewItemCheckIndicator, opt, self
            )
            if not check_rect.contains(event.pos()):
                new_state = (
                    Qt.CheckState.Unchecked
                    if item.checkState() == Qt.CheckState.Checked
                    else Qt.CheckState.Checked
                )
                item.setCheckState(new_state)
                return
        super().mousePressEvent(event)


# ── Widget ────────────────────────────────────────────────────────────────────


class DirectoryTreeWidget(QWidget):
    """Directory tree group loader, embeddable as a page in AdvancedLoaderDialog."""

    validity_changed = Signal(bool)

    def __init__(self, file_exts: set[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._file_exts = file_exts
        self._root: Path | None = None
        # _all_entries = every folder-with-files found anywhere; _entries = the
        # subset within the currently selected depth range.
        self._all_entries: list[tuple[tuple[str, ...], list[Path]]] = []
        self._entries: list[tuple[tuple[str, ...], list[Path]]] = []
        self._checked_levels: set[int] = set()
        self._seen_levels: set[int] = set()
        self._result: dict[str, list[Path]] = {}
        self._expanded_groups: set[str] = set()

        self._build_ui()
        self._rescan()

    def is_valid(self) -> bool:
        return bool(self._result)

    def result_groups(self) -> dict[str, list[Path]]:
        return dict(self._result)

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        t = _get_theme()

        def _sub_header(text: str) -> QLabel:
            lbl = QLabel(text)
            lbl.setStyleSheet(
                f"color: {t.muted}; font-size: 11px; font-weight: bold; padding-top: 4px;"
            )
            return lbl

        def _sep() -> QFrame:
            f = QFrame()
            f.setFrameShape(QFrame.Shape.HLine)
            f.setStyleSheet(f"color: {t.sep}; margin: 4px 0;")
            return f

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        # ── Step 2: Root directory (always visible) ───────────────────────────
        self._hdr2 = QLabel("2.  Root directory")
        self._hdr2.setStyleSheet(
            f"color: {t.accent}; font-size: 11px; font-weight: bold; padding-top: 4px;"
        )
        outer.addWidget(self._hdr2)

        root_row = QHBoxLayout()
        root_row.setSpacing(8)
        choose_btn = QPushButton("Choose folder…")
        choose_btn.setObjectName("secondaryButton")
        choose_btn.clicked.connect(self._choose_root)
        self._root_lbl = QLabel("No folder chosen.")
        self._root_lbl.setObjectName("mutedLabel")
        root_row.addWidget(choose_btn)
        root_row.addWidget(self._root_lbl, stretch=1)
        outer.addLayout(root_row)

        # ── Step 3: Depth + levels + preview (hidden until root chosen) ───────
        self._step3_container = QWidget()
        self._step3_container.setVisible(False)
        s3 = QVBoxLayout(self._step3_container)
        s3.setContentsMargins(0, 0, 0, 0)
        s3.setSpacing(8)

        s3.addWidget(_sep())

        self._hdr3 = QLabel("3.  Depth & groups")
        self._hdr3.setStyleSheet(
            f"color: {t.muted}; font-size: 11px; font-weight: bold; padding-top: 4px;"
        )
        s3.addWidget(self._hdr3)

        depth_row = QHBoxLayout()
        depth_row.setSpacing(8)
        depth_row.addWidget(QLabel("Min:"))
        self._min_spin = QSpinBox()
        self._min_spin.setRange(1, 99)
        self._min_spin.setToolTip(
            "Shallowest folder depth to collect from. Bounded by where files were "
            "actually found; pushes Max up if it would cross it."
        )
        self._min_spin.valueChanged.connect(self._on_min_changed)
        depth_row.addWidget(self._min_spin)
        depth_row.addSpacing(8)
        depth_row.addWidget(QLabel("Max:"))
        self._max_spin = QSpinBox()
        self._max_spin.setRange(1, 99)
        self._max_spin.setToolTip(
            "Deepest folder depth to collect from. Bounded by where files were "
            "actually found; pushes Min down if it would cross it."
        )
        self._max_spin.valueChanged.connect(self._on_max_changed)
        depth_row.addWidget(self._max_spin)
        depth_row.addStretch()
        s3.addLayout(depth_row)

        s3.addWidget(_sub_header("Group by levels"))

        self._levels_placeholder = QLabel("Choose a folder to see grouping levels.")
        self._levels_placeholder.setStyleSheet(f"color: {t.muted}; font-size: 12px;")
        s3.addWidget(self._levels_placeholder)

        self._levels_list = _CheckableListWidget()
        self._levels_list.setMaximumHeight(120)
        self._levels_list.setVisible(False)
        self._levels_list.itemChanged.connect(self._on_level_changed)
        s3.addWidget(self._levels_list)

        self._preview_area = QScrollArea()
        self._preview_area.setObjectName("previewArea")
        self._preview_area.setWidgetResizable(True)
        self._preview_area.setMinimumHeight(100)
        s3.addWidget(self._preview_area, stretch=1)

        outer.addWidget(self._step3_container, stretch=1)

        # Trailing stretch — pins step 2 to the top before step 3 appears.
        # Without it, the only stretch item (step 3) is hidden, so the box layout
        # sees zero total stretch and spreads the slack evenly around every item,
        # vertically centring step 2. Once step 3 is shown its preview area takes
        # the slack instead, so this collapses to 0 (see _update_step_visibility).
        self._outer_layout = outer
        self._bottom_stretch_index = outer.count()
        outer.addStretch(1)

    # ── Step visibility ───────────────────────────────────────────────────────

    def _update_step_visibility(self) -> None:
        t = _get_theme()
        root_chosen = self._root is not None
        self._step3_container.setVisible(root_chosen)

        # While step 3 is up its preview takes the slack; otherwise the trailing
        # spacer takes it so step 2 stays pinned to the top instead of being
        # vertically centred in the empty space below it.
        self._outer_layout.setStretch(self._bottom_stretch_index, 0 if root_chosen else 1)

        _active = f"color: {t.accent}; font-size: 11px; font-weight: bold; padding-top: 4px;"
        _done = f"color: {t.muted}; font-size: 11px; font-weight: bold; padding-top: 4px;"
        if root_chosen:
            self._hdr2.setStyleSheet(_done)
            self._hdr3.setStyleSheet(_active)
        else:
            self._hdr2.setStyleSheet(_active)

    # ── Depth spin handlers ───────────────────────────────────────────────────

    def _on_min_changed(self, value: int) -> None:
        # Min pushes Max up if it would cross it. Re-filtering the cached scan is
        # cheap, so no filesystem rescan is needed for a depth change.
        if value > self._max_spin.value():
            self._max_spin.blockSignals(True)
            self._max_spin.setValue(value)
            self._max_spin.blockSignals(False)
        self._apply_depth_filter()

    def _on_max_changed(self, value: int) -> None:
        # Max pushes Min down if it would cross it.
        if value < self._min_spin.value():
            self._min_spin.blockSignals(True)
            self._min_spin.setValue(value)
            self._min_spin.blockSignals(False)
        self._apply_depth_filter()

    # ── Root picker ───────────────────────────────────────────────────────────

    def _choose_root(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select root directory")
        if not folder:
            return
        self._root = Path(folder)
        self._root_lbl.setText(str(self._root))
        self._root_lbl.setToolTip(str(self._root))
        self._expanded_groups.clear()
        self._checked_levels.clear()
        self._seen_levels.clear()
        self._rescan()

    # ── Level checkbox handler ────────────────────────────────────────────────

    def _on_level_changed(self, item: QListWidgetItem) -> None:
        level: int = item.data(Qt.ItemDataRole.UserRole)
        if item.checkState() == Qt.CheckState.Checked:
            self._checked_levels.add(level)
        else:
            self._checked_levels.discard(level)
        self._recompute()

    # ── Preview link handler ──────────────────────────────────────────────────

    def _on_preview_link(self, href: str) -> None:
        if href.startswith("expand:"):
            self._expanded_groups.add(href[len("expand:") :])
        elif href.startswith("collapse:"):
            self._expanded_groups.discard(href[len("collapse:") :])
        self._rebuild_preview()

    # ── Scan + recompute + preview ────────────────────────────────────────────

    def _rescan(self) -> None:
        """Walk the whole tree once, then bound the depth spinboxes to the range of
        depths where files were actually found and select that full range."""
        if self._root is not None:
            self._all_entries = _walk_tree(self._root, 1, 99, self._file_exts)
        else:
            self._all_entries = []

        depths = sorted({len(parts) for parts, _ in self._all_entries})
        self._min_spin.blockSignals(True)
        self._max_spin.blockSignals(True)
        if depths:
            lo, hi = depths[0], depths[-1]
            self._min_spin.setRange(lo, hi)
            self._max_spin.setRange(lo, hi)
            self._min_spin.setValue(lo)
            self._max_spin.setValue(hi)
            self._min_spin.setEnabled(lo != hi)
            self._max_spin.setEnabled(lo != hi)
        else:
            # No files anywhere — nothing to choose.
            self._min_spin.setRange(1, 1)
            self._max_spin.setRange(1, 1)
            self._min_spin.setEnabled(False)
            self._max_spin.setEnabled(False)
        self._min_spin.blockSignals(False)
        self._max_spin.blockSignals(False)

        self._apply_depth_filter()

    def _apply_depth_filter(self) -> None:
        """Filter the cached scan to the selected depth range, then recompute. Cheap
        — no filesystem access — so depth tweaks are instant."""
        lo, hi = self._min_spin.value(), self._max_spin.value()
        self._entries = [e for e in self._all_entries if lo <= len(e[0]) <= hi]
        self._rebuild_levels()
        self._recompute()

    def _rebuild_levels(self) -> None:
        """Rebuild level checkboxes from current entries; preserve checked state."""
        self._levels_list.blockSignals(True)
        self._levels_list.clear()

        if not self._entries:
            self._levels_placeholder.setVisible(True)
            self._levels_list.setVisible(False)
            self._levels_list.blockSignals(False)
            return

        max_depth = max(len(parts) for parts, _ in self._entries)

        for level in range(1, max_depth + 1):
            unique_vals = sorted(
                {parts[level - 1] for parts, _ in self._entries if len(parts) >= level}
            )
            if len(unique_vals) <= 5:
                vals_str = ", ".join(unique_vals)
            else:
                vals_str = ", ".join(unique_vals[:5]) + ", …"
            label = f"Level {level}  —  {vals_str}"

            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, level)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)

            # New levels (not yet seen) default to checked
            if level not in self._seen_levels:
                self._checked_levels.add(level)
                self._seen_levels.add(level)

            item.setCheckState(
                Qt.CheckState.Checked if level in self._checked_levels else Qt.CheckState.Unchecked
            )
            self._levels_list.addItem(item)

        self._levels_placeholder.setVisible(False)
        self._levels_list.setVisible(True)
        self._levels_list.blockSignals(False)

    def _recompute(self) -> None:
        """Recompute groups from cached entries + current checked levels, then refresh preview."""
        self._result = _compute_groups(self._entries, self._checked_levels)
        self._expanded_groups &= set(self._result)
        self._update_step_visibility()
        self._rebuild_preview()
        self.validity_changed.emit(self.is_valid())

    def _rebuild_preview(self) -> None:
        t = _get_theme()
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        if self._root is None:
            placeholder = QLabel("Choose a folder to see a preview.")
            placeholder.setStyleSheet(f"color: {t.muted}; font-size: 12px;")
            layout.addWidget(placeholder)
            layout.addStretch()
            self._preview_area.setWidget(content)
            return

        if not self._result:
            placeholder = QLabel("No matching files found at the chosen depth.")
            placeholder.setStyleSheet(f"color: {t.muted}; font-size: 12px;")
            layout.addWidget(placeholder)
            layout.addStretch()
            self._preview_area.setWidget(content)
            return

        for gname in sorted(self._result):
            files = self._result[gname]
            is_expanded = gname in self._expanded_groups
            visible = files if is_expanded else files[:5]

            lines = [
                f'<b style="color:{t.text};">{gname}</b>'
                f'<span style="color:{t.muted};">  ({len(files)} files)</span>'
            ]
            for p in visible:
                lines.append(
                    f'<span style="color:{t.muted};">&nbsp;&nbsp;&nbsp;&nbsp;{p.name}</span>'
                )

            group_lbl = QLabel("<br>".join(lines))
            group_lbl.setTextFormat(Qt.TextFormat.RichText)
            group_lbl.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
            group_lbl.setWordWrap(False)
            layout.addWidget(group_lbl)

            extra = len(files) - 5
            if not is_expanded and extra > 0:
                noun = "file" if extra == 1 else "files"
                more_lbl = QLabel(
                    f'<a href="expand:{gname}" style="color:{t.sep}; text-decoration:none;">'
                    f"&nbsp;&nbsp;&nbsp;&nbsp;... and {extra} more {noun}</a>"
                )
                more_lbl.setTextFormat(Qt.TextFormat.RichText)
                more_lbl.setOpenExternalLinks(False)
                more_lbl.linkActivated.connect(self._on_preview_link)
                layout.addWidget(more_lbl)
            elif is_expanded and len(files) > 5:
                less_lbl = QLabel(
                    f'<a href="collapse:{gname}" style="color:{t.sep}; text-decoration:none;">'
                    f"&nbsp;&nbsp;&nbsp;&nbsp;show less</a>"
                )
                less_lbl.setTextFormat(Qt.TextFormat.RichText)
                less_lbl.setOpenExternalLinks(False)
                less_lbl.linkActivated.connect(self._on_preview_link)
                layout.addWidget(less_lbl)

        layout.addStretch()
        self._preview_area.setWidget(content)
