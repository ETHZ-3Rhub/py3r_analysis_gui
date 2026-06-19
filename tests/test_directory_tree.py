"""Tests for pure functions in app/directory_tree_widget.py."""

from pathlib import Path

from app.directory_tree_widget import _compute_groups, _walk_tree

# ── helpers ───────────────────────────────────────────────────────────────────


def _make_tree(tmp_path: Path) -> Path:
    """Create a 3-level tree: pre/post × control/stressor × F/M with .csv stubs."""
    root = tmp_path / "tree"
    for tp in ("pre", "post"):
        for tr in ("control", "stressor"):
            for sx in ("F", "M"):
                folder = root / tp / tr / sx
                folder.mkdir(parents=True)
                (folder / f"{tp}_{tr}_{sx}.csv").touch()
    return root


# ── _walk_tree ────────────────────────────────────────────────────────────────


def test_walk_tree_basic(tmp_path):
    root = _make_tree(tmp_path)
    entries = _walk_tree(root, 3, 3, {".csv"})
    assert len(entries) == 8
    # each entry has 3-part path and one file
    for parts, files in entries:
        assert len(parts) == 3
        assert len(files) == 1


def test_walk_tree_max_depth_2(tmp_path):
    root = _make_tree(tmp_path)
    entries = _walk_tree(root, 2, 2, {".csv"})
    # at depth 2 (treatment level) there are no files — files are at depth 3
    assert len(entries) == 0


def test_walk_tree_max_depth_2_with_files(tmp_path):
    root = tmp_path / "tree2"
    # place files at depth 2 as well
    for tp in ("pre", "post"):
        for tr in ("control", "stressor"):
            folder = root / tp / tr
            folder.mkdir(parents=True)
            (folder / f"{tp}_{tr}.csv").touch()
    entries = _walk_tree(root, 2, 2, {".csv"})
    assert len(entries) == 4


def test_walk_tree_max_depth_1(tmp_path):
    root = _make_tree(tmp_path)
    entries = _walk_tree(root, 1, 1, {".csv"})
    # no files at depth 1 (timepoint folders only contain subdirs)
    assert len(entries) == 0


def test_walk_tree_empty_folder_skipped(tmp_path):
    root = _make_tree(tmp_path)
    # add empty leaf
    (root / "pre" / "control" / "Z").mkdir()
    entries = _walk_tree(root, 3, 3, {".csv"})
    assert len(entries) == 8  # still 8, Z has no csv files


def test_walk_tree_extension_filter(tmp_path):
    root = tmp_path / "tree"
    folder = root / "group"
    folder.mkdir(parents=True)
    (folder / "data.txt").touch()
    (folder / "data.csv").touch()
    entries = _walk_tree(root, 1, 1, {".csv"})
    assert len(entries) == 1
    assert len(entries[0][1]) == 1
    assert entries[0][1][0].suffix == ".csv"


def test_walk_tree_parts_correct(tmp_path):
    root = _make_tree(tmp_path)
    entries = _walk_tree(root, 3, 3, {".csv"})
    all_parts = {e[0] for e in entries}
    assert ("pre", "control", "F") in all_parts
    assert ("post", "stressor", "M") in all_parts


# ── _compute_groups ───────────────────────────────────────────────────────────


def _entries_from_tree(tmp_path):
    root = _make_tree(tmp_path)
    return _walk_tree(root, 3, 3, {".csv"})


def test_compute_groups_all_levels(tmp_path):
    entries = _entries_from_tree(tmp_path)
    groups = _compute_groups(entries, {1, 2, 3})
    assert len(groups) == 8
    assert "pre_control_F" in groups
    assert "post_stressor_M" in groups


def test_compute_groups_level3_deselected(tmp_path):
    entries = _entries_from_tree(tmp_path)
    groups = _compute_groups(entries, {1, 2})
    assert len(groups) == 4
    assert "pre_control" in groups
    # each group should have 2 files (F + M merged)
    for files in groups.values():
        assert len(files) == 2


def test_compute_groups_level1_deselected(tmp_path):
    entries = _entries_from_tree(tmp_path)
    groups = _compute_groups(entries, {2, 3})
    assert len(groups) == 4
    assert "control_F" in groups
    # pre_control_F and post_control_F both merge into control_F
    assert len(groups["control_F"]) == 2


def test_compute_groups_all_deselected(tmp_path):
    entries = _entries_from_tree(tmp_path)
    groups = _compute_groups(entries, set())
    assert len(groups) == 1
    assert "Group" in groups
    assert len(groups["Group"]) == 8


def test_compute_groups_merge_no_duplicates(tmp_path):
    # two entries map to same name — files must be combined, no duplicates
    f1 = tmp_path / "a.csv"
    f2 = tmp_path / "b.csv"
    f1.touch()
    f2.touch()
    entries = [
        (("level1a", "X"), [f1]),
        (("level1b", "X"), [f2]),
    ]
    groups = _compute_groups(entries, {2})
    assert "X" in groups
    assert sorted(groups["X"]) == sorted([f1, f2])


def test_compute_groups_dedup_same_file(tmp_path):
    f1 = tmp_path / "a.csv"
    f1.touch()
    entries = [
        (("A",), [f1]),
        (("B",), [f1]),
    ]
    # both collapse to "Group" when all deselected
    groups = _compute_groups(entries, set())
    assert groups["Group"] == [f1]  # deduped


def test_compute_groups_shallower_than_selected(tmp_path):
    # entry has only 1 part but level 3 is selected — maps to default "Group"
    f1 = tmp_path / "a.csv"
    f1.touch()
    entries = [(("only_one",), [f1])]
    groups = _compute_groups(entries, {3})
    assert "Group" in groups
    assert groups["Group"] == [f1]
