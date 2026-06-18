"""Directory-tree group loader widget.

Walks a root directory to depth range [min, max] and collects files into
groups — one group per folder that contains matching files. Group name =
path segments from root to that folder joined with '_'.

Embeddable as page 1 of AdvancedLoaderDialog's QStackedWidget.
Same public API as CsvImportWidget: validity_changed signal, is_valid(),
result_groups().
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSpinBox,
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
) -> dict[str, list[Path]]:
    """Return {group_name: [Path, ...]} for all folders in [min_depth, max_depth]
    that contain at least one matching file. Groups are ordered by path."""
    groups: dict[str, list[Path]] = {}
    try:
        children = sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))
    except (PermissionError, OSError):
        return {}
    for child in children:
        _recurse(root, child, 1, min_depth, max_depth, file_exts, groups)
    return groups


def _recurse(
    root: Path,
    current: Path,
    depth: int,
    min_depth: int,
    max_depth: int,
    file_exts: set[str],
    groups: dict[str, list[Path]],
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
            group_name = "_".join(parts)
            groups[group_name] = files

    if depth < max_depth:
        try:
            subdirs = sorted(
                p for p in current.iterdir() if p.is_dir() and not p.name.startswith(".")
            )
        except (PermissionError, OSError):
            subdirs = []
        for subdir in subdirs:
            _recurse(root, subdir, depth + 1, min_depth, max_depth, file_exts, groups)


# ── Widget ────────────────────────────────────────────────────────────────────


class DirectoryTreeWidget(QWidget):
    """Directory tree group loader, embeddable as a page in AdvancedLoaderDialog."""

    validity_changed = Signal(bool)

    def __init__(self, file_exts: set[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._file_exts = file_exts
        self._root: Path | None = None
        self._result: dict[str, list[Path]] = {}
        self._expanded_groups: set[str] = set()

        self._build_ui()
        self._refresh()

    def is_valid(self) -> bool:
        return bool(self._result)

    def result_groups(self) -> dict[str, list[Path]]:
        return dict(self._result)

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        t = _get_theme()

        def _header(text: str, tooltip: str = "") -> QLabel:
            lbl = QLabel(text)
            lbl.setStyleSheet(
                f"color: {t.muted}; font-size: 11px; font-weight: bold; padding-top: 4px;"
            )
            if tooltip:
                lbl.setToolTip(tooltip)
            return lbl

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        # ── Root directory ────────────────────────────────────────────────────
        outer.addWidget(_header("Root directory"))

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

        # ── Depth ─────────────────────────────────────────────────────────────
        outer.addWidget(_header("Depth"))

        depth_row = QHBoxLayout()
        depth_row.setSpacing(8)

        depth_row.addWidget(QLabel("Min:"))
        self._min_spin = QSpinBox()
        self._min_spin.setRange(1, 99)
        self._min_spin.setValue(1)
        self._min_spin.valueChanged.connect(self._on_min_changed)
        depth_row.addWidget(self._min_spin)

        depth_row.addSpacing(8)
        depth_row.addWidget(QLabel("Max:"))
        self._max_spin = QSpinBox()
        self._max_spin.setRange(1, 99)
        self._max_spin.setValue(1)
        self._max_spin.valueChanged.connect(self._on_max_changed)
        depth_row.addWidget(self._max_spin)

        depth_row.addSpacing(8)
        self._link_check = QCheckBox("Link")
        self._link_check.setChecked(True)
        self._link_check.setToolTip(
            "When checked, Min and Max move together. Uncheck to set them independently."
        )
        self._link_check.toggled.connect(self._on_link_toggled)
        depth_row.addWidget(self._link_check)

        depth_row.addStretch()
        outer.addLayout(depth_row)

        # ── Preview ───────────────────────────────────────────────────────────
        self._preview_area = QScrollArea()
        self._preview_area.setObjectName("previewArea")
        self._preview_area.setWidgetResizable(True)
        self._preview_area.setMinimumHeight(160)
        outer.addWidget(self._preview_area, stretch=1)

    # ── Depth spin handlers ───────────────────────────────────────────────────

    def _on_min_changed(self, value: int) -> None:
        if self._link_check.isChecked():
            self._max_spin.blockSignals(True)
            self._max_spin.setValue(value)
            self._max_spin.blockSignals(False)
        else:
            # clamp: min must not exceed max
            if value > self._max_spin.value():
                self._min_spin.blockSignals(True)
                self._min_spin.setValue(self._max_spin.value())
                self._min_spin.blockSignals(False)
                return
        self._refresh()

    def _on_max_changed(self, value: int) -> None:
        if self._link_check.isChecked():
            self._min_spin.blockSignals(True)
            self._min_spin.setValue(value)
            self._min_spin.blockSignals(False)
        else:
            # clamp: max must not be less than min
            if value < self._min_spin.value():
                self._max_spin.blockSignals(True)
                self._max_spin.setValue(self._min_spin.value())
                self._max_spin.blockSignals(False)
                return
        self._refresh()

    def _on_link_toggled(self, checked: bool) -> None:
        if checked:
            # snap both to current max on re-link
            max_val = self._max_spin.value()
            self._min_spin.blockSignals(True)
            self._min_spin.setValue(max_val)
            self._min_spin.blockSignals(False)
        self._refresh()

    # ── Root picker ───────────────────────────────────────────────────────────

    def _choose_root(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select root directory")
        if not folder:
            return
        self._root = Path(folder)
        self._root_lbl.setText(str(self._root))
        self._root_lbl.setToolTip(str(self._root))
        self._expanded_groups.clear()
        self._refresh()

    # ── Preview link handler ──────────────────────────────────────────────────

    def _on_preview_link(self, href: str) -> None:
        if href.startswith("expand:"):
            self._expanded_groups.add(href[len("expand:") :])
        elif href.startswith("collapse:"):
            self._expanded_groups.discard(href[len("collapse:") :])
        self._rebuild_preview()

    # ── Scan + preview ────────────────────────────────────────────────────────

    def _refresh(self) -> None:
        if self._root is not None:
            self._result = _walk_tree(
                self._root,
                self._min_spin.value(),
                self._max_spin.value(),
                self._file_exts,
            )
        else:
            self._result = {}
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
