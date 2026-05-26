"""py3r_behaviour pipeline modules.

Each file in this package contains the full analysis logic for one arena type:
loading tracking data, computing features, generating summaries, and exporting
results.  These modules know about py3r_behaviour and nothing else — they do
not touch the GUI or the tracker.

Convention
----------
Every pipeline module must expose:

    def run(
        group_csv_dirs: dict[str, Path],
        output_dir: Path,
        progress_cb: Callable[[str, float | None], None],
    ) -> None: ...
"""
