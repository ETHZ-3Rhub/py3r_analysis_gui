"""Advanced Options dialog — renders a pipeline's advanced options as a form."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.theme import get_theme as _get_theme


def _range_label(lo: int | float, hi: int | float) -> QLabel:
    lbl = QLabel(f"{lo}–{hi}")
    lbl.setStyleSheet(f"color: {_get_theme().muted}; font-size: 11px;")
    return lbl


CPU_OPTION = "tracking_cpu"


def batch_option_name(role: str) -> str:
    return f"batch:{role}"


def tracking_option_specs(config: dict | None) -> list[dict]:
    """Tracking rows for the dialog, shown whenever the source is video.

    One batch-size row per model in the pipeline (each starts at the config's own
    value, since the right size depends on that model's input size), plus a
    machine-level "CPU only" switch. None when the pipeline has no tracking models
    (or no pipeline is picked yet).
    """
    models = config["models"] if config else {}
    if not models:
        return []
    rows = [
        {
            "name": batch_option_name(role),
            "type": int,
            "default": m.get("batch") or 1,
            "label": "Batch size" if len(models) == 1 else f"Batch size ({role})",
            "min": 1,
            "max": 128,
        }
        for role, m in models.items()
    ]
    rows.append({"name": CPU_OPTION, "type": bool, "default": False, "label": "CPU only"})
    return rows


class AdvancedOptionsDialog(QDialog):
    """Dialog that builds a form from a pipeline's option specs.

    Each option dict: ``{"name": str, "type": type, "default": any, "label": str}``

    ``tracking`` rows (see :func:`tracking_option_specs`) go in a separate
    "Tracking performance settings" section with an explanatory note, so they
    don't read as settings that change the analysis.

    Supported type/default combinations:
      - ``int`` + ``None`` default  → checkbox (opt-in) + spinbox
      - ``int`` + non-None default  → spinbox
      - ``bool``                    → checkbox
      - ``str``                     → line edit
    """

    def __init__(
        self,
        options: list[dict],
        current: dict,
        parent: QWidget | None = None,
        tracking: list[dict] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Advanced Options")
        self.setMinimumWidth(400)
        self._widgets: dict[str, QWidget | tuple] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 12)
        outer.setSpacing(12)

        if options:
            outer.addLayout(self._build_form(options, current))
        if tracking:
            outer.addWidget(self._build_tracking_section(tracking, current))

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("dlgBtn")
        cancel_btn.clicked.connect(self.reject)
        ok_btn = QPushButton("OK")
        ok_btn.setObjectName("dlgBtn")
        ok_btn.clicked.connect(self.accept)
        btn_row.addWidget(cancel_btn)
        btn_row.addSpacing(6)
        btn_row.addWidget(ok_btn)
        outer.addLayout(btn_row)

        self._apply_stylesheet()

    def _build_form(self, options: list[dict], current: dict) -> QFormLayout:
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        form.setSpacing(8)
        for opt in options:
            self._add_row(form, opt, current)
        return form

    def _build_tracking_section(self, options: list[dict], current: dict) -> QWidget:
        """Section for the machine-dependent tracking settings, set apart from the
        pipeline's own options."""
        section = QWidget()
        layout = QVBoxLayout(section)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(8)

        heading = QLabel("Tracking performance settings")
        heading.setObjectName("sectionHeading")
        layout.addWidget(heading)

        hint = QLabel(
            "These settings only change how fast tracking runs and how much memory "
            "it uses, not what it measures. If tracking is very slow or your computer "
            "lags, lower the batch size. Select 'CPU only' if tracking fails on your GPU."
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addLayout(self._build_form(options, current))
        return section

    def _add_row(self, form: QFormLayout, opt: dict, current: dict) -> None:
        name: str = opt["name"]
        typ = opt["type"]
        default = opt["default"]
        label: str = opt.get("label", name)
        current_val = current.get(name, default)

        if typ is int and default is None:
            # Optional integer: checkbox enables the spinbox
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(8)

            check = QCheckBox()
            spin = QSpinBox()
            lo, hi = opt.get("min", 2), opt.get("max", 999)
            spin.setRange(lo, hi)
            spin.setValue(current_val if current_val is not None else lo)
            spin.setEnabled(current_val is not None)
            check.setChecked(current_val is not None)
            check.toggled.connect(spin.setEnabled)

            row_layout.addWidget(check)
            row_layout.addWidget(spin)
            if "min" in opt and "max" in opt:
                row_layout.addWidget(_range_label(lo, hi))
            row_layout.addStretch()

            self._widgets[name] = (check, spin)
            form.addRow(QLabel(label + ":"), row)

        elif typ is int:
            spin = QSpinBox()
            lo, hi = opt.get("min", 1), opt.get("max", 9999)
            spin.setRange(lo, hi)
            spin.setValue(int(current_val) if current_val is not None else int(default))
            self._widgets[name] = spin
            if "min" in opt and "max" in opt:
                spin_row = QWidget()
                spin_row_layout = QHBoxLayout(spin_row)
                spin_row_layout.setContentsMargins(0, 0, 0, 0)
                spin_row_layout.setSpacing(8)
                spin_row_layout.addWidget(spin)
                spin_row_layout.addWidget(_range_label(lo, hi))
                spin_row_layout.addStretch()
                form.addRow(QLabel(label + ":"), spin_row)
            else:
                form.addRow(QLabel(label + ":"), spin)

        elif typ is float and default is None:
            # Optional float: checkbox enables the spinbox
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(8)

            check = QCheckBox()
            spin = QDoubleSpinBox()
            lo, hi = opt.get("min", 0.0), opt.get("max", 1.0)
            spin.setRange(lo, hi)
            spin.setDecimals(3)
            spin.setSingleStep(0.01)
            spin.setValue(float(current_val) if current_val is not None else lo)
            spin.setEnabled(current_val is not None)
            check.setChecked(current_val is not None)
            check.toggled.connect(spin.setEnabled)

            row_layout.addWidget(check)
            row_layout.addWidget(spin)
            if "min" in opt and "max" in opt:
                row_layout.addWidget(_range_label(lo, hi))
            row_layout.addStretch()

            self._widgets[name] = (check, spin)
            form.addRow(QLabel(label + ":"), row)

        elif typ is float:
            spin = QDoubleSpinBox()
            lo, hi = opt.get("min", 0.0), opt.get("max", 1.0)
            spin.setRange(lo, hi)
            spin.setDecimals(3)
            spin.setSingleStep(0.01)
            spin.setValue(float(current_val) if current_val is not None else float(default))
            self._widgets[name] = spin
            if "min" in opt and "max" in opt:
                spin_row = QWidget()
                spin_row_layout = QHBoxLayout(spin_row)
                spin_row_layout.setContentsMargins(0, 0, 0, 0)
                spin_row_layout.setSpacing(8)
                spin_row_layout.addWidget(spin)
                spin_row_layout.addWidget(_range_label(lo, hi))
                spin_row_layout.addStretch()
                form.addRow(QLabel(label + ":"), spin_row)
            else:
                form.addRow(QLabel(label + ":"), spin)

        elif typ is bool:
            # The macOS style reserves layout margins for its native checkbox and
            # subtracts them, which clips our stylesheet-drawn one; lay it out by
            # its actual size instead.
            check = QCheckBox()
            check.setAttribute(Qt.WidgetAttribute.WA_LayoutUsesWidgetRect)
            val = current_val if current_val is not None else default
            check.setChecked(bool(val))
            self._widgets[name] = check
            form.addRow(QLabel(label + ":"), check)

        elif typ is str:
            edit = QLineEdit()
            edit.setText(str(current_val) if current_val is not None else (default or ""))
            self._widgets[name] = edit
            form.addRow(QLabel(label + ":"), edit)

    def values(self) -> dict:
        """Return ``{name: value}`` for all options. Optional ints are ``None`` when unchecked."""
        result = {}
        for name, widget in self._widgets.items():
            if isinstance(widget, tuple):
                check, spin = widget
                result[name] = spin.value() if check.isChecked() else None
            elif isinstance(widget, QSpinBox | QDoubleSpinBox):
                result[name] = widget.value()
            elif isinstance(widget, QCheckBox):
                result[name] = widget.isChecked()
            elif isinstance(widget, QLineEdit):
                result[name] = widget.text() or None
        return result

    def _apply_stylesheet(self) -> None:
        _T = _get_theme()
        self.setStyleSheet(f"""
            QDialog, QWidget {{
                background-color: {_T.bg};
                color: {_T.panel_text};
                font-family: "Helvetica Neue", Arial, sans-serif;
                font-size: 13px;
            }}
            QLabel {{ background: transparent; color: {_T.panel_text}; }}
            QLineEdit {{
                background-color: {_T.display};
                color: {_T.text};
                border: 1px solid {_T.muted};
                border-radius: 4px;
                padding: 4px 8px;
            }}
            QSpinBox, QDoubleSpinBox {{
                background-color: {_T.display};
                color: {_T.text};
                border: 1px solid {_T.muted};
                border-radius: 4px;
                padding: 3px 3px 3px 8px;
                min-width: 64px;
            }}
            QSpinBox:disabled, QDoubleSpinBox:disabled {{
                color: {_T.muted};
                background-color: {_T.bg};
                border-color: {_T.bg};
            }}
            QSpinBox::up-button, QSpinBox::down-button,
            QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
                subcontrol-origin: border;
                width: 18px;
                background-color: {_T.sep};
                border-left: 1px solid {_T.muted};
            }}
            QSpinBox::up-button, QDoubleSpinBox::up-button {{
                subcontrol-position: top right;
                border-bottom: 1px solid {_T.muted};
                border-top-right-radius: 3px;
            }}
            QSpinBox::down-button, QDoubleSpinBox::down-button {{
                subcontrol-position: bottom right;
                border-bottom-right-radius: 3px;
            }}
            QSpinBox::up-button:hover, QSpinBox::down-button:hover,
            QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {{
                background-color: {_T.muted};
            }}
            QSpinBox::up-button:disabled, QSpinBox::down-button:disabled,
            QDoubleSpinBox::up-button:disabled, QDoubleSpinBox::down-button:disabled {{
                background-color: {_T.bg};
                border-color: {_T.bg};
            }}
            QLabel#hint {{ color: {_T.muted}; font-size: 12px; }}
            QLabel#sectionHeading {{ font-weight: 600; }}
            QCheckBox {{ spacing: 6px; color: {_T.panel_text}; }}
            QCheckBox::indicator {{
                width: 12px;
                height: 12px;
                border: 1px solid {_T.muted};
                border-radius: 2px;
                background: transparent;
            }}
            QCheckBox::indicator:checked {{
                background-color: {_T.accent};
                border-color: {_T.accent};
                border-radius: 2px;
            }}
            QPushButton#dlgBtn {{
                background-color: transparent;
                color: {_T.accent};
                border: 1px solid {_T.accent};
                border-radius: 5px;
                padding: 6px 20px;
                min-width: 72px;
            }}
            QPushButton#dlgBtn:hover {{ background-color: {_T.accent}; color: {_T.accent_text}; }}
        """)
