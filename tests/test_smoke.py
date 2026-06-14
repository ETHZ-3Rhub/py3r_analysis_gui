import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


from app import arenas as arena_pkg  # noqa: E402
from app.window import MainWindow  # noqa: E402


def test_main_window_constructs_and_discovers_arenas(qapp):
    arenas = arena_pkg.discover()
    assert arenas, "expected at least one arena to be discovered"

    window = MainWindow()
    # combo includes a leading "— select pipeline —" placeholder entry
    assert window._arena_combo.count() == len(arenas) + 1
