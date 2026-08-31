"""Manage Pipelines: the single place to add a git pipeline source, remove
one, and control which individual pipelines show up in the main pipeline
picker.

One interaction per row, no selection state: a small eye button toggles a
pipeline's visibility and dims the row — no checkboxes, no highlighting. A
source (not an individual pipeline) can only be removed outright, not hidden
— hiding a whole source doesn't mean anything a lab user would want; if they
don't want it, it shouldn't be installed. Update checking is automatic (once
per launch, no manual button here) — see PipelineManagerButton below, which
owns that background check and is what the main window places beside the
pipeline combo; this dialog just displays whatever it already found and lets
the user act on it.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app import pipeline_config, pipeline_sources
from app.confirm_dialog import ask, error_with_copy
from app.theme import get_theme as _get_theme

_REMOVE_BTN_WIDTH = 28
_EYE_BTN_WIDTH = 48


# ── background workers ───────────────────────────────────────────────────────
class _InstallWorker(QThread):
    """Installs (or updates) one source. If *ref* is None, resolves it to the
    repo's latest stable release first."""

    done = Signal(object, object)  # (Source | None, error message str | None)

    def __init__(self, owner_repo: str, ref: str | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._owner_repo = owner_repo
        self._ref = ref

    def run(self) -> None:
        try:
            ref = self._ref
            if not ref:
                latest = pipeline_sources.latest_release(self._owner_repo)
                if latest is None:
                    raise pipeline_sources.SourceError(
                        f"{self._owner_repo} has no releases to install."
                    )
                ref = latest.tag
            source_id = pipeline_sources.sanitize_id(self._owner_repo)
            pipeline_sources.install_source(
                self._owner_repo, ref, pipeline_sources.source_dir(source_id)
            )
            source = pipeline_sources.Source(id=source_id, repo=self._owner_repo, ref=ref)
            self.done.emit(source, None)
        except pipeline_sources.SourceError as exc:
            self.done.emit(None, str(exc))
        except Exception as exc:  # network/zip surprises shouldn't crash the thread
            self.done.emit(None, f"unexpected error installing {self._owner_repo}: {exc}")


class _UpdateCheckWorker(QThread):
    """Checks a batch of sources for updates in one background pass."""

    done = Signal(dict)  # {source_id: [ReleaseInfo, ...]}

    def __init__(
        self, sources: list[pipeline_sources.Source], parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._sources = sources

    def run(self) -> None:
        result: dict[str, list] = {}
        for s in self._sources:
            try:
                result[s.id] = pipeline_sources.check_source_for_updates(s)
            except Exception:
                result[s.id] = []
        self.done.emit(result)


# ── add-source prompt ────────────────────────────────────────────────────────
class _AddSourceDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add pipeline source")
        self.setMinimumWidth(420)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 16)
        outer.setSpacing(10)

        outer.addWidget(QLabel("GitHub URL (or owner/name):"))
        self._repo_edit = QLineEdit()
        self._repo_edit.setPlaceholderText("https://github.com/ETHZ-INS/oft-pipeline")
        outer.addWidget(self._repo_edit)

        outer.addWidget(QLabel("Version (leave blank for the latest release):"))
        self._ref_edit = QLineEdit()
        self._ref_edit.setPlaceholderText("v1.2.0")
        outer.addWidget(self._ref_edit)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("dlgBtn")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        ok_btn = QPushButton("Add")
        ok_btn.setObjectName("dlgBtnPrimary")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self.accept)
        btn_row.addWidget(ok_btn)
        outer.addLayout(btn_row)

        self._apply_stylesheet()

    def values(self) -> tuple[str, str | None]:
        return self._repo_edit.text(), self._ref_edit.text().strip() or None

    def _apply_stylesheet(self) -> None:
        t = _get_theme()
        self.setStyleSheet(f"""
            QDialog {{ background-color: {t.bg}; color: {t.panel_text}; font-size: 13px; }}
            QLabel {{ background: transparent; color: {t.panel_text}; }}
            QLineEdit {{
                background-color: {t.display}; border: 1px solid {t.muted};
                border-radius: 5px; padding: 6px; color: {t.text};
            }}
            QPushButton#dlgBtn {{
                background-color: transparent; color: {t.accent};
                border: 1px solid {t.accent}; border-radius: 5px;
                padding: 6px 20px; min-width: 72px;
            }}
            QPushButton#dlgBtn:hover {{ background-color: {t.accent}; color: {t.accent_text}; }}
            QPushButton#dlgBtnPrimary {{
                background-color: {t.accent}; color: {t.accent_text}; border: none;
                border-radius: 5px; padding: 6px 20px; min-width: 72px; font-weight: bold;
            }}
            QPushButton#dlgBtnPrimary:hover {{ background-color: {t.accent_hover}; }}
        """)


# ── main dialog ───────────────────────────────────────────────────────────
class PipelineManagerDialog(QDialog):
    def __init__(
        self, parent: QWidget | None = None, updates: dict[str, list] | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Manage pipelines")
        self.setMinimumSize(520, 480)

        self._sources: list[pipeline_sources.Source] = pipeline_sources.load_sources()
        self._hidden: set[tuple[str, str]] = pipeline_config.read_hidden()
        self._updates: dict[str, list] = dict(updates or {})
        # What the caller (PipelineManagerButton) should show as still-pending
        # once this dialog closes — updates get dropped from here as installed.
        self.remaining_updates: dict[str, list] = dict(self._updates)
        self._install_worker: _InstallWorker | None = None

        self._build_ui()
        self._apply_stylesheet()
        self._populate()

    # ── UI ────────────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 16)
        outer.setSpacing(12)

        top_row = QHBoxLayout()
        title = QLabel("Pipelines")
        title.setObjectName("sectionTitle")
        top_row.addWidget(title)
        self._status_label = QLabel("")
        self._status_label.setObjectName("mutedLabel")
        top_row.addWidget(self._status_label)
        top_row.addStretch()
        add_btn = QPushButton("+  Add from URL")
        add_btn.setObjectName("dlgBtn")
        add_btn.clicked.connect(self._add_source)
        self._add_btn = add_btn
        top_row.addWidget(add_btn)
        outer.addLayout(top_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setObjectName("pipelineScroll")
        self._list_widget = QWidget()
        self._list_layout = QVBoxLayout(self._list_widget)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(2)
        self._list_layout.addStretch()  # rows always insert before this
        scroll.setWidget(self._list_widget)
        outer.addWidget(scroll, stretch=1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setObjectName("dlgBtnPrimary")
        close_btn.setDefault(True)
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        outer.addLayout(btn_row)

    def _insert_row(self, widget: QWidget) -> None:
        self._list_layout.insertWidget(self._list_layout.count() - 1, widget)

    def _populate(self) -> None:
        while self._list_layout.count() > 1:
            item = self._list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        entries = pipeline_config.discover(include_hidden=True)
        by_source: dict[str, list[dict]] = {}
        for e in entries:
            by_source.setdefault(e["source"], []).append(e)

        self._add_group(_group_header("Bundled (py3r)"), by_source.get("bundled", []))
        if by_source.get("user"):
            self._add_group(_group_header("Your /user files"), by_source.get("user", []))
        for s in self._sources:
            pending = self._updates.get(s.id) or []
            header = _source_header(
                s, pending[0] if pending else None, self._on_update_clicked, self._on_remove_clicked
            )
            self._add_group(header, by_source.get(s.id, []))

    def _add_group(self, header: QWidget, entries: list[dict]) -> None:
        self._insert_row(header)
        for e in sorted(entries, key=lambda e: e["label"].lower()):
            self._insert_row(_pipeline_row(e, self._hidden, self._on_visibility_toggle))

    # ── visibility ────────────────────────────────────────────────────────
    def _on_visibility_toggle(self, key: tuple[str, str], hidden_now: bool) -> None:
        if hidden_now:
            self._hidden.add(key)
        else:
            self._hidden.discard(key)
        pipeline_config.write_hidden(self._hidden)

    # ── add / update (both install one ref of one repo) ────────────────────
    def _add_source(self) -> None:
        dlg = _AddSourceDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        raw_repo, ref = dlg.values()
        repo = pipeline_sources.parse_repo_url(raw_repo)
        if repo is None:
            error_with_copy(
                self,
                "Add pipeline source",
                f'Couldn\'t make sense of "{raw_repo}" — paste a GitHub URL '
                "(e.g. https://github.com/ETHZ-INS/oft-pipeline) or just owner/name.",
            )
            return
        self._start_install(repo, ref, busy_text=f"Installing {repo}…")

    def _on_update_clicked(self, source: pipeline_sources.Source, ref: str) -> None:
        self._start_install(source.repo, ref, busy_text=f"Updating {source.repo}…")

    def _start_install(self, repo: str, ref: str | None, *, busy_text: str) -> None:
        self._add_btn.setEnabled(False)
        self._list_widget.setEnabled(False)
        self._status_label.setText(busy_text)
        self._install_worker = _InstallWorker(repo, ref, self)
        self._install_worker.done.connect(self._on_install_done)
        self._install_worker.finished.connect(self._install_worker.deleteLater)
        self._install_worker.start()

    def _on_install_done(self, source: pipeline_sources.Source | None, error: str | None) -> None:
        self._install_worker = None
        self._add_btn.setEnabled(True)
        self._list_widget.setEnabled(True)
        self._status_label.setText("")
        if error:
            error_with_copy(self, "Couldn't install pipeline source", error)
            return
        self._sources = pipeline_sources.add_or_replace(self._sources, source)
        pipeline_sources.save_sources(self._sources)
        self._updates.pop(source.id, None)
        self.remaining_updates.pop(source.id, None)
        self._populate()

    # ── remove ───────────────────────────────────────────────────────────
    def _on_remove_clicked(self, source: pipeline_sources.Source) -> None:
        if not ask(
            self,
            "Remove pipeline source?",
            f'Remove "{source.repo}" and all its installed pipelines/models? '
            "This can't be undone.",
            yes_label="Remove",
            no_label="Cancel",
        ):
            return
        pipeline_sources.uninstall_source(source.id)
        self._sources = [s for s in self._sources if s.id != source.id]
        pipeline_sources.save_sources(self._sources)
        self._hidden = {(src, pid) for src, pid in self._hidden if src != source.id}
        pipeline_config.write_hidden(self._hidden)
        self._updates.pop(source.id, None)
        self.remaining_updates.pop(source.id, None)
        self._populate()

    # ── teardown ─────────────────────────────────────────────────────────
    def closeEvent(self, event) -> None:
        if self._install_worker is not None and self._install_worker.isRunning():
            self._install_worker.wait()
        super().closeEvent(event)

    def _apply_stylesheet(self) -> None:
        t = _get_theme()
        self.setStyleSheet(f"""
            QDialog {{ background-color: {t.bg}; color: {t.panel_text}; font-size: 13px; }}
            QLabel {{ background: transparent; color: {t.panel_text}; }}
            QLabel#sectionTitle {{
                color: {t.title}; font-weight: bold; font-size: 12px;
                letter-spacing: 1px; text-transform: uppercase;
            }}
            QLabel#groupHeader {{
                color: {t.title}; font-weight: bold; font-size: 12px; padding-top: 6px;
            }}
            QLabel#mutedLabel {{ color: {t.muted}; font-size: 12px; }}
            QLabel#pipelineRowLabel {{ color: {t.text}; padding-left: 20px; }}
            QLabel#pipelineRowLabel[dim="true"] {{ color: {t.muted}; }}
            QScrollArea#pipelineScroll {{
                background-color: {t.display}; border: 1px solid {t.muted}; border-radius: 5px;
            }}
            QPushButton#eyeButton {{
                background: transparent; color: {t.muted}; border: none; font-size: 11px;
            }}
            QPushButton#eyeButton:hover {{ color: {t.panel_text}; }}
            QPushButton#removeButton {{
                background: transparent; color: {t.muted}; border: none; font-size: 12px;
            }}
            QPushButton#removeButton:hover {{ color: {t.error}; }}
            QPushButton#updateLinkButton {{
                background: transparent; color: {t.success}; border: none; font-size: 12px;
                font-weight: bold;
            }}
            QPushButton#updateLinkButton:hover {{ text-decoration: underline; }}
            QPushButton#dlgBtn {{
                background-color: transparent; color: {t.accent};
                border: 1px solid {t.accent}; border-radius: 5px;
                padding: 6px 16px;
            }}
            QPushButton#dlgBtn:hover {{ background-color: {t.accent}; color: {t.accent_text}; }}
            QPushButton#dlgBtn:disabled {{ color: {t.muted}; border-color: {t.muted}; }}
            QPushButton#dlgBtnPrimary {{
                background-color: {t.accent}; color: {t.accent_text}; border: none;
                border-radius: 5px; padding: 6px 20px; min-width: 72px; font-weight: bold;
            }}
            QPushButton#dlgBtnPrimary:hover {{ background-color: {t.accent_hover}; }}
        """)


# ── row builders ─────────────────────────────────────────────────────────
def _group_header(title: str) -> QWidget:
    label = QLabel(title)
    label.setObjectName("groupHeader")
    return label


def _source_header(
    source: pipeline_sources.Source,
    update,  # ReleaseInfo | None
    on_update,
    on_remove,
) -> QWidget:
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 6, 0, 2)
    layout.setSpacing(8)

    label = QLabel(f"{source.repo}  ·  {source.ref}")
    label.setObjectName("groupHeader")
    layout.addWidget(label)
    layout.addStretch()

    if update is not None:
        update_btn = QPushButton(f"⬆  Update to {update.tag}")
        update_btn.setObjectName("updateLinkButton")
        update_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        update_btn.clicked.connect(lambda: on_update(source, update.tag))
        layout.addWidget(update_btn)

    remove_btn = QPushButton("✕")
    remove_btn.setObjectName("removeButton")
    remove_btn.setFixedWidth(_REMOVE_BTN_WIDTH)
    remove_btn.setToolTip(f'Remove "{source.repo}"')
    remove_btn.clicked.connect(lambda: on_remove(source))
    layout.addWidget(remove_btn)

    return row


def _pipeline_row(entry: dict, hidden: set[tuple[str, str]], on_toggle) -> QWidget:
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)

    key = (entry["source"], entry["id"])
    state = {"hidden": key in hidden}

    label = QLabel(entry["label"])
    label.setObjectName("pipelineRowLabel")
    layout.addWidget(label, stretch=1)

    eye_btn = QPushButton()
    eye_btn.setObjectName("eyeButton")
    eye_btn.setFixedWidth(_EYE_BTN_WIDTH)
    layout.addWidget(eye_btn)

    def _apply() -> None:
        label.setProperty("dim", state["hidden"])
        label.style().unpolish(label)
        label.style().polish(label)
        eye_btn.setText("Show" if state["hidden"] else "Hide")
        eye_btn.setToolTip(
            "Show in the pipeline picker" if state["hidden"] else "Hide from the pipeline picker"
        )

    def _clicked() -> None:
        state["hidden"] = not state["hidden"]
        _apply()
        on_toggle(key, state["hidden"])

    eye_btn.clicked.connect(_clicked)
    _apply()
    return row


_MANAGE_LABEL = "Manage pipelines"
_MANAGE_LABEL_WITH_UPDATE = "⬆  Manage pipelines"


# ── trigger button (lives beside the pipeline combo in window.py) ───────────
class PipelineManagerButton(QPushButton):
    """A proper labelled button (styled like the "All pairs" secondaryButton,
    not a tiny icon) that opens PipelineManagerDialog. Checks tracked sources
    for updates once per launch in the background — no manual "check for
    updates" button anywhere — and tints itself + gains an up-arrow prefix and
    a tooltip listing which repos have one, mirroring
    app/update_indicator.py's UpdateIndicator for the app's own updates."""

    dialog_closed = Signal()  # emitted after the manage dialog closes — window.py
    # refreshes the pipeline combo on this rather than on clicked, so it fires
    # once the on-disk state has actually settled.

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(_MANAGE_LABEL, parent)
        self.setObjectName("secondaryButton")
        self.setToolTip("Manage pipelines…")
        self._updates: dict[str, list] = {}
        self._worker: _UpdateCheckWorker | None = None
        self.clicked.connect(self._open_dialog)

    def kick_check(self) -> None:
        sources = pipeline_sources.load_sources()
        if not sources or self._worker is not None:
            return
        self._worker = _UpdateCheckWorker(sources, self)
        self._worker.done.connect(self._on_check_done)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.start()

    def shutdown(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.wait()

    def refresh_theme(self) -> None:
        self._refresh_style()

    def _on_check_done(self, result: dict) -> None:
        self._worker = None
        self._updates = {sid: releases for sid, releases in result.items() if releases}
        self._refresh_style()

    def _refresh_style(self) -> None:
        if self._updates:
            repos = [s.repo for s in pipeline_sources.load_sources() if s.id in self._updates]
            self.setText(_MANAGE_LABEL_WITH_UPDATE)
            t = _get_theme()
            self.setStyleSheet(f"""
                QPushButton {{
                    background-color: transparent; color: {t.success};
                    border: 1px solid {t.success}; border-radius: 5px;
                    padding: 6px 10px; font-weight: bold;
                }}
                QPushButton:hover {{ background-color: {t.success}; color: {t.bg}; }}
            """)
            self.setToolTip("Pipeline updates available: " + ", ".join(repos))
        else:
            self.setText(_MANAGE_LABEL)
            self.setStyleSheet("")
            self.setToolTip("Manage pipelines…")

    def _open_dialog(self) -> None:
        dlg = PipelineManagerDialog(self, updates=self._updates)
        dlg.exec()
        self._updates = dlg.remaining_updates
        self._refresh_style()
        self.dialog_closed.emit()
