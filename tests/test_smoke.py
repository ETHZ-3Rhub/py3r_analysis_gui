import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


from app import pipeline_config  # noqa: E402
from app.window import MainWindow  # noqa: E402


def test_discovers_bundled_pipelines():
    pipelines = pipeline_config.discover()
    ids = {e["id"] for e in pipelines}
    assert {"oft", "epm"} <= ids, "expected the bundled OFT and EPM configs"

    # LDB's tracker model isn't ready yet — no ldb.toml ships, so it must be absent.
    assert "ldb" not in ids


def test_main_window_constructs(qapp):
    pipelines = pipeline_config.discover()
    window = MainWindow()
    # No user configs in a clean checkout → all entries above the divider, plus
    # the leading "— select pipeline —" placeholder (no divider row).
    assert window._arena_combo.count() == len(pipelines) + 1
