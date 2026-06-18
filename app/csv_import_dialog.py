"""Import-groups-from-CSV wizard.

Primary use: embedded as CsvImportWidget inside AdvancedLoaderDialog.
Also available standalone via the thin CsvImportDialog wrapper.
"""

from __future__ import annotations

import csv
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QStyle,
    QStyleOptionViewItem,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from app.theme import get_theme as _get_theme

# ── Tooltip-on-disabled shim (same pattern as app/gating.py) ─────────────────


class _TooltipOnDisabled(QObject):
    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # type: ignore[override]
        if isinstance(obj, QWidget) and not obj.isEnabled() and event.type() == QEvent.Type.ToolTip:
            tip = obj.toolTip()
            if tip:
                QToolTip.showText(event.globalPos(), tip, obj)  # type: ignore[attr-defined]
            return True
        return super().eventFilter(obj, event)


class _CheckableListWidget(QListWidget):
    """QListWidget where clicking anywhere on a row toggles its checkbox.

    Normally only clicking the indicator itself toggles the check state.
    This subclass intercepts mouse presses: if the click lands outside the
    indicator rect, it toggles manually and skips the normal press (avoiding
    double-toggle when the indicator IS clicked).
    """

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


class _ElideLeftLabel(QLabel):
    """QLabel that elides from the left so the filename end is always visible.

    Recomputes the elided display text on every resize. Full path stored as
    tooltip so the user can always see the complete path on hover.
    """

    def __init__(self, full_text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full_text = full_text
        self.setToolTip(full_text)
        self.setMinimumWidth(0)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        elided = self.fontMetrics().elidedText(
            self._full_text, Qt.TextElideMode.ElideLeft, self.width()
        )
        QLabel.setText(self, elided)

    def minimumSizeHint(self) -> QSize:
        hint = super().minimumSizeHint()
        return QSize(0, hint.height())


class _FocusActivatesRadio(QObject):
    """When an associated widget receives focus, check a radio button.

    Used so that clicking into the spinbox/text-field activates its paired radio
    automatically, without the user needing to click the radio first.
    """

    def __init__(self, radio: QRadioButton, parent: QObject) -> None:
        super().__init__(parent)
        self._radio = radio

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # type: ignore[override]
        if event.type() == QEvent.Type.FocusIn:
            self._radio.setChecked(True)
        return super().eventFilter(obj, event)


# ── Pure matching functions ──────────────────────────────────────────────────


def _apply_zero_tolerance(s: str) -> str:
    """Strip leading zeros from each contiguous digit run: '0012' → '12'."""
    return re.sub(r"(?<!\d)0+(\d)", r"\1", s)


def _lcs(a: str, b: str) -> str:
    """Longest common substring of a and b via O(n*m) two-row DP."""
    if not a or not b:
        return ""
    m, n = len(a), len(b)
    best, best_end = 0, 0
    prev = [0] * (n + 1)
    for i in range(m):
        curr = [0] * (n + 1)
        for j in range(n):
            if a[i] == b[j]:
                curr[j + 1] = prev[j] + 1
                if curr[j + 1] > best:
                    best = curr[j + 1]
                    best_end = j + 1
        prev = curr
    return b[best_end - best : best_end]


def _check_boundary_chars(s: str, start: int, length: int, boundary_chars: str) -> bool:
    before = start == 0 or s[start - 1] in boundary_chars
    after_pos = start + length
    after = after_pos == len(s) or s[after_pos] in boundary_chars
    return before and after


def _find_match(
    id_val: str,
    stem: str,
    min_chars: int | None,
    tolerate_zeros: bool,
    whole_token: bool,
    boundary_chars: str,
) -> str | None:
    """Return matched substring or None.

    min_chars=None means Full (entire id_val must match). whole_token requires
    the matched region to be bordered by non-alphanumeric chars or string ends,
    preventing 'OFT1_1' from matching 'OFT1_11'.
    """
    a = _apply_zero_tolerance(id_val) if tolerate_zeros else id_val
    b = _apply_zero_tolerance(stem) if tolerate_zeros else stem
    matched = _lcs(a, b)
    if not matched:
        return None
    effective_min = len(a) if min_chars is None else min_chars
    if len(matched) < effective_min:
        return None
    if whole_token:
        idx = b.find(matched)
        while idx != -1:
            before = idx == 0 or not b[idx - 1].isalnum()
            after_pos = idx + len(matched)
            after = after_pos == len(b) or not b[after_pos].isalnum()
            if before and after:
                return matched
            idx = b.find(matched, idx + 1)
        return None
    if boundary_chars:
        idx = b.find(matched)
        while idx != -1:
            if _check_boundary_chars(b, idx, len(matched), boundary_chars):
                return matched
            idx = b.find(matched, idx + 1)
        return None
    return matched


def _group_name_for(row: dict, group_cols: list[str]) -> str | None:
    """Join group-col values with '_'. Returns None if any value is blank."""
    parts = []
    for col in group_cols:
        val = str(row.get(col, "")).strip()
        if not val or val.lower() in ("nan", "none"):
            return None
        parts.append(val)
    return "_".join(parts) if parts else None


def _read_csv(path: Path) -> tuple[list[dict], list[str], str]:
    """Try UTF-8-BOM then Latin-1. Returns (rows, col_names, error)."""
    for encoding in ("utf-8-sig", "latin-1"):
        try:
            with path.open(newline="", encoding=encoding) as f:
                reader = csv.DictReader(f)
                rows = list(reader)
                cols = list(reader.fieldnames or [])
            return rows, cols, ""
        except UnicodeDecodeError:
            continue
        except OSError as e:
            return [], [], str(e)
    return [], [], f"Could not decode {path.name} as UTF-8 or Latin-1."


# ── Data classes ─────────────────────────────────────────────────────────────


@dataclass
class _Match:
    path: Path
    id_val: str
    matched_substr: str
    group_name: str


@dataclass
class _Conflict:
    label: str
    options: list[_Match]
    # None = unresolved; frozenset() = excluded; frozenset({0,2,...}) = include those indices
    selection: frozenset | None = None


@dataclass
class _MatchResult:
    clean_matches: list[_Match]
    files_not_in_csv: list[Path]
    rows_skipped_blank: int
    conflicts: list[_Conflict]
    groups_over_limit: bool
    error: str = ""


# ── Matching logic ────────────────────────────────────────────────────────────


def _compute_matches(
    rows: list[dict],
    match_col: str,
    group_cols: list[str],
    files: list[Path],
    min_chars: int | None,
    tolerate_zeros: bool,
    whole_token: bool,
    boundary_chars: str,
) -> _MatchResult:
    if not rows or not files:
        return _MatchResult([], list(files) if files else [], 0, [], False)

    rows_skipped_blank = 0
    candidate_matches: dict[Path, list[_Match]] = defaultdict(list)

    for row in rows:
        id_val = str(row.get(match_col, "")).strip()
        if not id_val:
            continue
        group_name = _group_name_for(row, group_cols)
        if group_name is None:
            rows_skipped_blank += 1
            continue
        for f in files:
            substr = _find_match(
                id_val, f.stem, min_chars, tolerate_zeros, whole_token, boundary_chars
            )
            if substr is not None:
                candidate_matches[f].append(
                    _Match(path=f, id_val=id_val, matched_substr=substr, group_name=group_name)
                )

    files_not_in_csv = [f for f in files if not candidate_matches[f]]
    conflicts: list[_Conflict] = []

    for f, ms in candidate_matches.items():
        if len(ms) > 1:
            conflicts.append(_Conflict(label=f"{f.name} matches {len(ms)} rows", options=ms))

    single: dict[Path, _Match] = {f: ms[0] for f, ms in candidate_matches.items() if len(ms) == 1}
    id_to_matches: dict[str, list[_Match]] = defaultdict(list)
    for m in single.values():
        id_to_matches[m.id_val].append(m)

    multi_file_ids = {iv for iv, ms in id_to_matches.items() if len(ms) > 1}
    clean_matches = [m for m in single.values() if m.id_val not in multi_file_ids]

    for id_val, ms in id_to_matches.items():
        if len(ms) > 1:
            conflicts.append(_Conflict(label=f"Row '{id_val}' matches {len(ms)} files", options=ms))

    all_group_names = {m.group_name for m in clean_matches}
    for c in conflicts:
        for m in c.options:
            all_group_names.add(m.group_name)

    return _MatchResult(
        clean_matches=clean_matches,
        files_not_in_csv=files_not_in_csv,
        rows_skipped_blank=rows_skipped_blank,
        conflicts=conflicts,
        groups_over_limit=len(all_group_names) > 12,
    )


def _build_result_groups(
    clean_matches: list[_Match],
    conflicts: list[_Conflict],
) -> dict[str, list[Path]]:
    groups: dict[str, list[Path]] = {}
    for m in clean_matches:
        groups.setdefault(m.group_name, []).append(m.path)
    for c in conflicts:
        if not isinstance(c.selection, frozenset) or len(c.selection) == 0:
            continue  # unresolved or explicitly excluded
        for idx in c.selection:
            m = c.options[idx]
            groups.setdefault(m.group_name, []).append(m.path)
    return groups


# ── Shared stylesheet helper ──────────────────────────────────────────────────


def _csv_widget_stylesheet(t) -> str:
    """CSS used by any dialog that embeds CsvImportWidget."""
    return f"""
        QLabel#mutedLabel {{ color: {t.muted}; font-size: 12px; }}
        QSpinBox {{
            background-color: {t.display};
            color: {t.text};
            border: 1px solid {t.muted};
            border-radius: 4px;
            padding: 3px 3px 3px 8px;
            min-width: 56px;
        }}
        QSpinBox:disabled {{
            color: {t.muted};
            background-color: {t.bg};
            border-color: {t.bg};
        }}
        QSpinBox::up-button, QSpinBox::down-button {{
            subcontrol-origin: border;
            width: 18px;
            background-color: {t.sep};
            border-left: 1px solid {t.muted};
        }}
        QSpinBox::up-button {{
            subcontrol-position: top right;
            border-bottom: 1px solid {t.muted};
            border-top-right-radius: 3px;
        }}
        QSpinBox::down-button {{
            subcontrol-position: bottom right;
            border-bottom-right-radius: 3px;
        }}
        QSpinBox::up-button:disabled, QSpinBox::down-button:disabled {{
            background-color: {t.bg};
            border-color: {t.bg};
        }}
        QCheckBox {{
            spacing: 6px;
            color: {t.panel_text};
        }}
        QCheckBox:disabled {{ color: {t.muted}; }}
        QCheckBox::indicator {{
            width: 12px;
            height: 12px;
            border: 1px solid {t.muted};
            border-radius: 2px;
            background: transparent;
        }}
        QCheckBox::indicator:checked {{
            background-color: {t.accent};
            border-color: {t.accent};
        }}
        QCheckBox::indicator:disabled {{
            background-color: transparent;
            border-color: {t.sep};
        }}
        QComboBox:disabled {{
            color: {t.muted};
            background-color: {t.bg};
            border-color: {t.sep};
        }}
        QLineEdit:disabled {{
            color: {t.muted};
            background-color: {t.bg};
            border-color: {t.sep};
        }}
        QListWidget#csvGroupColsList {{
            background-color: {t.display};
            color: {t.text};
            border: 1px solid {t.muted};
            border-radius: 5px;
        }}
        QListWidget#csvGroupColsList:disabled {{
            background-color: {t.bg};
            border-color: {t.sep};
            color: {t.muted};
        }}
        QListWidget#csvGroupColsList::item {{
            padding: 2px 6px;
        }}
        QListWidget#csvGroupColsList::item:selected {{
            background: transparent;
        }}
        QScrollArea#previewArea {{
            background-color: {t.display};
            border: 1px solid {t.muted};
            border-radius: 5px;
        }}
        QScrollArea#previewArea > QWidget > QWidget {{
            background-color: {t.display};
        }}
    """


# ── Embeddable widget ─────────────────────────────────────────────────────────


class CsvImportWidget(QWidget):
    """CSV group import wizard, embeddable as a page in a larger dialog.

    Emits validity_changed(bool) whenever the importable state changes.
    Call is_valid() to check current state; result_groups() to get the result.
    """

    validity_changed = Signal(bool)

    def __init__(self, file_exts: set[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._file_exts = file_exts
        self._rows: list[dict] = []
        self._csv_cols: list[str] = []
        self._csv_path: Path | None = None
        self._added_files: list[Path] = []
        self._conflicts: list[_Conflict] = []
        self._last_result: _MatchResult | None = None
        self._expanded_groups: set[str] = set()
        self._tooltip_filter = _TooltipOnDisabled(self)

        self._build_ui()
        self._update_controls_state()
        self._refresh()

    def is_valid(self) -> bool:
        return (
            bool(self._rows)
            and bool(self._match_combo.currentText())
            and bool(self._selected_group_cols())
            and all(c.selection is not None for c in self._conflicts)
            and self._last_result is not None
            and bool(
                self._last_result.clean_matches
                or any(
                    isinstance(c.selection, frozenset) and len(c.selection) > 0
                    for c in self._conflicts
                )
            )
        )

    def result_groups(self) -> dict[str, list[Path]]:
        if self._last_result is None:
            return {}
        return _build_result_groups(self._last_result.clean_matches, self._conflicts)

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

        outer.addWidget(
            _header(
                "Load manifest / protocol",
                "A manifest CSV maps each recording to a group. Load the CSV your "
                "protocol or unblinding sheet produced.",
            )
        )

        # CSV load row
        csv_row = QHBoxLayout()
        csv_row.setSpacing(8)
        load_btn = QPushButton("Load CSV…")
        load_btn.setObjectName("secondaryButton")
        load_btn.clicked.connect(self._load_csv)
        self._csv_name_lbl = QLabel("No CSV loaded.")
        self._csv_name_lbl.setObjectName("mutedLabel")
        csv_row.addWidget(load_btn)
        csv_row.addWidget(self._csv_name_lbl, stretch=1)
        outer.addLayout(csv_row)

        outer.addWidget(
            _header(
                "Match manifest / protocol to filenames",
                "Tell the wizard which column contains the ID to match against your "
                "filenames, and which column(s) define the group each recording belongs to.",
            )
        )

        # Column selection: match col + group cols, even split
        col_row = QHBoxLayout()
        col_row.setSpacing(14)

        match_layout = QVBoxLayout()
        match_layout.setSpacing(4)
        match_layout.addWidget(QLabel("Match on column:"))
        self._match_combo = QComboBox()
        self._match_combo.currentIndexChanged.connect(self._on_match_col_changed)
        self._match_combo.installEventFilter(self._tooltip_filter)
        match_layout.addWidget(self._match_combo)
        match_layout.addStretch()
        col_row.addLayout(match_layout, stretch=1)

        group_layout = QVBoxLayout()
        group_layout.setSpacing(4)
        group_layout.addWidget(QLabel("Group by columns:"))
        self._group_cols_list = _CheckableListWidget()
        self._group_cols_list.setObjectName("csvGroupColsList")
        self._group_cols_list.setFixedHeight(96)
        self._group_cols_list.itemChanged.connect(self._refresh)
        self._group_cols_list.installEventFilter(self._tooltip_filter)
        group_layout.addWidget(self._group_cols_list)
        col_row.addLayout(group_layout, stretch=1)

        outer.addLayout(col_row)

        # Line A — number of characters to match
        match_len_row = QHBoxLayout()
        match_len_row.setSpacing(8)
        match_len_lbl = QLabel("Number of characters to match:")
        match_len_lbl.setToolTip("Controls how much of the CSV ID must appear in the filename.")
        match_len_row.addWidget(match_len_lbl)

        self._all_radio = QRadioButton("all")
        self._all_radio.setChecked(True)
        self._all_radio.setToolTip(
            "The full ID from the CSV must be found in the filename. Safest — "
            "use this unless you have a reason to allow partial matches."
        )
        self._all_radio.installEventFilter(self._tooltip_filter)
        self._atleast_radio = QRadioButton("at least")
        self._atleast_radio.setToolTip(
            "The matched substring must be at least this many characters long. "
            "Lower values allow more partial matches but risk false positives."
        )
        self._atleast_radio.installEventFilter(self._tooltip_filter)
        matchlen_group = QButtonGroup(self)
        matchlen_group.addButton(self._all_radio)
        matchlen_group.addButton(self._atleast_radio)

        self._min_spin = QSpinBox()
        self._min_spin.setRange(1, 999)
        self._min_spin.setValue(3)
        self._min_spin.setEnabled(False)
        self._min_spin.valueChanged.connect(self._refresh)
        self._min_spin.installEventFilter(self._tooltip_filter)
        self._min_spin.installEventFilter(_FocusActivatesRadio(self._atleast_radio, self))

        self._all_radio.toggled.connect(self._on_matchlen_radio_changed)
        match_len_row.addWidget(self._all_radio)
        match_len_row.addWidget(self._atleast_radio)
        match_len_row.addWidget(self._min_spin)
        match_len_row.addStretch()
        outer.addLayout(match_len_row)

        # Line B — separator characters
        sep_row = QHBoxLayout()
        sep_row.setSpacing(8)
        sep_lbl = QLabel("Separator characters:")
        sep_lbl.setToolTip("Controls what counts as a word boundary around the matched text.")
        sep_row.addWidget(sep_lbl)

        self._nonalpha_radio = QRadioButton("all non-alphanumeric")
        self._nonalpha_radio.setChecked(True)
        self._nonalpha_radio.setToolTip(
            "Prevents partial-word matches — e.g. stops 'OFT1' matching inside "
            "'OFT10'. Recommended for most datasets."
        )
        self._nonalpha_radio.installEventFilter(self._tooltip_filter)
        self._specify_radio = QRadioButton("specify:")
        self._specify_radio.setToolTip(
            "Only these characters are treated as separators between ID tokens. "
            "Leave the field empty to apply no separator requirement."
        )
        self._specify_radio.installEventFilter(self._tooltip_filter)
        sep_group = QButtonGroup(self)
        sep_group.addButton(self._nonalpha_radio)
        sep_group.addButton(self._specify_radio)

        self._boundary_edit = QLineEdit()
        self._boundary_edit.setPlaceholderText("e.g. _-.")
        self._boundary_edit.setMaximumWidth(120)
        self._boundary_edit.setEnabled(False)
        self._boundary_edit.textChanged.connect(self._refresh)
        self._boundary_edit.installEventFilter(self._tooltip_filter)
        self._boundary_edit.installEventFilter(_FocusActivatesRadio(self._specify_radio, self))

        self._nonalpha_radio.toggled.connect(self._on_separator_radio_changed)
        sep_row.addWidget(self._nonalpha_radio)
        sep_row.addWidget(self._specify_radio)
        sep_row.addWidget(self._boundary_edit)
        sep_row.addStretch()
        outer.addLayout(sep_row)

        # Line C — tolerate leading zeros
        zeros_row = QHBoxLayout()
        zeros_row.setSpacing(8)
        self._zeros_check = QCheckBox("Tolerate leading zeros")
        self._zeros_check.setToolTip(
            "Treats '001', '01', and '1' as equivalent when matching — useful if "
            "your CSV IDs and filenames use inconsistent zero-padding."
        )
        self._zeros_check.toggled.connect(self._refresh)
        self._zeros_check.installEventFilter(self._tooltip_filter)
        zeros_row.addWidget(self._zeros_check)
        zeros_row.addStretch()
        outer.addLayout(zeros_row)

        outer.addWidget(_header("Load data"))

        # File add row
        files_row = QHBoxLayout()
        files_row.setSpacing(8)
        self._add_folder_btn = QPushButton("Add folder…")
        self._add_folder_btn.setObjectName("secondaryButton")
        self._add_folder_btn.clicked.connect(self._add_folder)
        self._add_folder_btn.installEventFilter(self._tooltip_filter)
        self._add_files_btn = QPushButton("Add files…")
        self._add_files_btn.setObjectName("secondaryButton")
        self._add_files_btn.clicked.connect(self._add_files)
        self._add_files_btn.installEventFilter(self._tooltip_filter)
        self._file_count_lbl = QLabel("No files added.")
        self._file_count_lbl.setObjectName("mutedLabel")
        files_row.addWidget(self._add_folder_btn)
        files_row.addWidget(self._add_files_btn)
        files_row.addWidget(self._file_count_lbl)
        files_row.addStretch()
        outer.addLayout(files_row)

        # Preview
        self._preview_area = QScrollArea()
        self._preview_area.setObjectName("previewArea")
        self._preview_area.setWidgetResizable(True)
        self._preview_area.setMinimumHeight(160)
        outer.addWidget(self._preview_area, stretch=1)

    # ── State management ──────────────────────────────────────────────────────

    def _update_controls_state(self) -> None:
        """Enable/disable controls depending on whether a CSV is loaded."""
        csv_loaded = bool(self._rows)
        no_csv_tip = "Load a CSV first."

        for widget in (
            self._match_combo,
            self._group_cols_list,
            self._all_radio,
            self._atleast_radio,
            self._nonalpha_radio,
            self._specify_radio,
            self._zeros_check,
            self._add_folder_btn,
            self._add_files_btn,
        ):
            widget.setEnabled(csv_loaded)
            if not csv_loaded:
                widget.setToolTip(no_csv_tip)

        # Spinbox: enabled only when CSV loaded AND "At least" radio is selected
        self._min_spin.setEnabled(csv_loaded and self._atleast_radio.isChecked())
        if not csv_loaded:
            self._min_spin.setToolTip(no_csv_tip)

        # Boundary edit: enabled only when CSV loaded AND "Specify" radio is selected
        self._boundary_edit.setEnabled(csv_loaded and self._specify_radio.isChecked())
        if not csv_loaded:
            self._boundary_edit.setToolTip(no_csv_tip)

    # ── Event handlers ────────────────────────────────────────────────────────

    def _on_matchlen_radio_changed(self, all_checked: bool) -> None:
        self._min_spin.setEnabled(not all_checked and bool(self._rows))
        self._refresh()

    def _on_separator_radio_changed(self, nonalpha_checked: bool) -> None:
        self._boundary_edit.setEnabled(not nonalpha_checked and bool(self._rows))
        self._refresh()

    def _load_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select metadata CSV", filter="CSV files (*.csv)"
        )
        if not path:
            return
        rows, cols, error = _read_csv(Path(path))
        if error:
            self._csv_name_lbl.setText(f"Error: {error}")
            return

        self._csv_path = Path(path)
        self._rows = rows
        self._csv_cols = cols

        self._match_combo.blockSignals(True)
        self._match_combo.clear()
        self._match_combo.addItems(cols)
        self._match_combo.blockSignals(False)

        self._csv_name_lbl.setText(self._csv_path.name)
        self._csv_name_lbl.setToolTip(str(self._csv_path))

        self._update_controls_state()
        self._on_match_col_changed()  # populates group cols list + calls _refresh

    def _on_match_col_changed(self) -> None:
        """Repopulate group cols list excluding the current match column."""
        match_col = self._match_combo.currentText()
        prev_checked = set(self._selected_group_cols())

        self._group_cols_list.blockSignals(True)
        self._group_cols_list.clear()
        for col in self._csv_cols:
            if col == match_col:
                continue
            item = QListWidgetItem(col)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if col in prev_checked else Qt.CheckState.Unchecked
            )
            self._group_cols_list.addItem(item)
        self._group_cols_list.blockSignals(False)

        self._refresh()

    def _add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select a folder")
        if not folder:
            return
        folder_path = Path(folder)
        try:
            new_paths = sorted(
                p
                for p in folder_path.iterdir()
                if p.is_file()
                and not p.name.startswith(".")
                and p.suffix.lower() in self._file_exts
            )
        except (PermissionError, OSError):
            new_paths = []
        self._add_file_paths(new_paths)

    def _add_files(self) -> None:
        ext_label = "CSV" if self._file_exts == {".csv"} else "Video"
        pattern = " ".join(f"*{ext}" for ext in sorted(self._file_exts))
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Select files", filter=f"{ext_label} files ({pattern})"
        )
        self._add_file_paths([Path(p) for p in paths])

    def _add_file_paths(self, paths: list[Path]) -> None:
        existing = set(self._added_files)
        for p in paths:
            if p not in existing:
                self._added_files.append(p)
                existing.add(p)
        n = len(self._added_files)
        noun = "file" if n == 1 else "files"
        self._file_count_lbl.setText(f"{n} {noun} added.")
        self._refresh()

    # ── Matching + preview ────────────────────────────────────────────────────

    def _selected_group_cols(self) -> list[str]:
        return [
            self._group_cols_list.item(i).text()
            for i in range(self._group_cols_list.count())
            if self._group_cols_list.item(i).checkState() == Qt.CheckState.Checked
        ]

    def _refresh(self) -> None:
        match_col = self._match_combo.currentText()
        group_cols = self._selected_group_cols()

        if not self._rows or not match_col or not group_cols:
            self._conflicts = []
            self._last_result = None
            self._rebuild_preview(None)
            self._emit_validity()
            return

        result = _compute_matches(
            self._rows,
            match_col,
            group_cols,
            self._added_files,
            min_chars=None if self._all_radio.isChecked() else self._min_spin.value(),
            tolerate_zeros=self._zeros_check.isChecked(),
            whole_token=self._nonalpha_radio.isChecked(),
            boundary_chars=self._boundary_edit.text().strip(),
        )

        # Preserve user conflict selections across config changes
        old_selections = {c.label: c.selection for c in self._conflicts}
        self._conflicts = result.conflicts
        for c in self._conflicts:
            if c.label in old_selections:
                c.selection = old_selections[c.label]

        self._last_result = result
        self._rebuild_preview(result)
        self._emit_validity()

    def _emit_validity(self) -> None:
        self.validity_changed.emit(self.is_valid())

    def _on_preview_link(self, href: str) -> None:
        if href.startswith("expand:"):
            self._expanded_groups.add(href[len("expand:") :])
        elif href.startswith("collapse:"):
            self._expanded_groups.discard(href[len("collapse:") :])
        self._rebuild_preview(self._last_result)

    def _rebuild_preview(self, result: _MatchResult | None) -> None:
        t = _get_theme()
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        if result is None:
            msg = (
                "Load a CSV, pick match and group columns, then add video files."
                if not self._rows
                else "Pick a match column and at least one group-by column."
            )
            placeholder = QLabel(msg)
            placeholder.setWordWrap(True)
            placeholder.setStyleSheet(f"color: {t.muted}; font-size: 12px;")
            layout.addWidget(placeholder)
            layout.addStretch()
            self._preview_area.setWidget(content)
            return

        # Tree: group → files (clean matches only), one section per group
        by_group: dict[str, list[_Match]] = {}
        for m in result.clean_matches:
            by_group.setdefault(m.group_name, []).append(m)

        if by_group:
            for gname in sorted(by_group):
                ms = by_group[gname]
                is_expanded = gname in self._expanded_groups
                visible = ms if is_expanded else ms[:5]

                lines = [
                    f'<b style="color:{t.text};">{gname}</b>'
                    f'<span style="color:{t.muted};">  ({len(ms)} files)</span>'
                ]
                for m in visible:
                    lines.append(
                        f'<span style="color:{t.muted};">&nbsp;&nbsp;&nbsp;&nbsp;{m.path.name}'
                        f'</span><span style="color:{t.sep};">'
                        f"&nbsp; &larr; &nbsp;{m.id_val}&nbsp;({m.matched_substr})</span>"
                    )
                group_lbl = QLabel("<br>".join(lines))
                group_lbl.setTextFormat(Qt.TextFormat.RichText)
                group_lbl.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
                group_lbl.setWordWrap(False)
                layout.addWidget(group_lbl)

                extra = len(ms) - 5
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
                elif is_expanded and len(ms) > 5:
                    less_lbl = QLabel(
                        f'<a href="collapse:{gname}" style="color:{t.sep}; text-decoration:none;">'
                        f"&nbsp;&nbsp;&nbsp;&nbsp;show less</a>"
                    )
                    less_lbl.setTextFormat(Qt.TextFormat.RichText)
                    less_lbl.setOpenExternalLinks(False)
                    less_lbl.linkActivated.connect(self._on_preview_link)
                    layout.addWidget(less_lbl)

        elif self._added_files:
            no_match = QLabel("No matches yet — check your match column and controls.")
            no_match.setStyleSheet(f"color: {t.muted}; font-size: 12px;")
            layout.addWidget(no_match)

        # Conflicts
        if self._conflicts:
            sep = QLabel("Conflicts — resolve each before importing:")
            sep.setStyleSheet(f"color: {t.warn}; font-weight: bold; margin-top: 6px;")
            layout.addWidget(sep)
            for conflict in self._conflicts:
                layout.addWidget(self._build_conflict_widget(conflict, t))

        # Warnings
        if result.files_not_in_csv:
            n = len(result.files_not_in_csv)
            noun = "file" if n == 1 else "files"
            w = QLabel(f"{n} {noun} not matched in CSV (excluded)")
            w.setStyleSheet(f"color: {t.muted}; font-size: 11px;")
            layout.addWidget(w)

        if result.rows_skipped_blank:
            n = result.rows_skipped_blank
            noun = "row" if n == 1 else "rows"
            w = QLabel(f"{n} {noun} skipped: blank group value")
            w.setStyleSheet(f"color: {t.muted}; font-size: 11px;")
            layout.addWidget(w)

        if result.groups_over_limit:
            w = QLabel("Warning: more than 12 distinct groups — check your column selection.")
            w.setStyleSheet(f"color: {t.warn}; font-size: 11px;")
            layout.addWidget(w)

        layout.addStretch()
        self._preview_area.setWidget(content)

    def _build_conflict_widget(self, conflict: _Conflict, t) -> QWidget:
        w = QWidget()
        vbox = QVBoxLayout(w)
        vbox.setContentsMargins(0, 4, 0, 8)
        vbox.setSpacing(3)

        lbl = QLabel(conflict.label + ":")
        lbl.setStyleSheet(f"color: {t.warn}; font-size: 11px; font-weight: bold;")
        vbox.addWidget(lbl)

        current = conflict.selection
        is_excluded = current == frozenset()

        file_checks: list[QCheckBox] = []
        for i, opt in enumerate(conflict.options):
            cb_row = QHBoxLayout()
            cb_row.setContentsMargins(12, 0, 0, 0)
            cb_row.setSpacing(6)
            cb = QCheckBox()
            cb.setChecked(isinstance(current, frozenset) and i in current)
            cb.setEnabled(not is_excluded)
            path_lbl = _ElideLeftLabel(str(opt.path))
            path_lbl.setStyleSheet(f"color: {t.panel_text};")
            cb_row.addWidget(cb)
            cb_row.addWidget(path_lbl, stretch=1)
            file_checks.append(cb)
            vbox.addLayout(cb_row)

        none_row = QHBoxLayout()
        none_row.setContentsMargins(12, 2, 0, 0)
        none_row.setSpacing(0)
        none_cb = QCheckBox("None — exclude all")
        none_cb.setChecked(is_excluded)
        none_row.addWidget(none_cb)
        none_row.addStretch()
        vbox.addLayout(none_row)

        def recompute() -> None:
            sel = frozenset(i for i, c in enumerate(file_checks) if c.isChecked())
            if none_cb.isChecked():
                conflict.selection = frozenset()
            elif sel:
                conflict.selection = sel
            else:
                conflict.selection = None
            self._emit_validity()

        def on_file_changed(idx: int) -> None:
            if file_checks[idx].isChecked():
                none_cb.blockSignals(True)
                none_cb.setChecked(False)
                none_cb.blockSignals(False)
            recompute()

        def on_none_changed(checked: bool) -> None:
            for fc in file_checks:
                if checked:
                    fc.blockSignals(True)
                    fc.setChecked(False)
                    fc.blockSignals(False)
                fc.setEnabled(not checked)
            recompute()

        for idx, fc in enumerate(file_checks):
            fc.stateChanged.connect(lambda _state, i=idx: on_file_changed(i))
        none_cb.toggled.connect(on_none_changed)

        return w


# ── Standalone dialog wrapper ─────────────────────────────────────────────────


class CsvImportDialog(QDialog):
    """Thin standalone wrapper around CsvImportWidget. Call result_groups() after exec()."""

    def __init__(self, file_exts: set[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Import groups from CSV")
        self.resize(660, 660)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 14)
        outer.setSpacing(10)

        self._widget = CsvImportWidget(file_exts, self)
        outer.addWidget(self._widget, stretch=1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("secondaryButton")
        cancel_btn.clicked.connect(self.reject)
        self._ok_btn = QPushButton("Import Groups")
        self._ok_btn.setObjectName("importBtn")
        self._ok_btn.setEnabled(False)
        self._ok_btn.clicked.connect(self._on_accept)
        btn_row.addWidget(cancel_btn)
        btn_row.addSpacing(8)
        btn_row.addWidget(self._ok_btn)
        outer.addLayout(btn_row)

        self._widget.validity_changed.connect(self._ok_btn.setEnabled)
        self._apply_stylesheet()

    def result_groups(self) -> dict[str, list[Path]]:
        return self._widget.result_groups()

    def _on_accept(self) -> None:
        if self._widget.is_valid():
            self.accept()

    def _apply_stylesheet(self) -> None:
        t = _get_theme()
        base = ""
        if self.parent() is not None:
            win = self.parent().window()
            if win is not None:
                base = win.styleSheet()
        self.setStyleSheet(
            base
            + _csv_widget_stylesheet(t)
            + f"""
            QPushButton#importBtn {{
                background-color: {t.accent};
                color: white;
                border: none;
                border-radius: 5px;
                padding: 6px 20px;
                min-width: 80px;
                font-weight: bold;
            }}
            QPushButton#importBtn:hover {{ background-color: {t.accent_hover}; }}
            QPushButton#importBtn:disabled {{ background-color: {t.muted}; color: {t.bg}; }}
        """
        )
