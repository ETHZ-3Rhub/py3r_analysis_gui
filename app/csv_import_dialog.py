"""Import-groups-from-CSV wizard.

Primary use: embedded as CsvImportWidget inside AdvancedLoaderDialog.
Also available standalone via the thin CsvImportDialog wrapper.
"""

from __future__ import annotations

import csv
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
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
    QScrollArea,
    QSpinBox,
    QStyle,
    QStyleOptionViewItem,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from app.group_manifest_panel import _natural_key
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


def _token_spans(
    s: str, nonalpha: bool, boundary_strings: list[str], case_sensitive: bool
) -> list[tuple[str, int, int]]:
    """Return (raw_token, start, end) for each token in s — the raw substring and
    its character span in s. Splits on non-alphanumeric runs if nonalpha, else on
    the boundary strings."""
    if nonalpha:
        return [(m.group(), m.start(), m.end()) for m in re.finditer(r"[a-zA-Z0-9]+", s)]
    seps = [b for b in boundary_strings if b]
    if not seps:
        return [(s, 0, len(s))] if s else []
    pattern = "|".join(re.escape(sep) for sep in sorted(seps, key=len, reverse=True))
    flags = 0 if case_sensitive else re.IGNORECASE
    spans: list[tuple[str, int, int]] = []
    pos = 0
    for m in re.finditer(pattern, s, flags):
        if m.start() > pos:
            spans.append((s[pos : m.start()], pos, m.start()))
        pos = m.end()
    if pos < len(s):
        spans.append((s[pos:], pos, len(s)))
    return spans


def _tokenize(
    s: str,
    nonalpha: bool,
    boundary_strings: list[str],
    case_sensitive: bool,
    tolerate_zeros: bool,
    ignore_containing: list[str],
) -> list[tuple[str, int, int]]:
    """Tokenise and normalise s, keeping each token's span in the raw string.

    Returns (normalised_value, start, end) per surviving token. Pipeline: split →
    case-fold → strip leading zeros → drop tokens containing an ignore string. The
    span always refers to the original s, so it can highlight the raw text.
    """
    ignore = [g if case_sensitive else g.lower() for g in ignore_containing if g]
    out: list[tuple[str, int, int]] = []
    for raw, start, end in _token_spans(s, nonalpha, boundary_strings, case_sensitive):
        val = raw if case_sensitive else raw.lower()
        if tolerate_zeros:
            val = _apply_zero_tolerance(val)
        if ignore and any(g in val for g in ignore):
            continue
        out.append((val, start, end))
    return out


def _preprocess_tokens(
    s: str,
    nonalpha: bool,
    boundary_strings: list[str],
    case_sensitive: bool,
    tolerate_zeros: bool,
    ignore_containing: list[str],
) -> list[str]:
    """The normalised token values of s (see _tokenize), without spans."""
    return [
        v
        for v, _, _ in _tokenize(
            s, nonalpha, boundary_strings, case_sensitive, tolerate_zeros, ignore_containing
        )
    ]


def _lcs_indices(a: list[str], b: list[str]) -> tuple[list[int], list[int]]:
    """Longest common subsequence of a and b, returned as the matched index lists
    into a and b (order kept, gaps allowed)."""
    m, n = len(a), len(b)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(m - 1, -1, -1):
        for j in range(n - 1, -1, -1):
            dp[i][j] = dp[i + 1][j + 1] + 1 if a[i] == b[j] else max(dp[i + 1][j], dp[i][j + 1])
    ai: list[int] = []
    bi: list[int] = []
    i = j = 0
    while i < m and j < n:
        if a[i] == b[j]:
            ai.append(i)
            bi.append(j)
            i += 1
            j += 1
        elif dp[i + 1][j] >= dp[i][j + 1]:
            i += 1
        else:
            j += 1
    return ai, bi


def _subarray_indices(a: list[str], b: list[str]) -> tuple[list[int], list[int]]:
    """Longest contiguous common subarray of a and b (O(n*m) DP), as the matched
    index ranges into a and b."""
    if not a or not b:
        return [], []
    n = len(b)
    best, end_a, end_b = 0, 0, 0
    prev = [0] * (n + 1)
    for i in range(len(a)):
        curr = [0] * (n + 1)
        for j in range(n):
            if a[i] == b[j]:
                curr[j + 1] = prev[j] + 1
                if curr[j + 1] > best:
                    best = curr[j + 1]
                    end_a, end_b = i + 1, j + 1
        prev = curr
    return list(range(end_a - best, end_a)), list(range(end_b - best, end_b))


def _window_indices(a: list[str], b: list[str], min_n: int) -> tuple[list[int], list[int]] | None:
    """Largest N≥min_n with a same-multiset window a[i:i+N] / b[j:j+N]; the matched
    index ranges into a and b, or None."""
    for size in range(min(len(a), len(b)), min_n - 1, -1):
        for i in range(len(a) - size + 1):
            wa = Counter(a[i : i + size])
            for j in range(len(b) - size + 1):
                if Counter(b[j : j + size]) == wa:
                    return list(range(i, i + size)), list(range(j, j + size))
    return None


def _find_match(
    handle_tokens: list[str],
    stem_tokens: list[str],
    min_tokens: int,
    match_order: bool,
    match_uninterrupted: bool,
) -> tuple[list[int], list[int]] | None:
    """Return (handle_indices, stem_indices) for the matched tokens, or None if the
    threshold isn't met.

    The manifest ID (handle) is the needle; the file stem the haystack. The two
    flags select the algorithm and apply bidirectionally — the structure must hold
    in both token sequences. The returned indices are the exact tokens the match
    rests on, in each sequence, so the caller can highlight precisely what decided
    it. min_tokens == 0 means 'all' (threshold = len(handle_tokens)).
    """
    if not handle_tokens or not stem_tokens:
        return None
    effective_min = len(handle_tokens) if min_tokens == 0 else min_tokens
    if effective_min <= 0:
        return None

    if not match_order and not match_uninterrupted:
        common = set(handle_tokens) & set(stem_tokens)
        if len(common) < effective_min:
            return None
        return (
            [i for i, t in enumerate(handle_tokens) if t in common],
            [j for j, t in enumerate(stem_tokens) if t in common],
        )
    if match_order and not match_uninterrupted:
        ai, bi = _lcs_indices(handle_tokens, stem_tokens)
        return (ai, bi) if len(ai) >= effective_min else None
    if not match_order and match_uninterrupted:
        return _window_indices(handle_tokens, stem_tokens, effective_min)
    ai, bi = _subarray_indices(handle_tokens, stem_tokens)
    return (ai, bi) if len(ai) >= effective_min else None


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
    # character spans of the matched tokens, for highlighting: id_spans into
    # id_val, name_spans into path.stem (a prefix of path.name)
    id_spans: list[tuple[int, int]]
    name_spans: list[tuple[int, int]]
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
    unmatched_csv_ids: list[str]
    n_ids_with_group: int = 0
    # group → ids, and id → its candidate file matches, for the ID-centric preview.
    id_group: dict[str, str] = field(default_factory=dict)
    id_candidates: dict[str, list[_Match]] = field(default_factory=dict)
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
        return _MatchResult([], list(files) if files else [], 0, [], False, [])

    rows_skipped_blank = 0
    candidate_matches: dict[Path, list[_Match]] = defaultdict(list)
    id_candidates: dict[str, list[_Match]] = defaultdict(list)
    id_group: dict[str, str] = {}
    ids_with_group: set[str] = set()

    stem_toks = {
        f: _tokenize(
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
        ids_with_group.add(id_val)
        id_group[id_val] = group_name
        handle_toks = _tokenize(
            id_val, nonalpha, boundary_strings, case_sensitive, tolerate_zeros, ignore_containing
        )
        handle_vals = [v for v, _, _ in handle_toks]
        for f in files:
            file_toks = stem_toks[f]
            matched = _find_match(
                handle_vals,
                [v for v, _, _ in file_toks],
                min_tokens,
                match_order,
                match_uninterrupted,
            )
            if matched is not None:
                h_idx, s_idx = matched
                match = _Match(
                    path=f,
                    id_val=id_val,
                    id_spans=[handle_toks[i][1:] for i in h_idx],
                    name_spans=[file_toks[j][1:] for j in s_idx],
                    group_name=group_name,
                )
                candidate_matches[f].append(match)
                id_candidates[id_val].append(match)

    files_not_in_csv = [f for f in files if not candidate_matches[f]]
    ids_that_matched: set[str] = {m.id_val for ms in candidate_matches.values() for m in ms}
    unmatched_csv_ids = sorted(ids_with_group - ids_that_matched)
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
        unmatched_csv_ids=unmatched_csv_ids,
        n_ids_with_group=len(ids_with_group),
        id_group=dict(id_group),
        id_candidates=dict(id_candidates),
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


def _highlight(full: str, spans: list[tuple[int, int]], accent: str) -> str:
    """Bold the given character spans in full. Spans come straight from the match,
    so exactly the tokens the decision rests on are highlighted."""
    if not spans:
        return full
    out: list[str] = []
    pos = 0
    for start, end in sorted(spans):
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
        QPushButton#adjustToggle {{
            background: transparent;
            border: none;
            color: {t.muted};
            text-align: left;
            padding: 2px 0;
            font-size: 12px;
        }}
        QPushButton#adjustToggle:hover {{ color: {t.accent}; }}
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
        self._expanded_warnings: set[str] = set()
        self._duplicate_ids: list[str] = []
        self._boundary_strings: list[str] = []
        self._ignore_strings: list[str] = []
        self._tooltip_filter = _TooltipOnDisabled(self)

        self._build_ui()
        self._update_controls_state()
        self._refresh()

    def is_valid(self) -> bool:
        # Ambiguity no longer blocks: unresolved IDs simply don't import, and the
        # banner shows the count. Import just needs a clean manifest and at least
        # one file to actually import (a clean arrow or a manual pick).
        return (
            bool(self._rows)
            and not self._duplicate_ids
            and bool(self._match_combo.currentText())
            and bool(self._selected_group_cols())
            and self._last_result is not None
            and bool(self.result_groups())
        )

    def result_groups(self) -> dict[str, list[Path]]:
        if self._last_result is None:
            return {}
        return _build_result_groups(self._last_result.clean_matches, self._conflicts)

    def _confirmed_by_id(self) -> dict[str, set[Path]]:
        """id → the file paths confirmed for it: clean 1:1 matches plus whatever
        the user picked in manual resolution. Mirrors what result_groups imports,
        and drives both the ← / ? glyphs and the banner tally."""
        confirmed: dict[str, set[Path]] = defaultdict(set)
        if self._last_result is None:
            return confirmed
        for m in self._last_result.clean_matches:
            confirmed[m.id_val].add(m.path)
        for c in self._conflicts:
            if isinstance(c.selection, frozenset):
                for idx in c.selection:
                    opt = c.options[idx]
                    confirmed[opt.id_val].add(opt.path)
        return confirmed

    def files_in_multiple_groups(self) -> list[str]:
        """Filenames that the current result assigns to more than one group. Used
        by the parent dialog to warn (not block) at import time."""
        file_to_groups: dict[Path, set[str]] = defaultdict(set)
        for gname, paths in self.result_groups().items():
            for p in paths:
                file_to_groups[p].add(gname)
        return sorted(p.name for p, gs in file_to_groups.items() if len(gs) > 1)

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        t = _get_theme()

        def _sep() -> QFrame:
            f = QFrame()
            f.setFrameShape(QFrame.Shape.HLine)
            f.setStyleSheet(f"color: {t.sep}; margin: 4px 0;")
            return f

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        # ── Section 2: Load manifest & choose ID column (always visible) ──────
        # Two columns: heading + load/choose controls on the left, the handle
        # list on the right with its title level with the heading and its box top
        # level with the Load CSV button. The handle list mirrors the filename
        # list in section 3 so the two things being matched read as a pair.
        s2_box = QHBoxLayout()
        s2_box.setSpacing(14)

        s2_left = QVBoxLayout()
        s2_left.setSpacing(4)
        self._hdr2 = QLabel("2.  Load manifest & choose ID column")
        self._hdr2.setStyleSheet(
            f"color: {t.accent}; font-size: 11px; font-weight: bold; padding-top: 4px;"
        )
        self._hdr2.setToolTip(
            "A manifest CSV maps each recording to a group. Load the CSV your "
            "protocol or unblinding sheet produced, then pick the column that "
            "holds the recording ID."
        )
        s2_left.addWidget(self._hdr2)

        csv_row = QHBoxLayout()
        csv_row.setSpacing(8)
        load_btn = QPushButton("Load CSV…")
        load_btn.setObjectName("secondaryButton")
        load_btn.clicked.connect(self._load_csv)
        self._csv_name_lbl = QLabel("No CSV loaded.")
        self._csv_name_lbl.setObjectName("mutedLabel")
        csv_row.addWidget(load_btn)
        csv_row.addWidget(self._csv_name_lbl, stretch=1)
        s2_left.addLayout(csv_row)

        # Match-column chooser (revealed on load)
        self._s2_detail = QWidget()
        self._s2_detail.setVisible(False)
        match_layout = QVBoxLayout(self._s2_detail)
        match_layout.setContentsMargins(0, 0, 0, 0)
        match_layout.setSpacing(4)
        # Label stacked above its combo (rather than beside it) so the chooser
        # sits flush under the heading instead of looking offset to one side.
        match_layout.addWidget(QLabel("ID column:"))
        self._match_combo = QComboBox()
        self._match_combo.currentIndexChanged.connect(self._on_match_col_changed)
        self._match_combo.installEventFilter(self._tooltip_filter)
        match_layout.addWidget(self._match_combo)
        self._dup_id_lbl = QLabel()
        self._dup_id_lbl.setWordWrap(True)
        self._dup_id_lbl.setStyleSheet(f"color: {t.error}; font-size: 11px;")
        self._dup_id_lbl.setVisible(False)
        match_layout.addWidget(self._dup_id_lbl)
        s2_left.addWidget(self._s2_detail)
        s2_left.addStretch()
        s2_box.addLayout(s2_left, stretch=1)

        s2_right = QVBoxLayout()
        s2_right.setSpacing(4)
        # No header of its own — the spacer keeps the ID-list box top roughly
        # level with the Load CSV button across on the left.
        s2_right.addSpacing(22)
        self._match_preview = QListWidget()
        self._match_preview.setObjectName("matchPreviewList")
        self._match_preview.setFixedHeight(92)
        self._match_preview.setVisible(False)
        self._match_preview.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._match_preview.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        s2_right.addWidget(self._match_preview)
        s2_right.addStretch()
        s2_box.addLayout(s2_right, stretch=1)

        outer.addLayout(s2_box)

        # ── Section 3: Load data (hidden until a clean ID column is chosen) ────
        self._step3_container = QWidget()
        self._step3_container.setVisible(False)
        s3 = QVBoxLayout(self._step3_container)
        s3.setContentsMargins(0, 0, 0, 0)
        s3.setSpacing(8)

        s3.addWidget(_sep())

        # Same two-column shape as section 2: heading + buttons + info on the
        # left, filename list on the right with its title level with the heading
        # and its box top level with the buttons row.
        files_row = QHBoxLayout()
        files_row.setSpacing(14)

        files_left = QVBoxLayout()
        files_left.setSpacing(4)
        self._hdr3 = QLabel("3.  Load data")
        self._hdr3.setStyleSheet(
            f"color: {t.muted}; font-size: 11px; font-weight: bold; padding-top: 4px;"
        )
        files_left.addWidget(self._hdr3)

        buttons_row = QHBoxLayout()
        buttons_row.setSpacing(8)
        self._add_folder_btn = QPushButton("Add folder…")
        self._add_folder_btn.setObjectName("secondaryButton")
        self._add_folder_btn.clicked.connect(self._add_folder)
        self._add_folder_btn.installEventFilter(self._tooltip_filter)
        self._add_files_btn = QPushButton("Add files…")
        self._add_files_btn.setObjectName("secondaryButton")
        self._add_files_btn.clicked.connect(self._add_files)
        self._add_files_btn.installEventFilter(self._tooltip_filter)
        self._clear_files_btn = QPushButton("Clear")
        self._clear_files_btn.setObjectName("secondaryButton")
        self._clear_files_btn.clicked.connect(self._clear_files)
        self._clear_files_btn.installEventFilter(self._tooltip_filter)
        buttons_row.addWidget(self._add_folder_btn)
        buttons_row.addWidget(self._add_files_btn)
        buttons_row.addWidget(self._clear_files_btn)
        buttons_row.addStretch()
        files_left.addLayout(buttons_row)

        self._file_count_lbl = QLabel("No files added.")
        self._file_count_lbl.setObjectName("mutedLabel")
        self._dup_name_lbl = QLabel()
        self._dup_name_lbl.setWordWrap(True)
        self._dup_name_lbl.setStyleSheet(f"color: {t.muted}; font-size: 11px;")
        self._dup_name_lbl.setVisible(False)
        files_left.addWidget(self._file_count_lbl)
        files_left.addWidget(self._dup_name_lbl)
        files_left.addStretch()
        files_row.addLayout(files_left, stretch=1)

        file_layout = QVBoxLayout()
        file_layout.setSpacing(4)
        # Spacer keeps the filename box top level with the buttons row on the left.
        file_layout.addSpacing(22)
        self._file_preview = QListWidget()
        self._file_preview.setObjectName("matchPreviewList")
        self._file_preview.setFixedHeight(92)
        self._file_preview.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._file_preview.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        file_layout.addWidget(self._file_preview)
        file_layout.addStretch()
        files_row.addLayout(file_layout, stretch=1)

        s3.addLayout(files_row)
        outer.addWidget(self._step3_container)

        # ── Section 4: Define groups (hidden until files added) ───────────────
        self._step4_container = QWidget()
        self._step4_container.setVisible(False)
        s4 = QVBoxLayout(self._step4_container)
        s4.setContentsMargins(0, 0, 0, 0)
        s4.setSpacing(8)

        s4.addWidget(_sep())

        self._hdr4 = QLabel("4.  Define groups by columns")
        self._hdr4.setStyleSheet(
            f"color: {t.muted}; font-size: 11px; font-weight: bold; padding-top: 4px;"
        )
        self._hdr4.setToolTip(
            "Tick the column(s) that define which group each recording belongs "
            "to. Values are joined with '_' to name the group."
        )
        s4.addWidget(self._hdr4)

        self._group_cols_list = _CheckableListWidget()
        self._group_cols_list.setObjectName("csvGroupColsList")
        self._group_cols_list.setFixedHeight(92)
        self._group_cols_list.itemChanged.connect(self._refresh)
        self._group_cols_list.installEventFilter(self._tooltip_filter)
        s4.addWidget(self._group_cols_list)

        outer.addWidget(self._step4_container)

        # ── Step 5: Match controls + preview (hidden until files added) ───────
        self._step5_container = QWidget()
        self._step5_container.setVisible(False)
        s5 = QVBoxLayout(self._step5_container)
        s5.setContentsMargins(0, 0, 0, 0)
        s5.setSpacing(8)

        s5.addWidget(_sep())

        self._hdr5 = QLabel("5.  Match IDs to filenames")
        self._hdr5.setStyleSheet(
            f"color: {t.muted}; font-size: 11px; font-weight: bold; padding-top: 4px;"
        )
        s5.addWidget(self._hdr5)

        # Status banner — an agnostic tally of how the match stands, green only
        # when everything lines up. See _update_banner.
        self._banner = QLabel()
        self._banner.setWordWrap(True)
        self._banner.setStyleSheet(f"color: {t.muted}; font-size: 12px; padding: 4px 0;")
        s5.addWidget(self._banner)

        # The one knob most datasets need, phrased as a plain question. "all" (the
        # spinbox's special minimum, value 0) requires every word of the ID to line
        # up; a number requires only that many. The "additional matching rules"
        # disclosure rides on the same row, right-aligned, for those who want more
        # control — the forgiving defaults (case-insensitive, zero-tolerant, ordered,
        # uninterrupted) mean most never open it.
        tok_row = QHBoxLayout()
        tok_row.setSpacing(8)
        tok_lbl = QLabel("How many words of each ID must match the filename?")
        tok_lbl.setToolTip(
            "A “word” is a chunk of the ID between separators (e.g. OFT_2_12 has "
            "three: OFT, 2, 12). “all” requires every word to appear in the "
            "filename; a number requires only that many, letting the filename "
            "carry extra words beyond the matched ones."
        )
        self._min_spin = QSpinBox()
        self._min_spin.setRange(0, 99)
        self._min_spin.setValue(0)
        self._min_spin.setSpecialValueText("all")
        self._min_spin.setToolTip(tok_lbl.toolTip())
        self._min_spin.valueChanged.connect(self._refresh)
        self._min_spin.installEventFilter(self._tooltip_filter)

        # Collapsed disclosure for the advanced rules — no enumerated summary, just
        # a quiet caret. Lives on the spinner's row.
        self._adjust_toggle = QPushButton("▸  Additional matching rules")
        self._adjust_toggle.setObjectName("adjustToggle")
        self._adjust_toggle.setCheckable(True)
        self._adjust_toggle.clicked.connect(self._toggle_adjust)

        tok_row.addWidget(tok_lbl)
        tok_row.addWidget(self._min_spin)
        tok_row.addStretch()
        tok_row.addWidget(self._adjust_toggle)
        s5.addLayout(tok_row)

        self._adjust_body = QWidget()
        adj = QVBoxLayout(self._adjust_body)
        adj.setContentsMargins(0, 0, 8, 6)
        adj.setSpacing(6)

        def _sub(text: str) -> QLabel:
            lbl = QLabel(text)
            lbl.setStyleSheet(f"color: {t.muted}; font-size: 11px; font-weight: bold;")
            return lbl

        # ── Tokens — how names are split, and when two tokens count as equal ──
        adj.addWidget(_sub("Tokens"))

        # Separators — label above its controls so the panel stays narrow enough
        # to sit beside the preview rather than pushing it down.
        sep_lbl = QLabel("Separators:")
        sep_lbl.setToolTip("How filenames and IDs are split into tokens before matching.")
        adj.addWidget(sep_lbl)

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
        self._nonalpha_radio.toggled.connect(self._on_separator_radio_changed)

        sep_radios = QHBoxLayout()
        sep_radios.setSpacing(8)
        sep_radios.addWidget(self._nonalpha_radio)
        sep_radios.addWidget(self._strings_radio)
        sep_radios.addStretch()
        adj.addLayout(sep_radios)

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

        # Only relevant in "strings" mode, so hidden otherwise — keeps the panel
        # a row shorter in the common case.
        self._sep_input = QWidget()
        self._sep_input.setVisible(False)
        sep_input = QHBoxLayout(self._sep_input)
        sep_input.setContentsMargins(0, 0, 0, 0)
        sep_input.setSpacing(8)
        sep_input.addWidget(self._sep_edit)
        sep_input.addWidget(self._sep_add_btn)
        sep_input.addStretch()
        adj.addWidget(self._sep_input)

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
        adj.addWidget(self._sep_chips_scroll)

        # Ignore tokens containing
        ignore_lbl = QLabel("Ignore tokens containing:")
        ignore_lbl.setToolTip(
            "Drop any token (from the ID or the filename) that contains one of "
            "these strings before matching — e.g. a shared date, batch, or cohort tag."
        )
        adj.addWidget(ignore_lbl)

        self._ignore_edit = QLineEdit()
        self._ignore_edit.setPlaceholderText("string…")
        self._ignore_edit.setMaximumWidth(120)
        self._ignore_edit.returnPressed.connect(self._on_add_ignore)
        self._ignore_edit.installEventFilter(self._tooltip_filter)
        self._ignore_add_btn = QPushButton("Add")
        self._ignore_add_btn.setObjectName("secondaryButton")
        self._ignore_add_btn.clicked.connect(self._on_add_ignore)
        self._ignore_add_btn.installEventFilter(self._tooltip_filter)
        ignore_input = QHBoxLayout()
        ignore_input.setSpacing(8)
        ignore_input.addWidget(self._ignore_edit)
        ignore_input.addWidget(self._ignore_add_btn)
        ignore_input.addStretch()
        adj.addLayout(ignore_input)

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
        adj.addWidget(self._ignore_chips_scroll)

        # Case sensitivity + tolerate leading zeros (fit on one row at this width)
        zeros_row = QHBoxLayout()
        zeros_row.setSpacing(12)
        self._case_check = QCheckBox("Case-sensitive")
        self._case_check.setToolTip(
            "When checked, 'OFT1' and 'oft1' are treated as different — the case "
            "in the CSV ID and the filename must match. Uncheck to ignore case."
        )
        self._case_check.toggled.connect(self._refresh)
        self._case_check.installEventFilter(self._tooltip_filter)
        zeros_row.addWidget(self._case_check)
        self._zeros_check = QCheckBox("Tolerate leading zeros")
        self._zeros_check.setChecked(True)
        self._zeros_check.setToolTip(
            "Treats '001', '01', and '1' as equivalent when matching — useful if "
            "your CSV IDs and filenames use inconsistent zero-padding."
        )
        self._zeros_check.toggled.connect(self._refresh)
        self._zeros_check.installEventFilter(self._tooltip_filter)
        zeros_row.addWidget(self._zeros_check)
        zeros_row.addStretch()
        adj.addLayout(zeros_row)

        # ── Match strictness — order/contiguity of the matched tokens. The token
        # count itself is the primary control above the panel (see _build_ui's
        # step-5 token row); these two only refine *how* those tokens must line up.
        adj.addWidget(_sub("Match strictness"))

        # Order / contiguity flags
        flags_row = QHBoxLayout()
        flags_row.setSpacing(12)
        self._order_check = QCheckBox("Match order")
        self._order_check.setChecked(True)
        self._order_check.setToolTip(
            "Matched tokens must appear in the same relative order in both the ID and the filename."
        )
        self._order_check.toggled.connect(self._refresh)
        self._order_check.installEventFilter(self._tooltip_filter)
        flags_row.addWidget(self._order_check)
        self._unint_check = QCheckBox("Match uninterrupted")
        self._unint_check.setChecked(True)
        self._unint_check.setToolTip(
            "Matched tokens must be contiguous — no other tokens interleaved — in "
            "both the ID and the filename."
        )
        self._unint_check.toggled.connect(self._refresh)
        self._unint_check.installEventFilter(self._tooltip_filter)
        flags_row.addWidget(self._unint_check)
        flags_row.addStretch()
        adj.addLayout(flags_row)
        adj.addStretch()

        # Body row: the adjust panel (left, narrow) beside the preview (right).
        # Collapsed, the preview takes the full width; expanded, the panel drops
        # in on the left and the preview only narrows — it never loses vertical
        # lines, and expanding adds no height to the dialog. This also makes the
        # toggle→target relationship obvious. The panel lives in its own scroll
        # area so it can never clip its controls, however short the column gets.
        self._adjust_scroll = QScrollArea()
        self._adjust_scroll.setObjectName("sepChipsArea")  # borderless/transparent
        self._adjust_scroll.setWidgetResizable(True)
        self._adjust_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._adjust_scroll.setMaximumWidth(308)
        self._adjust_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._adjust_scroll.setVisible(False)
        self._adjust_scroll.setWidget(self._adjust_body)

        body_row = QHBoxLayout()
        body_row.setSpacing(14)
        body_row.addWidget(self._adjust_scroll)

        self._preview_area = QScrollArea()
        self._preview_area.setObjectName("previewArea")
        self._preview_area.setWidgetResizable(True)
        self._preview_area.setMinimumHeight(80)
        body_row.addWidget(self._preview_area, stretch=1)
        s5.addLayout(body_row, stretch=1)

        # Manual conflict resolution — a deliberate, opt-in escape hatch, shown
        # only when conflicts exist. The default workflow is to dial the word count
        # above until the arrows line up; this is for forcing the stubborn few, and
        # whatever the user does here is on them (duplicates are warned at import).
        self._resolve_btn = QPushButton("Resolve conflicts manually…")
        self._resolve_btn.setObjectName("secondaryButton")
        self._resolve_btn.setVisible(False)
        self._resolve_btn.clicked.connect(self._open_manual_resolve)
        resolve_row = QHBoxLayout()
        resolve_row.addWidget(self._resolve_btn)
        resolve_row.addStretch()
        s5.addLayout(resolve_row)

        outer.addWidget(self._step5_container, stretch=1)

        # Trailing stretch — pins earlier steps to the top before step 5 appears.
        # Without it, the only stretch item (step 5) is hidden, so the box layout
        # sees zero total stretch and spreads the slack evenly around every item,
        # vertically centring the visible steps. Once step 5 is shown its preview
        # area takes the slack instead, so this collapses to 0 (see
        # _update_step_visibility).
        self._outer_layout = outer
        self._bottom_stretch_index = outer.count()
        outer.addStretch(1)

    # ── Step visibility ───────────────────────────────────────────────────────

    def _update_step_visibility(self) -> None:
        t = _get_theme()
        csv_loaded = bool(self._rows)
        # A clean ID column (loaded, chosen, no duplicates) gates everything below
        # it — you can't load data against an ambiguous manifest.
        id_ok = csv_loaded and bool(self._match_combo.currentText()) and not self._duplicate_ids
        files_added = bool(self._added_files)
        groups_defined = bool(self._selected_group_cols())

        self._s2_detail.setVisible(csv_loaded)
        self._match_preview.setVisible(csv_loaded)
        self._step3_container.setVisible(id_ok)
        self._step4_container.setVisible(id_ok and files_added)
        step5_visible = id_ok and files_added and groups_defined
        self._step5_container.setVisible(step5_visible)

        # While section 5 is up its preview takes the slack; otherwise the
        # trailing spacer takes it so the visible sections stay pinned to the top
        # instead of being vertically centred in the empty space below them.
        self._outer_layout.setStretch(self._bottom_stretch_index, 0 if step5_visible else 1)

        _active = f"color: {t.accent}; font-size: 11px; font-weight: bold; padding-top: 4px;"
        _done = f"color: {t.muted}; font-size: 11px; font-weight: bold; padding-top: 4px;"
        self._hdr2.setStyleSheet(_done if id_ok else _active)
        self._hdr3.setStyleSheet(_active if (id_ok and not files_added) else _done)
        self._hdr4.setStyleSheet(
            _active if (id_ok and files_added and not groups_defined) else _done
        )
        self._hdr5.setStyleSheet(_active if step5_visible else _done)

    # ── State management ──────────────────────────────────────────────────────

    def _update_controls_state(self) -> None:
        """Enable/disable controls depending on whether a CSV is loaded."""
        csv_loaded = bool(self._rows)
        no_csv_tip = "Load a CSV first."

        for widget in (
            self._match_combo,
            self._group_cols_list,
            self._min_spin,
            self._order_check,
            self._unint_check,
            self._nonalpha_radio,
            self._strings_radio,
            self._ignore_edit,
            self._ignore_add_btn,
            self._case_check,
            self._zeros_check,
        ):
            widget.setEnabled(csv_loaded)
            if not csv_loaded:
                widget.setToolTip(no_csv_tip)

        # File buttons live in section 3 (only visible once a clean ID column is
        # chosen, which implies csv_loaded), but keep them enabled whenever a CSV
        # is loaded.
        self._add_folder_btn.setEnabled(csv_loaded)
        self._add_files_btn.setEnabled(csv_loaded)
        self._clear_files_btn.setEnabled(csv_loaded)

        # Sep edit + add: enabled only when CSV loaded AND "Strings" radio is selected
        strings_active = csv_loaded and self._strings_radio.isChecked()
        self._sep_edit.setEnabled(strings_active)
        self._sep_add_btn.setEnabled(strings_active)
        if not csv_loaded:
            self._sep_edit.setToolTip(no_csv_tip)
            self._sep_add_btn.setToolTip(no_csv_tip)

    # ── Event handlers ────────────────────────────────────────────────────────

    def _on_separator_radio_changed(self, nonalpha_checked: bool) -> None:
        strings_active = not nonalpha_checked and bool(self._rows)
        self._sep_edit.setEnabled(strings_active)
        self._sep_add_btn.setEnabled(strings_active)
        self._sep_input.setVisible(not nonalpha_checked)
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
        match_col = self._match_combo.currentText()
        prev_checked = set(self._selected_group_cols())

        self._group_cols_list.blockSignals(True)
        self._group_cols_list.clear()
        for col in self._csv_cols:
            if col == match_col:
                continue
            # One checkable row per column with its sample values inline (as the
            # tree loader does) — the real column name lives in UserRole so the
            # display text can carry the summary without confusing selection.
            unique_vals = sorted(
                {
                    str(row.get(col, "")).strip()
                    for row in self._rows
                    if str(row.get(col, "")).strip()
                }
            )
            summary = _col_values_summary(unique_vals)
            label = f"{col}    —    {summary}" if summary else col
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, col)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if col in prev_checked else Qt.CheckState.Unchecked
            )
            self._group_cols_list.addItem(item)
        self._group_cols_list.blockSignals(False)

        # Detect duplicate IDs in the chosen match column. A manifest with the
        # same ID twice is ambiguous (which group?), so this blocks everything
        # below — surfaced right here next to the handle list, not down in the
        # result.
        seen: set[str] = set()
        dupes: set[str] = set()
        for row in self._rows:
            val = str(row.get(match_col, "")).strip()
            if not val:
                continue
            if val in seen:
                dupes.add(val)
            seen.add(val)
        self._duplicate_ids = sorted(dupes)

        if self._duplicate_ids:
            n = len(self._duplicate_ids)
            self._dup_id_lbl.setText(
                f"⚠ This column has {n} duplicate {'value' if n == 1 else 'values'} "
                f"— choose a unique ID column or fix the CSV."
            )
            self._dup_id_lbl.setToolTip(", ".join(self._duplicate_ids))
            self._dup_id_lbl.setVisible(True)
        else:
            self._dup_id_lbl.setVisible(False)

        self._populate_match_preview(match_col)
        self._refresh()

    def _populate_match_preview(self, col: str) -> None:
        t = _get_theme()
        self._match_preview.clear()
        if not self._rows or not col:
            return
        dupes = set(self._duplicate_ids)
        values = [v for v in (str(row.get(col, "")).strip() for row in self._rows) if v]
        for val in sorted(values, key=_natural_key):
            item = QListWidgetItem(val)
            item.setToolTip(val)
            if val in dupes:
                item.setForeground(QColor(t.error))
            self._match_preview.addItem(item)

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
        self._rebuild_file_list()
        self._refresh()

    def _clear_files(self) -> None:
        self._added_files = []
        self._rebuild_file_list()
        self._refresh()

    def _rebuild_file_list(self) -> None:
        """Repopulate the flat filename list, the count hint and the dup warning.

        Names only (full path on hover) so it reads as a direct counterpart to
        the handle list. Because the path is hidden, same-named files from
        different folders would be indistinguishable — so duplicated names are
        flagged and highlighted. Unlike duplicate IDs this only warns: two files
        named the same in per-day folders is normal, and any clash over a single
        handle is settled by conflict resolution below.
        """
        t = _get_theme()
        files = self._added_files
        n = len(files)
        if n == 0:
            self._file_count_lbl.setText("No files added.")
        else:
            n_folders = len({p.parent for p in files})
            file_noun = "file" if n == 1 else "files"
            folder_noun = "folder" if n_folders == 1 else "folders"
            self._file_count_lbl.setText(f"{n} {file_noun} from {n_folders} {folder_noun}.")

        name_counts = Counter(p.name for p in files)
        dup_names = {name for name, count in name_counts.items() if count > 1}
        if dup_names:
            d = len(dup_names)
            self._dup_name_lbl.setText(
                f"{d} filename{'' if d == 1 else 's'} "
                f"{'appears' if d == 1 else 'appear'} in more than one folder."
            )
            self._dup_name_lbl.setVisible(True)
        else:
            self._dup_name_lbl.setVisible(False)

        self._file_preview.clear()
        for p in sorted(files, key=lambda q: _natural_key(q.name)):
            item = QListWidgetItem(p.name)
            item.setToolTip(str(p))
            if p.name in dup_names:
                item.setForeground(QColor(t.text))
            self._file_preview.addItem(item)

    # ── Matching + preview ────────────────────────────────────────────────────

    def _selected_group_cols(self) -> list[str]:
        return [
            self._group_cols_list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self._group_cols_list.count())
            if self._group_cols_list.item(i).checkState() == Qt.CheckState.Checked
        ]

    def _matching_config(self) -> dict:
        """Current matching controls as kwargs for _compute_matches."""
        return dict(
            nonalpha=self._nonalpha_radio.isChecked(),
            boundary_strings=self._boundary_strings,
            case_sensitive=self._case_check.isChecked(),
            tolerate_zeros=self._zeros_check.isChecked(),
            ignore_containing=self._ignore_strings,
            min_tokens=self._min_spin.value(),
            match_order=self._order_check.isChecked(),
            match_uninterrupted=self._unint_check.isChecked(),
        )

    def _toggle_adjust(self) -> None:
        self._adjust_scroll.setVisible(self._adjust_toggle.isChecked())
        self._update_adjust_summary()

    def _update_adjust_summary(self) -> None:
        """Just flip the disclosure caret — no enumerated rule summary (noise)."""
        caret = "▾" if self._adjust_toggle.isChecked() else "▸"
        self._adjust_toggle.setText(f"{caret}  Additional matching rules")

    def _update_banner(self, result: _MatchResult | None) -> None:
        """Lead section 5 with an agnostic tally of how the match currently stands.

        The counts read the same whether things line up or not — no alarm before
        the user has had a chance to dial in the rules. It only turns green, with a
        tick, once every file and every ID is matched and no conflicts remain.
        """
        t = _get_theme()
        if result is None:
            self._banner.setText("Add data and tick a group column to see matches.")
            self._banner.setStyleSheet(f"color: {t.muted}; font-size: 12px; padding: 4px 0;")
            return

        confirmed = self._confirmed_by_id()
        assigned_files = {p for paths in confirmed.values() for p in paths}
        total_files = len(self._added_files)
        total_ids = result.n_ids_with_group
        matched_ids = sum(1 for paths in confirmed.values() if paths)
        matched_files = len(assigned_files)

        parts = [
            f"{matched_ids} / {total_ids} IDs matched",
            f"{matched_files} / {total_files} files matched",
        ]
        n_conf = sum(1 for c in result.conflicts if c.selection is None)
        if n_conf:
            parts.append(f"{n_conf} conflict{'' if n_conf == 1 else 's'}")

        perfect = total_ids > 0 and matched_ids == total_ids and matched_files == total_files
        if perfect:
            n_groups = len(self.result_groups())
            parts.append(f"{n_groups} group{'' if n_groups == 1 else 's'}")
            self._banner.setText("✓  " + "   ·   ".join(parts))
            self._banner.setStyleSheet(
                f"color: {t.success}; font-size: 12px; font-weight: bold; padding: 4px 0;"
            )
        else:
            self._banner.setText("   ·   ".join(parts))
            self._banner.setStyleSheet(f"color: {t.muted}; font-size: 12px; padding: 4px 0;")

    def _refresh(self) -> None:
        self._update_step_visibility()
        self._update_adjust_summary()
        match_col = self._match_combo.currentText()
        group_cols = self._selected_group_cols()

        if not self._rows or not match_col or not group_cols or self._duplicate_ids:
            self._conflicts = []
            self._last_result = None
            self._rebuild_preview(None)
            self._update_resolve_button()
            self._emit_validity()
            return

        result = _compute_matches(
            self._rows,
            match_col,
            group_cols,
            self._added_files,
            **self._matching_config(),
        )

        # Preserve user conflict selections across config changes
        old_selections = {c.label: c.selection for c in self._conflicts}
        self._conflicts = result.conflicts
        for c in self._conflicts:
            if c.label in old_selections:
                c.selection = old_selections[c.label]

        self._last_result = result
        self._rebuild_preview(result)
        self._update_resolve_button()
        self._emit_validity()

    def _update_resolve_button(self) -> None:
        """Show the manual-resolve button only when there are conflicts to resolve,
        labelling it with the count of still-unresolved ones."""
        n = sum(1 for c in self._conflicts if c.selection is None)
        self._resolve_btn.setVisible(bool(self._conflicts))
        total = len(self._conflicts)
        if n:
            self._resolve_btn.setText(f"Resolve {n} conflict{'' if n == 1 else 's'} manually…")
        else:
            self._resolve_btn.setText(f"Adjust {total} manual choice{'' if total == 1 else 's'}…")

    def _emit_validity(self) -> None:
        self.validity_changed.emit(self.is_valid())

    def _on_preview_link(self, href: str) -> None:
        scroll_to_group: str | None = None
        if href.startswith("expand:"):
            gname = href[len("expand:") :]
            self._expanded_groups.add(gname)
            scroll_to_group = gname
        elif href.startswith("collapse:"):
            self._expanded_groups.discard(href[len("collapse:") :])
        elif href.startswith("expand-warn:"):
            self._expanded_warnings.add(href[len("expand-warn:") :])
        elif href.startswith("collapse-warn:"):
            self._expanded_warnings.discard(href[len("collapse-warn:") :])
        self._rebuild_preview(self._last_result, scroll_to_group=scroll_to_group)

    def _rebuild_preview(
        self, result: _MatchResult | None, scroll_to_group: str | None = None
    ) -> None:
        t = _get_theme()
        self._update_banner(result)
        scroll_val = self._preview_area.verticalScrollBar().value()
        scroll_target: QWidget | None = None
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        if result is None:
            if self._duplicate_ids:
                n = len(self._duplicate_ids)
                err_lbl = QLabel(
                    "Match column has duplicate values — fix your CSV before importing:"
                )
                err_lbl.setWordWrap(True)
                err_lbl.setStyleSheet(f"color: {t.error}; font-size: 12px; font-weight: bold;")
                layout.addWidget(err_lbl)
                layout.addWidget(
                    self._build_warning_widget(
                        key="dup_ids",
                        items=self._duplicate_ids,
                        header=f"{n} duplicate {'ID' if n == 1 else 'IDs'}:",
                        t=t,
                    )
                )
            else:
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
            QTimer.singleShot(
                0, lambda: self._preview_area.verticalScrollBar().setValue(scroll_val)
            )
            return

        # The didactic ID-centric list: every manifest ID, grouped, with the
        # file(s) it matches beneath it. A confirmed 1:1 (a clean match, or a
        # manual pick) shows ← ; anything unsettled — no file, several files, or a
        # file also claimed by another ID — shows ? and carries a tooltip saying
        # why, so the fix (more / fewer words) is legible.
        confirmed = self._confirmed_by_id()
        arrow = f'<span style="color:{t.sep};">&larr;</span>'
        qmark = f'<span style="color:{t.warn}; font-weight:bold;">?</span>'
        indent = "&nbsp;&nbsp;&nbsp;&nbsp;"

        def _id_block(id_val: str, candidates: list[_Match], conf: set[Path]) -> QLabel:
            id_spans = [s for m in candidates for s in m.id_spans]
            id_html = _highlight(id_val, id_spans, t.accent)
            if not candidates:
                lines = [
                    f'{indent}<span style="color:{t.text};">{id_html}</span>'
                    f"&nbsp;&nbsp;{qmark}&nbsp;&nbsp;"
                    f'<span style="color:{t.muted};">(no matching file)</span>'
                ]
            else:
                lines = []
                for k, m in enumerate(candidates):
                    glyph = arrow if m.path in conf else qmark
                    fname_html = _highlight(m.path.name, m.name_spans, t.accent)
                    head = (
                        f'{indent}<span style="color:{t.text};">{id_html}</span>'
                        if k == 0
                        else f"{indent}&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;"
                    )
                    lines.append(
                        f"{head}&nbsp;&nbsp;{glyph}&nbsp;&nbsp;"
                        f'<span style="color:{t.muted};">{fname_html}</span>'
                    )
            lbl = QLabel("<br>".join(lines))
            lbl.setTextFormat(Qt.TextFormat.RichText)
            lbl.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
            lbl.setWordWrap(False)
            if not conf:
                n = len(candidates)
                if n == 0:
                    lbl.setToolTip(
                        "No filename matched this ID. Lower the word count above to "
                        "loosen matching — otherwise it just won't be imported."
                    )
                elif n >= 2:
                    lbl.setToolTip(
                        f"This ID matches {n} filenames, so there's no single confident "
                        "match. Raise the word count to narrow it down, or resolve it "
                        "manually below."
                    )
                else:
                    lbl.setToolTip(
                        "The matching file also matches another ID, so this pairing "
                        "isn't confident. Raise the word count to separate them, or "
                        "resolve it manually below."
                    )
            return lbl

        ids_by_group: dict[str, list[str]] = {}
        for id_val, gname in result.id_group.items():
            ids_by_group.setdefault(gname, []).append(id_val)

        for gname in sorted(ids_by_group):
            ids = sorted(ids_by_group[gname], key=_natural_key)
            n_matched = sum(1 for i in ids if confirmed.get(i))
            has_problem = n_matched < len(ids)

            header = QLabel(
                f'<b style="color:{t.text};">{gname}</b>'
                f'<span style="color:{t.muted};">  ({len(ids)} IDs · {n_matched} matched)</span>'
            )
            header.setTextFormat(Qt.TextFormat.RichText)
            header.setStyleSheet("margin-top: 4px;")
            layout.addWidget(header)
            if gname == scroll_to_group:
                scroll_target = header

            # A tidy (fully matched) group collapses to its first 5; a group with
            # any unresolved ID shows every row, so nothing needing attention hides.
            is_expanded = gname in self._expanded_groups
            visible_ids = ids if (has_problem or is_expanded) else ids[:5]
            for i in visible_ids:
                layout.addWidget(
                    _id_block(i, result.id_candidates.get(i, []), confirmed.get(i, set()))
                )

            hidden = len(ids) - len(visible_ids)
            if hidden > 0:
                more_lbl = QLabel(
                    f'<a href="expand:{gname}" style="color:{t.sep}; text-decoration:none;">'
                    f"{indent}... and {hidden} more</a>"
                )
                more_lbl.setTextFormat(Qt.TextFormat.RichText)
                more_lbl.setOpenExternalLinks(False)
                more_lbl.linkActivated.connect(self._on_preview_link)
                layout.addWidget(more_lbl)
            elif is_expanded and not has_problem and len(ids) > 5:
                less_lbl = QLabel(
                    f'<a href="collapse:{gname}" style="color:{t.sep}; text-decoration:none;">'
                    f"{indent}show less</a>"
                )
                less_lbl.setTextFormat(Qt.TextFormat.RichText)
                less_lbl.setOpenExternalLinks(False)
                less_lbl.linkActivated.connect(self._on_preview_link)
                layout.addWidget(less_lbl)

        # Orphan files (matched no ID at all) can't appear in an ID list — surface
        # them quietly so nothing vanishes without a trace.
        if result.files_not_in_csv:
            n = len(result.files_not_in_csv)
            noun = "file" if n == 1 else "files"
            orphan = QLabel(f"{n} {noun} matched no manifest ID — won't be imported.")
            orphan.setWordWrap(True)
            orphan.setStyleSheet(f"color: {t.muted}; font-size: 11px; margin-top: 6px;")
            orphan.setToolTip("\n".join(p.name for p in result.files_not_in_csv))
            layout.addWidget(orphan)

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

        def _restore_scroll() -> None:
            self._preview_area.verticalScrollBar().setValue(scroll_val)
            if scroll_target is not None:
                QTimer.singleShot(0, lambda: self._preview_area.ensureWidgetVisible(scroll_target))

        QTimer.singleShot(0, _restore_scroll)

    def _build_warning_widget(self, key: str, items: list[str], header: str, t) -> QWidget:
        """Expandable amber list — used for the blocking duplicate-ID case."""
        w = QWidget()
        vbox = QVBoxLayout(w)
        vbox.setContentsMargins(0, 4, 0, 8)
        vbox.setSpacing(3)

        lbl = QLabel(header)
        lbl.setStyleSheet(f"color: {t.warn}; font-size: 11px; font-weight: bold;")
        vbox.addWidget(lbl)

        is_expanded = key in self._expanded_warnings
        visible = items if is_expanded else items[:5]
        for item_text in visible:
            item_lbl = QLabel(f"    {item_text}")
            item_lbl.setStyleSheet(f"color: {t.panel_text}; font-size: 11px;")
            vbox.addWidget(item_lbl)

        extra = len(items) - 5
        if not is_expanded and extra > 0:
            noun = "item" if extra == 1 else "items"
            more_lbl = QLabel(
                f'<a href="expand-warn:{key}" style="color:{t.sep}; text-decoration:none;">'
                f"    ... and {extra} more {noun}</a>"
            )
            more_lbl.setTextFormat(Qt.TextFormat.RichText)
            more_lbl.setOpenExternalLinks(False)
            more_lbl.linkActivated.connect(self._on_preview_link)
            vbox.addWidget(more_lbl)
        elif is_expanded and len(items) > 5:
            less_lbl = QLabel(
                f'<a href="collapse-warn:{key}" style="color:{t.sep}; text-decoration:none;">'
                f"    show less</a>"
            )
            less_lbl.setTextFormat(Qt.TextFormat.RichText)
            less_lbl.setOpenExternalLinks(False)
            less_lbl.linkActivated.connect(self._on_preview_link)
            vbox.addWidget(less_lbl)

        return w

    def _open_manual_resolve(self) -> None:
        """Opt-in escape hatch: a modal listing every conflict with the existing
        multiselect / ignore-all widgets. Whatever the user picks here overrides
        the automatic matching; cross-group duplicates are warned at import time."""
        if not self._conflicts:
            return
        t = _get_theme()
        dlg = QDialog(self)
        dlg.setWindowTitle("Resolve conflicts manually")
        dlg.resize(560, 520)
        v = QVBoxLayout(dlg)
        v.setContentsMargins(16, 16, 16, 14)
        v.setSpacing(10)

        intro = QLabel(
            "These IDs have no single confident match. Tick the file(s) each should "
            "use, or exclude it. Manual choices override the automatic matching — "
            "the same file may be assigned to more than one group (you'll be warned "
            "at import)."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color: {t.muted}; font-size: 12px;")
        v.addWidget(intro)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setObjectName("previewArea")
        inner = QWidget()
        iv = QVBoxLayout(inner)
        iv.setContentsMargins(8, 8, 8, 8)
        iv.setSpacing(6)
        for conflict in self._conflicts:
            iv.addWidget(self._build_conflict_widget(conflict, t))
        iv.addStretch()
        scroll.setWidget(inner)
        v.addWidget(scroll, stretch=1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        done_btn = QPushButton("Done")
        done_btn.setObjectName("secondaryButton")
        done_btn.clicked.connect(dlg.accept)
        btn_row.addWidget(done_btn)
        v.addLayout(btn_row)

        base = self.window().styleSheet() if self.window() is not None else ""
        dlg.setStyleSheet(base + _csv_widget_stylesheet(t))
        dlg.exec()
        # Selections were mutated live; re-render to reflect them.
        self._rebuild_preview(self._last_result)
        self._update_resolve_button()
        self._emit_validity()

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


def confirm_duplicate_files(parent: QWidget, dup_names: list[str]) -> bool:
    """If any files are assigned to more than one group, warn (don't block) and
    return whether to proceed. No duplicates → returns True immediately."""
    if not dup_names:
        return True
    n = len(dup_names)
    shown = "\n".join(dup_names[:10]) + ("\n…" if n > 10 else "")
    resp = QMessageBox.warning(
        parent,
        "Files in multiple groups",
        f"{n} file{'' if n == 1 else 's'} are assigned to more than one group:\n\n"
        f"{shown}\n\nImport anyway?",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    return resp == QMessageBox.StandardButton.Yes


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
        if self._widget.is_valid() and confirm_duplicate_files(
            self, self._widget.files_in_multiple_groups()
        ):
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
