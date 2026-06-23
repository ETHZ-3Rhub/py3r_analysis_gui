"""Centralised Qt stylesheet builders, themed at call time.

One source of truth for theming, shared by the main window and the dialogs.
QDialogs are top-level windows and do NOT inherit a parent widget's
``setStyleSheet()`` (only the QApplication-wide one), so each dialog builds its
full stylesheet from these helpers rather than copying the main window's. That
keeps a dialog's appearance from silently depending on having a ``MainWindow``
ancestor — the coupling the loader used to rely on.

Each builder takes the resolved theme ``t`` (see ``app.theme.get_theme``) so the
module itself stays import-cycle-free.
"""

from __future__ import annotations


def base_stylesheet(t) -> str:
    """The app-wide widget styling: shared by the main window and every dialog.

    Covers the generic widgets plus the named widgets that appear both in the
    main window and inside dialogs (the group lists and the manifest table), so
    a dialog that shows any of them gets them styled without reaching back into
    the window.
    """
    return f"""
        QWidget {{
            background-color: {t.bg};
            color: {t.panel_text};
            font-family: "Helvetica Neue", Arial, sans-serif;
            font-size: 13px;
        }}
        QFrame#panel {{
            background-color: {t.panel};
            border-radius: 8px;
        }}
        QWidget#gatedSection, QWidget#groupManifestPanel {{
            background: transparent;
        }}
        QLabel {{
            background: transparent;
        }}
        QLabel#sectionTitle {{
            color: {t.title};
            font-weight: bold;
            font-size: 12px;
            letter-spacing: 1px;
            text-transform: uppercase;
        }}
        QLabel#sectionTitle:disabled {{
            color: {t.muted};
        }}
        QPushButton#primaryButton {{
            background-color: {t.accent};
            color: white;
            border: none;
            border-radius: 6px;
            padding: 10px 0;
            font-size: 14px;
            font-weight: bold;
        }}
        QPushButton#primaryButton:hover {{ background-color: {t.accent_hover}; }}
        QPushButton#primaryButton:disabled {{ background-color: {t.muted}; }}
        QPushButton#secondaryButton {{
            background-color: transparent;
            color: {t.accent};
            border: 1px solid {t.accent};
            border-radius: 5px;
            padding: 6px 10px;
        }}
        QPushButton#secondaryButton:hover {{ background-color: {t.accent}; color: white; }}
        QPushButton#secondaryButton:disabled {{
            color: {t.muted};
            border-color: {t.muted};
            background-color: transparent;
        }}
        QPushButton#removeButton {{
            background: transparent;
            color: {t.muted};
            border: none;
            font-size: 12px;
        }}
        QPushButton#removeButton:hover {{ color: {t.error}; }}
        QRadioButton::indicator {{
            width: 14px;
            height: 14px;
            border-radius: 8px;
            border: 2px solid {t.muted};
            background-color: transparent;
        }}
        QRadioButton::indicator:checked {{
            border: 2px solid {t.accent};
            background-color: {t.accent};
        }}
        QRadioButton::indicator:disabled {{
            border: 2px solid {t.sep};
        }}
        QComboBox {{
            background-color: {t.display};
            color: {t.text};
            border: 1px solid {t.muted};
            border-radius: 5px;
            padding: 6px 10px;
        }}
        QComboBox#compCombo {{
            padding: 3px 6px;
            font-size: 12px;
        }}
        QComboBox::drop-down {{ border: none; width: 24px; }}
        QComboBox QAbstractItemView {{
            background-color: {t.display};
            color: {t.text};
            selection-background-color: {t.selection_bg};
        }}
        QLineEdit {{
            background-color: {t.display};
            color: {t.text};
            border: 1px solid {t.muted};
            border-radius: 5px;
            padding: 6px 10px;
        }}
        QListWidget#groupList, QListWidget#manifestGroupList {{
            background-color: {t.display};
            color: {t.text};
            border: 1px solid {t.muted};
            border-radius: 5px;
        }}
        QListWidget#groupList::item:selected,
        QListWidget#manifestGroupList::item:selected {{ background: transparent; }}
        QListWidget#groupList QWidget,
        QListWidget#manifestGroupList QWidget {{ background: transparent; }}
        QListWidget#groupList QComboBox,
        QListWidget#manifestGroupList QComboBox {{
            background-color: {t.display};
            color: {t.text};
        }}
        QListWidget#groupList QComboBox QAbstractItemView,
        QListWidget#manifestGroupList QComboBox QAbstractItemView {{
            background-color: {t.display};
            color: {t.text};
            selection-background-color: {t.selection_bg};
        }}
        QTableWidget#manifestTable {{
            background-color: {t.display};
            color: {t.text};
            border: 1px solid {t.muted};
            border-radius: 5px;
            gridline-color: transparent;
        }}
        QTableWidget#manifestTable::item {{
            padding: 4px 8px;
            border: none;
            color: {t.text};
        }}
        QTableWidget#manifestTable::item:selected {{
            background-color: {t.selection_bg};
            color: {t.text};
        }}
        QHeaderView::section {{
            background-color: transparent;
            color: {t.muted};
            border: none;
            border-bottom: 1px solid {t.sep};
            padding: 4px 8px;
            font-size: 10px;
            font-weight: bold;
            text-transform: uppercase;
            letter-spacing: 1px;
        }}
        QTextEdit#logBox {{
            background-color: {t.display};
            border: 1px solid {t.muted};
            border-radius: 5px;
            font-family: "Menlo", "Consolas", monospace;
            font-size: 11px;
            padding: 4px;
        }}
        QScrollBar:vertical {{
            background: {t.display};
            width: 8px;
        }}
        QScrollBar::handle:vertical {{
            background: {t.muted};
            border-radius: 4px;
        }}
        QPushButton#settingsButton {{
            background: transparent;
            color: {t.muted};
            border: none;
            font-size: 11px;
            padding: 2px 0;
            text-align: right;
        }}
        QPushButton#settingsButton:hover {{ color: {t.panel_text}; }}
        QToolTip {{
            background-color: {t.panel};
            color: {t.panel_text};
            border: 1px solid {t.muted};
            padding: 4px;
        }}
    """


def csv_widget_stylesheet(t) -> str:
    """Styling for the widgets specific to the advanced loader (CSV wizard and
    directory-tree page): spinboxes, checkboxes, the preview area, the separator
    chips and the disclosure toggle."""
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


def import_button_stylesheet(t) -> str:
    """The accent "import/OK" button used by the loader dialog."""
    return f"""
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


def dialog_stylesheet(t) -> str:
    """Full stylesheet for a loader dialog: app base + loader widgets + import button."""
    return base_stylesheet(t) + csv_widget_stylesheet(t) + import_button_stylesheet(t)
