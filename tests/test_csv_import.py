"""Tests for pure functions in app/csv_import_dialog.py."""

from app.csv_import_dialog import (
    _build_result_groups,
    _compute_matches,
    _Conflict,
    _find_match,
    _Match,
    _preprocess_tokens,
)

# ── _preprocess_tokens ────────────────────────────────────────────────────────


def _pp(s, *, nonalpha=True, boundary=None, case=True, zeros=False, ignore=None):
    return _preprocess_tokens(s, nonalpha, boundary or [], case, zeros, ignore or [])


def test_preprocess_nonalpha_split():
    assert _pp("20240115_OFT1-8.csv".removesuffix(".csv")) == ["20240115", "OFT1", "8"]


def test_preprocess_case_fold():
    assert _pp("OFT1_X", case=False) == ["oft1", "x"]


def test_preprocess_zero_tolerance_mid_token():
    # leading zeros stripped even inside a token: OFT01 -> OFT1, 008 -> 8
    assert _pp("OFT01_008", zeros=True) == ["OFT1", "8"]


def test_preprocess_zero_tolerance_off():
    assert _pp("OFT01_008", zeros=False) == ["OFT01", "008"]


def test_preprocess_strings_split():
    assert _pp("a_b-c", nonalpha=False, boundary=["_"]) == ["a", "b-c"]


def test_preprocess_strings_empty_is_whole():
    assert _pp("a_b-c", nonalpha=False, boundary=[]) == ["a_b-c"]


def test_preprocess_ignore_drops_token():
    assert _pp("OFT1_2024_8", ignore=["2024"]) == ["OFT1", "8"]


def test_preprocess_ignore_substring_and_case():
    # case-insensitive ignore matches as a substring
    assert _pp("OFT1_Batch3_8", case=False, ignore=["batch"]) == ["oft1", "8"]


# ── _find_match: set intersection (order=☐, uninterrupted=☐) ───────────────────


def test_find_set_all_present():
    matched = _find_match(["oft1", "8"], ["x", "8", "oft1", "y"], 0, False, False)
    assert matched == ["8", "oft1"]


def test_find_set_missing_one_fails_all():
    assert _find_match(["oft1", "8"], ["oft1", "9"], 0, False, False) is None


def test_find_set_at_least_one():
    assert _find_match(["oft1", "8"], ["oft1", "9"], 1, False, False) == ["oft1"]


# ── _find_match: order only (LCS subsequence) ─────────────────────────────────


def test_find_order_subsequence_with_gaps():
    matched = _find_match(["a", "b", "c"], ["a", "x", "b", "y", "c"], 0, True, False)
    assert matched == ["a", "b", "c"]


def test_find_order_wrong_order_fails():
    assert _find_match(["a", "b"], ["b", "a"], 0, True, False) is None


# ── _find_match: uninterrupted only (contiguous same-multiset window) ──────────


def test_find_window_reordered_block_matches():
    assert _find_match(["dog", "cat"], ["cat", "dog"], 0, False, True) == ["dog", "cat"]


def test_find_window_interrupted_fails():
    assert _find_match(["dog", "cat"], ["dog", "moose", "cat"], 0, False, True) is None


# ── _find_match: order + uninterrupted (common subarray) ──────────────────────


def test_find_subarray_block_in_noise():
    # OFT1,8 is a contiguous block in both; 012 noise can't form a length-2 block
    handle = ["012", "test", "blah", "oft1", "8"]
    assert _find_match(handle, ["oft1", "8"], 2, True, True) == ["oft1", "8"]


def test_find_subarray_order_matters():
    assert _find_match(["a", "b"], ["b", "a"], 2, True, True) is None


# ── _find_match: guards ───────────────────────────────────────────────────────


def test_find_empty_handle_is_none():
    assert _find_match([], ["a", "b"], 0, False, False) is None


def test_find_threshold_above_handle_length():
    assert _find_match(["a"], ["a"], 2, False, False) is None


# ── _compute_matches ──────────────────────────────────────────────────────────


def _cfg(**over):
    cfg = dict(
        nonalpha=True,
        boundary_strings=[],
        case_sensitive=False,
        tolerate_zeros=False,
        ignore_containing=[],
        min_tokens=0,
        match_order=False,
        match_uninterrupted=False,
    )
    cfg.update(over)
    return cfg


def _rows(*handles, treatment="control", timepoint="pre"):
    return [{"handle": h, "treatment": treatment, "timepoint": timepoint} for h in handles]


def test_compute_matches_clean(tmp_path):
    files = [tmp_path / "OFT1_1.csv", tmp_path / "OFT1_2.csv"]
    for f in files:
        f.touch()
    rows = _rows("OFT1_1", "OFT1_2")
    result = _compute_matches(rows, "handle", ["treatment"], files, **_cfg())
    assert len(result.clean_matches) == 2
    assert result.conflicts == []
    assert result.files_not_in_csv == []


def test_compute_matches_row_conflict(tmp_path):
    # "OFT1" (one token) matches both files in set/all mode -> row conflict
    files = [tmp_path / "OFT1_1.csv", tmp_path / "OFT1_2.csv"]
    for f in files:
        f.touch()
    rows = _rows("OFT1")
    result = _compute_matches(rows, "handle", ["treatment"], files, **_cfg())
    assert len(result.conflicts) == 1
    assert len(result.conflicts[0].options) == 2


def test_compute_matches_unmatched_file(tmp_path):
    files = [tmp_path / "OFT1_1.csv", tmp_path / "CALIBRATION.csv"]
    for f in files:
        f.touch()
    rows = _rows("OFT1_1")
    result = _compute_matches(rows, "handle", ["treatment"], files, **_cfg())
    assert len(result.clean_matches) == 1
    assert any(f.name == "CALIBRATION.csv" for f in result.files_not_in_csv)


def test_compute_matches_zero_tolerance(tmp_path):
    files = [tmp_path / "OFT1_1.csv"]
    files[0].touch()
    rows = _rows("OFT01_01")
    result = _compute_matches(rows, "handle", ["treatment"], files, **_cfg(tolerate_zeros=True))
    assert len(result.clean_matches) == 1


def test_compute_matches_multi_column_group(tmp_path):
    files = [tmp_path / "OFT1_1.csv", tmp_path / "OFT1_2.csv"]
    for f in files:
        f.touch()
    rows = [
        {"handle": "OFT1_1", "treatment": "control", "timepoint": "pre"},
        {"handle": "OFT1_2", "treatment": "stressor", "timepoint": "pre"},
    ]
    result = _compute_matches(rows, "handle", ["treatment", "timepoint"], files, **_cfg())
    groups = {m.group_name for m in result.clean_matches}
    assert groups == {"control_pre", "stressor_pre"}


# ── _build_result_groups ──────────────────────────────────────────────────────


def test_build_result_groups_clean(tmp_path):
    f1, f2 = tmp_path / "a.csv", tmp_path / "b.csv"
    matches = [
        _Match(path=f1, id_val="a", matched_tokens=["a"], group_name="G1"),
        _Match(path=f2, id_val="b", matched_tokens=["b"], group_name="G2"),
    ]
    groups = _build_result_groups(matches, [])
    assert set(groups.keys()) == {"G1", "G2"}
    assert groups["G1"] == [f1]


def test_build_result_groups_resolved_conflict(tmp_path):
    f1, f2 = tmp_path / "a.csv", tmp_path / "b.csv"
    m1 = _Match(path=f1, id_val="x", matched_tokens=["x"], group_name="G1")
    m2 = _Match(path=f2, id_val="x", matched_tokens=["x"], group_name="G1")
    conflict = _Conflict(label="x matches 2 files", options=[m1, m2], selection=frozenset({0}))
    groups = _build_result_groups([], [conflict])
    assert groups["G1"] == [f1]


def test_build_result_groups_excluded_conflict(tmp_path):
    f1 = tmp_path / "a.csv"
    m1 = _Match(path=f1, id_val="x", matched_tokens=["x"], group_name="G1")
    conflict = _Conflict(label="x matches 1 file", options=[m1], selection=frozenset())
    groups = _build_result_groups([], [conflict])
    assert groups == {}


def test_build_result_groups_unresolved_conflict_excluded(tmp_path):
    f1 = tmp_path / "a.csv"
    m1 = _Match(path=f1, id_val="x", matched_tokens=["x"], group_name="G1")
    conflict = _Conflict(label="x", options=[m1], selection=None)
    groups = _build_result_groups([], [conflict])
    assert groups == {}
