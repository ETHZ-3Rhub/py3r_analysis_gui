"""Tests for pure functions in app/csv_import_dialog.py."""

from app.csv_import_dialog import (
    _build_result_groups,
    _compute_matches,
    _Conflict,
    _find_match,
    _Match,
    _preprocess_tokens,
    _tokenize,
)

# ── _preprocess_tokens / _tokenize ────────────────────────────────────────────


def _pp(s, *, nonalpha=True, boundary=None, case=True, zeros=False, ignore=None):
    return _preprocess_tokens(s, nonalpha, boundary or [], case, zeros, ignore or [])


def test_preprocess_nonalpha_split():
    assert _pp("20240115_OFT1-8") == ["20240115", "OFT1", "8"]


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


def test_tokenize_keeps_raw_spans():
    # normalised value + the span of the RAW token (so highlighting hits raw text)
    assert _tokenize("OFT01_8", True, [], False, True, []) == [("oft1", 0, 5), ("8", 6, 7)]


# ── _find_match — returns (handle_indices, stem_indices) ──────────────────────


def _match_vals(handle, stem, min_tokens, order, unint):
    """Map _find_match's index result back to (handle_values, stem_values)."""
    res = _find_match(handle, stem, min_tokens, order, unint)
    if res is None:
        return None
    h_idx, s_idx = res
    return [handle[i] for i in h_idx], [stem[j] for j in s_idx]


# set intersection (order=☐, uninterrupted=☐)


def test_find_set_all_present():
    assert _match_vals(["oft1", "8"], ["x", "8", "oft1", "y"], 0, False, False) == (
        ["oft1", "8"],
        ["8", "oft1"],
    )


def test_find_set_missing_one_fails_all():
    assert _find_match(["oft1", "8"], ["oft1", "9"], 0, False, False) is None


def test_find_set_at_least_one():
    assert _match_vals(["oft1", "8"], ["oft1", "9"], 1, False, False) == (["oft1"], ["oft1"])


# order only (LCS subsequence)


def test_find_order_subsequence_with_gaps():
    assert _find_match(["a", "b", "c"], ["a", "x", "b", "y", "c"], 0, True, False) == (
        [0, 1, 2],
        [0, 2, 4],
    )


def test_find_order_wrong_order_fails():
    assert _find_match(["a", "b"], ["b", "a"], 0, True, False) is None


# uninterrupted only (contiguous same-multiset window)


def test_find_window_reordered_block_matches():
    assert _match_vals(["dog", "cat"], ["cat", "dog"], 0, False, True) == (
        ["dog", "cat"],
        ["cat", "dog"],
    )


def test_find_window_interrupted_fails():
    assert _find_match(["dog", "cat"], ["dog", "moose", "cat"], 0, False, True) is None


# order + uninterrupted (common subarray)


def test_find_subarray_block_in_noise():
    # oft1,8 is a contiguous block in both; 012 noise can't form a length-2 block
    handle = ["012", "test", "blah", "oft1", "8"]
    assert _find_match(handle, ["oft1", "8"], 2, True, True) == ([3, 4], [0, 1])


def test_find_subarray_order_matters():
    assert _find_match(["a", "b"], ["b", "a"], 2, True, True) is None


# guards


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


def test_compute_matches_spans_only_matched_block(tmp_path):
    # order+uninterrupted: the filename has a stray duplicate "8" before the block.
    # Only the actual matched block (OFT1_8) is recorded in the spans — the leading
    # "8" is NOT highlighted (this is the precision Option B buys over value-sets).
    f = tmp_path / "8_OFT1_8.csv"
    f.touch()
    rows = _rows("OFT1_8")
    result = _compute_matches(
        rows, "handle", ["treatment"], [f], **_cfg(match_order=True, match_uninterrupted=True)
    )
    assert len(result.clean_matches) == 1
    m = result.clean_matches[0]
    assert [f.stem[s:e] for s, e in m.name_spans] == ["OFT1", "8"]
    assert (0, 1) not in m.name_spans  # the stray leading "8" is left un-highlighted


def test_compute_matches_id_spans_map_into_id_val(tmp_path):
    f = tmp_path / "rec_OFT1_8.csv"
    f.touch()
    rows = _rows("OFT1_8")
    result = _compute_matches(rows, "handle", ["treatment"], [f], **_cfg())
    m = result.clean_matches[0]
    assert sorted(m.id_val[s:e] for s, e in m.id_spans) == ["8", "OFT1"]


# ── _build_result_groups ──────────────────────────────────────────────────────


def _mk(path, id_val="x", group="G1"):
    return _Match(path=path, id_val=id_val, id_spans=[], name_spans=[], group_name=group)


def test_build_result_groups_clean(tmp_path):
    f1, f2 = tmp_path / "a.csv", tmp_path / "b.csv"
    matches = [_mk(f1, "a", "G1"), _mk(f2, "b", "G2")]
    groups = _build_result_groups(matches, [])
    assert set(groups.keys()) == {"G1", "G2"}
    assert groups["G1"] == [f1]


def test_build_result_groups_resolved_conflict(tmp_path):
    f1, f2 = tmp_path / "a.csv", tmp_path / "b.csv"
    conflict = _Conflict(
        label="x matches 2 files", options=[_mk(f1), _mk(f2)], selection=frozenset({0})
    )
    groups = _build_result_groups([], [conflict])
    assert groups["G1"] == [f1]


def test_build_result_groups_excluded_conflict(tmp_path):
    f1 = tmp_path / "a.csv"
    conflict = _Conflict(label="x matches 1 file", options=[_mk(f1)], selection=frozenset())
    groups = _build_result_groups([], [conflict])
    assert groups == {}


def test_build_result_groups_unresolved_conflict_excluded(tmp_path):
    f1 = tmp_path / "a.csv"
    conflict = _Conflict(label="x", options=[_mk(f1)], selection=None)
    groups = _build_result_groups([], [conflict])
    assert groups == {}
