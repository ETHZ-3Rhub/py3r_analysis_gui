"""Import-groups-from-CSV wizard.

Primary use: embedded as CsvImportWidget inside AdvancedLoaderDialog.
Also available standalone via the thin CsvImportDialog wrapper.
"""

from __future__ import annotations

import csv
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
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
    QTableWidget,
    QTableWidgetItem,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from app.group_manifest_panel import _FOLDER_TEXT_COLOURS, _ElideLeftDelegate
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
    """Strip leading zeros from each numeric run: '0012' → '12', 'OFT01' → 'OFT1'."""
    return re.sub(r"(?<!\d)0+(\d)", r"\1", s)


def _preprocess_tokens(
    s: str,
    nonalpha: bool,
    boundary_strings: list[str],
    case_sensitive: bool,
    tolerate_zeros: bool,
    ignore_containing: list[str],
) -> list[str]:
    """Tokenise and normalise s; drop ignored tokens. Returns the token list.

    Pipeline: case-fold → tokenise (on non-alphanumeric runs if nonalpha, else
    on the boundary strings) → strip leading zeros per token → drop any token
    that contains an ignore string.
    """
    if not case_sensitive:
        s = s.lower()
    if nonalpha:
        tokens = re.findall(r"[a-zA-Z0-9]+", s)
    else:
        seps = [b if case_sensitive else b.lower() for b in boundary_strings if b]
        if seps:
            pattern = "|".join(re.escape(sep) for sep in sorted(seps, key=len, reverse=True))
            tokens = [t for t in re.split(pattern, s) if t]
        else:
            tokens = [s] if s else []
    if tolerate_zeros:
        tokens = [_apply_zero_tolerance(t) for t in tokens]
    ignore = [g if case_sensitive else g.lower() for g in ignore_containing if g]
    if ignore:
        tokens = [t for t in tokens if not any(g in t for g in ignore)]
    return tokens


def _token_lcs_subsequence(a: list[str], b: list[str]) -> list[str]:
    """Longest common subsequence of token lists a and b (order kept, gaps allowed)."""
    m, n = len(a), len(b)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(m - 1, -1, -1):
        for j in range(n - 1, -1, -1):
            dp[i][j] = dp[i + 1][j + 1] + 1 if a[i] == b[j] else max(dp[i + 1][j], dp[i][j + 1])
    out: list[str] = []
    i = j = 0
    while i < m and j < n:
        if a[i] == b[j]:
            out.append(a[i])
            i += 1
            j += 1
        elif dp[i + 1][j] >= dp[i][j + 1]:
            i += 1
        else:
            j += 1
    return out


def _token_common_subarray(a: list[str], b: list[str]) -> list[str]:
    """Longest contiguous common subarray of token lists a and b via O(n*m) DP."""
    if not a or not b:
        return []
    n = len(b)
    best, best_end = 0, 0
    prev = [0] * (n + 1)
    for i in range(len(a)):
        curr = [0] * (n + 1)
        for j in range(n):
            if a[i] == b[j]:
                curr[j + 1] = prev[j] + 1
                if curr[j + 1] > best:
                    best = curr[j + 1]
                    best_end = i + 1
        prev = curr
    return a[best_end - best : best_end]


def _token_window_match(a: list[str], b: list[str], min_n: int) -> list[str]:
    """Largest N≥min_n with a same-multiset window a[i:i+N] / b[j:j+N]; those tokens or []."""
    for size in range(min(len(a), len(b)), min_n - 1, -1):
        for i in range(len(a) - size + 1):
            wa = Counter(a[i : i + size])
            for j in range(len(b) - size + 1):
                if Counter(b[j : j + size]) == wa:
                    return a[i : i + size]
    return []


def _find_match(
    handle_tokens: list[str],
    stem_tokens: list[str],
    min_tokens: int,
    match_order: bool,
    match_uninterrupted: bool,
) -> list[str] | None:
    """Return the matched token list, or None if the threshold isn't met.

    The manifest ID (handle) is the needle; the file stem the haystack. The two
    flags select the algorithm and apply bidirectionally — the structure must
    hold in both token sequences. min_tokens == 0 means 'all' (threshold =
    len(handle_tokens)).
    """
    if not handle_tokens or not stem_tokens:
        return None
    effective_min = len(handle_tokens) if min_tokens == 0 else min_tokens
    if effective_min <= 0:
        return None

    if not match_order and not match_uninterrupted:
        matched = sorted(set(handle_tokens) & set(stem_tokens))
    elif match_order and not match_uninterrupted:
        matched = _token_lcs_subsequence(handle_tokens, stem_tokens)
    elif not match_order and match_uninterrupted:
        matched = _token_window_match(handle_tokens, stem_tokens, effective_min)
    else:
        matched = _token_common_subarray(handle_tokens, stem_tokens)

    return matched if len(matched) >= effective_min else None


def _col_values_summary(unique_vals: list[str], max_shown: int = 4) -> str:
    if not unique_vals:
        return ""
    shown = ", ".join(unique_vals[:max_shown])
    extra = len(unique_vals) - max_shown
    return f"{shown}  ...+{extra}" if extra > 0 else shown


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
    matched_tokens: list[str]
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
    *,
    nonalpha: bool,
    boundary_strings: list[str],
    case_sensitive: bool,
    tolerate_zeros: bool,
    ignore_containing: list[str],
    min_tokens: int,
    match_order: bool,
    match_uninterrupted: bool,
) -> _MatchResult:
    if not rows or not files:
        return _MatchResult([], list(files) if files else [], 0, [], False)

    rows_skipped_blank = 0
    candidate_matches: dict[Path, list[_Match]] = defaultdict(list)

    stem_tokens = {
        f: _preprocess_tokens(
            f.stem, nonalpha, boundary_strings, case_sensitive, tolerate_zeros, ignore_containing
        )
        for f in files
    }

    for row in rows:
        id_val = str(row.get(match_col, "")).strip()
        if not id_val:
            continue
        group_name = _group_name_for(row, group_cols)
        if group_name is None:
            rows_skipped_blank += 1
            continue
        handle_tokens = _preprocess_tokens(
            id_val, nonalpha, boundary_strings, case_sensitive, tolerate_zeros, ignore_containing
        )
        for f in files:
            matched = _find_match(
                handle_tokens, stem_tokens[f], min_tokens, match_order, match_uninterrupted
            )
            if matched is not None:
                candidate_matches[f].append(
                    _Match(path=f, id_val=id_val, matched_tokens=matched, group_name=group_name)
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


def _highlight(full: str, tokens: list[str], accent: str) -> str:
    """Bold each token's first case-insensitive occurrence in full (best-effort).

    Tokens are normalised (case-folded, zero-stripped) so some may not appear
    verbatim in the raw text — those are silently left unbolded.
    """
    low = full.lower()
    spans: list[tuple[int, int]] = []
    for tok in tokens:
        if not tok:
            continue
        idx = low.find(tok.lower())
        if idx != -1:
            spans.append((idx, idx + len(tok)))
    if not spans:
        return full
    spans.sort()
    out: list[str] = []
    pos = 0
    for start, end in spans:
        if start < pos:
            continue  # overlaps an already-bolded span
        out.append(full[pos:start])
        out.append(f'<b style="color:{accent};">{full[start:end]}</b>')
        pos = end
    out.append(full[pos:])
    return "".join(out)


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
        QTableWidget#filePreviewTable {{
            background-color: {t.display};
            border: 1px solid {t.muted};
            border-radius: 4px;
            font-size: 11px;
        }}
        QTableWidget#filePreviewTable::item {{
            padding: 1px 4px;
        }}
        QListWidget#matchPreviewList {{
            background-color: {t.display};
            color: {t.muted};
            border: 1px solid {t.muted};
            border-radius: 4px;
            font-size: 11px;
        }}
        QListWidget#matchPreviewList::item {{
            padding: 1px 6px;
        }}
        QListWidget#matchPreviewList::item:selected {{
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
        QScrollArea#sepChipsArea {{
            background-color: transparent;
            border: none;
        }}
        QScrollArea#sepChipsArea > QWidget > QWidget {{
            background-color: transparent;
        }}
        QPushButton#sepChip {{
            background-color: {t.sep};
            color: {t.text};
            border: 1px solid {t.muted};
            border-radius: 10px;
            padding: 2px 10px;
            font-size: 11px;
        }}
        QPushButton#sepChip:hover {{
            background-color: {t.muted};
            color: {t.bg};
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
        self._boundary_strings: list[str] = []
        self._ignore_strings: list[str] = []
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

        def _sep() -> QFrame:
            f = QFrame()
            f.setFrameShape(QFrame.Shape.HLine)
            f.setStyleSheet(f"color: {t.sep}; margin: 4px 0;")
            return f

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

        outer.addWidget(_sep())
        outer.addWidget(_header("Load data"))

        # Load data: buttons on left, filename preview on right
        files_row = QHBoxLayout()
        files_row.setSpacing(14)

        files_left = QVBoxLayout()
        files_left.setSpacing(4)
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
        files_left.addWidget(self._add_folder_btn)
        files_left.addWidget(self._add_files_btn)
        files_left.addWidget(self._file_count_lbl)
        files_left.addStretch()
        files_row.addLayout(files_left, stretch=1)

        self._file_preview = QTableWidget(0, 2)
        self._file_preview.setObjectName("filePreviewTable")
        self._file_preview.setFixedHeight(88)
        self._file_preview.horizontalHeader().hide()
        self._file_preview.verticalHeader().hide()
        self._file_preview.setShowGrid(False)
        self._file_preview.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._file_preview.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._file_preview.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._file_preview.setItemDelegateForColumn(0, _ElideLeftDelegate(self._file_preview))
        self._file_preview.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Interactive
        )
        self._file_preview.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self._file_preview.setColumnWidth(0, 110)
        files_row.addWidget(self._file_preview, stretch=1)

        outer.addLayout(files_row)

        outer.addWidget(_sep())
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
        self._match_preview = QListWidget()
        self._match_preview.setObjectName("matchPreviewList")
        self._match_preview.setFixedHeight(76)
        self._match_preview.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._match_preview.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        match_layout.addWidget(self._match_preview)
        col_row.addLayout(match_layout, stretch=1)

        group_layout = QVBoxLayout()
        group_layout.setSpacing(4)
        group_layout.addWidget(QLabel("Group by columns:"))
        self._group_cols_list = _CheckableListWidget()
        self._group_cols_list.setObjectName("csvGroupColsList")
        self._group_cols_list.setFixedHeight(110)
        self._group_cols_list.itemChanged.connect(self._refresh)
        self._group_cols_list.installEventFilter(self._tooltip_filter)
        group_layout.addWidget(self._group_cols_list)
        col_row.addLayout(group_layout, stretch=1)

        outer.addLayout(col_row)

        # Row 1 — separators
        sep_row = QHBoxLayout()
        sep_row.setSpacing(8)
        sep_lbl = QLabel("Separators:")
        sep_lbl.setToolTip("How filenames and IDs are split into tokens before matching.")
        sep_row.addWidget(sep_lbl)

        self._nonalpha_radio = QRadioButton("all non-alphanumeric")
        self._nonalpha_radio.setChecked(True)
        self._nonalpha_radio.setToolTip(
            "Split on any run of non-alphanumeric characters. Recommended for most datasets."
        )
        self._nonalpha_radio.installEventFilter(self._tooltip_filter)
        self._strings_radio = QRadioButton("strings")
        self._strings_radio.setToolTip(
            "Split only on the separator strings you add below — useful when your "
            "tokens themselves contain punctuation."
        )
        self._strings_radio.installEventFilter(self._tooltip_filter)
        sep_group = QButtonGroup(self)
        sep_group.addButton(self._nonalpha_radio)
        sep_group.addButton(self._strings_radio)

        self._sep_edit = QLineEdit()
        self._sep_edit.setPlaceholderText("separator…")
        self._sep_edit.setMaximumWidth(120)
        self._sep_edit.setEnabled(False)
        self._sep_edit.returnPressed.connect(self._on_add_sep)
        self._sep_edit.installEventFilter(self._tooltip_filter)
        self._sep_edit.installEventFilter(_FocusActivatesRadio(self._strings_radio, self))

        self._sep_add_btn = QPushButton("Add")
        self._sep_add_btn.setObjectName("secondaryButton")
        self._sep_add_btn.setEnabled(False)
        self._sep_add_btn.clicked.connect(self._on_add_sep)
        self._sep_add_btn.installEventFilter(self._tooltip_filter)

        self._nonalpha_radio.toggled.connect(self._on_separator_radio_changed)
        sep_row.addWidget(self._nonalpha_radio)
        sep_row.addWidget(self._strings_radio)
        sep_row.addWidget(self._sep_edit)
        sep_row.addWidget(self._sep_add_btn)
        sep_row.addStretch()
        outer.addLayout(sep_row)

        # Separator chips (shown only in "strings" mode)
        self._sep_chips_scroll = QScrollArea()
        self._sep_chips_scroll.setObjectName("sepChipsArea")
        self._sep_chips_scroll.setWidgetResizable(True)
        self._sep_chips_scroll.setFixedHeight(36)
        self._sep_chips_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._sep_chips_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._sep_chips_scroll.setVisible(False)
        self._sep_chips_widget = QWidget()
        self._sep_chips_layout = QHBoxLayout(self._sep_chips_widget)
        self._sep_chips_layout.setContentsMargins(4, 2, 4, 2)
        self._sep_chips_layout.setSpacing(4)
        self._sep_chips_layout.addStretch()
        self._sep_chips_scroll.setWidget(self._sep_chips_widget)
        outer.addWidget(self._sep_chips_scroll)

        # Row 2 — how many ID tokens must be found, and order/contiguity
        idtok_row = QHBoxLayout()
        idtok_row.setSpacing(8)
        idtok_lbl = QLabel("ID tokens in filename:")
        idtok_lbl.setToolTip(
            "How many of the manifest ID's tokens must be found in the filename. "
            "The filename may carry extra tokens, which are ignored."
        )
        idtok_row.addWidget(idtok_lbl)

        self._all_radio = QRadioButton("all")
        self._all_radio.setChecked(True)
        self._all_radio.setToolTip(
            "Every token of the manifest ID must be found in the filename. Safest "
            "— use this unless you have a reason to allow partial matches."
        )
        self._all_radio.installEventFilter(self._tooltip_filter)
        self._atleast_radio = QRadioButton("at least")
        self._atleast_radio.setToolTip(
            "At least this many of the ID's tokens must be found. Lower values "
            "allow more partial matches but risk false positives."
        )
        self._atleast_radio.installEventFilter(self._tooltip_filter)
        tokcount_group = QButtonGroup(self)
        tokcount_group.addButton(self._all_radio)
        tokcount_group.addButton(self._atleast_radio)

        self._min_spin = QSpinBox()
        self._min_spin.setRange(1, 99)
        self._min_spin.setValue(1)
        self._min_spin.setEnabled(False)
        self._min_spin.valueChanged.connect(self._refresh)
        self._min_spin.installEventFilter(self._tooltip_filter)
        self._min_spin.installEventFilter(_FocusActivatesRadio(self._atleast_radio, self))
        self._all_radio.toggled.connect(self._on_tokcount_radio_changed)

        idtok_row.addWidget(self._all_radio)
        idtok_row.addWidget(self._atleast_radio)
        idtok_row.addWidget(self._min_spin)
        idtok_row.addSpacing(16)

        self._order_check = QCheckBox("Match order")
        self._order_check.setToolTip(
            "Matched tokens must appear in the same relative order in both the ID and the filename."
        )
        self._order_check.toggled.connect(self._refresh)
        self._order_check.installEventFilter(self._tooltip_filter)
        self._unint_check = QCheckBox("Match uninterrupted")
        self._unint_check.setToolTip(
            "Matched tokens must be contiguous — no other tokens interleaved — in "
            "both the ID and the filename."
        )
        self._unint_check.toggled.connect(self._refresh)
        self._unint_check.installEventFilter(self._tooltip_filter)
        idtok_row.addWidget(self._order_check)
        idtok_row.addWidget(self._unint_check)
        idtok_row.addStretch()
        outer.addLayout(idtok_row)

        # Row 3 — ignore tokens containing
        ignore_row = QHBoxLayout()
        ignore_row.setSpacing(8)
        ignore_lbl = QLabel("Ignore tokens containing:")
        ignore_lbl.setToolTip(
            "Drop any token (from the ID or the filename) that contains one of "
            "these strings before matching — e.g. a shared date, batch, or cohort tag."
        )
        ignore_row.addWidget(ignore_lbl)

        self._ignore_edit = QLineEdit()
        self._ignore_edit.setPlaceholderText("string…")
        self._ignore_edit.setMaximumWidth(120)
        self._ignore_edit.returnPressed.connect(self._on_add_ignore)
        self._ignore_edit.installEventFilter(self._tooltip_filter)
        self._ignore_add_btn = QPushButton("Add")
        self._ignore_add_btn.setObjectName("secondaryButton")
        self._ignore_add_btn.clicked.connect(self._on_add_ignore)
        self._ignore_add_btn.installEventFilter(self._tooltip_filter)
        ignore_row.addWidget(self._ignore_edit)
        ignore_row.addWidget(self._ignore_add_btn)
        ignore_row.addStretch()
        outer.addLayout(ignore_row)

        # Ignore chips (shown only when at least one string is set)
        self._ignore_chips_scroll = QScrollArea()
        self._ignore_chips_scroll.setObjectName("sepChipsArea")
        self._ignore_chips_scroll.setWidgetResizable(True)
        self._ignore_chips_scroll.setFixedHeight(36)
        self._ignore_chips_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._ignore_chips_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._ignore_chips_scroll.setVisible(False)
        self._ignore_chips_widget = QWidget()
        self._ignore_chips_layout = QHBoxLayout(self._ignore_chips_widget)
        self._ignore_chips_layout.setContentsMargins(4, 2, 4, 2)
        self._ignore_chips_layout.setSpacing(4)
        self._ignore_chips_layout.addStretch()
        self._ignore_chips_scroll.setWidget(self._ignore_chips_widget)
        outer.addWidget(self._ignore_chips_scroll)

        # Line C — case sensitivity + tolerate leading zeros
        zeros_row = QHBoxLayout()
        zeros_row.setSpacing(16)
        self._case_check = QCheckBox("Case-sensitive")
        self._case_check.setChecked(True)
        self._case_check.setToolTip(
            "When checked, 'OFT1' and 'oft1' are treated as different — the case "
            "in the CSV ID and the filename must match. Uncheck to ignore case."
        )
        self._case_check.toggled.connect(self._refresh)
        self._case_check.installEventFilter(self._tooltip_filter)
        zeros_row.addWidget(self._case_check)
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
            self._order_check,
            self._unint_check,
            self._nonalpha_radio,
            self._strings_radio,
            self._ignore_edit,
            self._ignore_add_btn,
            self._case_check,
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

        # Sep edit + add: enabled only when CSV loaded AND "Strings" radio is selected
        strings_active = csv_loaded and self._strings_radio.isChecked()
        self._sep_edit.setEnabled(strings_active)
        self._sep_add_btn.setEnabled(strings_active)
        if not csv_loaded:
            self._sep_edit.setToolTip(no_csv_tip)
            self._sep_add_btn.setToolTip(no_csv_tip)

    # ── Event handlers ────────────────────────────────────────────────────────

    def _on_tokcount_radio_changed(self, all_checked: bool) -> None:
        self._min_spin.setEnabled(not all_checked and bool(self._rows))
        self._refresh()

    def _on_separator_radio_changed(self, nonalpha_checked: bool) -> None:
        strings_active = not nonalpha_checked and bool(self._rows)
        self._sep_edit.setEnabled(strings_active)
        self._sep_add_btn.setEnabled(strings_active)
        self._sep_chips_scroll.setVisible(not nonalpha_checked)
        self._refresh()

    def _on_add_sep(self) -> None:
        sep = self._sep_edit.text()
        if not sep or sep in self._boundary_strings:
            return
        self._boundary_strings.append(sep)
        self._sep_edit.clear()
        self._rebuild_chips()
        self._refresh()

    def _remove_sep(self, sep: str) -> None:
        if sep in self._boundary_strings:
            self._boundary_strings.remove(sep)
        self._rebuild_chips()
        self._refresh()

    def _rebuild_chips(self) -> None:
        while self._sep_chips_layout.count() > 1:
            item = self._sep_chips_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for sep in self._boundary_strings:
            btn = QPushButton(f"{sep}  ×")
            btn.setObjectName("sepChip")
            btn.clicked.connect(lambda checked=False, s=sep: self._remove_sep(s))
            self._sep_chips_layout.insertWidget(self._sep_chips_layout.count() - 1, btn)

    def _on_add_ignore(self) -> None:
        s = self._ignore_edit.text()
        if not s or s in self._ignore_strings:
            return
        self._ignore_strings.append(s)
        self._ignore_edit.clear()
        self._rebuild_ignore_chips()
        self._refresh()

    def _remove_ignore(self, s: str) -> None:
        if s in self._ignore_strings:
            self._ignore_strings.remove(s)
        self._rebuild_ignore_chips()
        self._refresh()

    def _rebuild_ignore_chips(self) -> None:
        while self._ignore_chips_layout.count() > 1:
            item = self._ignore_chips_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for s in self._ignore_strings:
            btn = QPushButton(f"{s}  ×")
            btn.setObjectName("sepChip")
            btn.clicked.connect(lambda checked=False, v=s: self._remove_ignore(v))
            self._ignore_chips_layout.insertWidget(self._ignore_chips_layout.count() - 1, btn)
        self._ignore_chips_scroll.setVisible(bool(self._ignore_strings))

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
        """Repopulate group cols list and match preview for the current match column."""
        t = _get_theme()
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
            unique_vals = sorted(
                {
                    str(row.get(col, "")).strip()
                    for row in self._rows
                    if str(row.get(col, "")).strip()
                }
            )
            summary = _col_values_summary(unique_vals)
            if summary:
                sub = QListWidgetItem(f"    {summary}")
                sub.setFlags(Qt.ItemFlag.ItemIsEnabled)
                sub.setForeground(QColor(t.muted))
                font = sub.font()
                font.setPointSize(max(font.pointSize() - 1, 8))
                sub.setFont(font)
                self._group_cols_list.addItem(sub)
        self._group_cols_list.blockSignals(False)

        self._populate_match_preview(match_col)
        self._refresh()

    def _populate_match_preview(self, col: str) -> None:
        self._match_preview.clear()
        if not self._rows or not col:
            return
        for row in self._rows:
            val = str(row.get(col, "")).strip()
            if val:
                self._match_preview.addItem(val)

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
        folder_colour: dict[Path, QBrush] = {}
        for p in self._added_files:
            if p.parent not in folder_colour:
                idx = len(folder_colour) % len(_FOLDER_TEXT_COLOURS)
                folder_colour[p.parent] = QBrush(QColor(_FOLDER_TEXT_COLOURS[idx]))
        self._file_preview.setRowCount(len(self._added_files))
        for row, p in enumerate(self._added_files):
            colour = folder_colour[p.parent]
            dir_item = QTableWidgetItem(str(p.parent) + "/")
            dir_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            dir_item.setForeground(colour)
            dir_item.setToolTip(str(p.parent))
            name_item = QTableWidgetItem(p.name)
            name_item.setForeground(colour)
            name_item.setToolTip(str(p))
            self._file_preview.setItem(row, 0, dir_item)
            self._file_preview.setItem(row, 1, name_item)
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
            nonalpha=self._nonalpha_radio.isChecked(),
            boundary_strings=self._boundary_strings,
            case_sensitive=self._case_check.isChecked(),
            tolerate_zeros=self._zeros_check.isChecked(),
            ignore_containing=self._ignore_strings,
            min_tokens=0 if self._all_radio.isChecked() else self._min_spin.value(),
            match_order=self._order_check.isChecked(),
            match_uninterrupted=self._unint_check.isChecked(),
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
                    fname_html = _highlight(m.path.name, m.matched_tokens, t.accent)
                    id_html = _highlight(m.id_val, m.matched_tokens, t.accent)
                    lines.append(
                        f'<span style="color:{t.muted};">&nbsp;&nbsp;&nbsp;&nbsp;{fname_html}'
                        f'</span><span style="color:{t.sep};">'
                        f"&nbsp; &larr; &nbsp;{id_html}</span>"
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
