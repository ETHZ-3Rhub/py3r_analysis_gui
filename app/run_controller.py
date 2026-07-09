"""Run-lifecycle controller — owns the Analyse/Cancel button, the run-button
gating logic, the log panel, and the `PipelineRunner` it drives."""

from __future__ import annotations

import datetime
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject
from PySide6.QtGui import QColor, QTextCursor
from PySide6.QtWidgets import (
    QComboBox,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QTextEdit,
    QWidget,
)

from app.comparisons_panel import ComparisonsPanel
from app.confirm_dialog import ask
from app.group_manifest_panel import GroupManifestPanel
from app.runner import PipelineRunner
from app.theme import get_theme as _get_theme
from app.tracking_env_panel import TrackingEnvPanel

_SPINNER_CHARS = "|/-\\"


class RunController(QObject):
    def __init__(
        self,
        *,
        dialog_parent: QWidget,
        pipeline_combo: QComboBox,
        options_btn: QPushButton,
        out_edit: QLineEdit,
        run_btn: QPushButton,
        log: QTextEdit,
        open_btn: QPushButton,
        group_panel: GroupManifestPanel,
        comp_panel: ComparisonsPanel,
        video_radio: QRadioButton,
        csv_radio: QRadioButton,
        env_panel: TrackingEnvPanel,
        get_config: Callable[[], dict | None],
        get_options: Callable[[], dict],
        on_video_radio_refresh: Callable[[], None],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._dialog_parent = dialog_parent
        self._pipeline_combo = pipeline_combo
        self._get_config = get_config
        self._options_btn = options_btn
        self._out_edit = out_edit
        self._run_btn = run_btn
        self._log = log
        self._open_btn = open_btn
        self._group_panel = group_panel
        self._comp_panel = comp_panel
        self._video_radio = video_radio
        self._csv_radio = csv_radio
        self._env_panel = env_panel
        self._get_options = get_options
        self._on_video_radio_refresh = on_video_radio_refresh

        self._runner: PipelineRunner | None = None
        self.last_output: str | None = None
        self._spinner_active = False
        self._spinner_idx = 0

        self._run_btn.clicked.connect(self.toggle_run)
        self._out_edit.textChanged.connect(self.refresh_run_button)
        self._group_panel.group_added.connect(self.refresh_run_button)
        self._group_panel.group_removed.connect(self.refresh_run_button)
        self._group_panel.files_changed.connect(self.refresh_run_button)

    @property
    def is_running(self) -> bool:
        return self._runner is not None

    def shutdown(self) -> None:
        if self.is_running:
            self.cancel_run()

    # ── Run button gating ───────────────────────────────────────────────────
    def refresh_run_button(self) -> None:
        config = self._get_config()
        options = config["script"]["options"] if (config and config["script"]) else {}
        self._options_btn.setEnabled(bool(options))
        if config is None:
            self._options_btn.setToolTip("No pipeline selected.")
        elif not options:
            self._options_btn.setToolTip("This pipeline has no advanced options.")
        else:
            n = len(options)
            self._options_btn.setToolTip(f"{n} advanced option{'s' if n != 1 else ''} available.")

        reasons: list[str] = []
        groups = self._group_panel.groups()

        if config is None:
            reasons.append("No pipeline selected.")
        if not groups:
            reasons.append("No groups added.")
        if not self._out_edit.text().strip():
            reasons.append("No output folder set.")

        # Hard block: tracking env not ready (only matters when actually tracking)
        skip = self._csv_radio.isChecked()
        if not skip and not self._env_panel.env_ready():
            env_status = self._env_panel.status()
            if env_status == "installing":
                reasons.append("Tracking environment is installing — please wait for it to finish.")
            elif env_status == "checking":
                reasons.append("Checking tracking environment…")
            else:
                reasons.append(
                    "Tracking environment is not set up.\n"
                    "     Select 'Video files' as the source to set it up, or\n"
                    "     choose 'Pre-tracked CSV files' to skip tracking."
                )

        # Hard block: any group with zero relevant files
        ext_label = "CSV" if skip else "video"
        for name, files in groups.items():
            if not files:
                reasons.append(f'Group "{name}" contains no {ext_label} files.')

        if self._runner is not None:
            # Pipeline is running — keep Cancel button enabled regardless of validation
            self._run_btn.setEnabled(True)
        else:
            can_run = not reasons
            self._run_btn.setEnabled(can_run)

        if reasons:
            self._run_btn.setToolTip("Cannot run:\n" + "\n".join(f"  •  {r}" for r in reasons))
        else:
            self._run_btn.setToolTip("Run the analysis pipeline.")

    def _collect_warnings(self) -> list[str]:
        """Return soft-warning strings shown in the 'proceed anyway?' dialog.

        Hard errors (0 files in a group) already block the run button and are
        never included here — by the time this is called those groups are fixed.
        """
        warnings: list[str] = []

        ext_label = "CSV" if self._csv_radio.isChecked() else "video"
        for name, files in self._group_panel.groups().items():
            # 0 files is a hard error handled by the run button gate; skip here
            if 0 < len(files) < 5:
                warnings.append(
                    f'Group "{name}": only {len(files)} {ext_label} file(s) — '
                    f"results may be underpowered (expected ≥ 5)."
                )

        if not self._comp_panel.get_comparisons():
            warnings.append(
                "No group comparisons defined — pairwise statistics and BFA comparison "
                "plots will be skipped."
            )

        return warnings

    # ── Run controls ─────────────────────────────────────────────────────────
    def toggle_run(self) -> None:
        if self._runner is not None:
            self.cancel_run()
        else:
            self.start_run()

    def start_run(self) -> None:
        config = self._get_config()
        if config is None:
            return

        # Run-start coherence gate: catch the one degenerate input×pipeline
        # corner (already-tracked CSVs fed to a tracking-only pipeline — nothing
        # to do) here rather than reactively greying controls.
        if self._csv_radio.isChecked() and config["script"] is None:
            QMessageBox.information(
                self._dialog_parent,
                "Nothing to do",
                "This pipeline only produces tracking output, but you've supplied "
                "already-tracked CSV files — so there's nothing left to do.\n\n"
                "Pick a pipeline with an analysis step, or switch the source to "
                "video files.",
            )
            return

        # Nest everything inside a timestamped run folder of our own — if the
        # user points this at somewhere like their Desktop, we don't want to
        # scatter our tracking/figures/etc. folders directly into it.
        run_name = f"Analys3R_{datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}"
        out_root = Path(self._out_edit.text().strip())
        output_dir = out_root / run_name
        if output_dir.exists():
            # Timestamp collisions should be near-impossible (one-second
            # resolution), but if the user fires off two runs in the same
            # second — or a folder with this exact name already exists for
            # some other reason — disambiguate rather than silently mixing
            # outputs together in one folder.
            original_name = run_name
            suffix = 2
            while (out_root / f"{original_name}_{suffix}").exists():
                suffix += 1
            run_name = f"{original_name}_{suffix}"
            output_dir = out_root / run_name
            QMessageBox.information(
                self._dialog_parent,
                "Output folder already exists",
                f'A folder named "{original_name}" already exists in the '
                f'chosen output location.\n\nResults will be written to "{run_name}" instead.',
            )
        output_dir.mkdir(parents=True, exist_ok=True)
        groups = self._group_panel.groups()
        comparisons = self._comp_panel.get_comparisons()
        skip_tracking = self._csv_radio.isChecked()

        warnings = self._collect_warnings()
        if warnings:
            bullet_list = "\n".join(f"  ⚠  {w}" for w in warnings)
            if not ask(
                self._dialog_parent,
                "Warnings — proceed?",
                f"The following issues were detected:\n\n{bullet_list}\n\nProceed anyway?",
                yes_label="Proceed",
                no_label="Go back",
            ):
                return

        self._log.clear()
        self._open_btn.setVisible(False)
        self._run_btn.setText("■  Cancel")
        self._pipeline_combo.setEnabled(False)
        self._out_edit.setEnabled(False)
        self._comp_panel.set_list_enabled(False)
        self._video_radio.setEnabled(False)
        self._csv_radio.setEnabled(False)

        self._runner = PipelineRunner(
            config,
            groups,
            output_dir,
            comparisons,
            skip_tracking=skip_tracking,
            options=self._get_options(),
        )
        self._runner.log.connect(self._on_log)
        self._runner.warning.connect(self._on_warning)
        self._runner.subprocess_output.connect(self._on_subprocess_output)
        self._runner.heartbeat.connect(self._on_heartbeat)
        self._runner.finished.connect(self._on_finished)
        self._runner.error.connect(self._on_error)
        self._spinner_active = False
        self._spinner_idx = 0
        self._runner.start()

    def cancel_run(self) -> None:
        if self._runner:
            # Disconnect our handlers so stray signals from the dying thread
            # don't affect the UI after we've already reset it.
            self._runner.log.disconnect(self._on_log)
            self._runner.warning.disconnect(self._on_warning)
            self._runner.subprocess_output.disconnect(self._on_subprocess_output)
            self._runner.heartbeat.disconnect(self._on_heartbeat)
            self._runner.finished.disconnect(self._on_finished)
            self._runner.error.disconnect(self._on_error)
            # cancel() kills the running subprocess immediately, so run()
            # returns within milliseconds — safe to wait() on the GUI thread.
            self._runner.cancel()
            self._runner.wait()
            self._runner.deleteLater()
            self._runner = None
        _T = _get_theme()
        self._log_line("Cancelled.", colour=_T.error)
        self.reset_controls()

    def reset_controls(self) -> None:
        self._run_btn.setText("▶  Analyse")
        self._pipeline_combo.setEnabled(True)
        self._out_edit.setEnabled(True)
        self._comp_panel.set_list_enabled(True)
        self._on_video_radio_refresh()
        self._csv_radio.setEnabled(True)
        self.refresh_run_button()

    # ── Runner signal handlers ──────────────────────────────────────────────
    def _on_log(self, msg: str) -> None:
        self._log_line(msg)

    def _on_finished(self, output_path: str) -> None:
        _T = _get_theme()
        self._runner.wait()  # ensure Qt thread machinery has fully stopped before GC
        self._runner = None
        self.last_output = output_path
        self._log_line(f"✅  Complete — results in {output_path}", colour=_T.success)
        self._open_btn.setVisible(True)
        self.reset_controls()

    def _on_subprocess_output(self, chunk: str) -> None:
        _T = _get_theme()
        self._clear_spinner()
        self._log.setTextColor(QColor(_T.muted))
        self._log.insertPlainText(chunk)
        self._log.ensureCursorVisible()

    def _on_heartbeat(self) -> None:
        _T = _get_theme()
        cursor = self._log.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if self._spinner_active:
            cursor.deletePreviousChar()
        else:
            self._spinner_active = True
        self._log.setTextColor(QColor(_T.muted))
        cursor.insertText(_SPINNER_CHARS[self._spinner_idx % len(_SPINNER_CHARS)])
        self._spinner_idx += 1
        self._log.ensureCursorVisible()

    def _clear_spinner(self) -> None:
        if not self._spinner_active:
            return
        self._spinner_active = False
        cursor = self._log.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.deletePreviousChar()

    def _on_warning(self, msg: str) -> None:
        _T = _get_theme()
        self._log_line(f"⚠  {msg}", colour=_T.warn)

    def _on_error(self, tb: str) -> None:
        _T = _get_theme()
        self._runner.wait()  # ensure Qt thread machinery has fully stopped before GC
        self._runner = None
        self._log_line("❌  Pipeline error:", colour=_T.error)
        for line in tb.splitlines():
            self._log_line(line, colour=_T.error)
        self.reset_controls()

    # ── Log helpers ──────────────────────────────────────────────────────────
    def _log_line(self, message: str, colour: str | None = None) -> None:
        _T = _get_theme()
        if colour is None:
            colour = _T.text
        self._clear_spinner()
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        cursor = self._log.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if cursor.columnNumber() > 0:
            self._log.insertPlainText("\n")
        self._log.setTextColor(QColor(_T.muted))
        self._log.insertPlainText(f"[{ts}] ")
        self._log.setTextColor(QColor(colour))
        self._log.insertPlainText(message + "\n")
        self._log.ensureCursorVisible()
