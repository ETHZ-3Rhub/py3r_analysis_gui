"""Import-groups-from-CSV wizard.

Embedded as CsvImportWidget inside AdvancedLoaderDialog.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
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
    QVBoxLayout,
    QWidget,
)

from app.csv_matching import (
    Conflict,
    MatchResult,
    build_result_groups,
    col_values_summary,
    compute_matches,
    highlight,
    read_csv,
)
from app.gating import TooltipOnDisabled
from app.text_utils import natural_key
from app.theme import get_theme as _get_theme
from app.widgets import CheckableListWidget


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
        /* Descendant (not just child) so the viewport AND any nested wrapper
           widgets (e.g. the results grid) get the dark backing rather than falling
           through to a gold `QWidget` rule from the main window. QLabels are a
           different class, so they stay transparent over this. */
        QScrollArea#previewArea QWidget {{
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
        self._conflicts: list[Conflict] = []
        self._last_result: MatchResult | None = None
        self._expanded_groups: set[str] = set()
        self._expanded_id_files: set[str] = set()
        self._expanded_warnings: set[str] = set()
        self._duplicate_ids: list[str] = []
        self._boundary_strings: list[str] = []
        self._ignore_strings: list[str] = []
        self._tooltip_filter = TooltipOnDisabled(self)

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
        # Only clean 1:1 matches import — ambiguous/unmatched IDs are left for the
        # user to fix in their CSV (they're flagged in the preview and warned at
        # import). build_result_groups with no conflicts == clean matches only.
        if self._last_result is None:
            return {}
        return build_result_groups(self._last_result.clean_matches, [])

    def unmatched_summary(self) -> tuple[int, int]:
        """(files that won't import, manifest IDs without a clean 1:1 match) — for
        the heads-up warning shown when the user clicks import."""
        if self._last_result is None:
            return (0, 0)
        imported = {m.path for m in self._last_result.clean_matches}
        n_files = len(self._added_files) - len(imported)
        n_ids = self._last_result.n_ids_with_group - len(self._last_result.clean_matches)
        return (n_files, n_ids)

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
        self._dup_name_lbl.setStyleSheet(f"color: {t.warn}; font-size: 11px;")
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

        self._group_cols_list = CheckableListWidget()
        self._group_cols_list.setObjectName("csvGroupColsList")
        self._group_cols_list.setFixedHeight(92)
        # No selection/focus highlight — rows are toggled by their checkbox, and
        # selecting the text gave a jarring (sometimes black) highlight.
        self._group_cols_list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._group_cols_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
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

        # Body row: the preview (left, stretches) beside the adjust panel (right,
        # narrow). The panel sits on the right, directly under its top-right toggle,
        # so expanding it leaves the left-aligned preview text exactly where it was —
        # only the preview narrows. The panel lives in its own scroll area so it can
        # never clip its controls, however short the column gets.
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
        self._preview_area = QScrollArea()
        self._preview_area.setObjectName("previewArea")
        self._preview_area.setWidgetResizable(True)
        self._preview_area.setMinimumHeight(80)
        body_row.addWidget(self._preview_area, stretch=1)
        body_row.addWidget(self._adjust_scroll)
        s5.addLayout(body_row, stretch=1)

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
        """Enable/disable controls depending on whether a CSV is loaded. No
        "load a CSV first" tooltips — these controls are hidden until a CSV is
        loaded anyway, and setting them left a stale tooltip clobbering the real
        one once it was."""
        csv_loaded = bool(self._rows)

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
        rows, cols, error = read_csv(Path(path))
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
            summary = col_values_summary(unique_vals)
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
        for val in sorted(values, key=natural_key):
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
        for p in sorted(files, key=lambda q: natural_key(q.name)):
            item = QListWidgetItem(p.name)
            item.setToolTip(str(p))
            if p.name in dup_names:
                item.setForeground(QColor(t.warn))
            self._file_preview.addItem(item)

    # ── Matching + preview ────────────────────────────────────────────────────

    def _selected_group_cols(self) -> list[str]:
        return [
            self._group_cols_list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self._group_cols_list.count())
            if self._group_cols_list.item(i).checkState() == Qt.CheckState.Checked
        ]

    def _matching_config(self) -> dict:
        """Current matching controls as kwargs for compute_matches."""
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

    def _update_banner(self, result: MatchResult | None) -> None:
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

        # Only clean 1:1 matches count as "matched" — they're all that imports.
        clean = result.clean_matches
        matched_ids = len(clean)
        matched_files = len({m.path for m in clean})
        total_ids = result.n_ids_with_group
        total_files = len(self._added_files)

        parts = [
            f"{matched_ids} / {total_ids} IDs matched",
            f"{matched_files} / {total_files} files matched",
        ]

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
            self._emit_validity()
            return

        result = compute_matches(
            self._rows,
            match_col,
            group_cols,
            self._added_files,
            **self._matching_config(),
        )
        self._conflicts = result.conflicts
        self._last_result = result
        self._rebuild_preview(result)
        self._emit_validity()

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
        elif href.startswith("expand-files:"):
            self._expanded_id_files.add(href[len("expand-files:") :])
        elif href.startswith("collapse-files:"):
            self._expanded_id_files.discard(href[len("collapse-files:") :])
        elif href.startswith("expand-warn:"):
            self._expanded_warnings.add(href[len("expand-warn:") :])
        elif href.startswith("collapse-warn:"):
            self._expanded_warnings.discard(href[len("collapse-warn:") :])
        self._rebuild_preview(self._last_result, scroll_to_group=scroll_to_group)

    def _rebuild_preview(
        self, result: MatchResult | None, scroll_to_group: str | None = None
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

        # The didactic ID-centric list, laid out in a grid so the arrow column and
        # the filenames line up across every row (a proportional font can't be
        # aligned with padding). Columns: 0 = status (one orange ? when the ID isn't
        # a clean 1:1, blank otherwise), 1 = ID, 2 = → , 3 = filename. Only clean
        # matches import; the ? rows tell the user to change the words or fix the CSV.
        clean_ids = {m.id_val for m in result.clean_matches}
        rarrow = f'<span style="color:{t.sep};">&rarr;</span>'
        qmark = f'<span style="color:{t.warn}; font-weight:bold;">?</span>'

        grid_widget = QWidget()
        grid = QGridLayout(grid_widget)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(3)
        grid.setColumnStretch(3, 1)
        top = Qt.AlignmentFlag.AlignTop

        def _rich(html: str, *, color: str | None = None, tip: str | None = None) -> QLabel:
            lbl = QLabel(html)
            lbl.setTextFormat(Qt.TextFormat.RichText)
            lbl.setWordWrap(False)
            lbl.setAlignment(top | Qt.AlignmentFlag.AlignLeft)
            if color:
                lbl.setStyleSheet(f"color: {color};")
            if tip:
                lbl.setToolTip(tip)
            return lbl

        def _link(href: str, text: str) -> QLabel:
            link = _rich(
                f'<a href="{href}" style="color:{t.sep}; text-decoration:none;">{text}</a>'
            )
            link.setOpenExternalLinks(False)
            link.linkActivated.connect(self._on_preview_link)
            return link

        def _add_id_rows(row: int, id_val: str) -> int:
            candidates = sorted(
                result.id_candidates.get(id_val, []), key=lambda m: natural_key(m.path.name)
            )
            id_spans = [s for m in candidates for s in m.id_spans]
            id_html = highlight(id_val, id_spans, t.accent)

            # One ? to the left of the whole ID when it isn't a clean 1:1 — far
            # calmer than a ? per file, and the eye lands straight on the problems.
            if id_val not in clean_ids:
                if not candidates:
                    tip = (
                        "No filename matched this ID. Lower the word count to loosen "
                        "matching, or fix the manifest — otherwise it won't be imported."
                    )
                elif len(candidates) >= 2:
                    tip = (
                        f"This ID matches {len(candidates)} filenames, so there's no "
                        "single confident match — it won't be imported. Raise the word "
                        "count to narrow it down, or fix the manifest."
                    )
                else:
                    tip = (
                        "This file also matches another ID, so the pairing isn't "
                        "confident — it won't be imported. Raise the word count to "
                        "separate them, or fix the manifest."
                    )
                grid.addWidget(_rich(qmark, tip=tip), row, 0, top)

            grid.addWidget(_rich(id_html, color=t.text), row, 1, top)

            if not candidates:
                grid.addWidget(_rich("(no matching file)", color=t.muted), row, 3, top)
                return row + 1

            # Cap the file list per ID too — an ambiguous ID can match many files,
            # and 3 is plenty to see the pattern. Expands in place, independent of
            # the per-group ID cap, via its own expand-files: link.
            files_expanded = id_val in self._expanded_id_files
            visible = candidates if files_expanded else candidates[:3]
            for k, m in enumerate(visible):
                r = row + k
                grid.addWidget(_rich(rarrow), r, 2, top)
                fname_html = highlight(m.path.name, m.name_spans, t.accent)
                grid.addWidget(_rich(fname_html, color=t.muted), r, 3, top)
            row += len(visible)

            hidden = len(candidates) - len(visible)
            if hidden > 0:
                more = _link(f"expand-files:{id_val}", f"... and {hidden} more")
                grid.addWidget(more, row, 3, top)
                row += 1
            elif files_expanded and len(candidates) > 3:
                grid.addWidget(_link(f"collapse-files:{id_val}", "show less"), row, 3, top)
                row += 1
            return row

        def _span_link(row: int, href: str, text: str) -> int:
            grid.addWidget(_link(href, text), row, 1, 1, 3)
            return row + 1

        ids_by_group: dict[str, list[str]] = {}
        for id_val, gname in result.id_group.items():
            ids_by_group.setdefault(gname, []).append(id_val)

        grid_row = 0
        for gname in sorted(ids_by_group):
            ids = sorted(ids_by_group[gname], key=natural_key)
            n_matched = sum(1 for i in ids if i in clean_ids)

            header = _rich(
                f'<b style="color:{t.text};">{gname}</b>'
                f'<span style="color:{t.muted};">  ({len(ids)} IDs · {n_matched} matched)</span>'
            )
            header.setStyleSheet("margin-top: 4px;")
            grid.addWidget(header, grid_row, 0, 1, 4)
            if gname == scroll_to_group:
                scroll_target = header
            grid_row += 1

            # Always collapse to the first 5 (ambiguous groups get long) with an
            # expand-in-place link, the same behaviour as everywhere else.
            is_expanded = gname in self._expanded_groups
            visible_ids = ids if is_expanded else ids[:5]
            for i in visible_ids:
                grid_row = _add_id_rows(grid_row, i)

            hidden = len(ids) - len(visible_ids)
            if hidden > 0:
                grid_row = _span_link(grid_row, f"expand:{gname}", f"... and {hidden} more")
            elif is_expanded and len(ids) > 5:
                grid_row = _span_link(grid_row, f"collapse:{gname}", "show less")

        layout.addWidget(grid_widget)

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


def confirm_partial_import(parent: QWidget, n_files: int, n_ids: int) -> bool:
    """If some files/IDs didn't produce a clean match, warn that they won't be
    imported and ask whether to go ahead. Nothing skipped → returns True."""
    if n_files == 0 and n_ids == 0:
        return True
    bits = []
    if n_ids:
        bits.append(f"{n_ids} manifest ID{'' if n_ids == 1 else 's'}")
    if n_files:
        bits.append(f"{n_files} file{'' if n_files == 1 else 's'}")
    resp = QMessageBox.warning(
        parent,
        "Some recordings didn't match",
        f"{' and '.join(bits)} didn't produce a clean one-to-one match and won't be "
        "imported.\n\nYou can fix your manifest CSV (or change the matching rules) and "
        "try again, or import the matched groups now.\n\nImport matched groups anyway?",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    return resp == QMessageBox.StandardButton.Yes
