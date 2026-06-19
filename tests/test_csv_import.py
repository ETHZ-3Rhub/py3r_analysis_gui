"""Tests for pure functions in app/csv_import_dialog.py."""

from app.csv_import_dialog import (
    _build_result_groups,
    _compute_matches,
    _Conflict,
    _find_match,
    _Match,
)

# ── _find_match ───────────────────────────────────────────────────────────────


def test_find_match_clean():
    assert _find_match("OFT1_1", "OFT1_1", None, False, False, []) == "OFT1_1"


def test_find_match_leading_zeros_tolerated():
    # handle OFT1_01, file OFT1_1 — normalised to same
    assert _find_match("OFT1_01", "OFT1_1", None, True, False, []) is not None


def test_find_match_leading_zeros_not_tolerated():
    assert _find_match("OFT1_01", "OFT1_1", None, False, False, []) is None


def test_find_match_substring_with_min_chars():
    # OFT1_1 appears inside 20240115_OFT1_1_arena2; min_chars=5 -> matches
    result = _find_match("OFT1_1", "20240115_OFT1_1_arena2", 5, False, False, [])
    assert result is not None


def test_find_match_substring_min_chars_all_fails():
    # whole_token mode: LCS finds "OFT1_1"; whole-word check confirms it's
    # bounded by _ on both sides in "20240115_OFT1_1_arena2" → matches.
    result = _find_match("OFT1_1", "20240115_OFT1_1_arena2", None, False, True, [])
    assert result is not None  # OFT1_1 is a whole word in that stem


def test_find_match_whole_token_no_false_prefix():
    # whole_token=True: OFT1_1 should NOT match OFT1_10 (1 is not a whole token in OFT1_10)
    result = _find_match("OFT1_1", "OFT1_10", None, False, True, [])
    assert result is None


def test_find_match_whole_token_exact():
    # whole_token=True: OFT1_1 matches OFT1_1.csv stem
    result = _find_match("OFT1_1", "OFT1_1", None, False, True, [])
    assert result is not None


def test_find_match_case_sensitive_no_match():
    assert _find_match("OFT1_1", "oft1_1", None, False, False, [], case_sensitive=True) is None


def test_find_match_case_insensitive_match():
    result = _find_match("OFT1_1", "oft1_1", None, False, False, [], case_sensitive=False)
    assert result is not None


def test_find_match_no_match():
    assert _find_match("OFT1_99", "OFT1_1", None, False, False, []) is None


# ── _compute_matches ──────────────────────────────────────────────────────────


def _rows(*handles, treatment="control", timepoint="pre", sex="M"):
    return [
        {"handle": h, "treatment": treatment, "timepoint": timepoint, "sex": sex} for h in handles
    ]


def test_compute_matches_clean(tmp_path):
    files = [tmp_path / "OFT1_1.csv", tmp_path / "OFT1_2.csv"]
    for f in files:
        f.touch()
    rows = _rows("OFT1_1", "OFT1_2")
    result = _compute_matches(rows, "handle", ["treatment"], files, None, False, False, [])
    assert len(result.clean_matches) == 2
    assert result.conflicts == []
    assert result.files_not_in_csv == []


def test_compute_matches_conflict(tmp_path):
    # OFT1_1 should conflict with OFT1_10 and OFT1_11 in plain (no whole_token) mode
    files = [tmp_path / n for n in ("OFT1_1.csv", "OFT1_10.csv", "OFT1_11.csv")]
    for f in files:
        f.touch()
    rows = _rows("OFT1_1")
    result = _compute_matches(rows, "handle", ["treatment"], files, None, False, False, [])
    # OFT1_1 matches all three via LCS; three candidates → conflict
    assert len(result.conflicts) == 1
    assert len(result.conflicts[0].options) == 3


def test_compute_matches_conflict_resolved_by_whole_token(tmp_path):
    files = [tmp_path / n for n in ("OFT1_1.csv", "OFT1_10.csv", "OFT1_11.csv")]
    for f in files:
        f.touch()
    rows = _rows("OFT1_1")
    result = _compute_matches(rows, "handle", ["treatment"], files, None, False, True, [])
    assert result.conflicts == []
    assert len(result.clean_matches) == 1
    assert result.clean_matches[0].path.name == "OFT1_1.csv"


def test_compute_matches_unmatched_file(tmp_path):
    files = [tmp_path / "OFT1_1.csv", tmp_path / "CALIBRATION.csv"]
    for f in files:
        f.touch()
    rows = _rows("OFT1_1")
    result = _compute_matches(rows, "handle", ["treatment"], files, None, False, False, [])
    assert len(result.clean_matches) == 1
    assert any(f.name == "CALIBRATION.csv" for f in result.files_not_in_csv)


def test_compute_matches_no_match_for_row(tmp_path):
    files = [tmp_path / "OFT1_1.csv"]
    files[0].touch()
    rows = _rows("OFT1_99")
    result = _compute_matches(rows, "handle", ["treatment"], files, None, False, False, [])
    assert result.clean_matches == []
    assert len(result.files_not_in_csv) == 1


def test_compute_matches_multi_column_group(tmp_path):
    files = [tmp_path / "OFT1_1.csv", tmp_path / "OFT1_2.csv"]
    for f in files:
        f.touch()
    rows = [
        {"handle": "OFT1_1", "treatment": "control", "timepoint": "pre"},
        {"handle": "OFT1_2", "treatment": "stressor", "timepoint": "pre"},
    ]
    result = _compute_matches(
        rows, "handle", ["treatment", "timepoint"], files, None, False, False, []
    )
    assert len(result.clean_matches) == 2
    groups = {m.group_name for m in result.clean_matches}
    assert "control_pre" in groups
    assert "stressor_pre" in groups


# ── _build_result_groups ──────────────────────────────────────────────────────


def test_build_result_groups_clean(tmp_path):
    f1, f2 = tmp_path / "a.csv", tmp_path / "b.csv"
    matches = [
        _Match(path=f1, id_val="a", matched_substr="a", group_name="G1"),
        _Match(path=f2, id_val="b", matched_substr="b", group_name="G2"),
    ]
    groups = _build_result_groups(matches, [])
    assert set(groups.keys()) == {"G1", "G2"}
    assert groups["G1"] == [f1]


def test_build_result_groups_resolved_conflict(tmp_path):
    f1, f2 = tmp_path / "a.csv", tmp_path / "b.csv"
    m1 = _Match(path=f1, id_val="x", matched_substr="x", group_name="G1")
    m2 = _Match(path=f2, id_val="x", matched_substr="x", group_name="G1")
    conflict = _Conflict(label="x matches 2 files", options=[m1, m2], selection=frozenset({0}))
    groups = _build_result_groups([], [conflict])
    assert groups["G1"] == [f1]


def test_build_result_groups_excluded_conflict(tmp_path):
    f1 = tmp_path / "a.csv"
    m1 = _Match(path=f1, id_val="x", matched_substr="x", group_name="G1")
    conflict = _Conflict(label="x matches 1 file", options=[m1], selection=frozenset())
    groups = _build_result_groups([], [conflict])
    assert groups == {}


def test_build_result_groups_unresolved_conflict_excluded(tmp_path):
    f1 = tmp_path / "a.csv"
    m1 = _Match(path=f1, id_val="x", matched_substr="x", group_name="G1")
    conflict = _Conflict(label="x", options=[m1], selection=None)
    groups = _build_result_groups([], [conflict])
    assert groups == {}
