"""Pure, Qt-free matching engine for the manifest-CSV loader.

Given the parsed rows of a manifest CSV (an ID column + group columns) and a
list of data files, work out which file belongs to which ID — and therefore
which group — by tokenising both the IDs and the filenames and looking for a
structural match between the token sequences. Everything here is independent of
the GUI (no Qt imports) and unit-tested directly; the widget in
``csv_import_dialog`` is a thin shell over these functions.

Public surface used by the widget and tests:
  * ``tokenize`` / ``preprocess_tokens`` — split + normalise a string to tokens
  * ``find_match`` — does one ID's tokens match one filename's tokens?
  * ``compute_matches`` — run the match over every (row, file) pair
  * ``build_result_groups`` — turn matches into {group: [path, ...]}
  * ``highlight`` / ``col_values_summary`` / ``read_csv`` — small presentation/IO helpers
  * ``Match`` / ``Conflict`` / ``MatchResult`` — the result data classes
"""

from __future__ import annotations

import csv
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

# ── Tokenisation ──────────────────────────────────────────────────────────────


def _apply_zero_tolerance(s: str) -> str:
    """Strip leading zeros from each numeric run: '0012' → '12', 'OFT01' → 'OFT1'."""
    return re.sub(r"(?<!\d)0+(\d)", r"\1", s)


def _token_spans(
    s: str, nonalpha: bool, boundary_strings: list[str], case_sensitive: bool
) -> list[tuple[str, int, int]]:
    """Return (raw_token, start, end) for each token in s — the raw substring and
    its character span in s. Splits on non-alphanumeric runs if nonalpha, else on
    the boundary strings."""
    if nonalpha:
        return [(m.group(), m.start(), m.end()) for m in re.finditer(r"[a-zA-Z0-9]+", s)]
    seps = [b for b in boundary_strings if b]
    if not seps:
        return [(s, 0, len(s))] if s else []
    pattern = "|".join(re.escape(sep) for sep in sorted(seps, key=len, reverse=True))
    flags = 0 if case_sensitive else re.IGNORECASE
    spans: list[tuple[str, int, int]] = []
    pos = 0
    for m in re.finditer(pattern, s, flags):
        if m.start() > pos:
            spans.append((s[pos : m.start()], pos, m.start()))
        pos = m.end()
    if pos < len(s):
        spans.append((s[pos:], pos, len(s)))
    return spans


def tokenize(
    s: str,
    nonalpha: bool,
    boundary_strings: list[str],
    case_sensitive: bool,
    tolerate_zeros: bool,
    ignore_containing: list[str],
) -> list[tuple[str, int, int]]:
    """Tokenise and normalise s, keeping each token's span in the raw string.

    Returns (normalised_value, start, end) per surviving token. Pipeline: split →
    case-fold → strip leading zeros → drop tokens containing an ignore string. The
    span always refers to the original s, so it can highlight the raw text.
    """
    ignore = [g if case_sensitive else g.lower() for g in ignore_containing if g]
    out: list[tuple[str, int, int]] = []
    for raw, start, end in _token_spans(s, nonalpha, boundary_strings, case_sensitive):
        val = raw if case_sensitive else raw.lower()
        if tolerate_zeros:
            val = _apply_zero_tolerance(val)
        if ignore and any(g in val for g in ignore):
            continue
        out.append((val, start, end))
    return out


def preprocess_tokens(
    s: str,
    nonalpha: bool,
    boundary_strings: list[str],
    case_sensitive: bool,
    tolerate_zeros: bool,
    ignore_containing: list[str],
) -> list[str]:
    """The normalised token values of s (see tokenize), without spans."""
    return [
        v
        for v, _, _ in tokenize(
            s, nonalpha, boundary_strings, case_sensitive, tolerate_zeros, ignore_containing
        )
    ]


# ── Match algorithms ──────────────────────────────────────────────────────────


def _lcs_indices(a: list[str], b: list[str]) -> tuple[list[int], list[int]]:
    """Longest common subsequence of a and b, returned as the matched index lists
    into a and b (order kept, gaps allowed)."""
    m, n = len(a), len(b)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(m - 1, -1, -1):
        for j in range(n - 1, -1, -1):
            dp[i][j] = dp[i + 1][j + 1] + 1 if a[i] == b[j] else max(dp[i + 1][j], dp[i][j + 1])
    ai: list[int] = []
    bi: list[int] = []
    i = j = 0
    while i < m and j < n:
        if a[i] == b[j]:
            ai.append(i)
            bi.append(j)
            i += 1
            j += 1
        elif dp[i + 1][j] >= dp[i][j + 1]:
            i += 1
        else:
            j += 1
    return ai, bi


def _subarray_indices(a: list[str], b: list[str]) -> tuple[list[int], list[int]]:
    """Longest contiguous common subarray of a and b (O(n*m) DP), as the matched
    index ranges into a and b."""
    if not a or not b:
        return [], []
    n = len(b)
    best, end_a, end_b = 0, 0, 0
    prev = [0] * (n + 1)
    for i in range(len(a)):
        curr = [0] * (n + 1)
        for j in range(n):
            if a[i] == b[j]:
                curr[j + 1] = prev[j] + 1
                if curr[j + 1] > best:
                    best = curr[j + 1]
                    end_a, end_b = i + 1, j + 1
        prev = curr
    return list(range(end_a - best, end_a)), list(range(end_b - best, end_b))


def _window_indices(a: list[str], b: list[str], min_n: int) -> tuple[list[int], list[int]] | None:
    """Largest N≥min_n with a same-multiset window a[i:i+N] / b[j:j+N]; the matched
    index ranges into a and b, or None."""
    for size in range(min(len(a), len(b)), min_n - 1, -1):
        for i in range(len(a) - size + 1):
            wa = Counter(a[i : i + size])
            for j in range(len(b) - size + 1):
                if Counter(b[j : j + size]) == wa:
                    return list(range(i, i + size)), list(range(j, j + size))
    return None


def find_match(
    handle_tokens: list[str],
    stem_tokens: list[str],
    min_tokens: int,
    match_order: bool,
    match_uninterrupted: bool,
) -> tuple[list[int], list[int]] | None:
    """Return (handle_indices, stem_indices) for the matched tokens, or None if the
    threshold isn't met.

    The manifest ID (handle) is the needle; the file stem the haystack. The two
    flags select the algorithm and apply bidirectionally — the structure must hold
    in both token sequences. The returned indices are the exact tokens the match
    rests on, in each sequence, so the caller can highlight precisely what decided
    it. min_tokens == 0 means 'all' (threshold = len(handle_tokens)).
    """
    if not handle_tokens or not stem_tokens:
        return None
    effective_min = len(handle_tokens) if min_tokens == 0 else min_tokens
    if effective_min <= 0:
        return None

    if not match_order and not match_uninterrupted:
        common = set(handle_tokens) & set(stem_tokens)
        if len(common) < effective_min:
            return None
        return (
            [i for i, t in enumerate(handle_tokens) if t in common],
            [j for j, t in enumerate(stem_tokens) if t in common],
        )
    if match_order and not match_uninterrupted:
        ai, bi = _lcs_indices(handle_tokens, stem_tokens)
        return (ai, bi) if len(ai) >= effective_min else None
    if not match_order and match_uninterrupted:
        return _window_indices(handle_tokens, stem_tokens, effective_min)
    ai, bi = _subarray_indices(handle_tokens, stem_tokens)
    return (ai, bi) if len(ai) >= effective_min else None


# ── Small presentation / IO helpers ───────────────────────────────────────────


def col_values_summary(unique_vals: list[str], max_shown: int = 4) -> str:
    if not unique_vals:
        return ""
    shown = ", ".join(unique_vals[:max_shown])
    extra = len(unique_vals) - max_shown
    return f"{shown}  ...+{extra}" if extra > 0 else shown


def _group_name_for(row: dict, group_cols: list[str]) -> str | None:
    """Join group-col values with '_'. Returns None if any value is blank."""
    parts = []
    for col in group_cols:
        val = str(row.get(col, "")).strip()
        if not val or val.lower() in ("nan", "none"):
            return None
        parts.append(val)
    return "_".join(parts) if parts else None


def read_csv(path: Path) -> tuple[list[dict], list[str], str]:
    """Try UTF-8-BOM then Latin-1. Returns (rows, col_names, error)."""
    for encoding in ("utf-8-sig", "latin-1"):
        try:
            with path.open(newline="", encoding=encoding) as f:
                reader = csv.DictReader(f)
                rows = list(reader)
                cols = list(reader.fieldnames or [])
            return rows, cols, ""
        except UnicodeDecodeError:
            continue
        except OSError as e:
            return [], [], str(e)
    return [], [], f"Could not decode {path.name} as UTF-8 or Latin-1."


def highlight(full: str, spans: list[tuple[int, int]], accent: str) -> str:
    """Bold the given character spans in full. Spans come straight from the match,
    so exactly the tokens the decision rests on are highlighted."""
    if not spans:
        return full
    out: list[str] = []
    pos = 0
    for start, end in sorted(spans):
        if start < pos:
            continue  # overlaps an already-bolded span
        out.append(full[pos:start])
        out.append(f'<b style="color:{accent};">{full[start:end]}</b>')
        pos = end
    out.append(full[pos:])
    return "".join(out)


# ── Data classes ─────────────────────────────────────────────────────────────


@dataclass
class Match:
    path: Path
    id_val: str
    # character spans of the matched tokens, for highlighting: id_spans into
    # id_val, name_spans into path.stem (a prefix of path.name)
    id_spans: list[tuple[int, int]]
    name_spans: list[tuple[int, int]]
    group_name: str


@dataclass
class Conflict:
    label: str
    options: list[Match]
    # None = unresolved; frozenset() = excluded; frozenset({0,2,...}) = include those indices
    selection: frozenset | None = None


@dataclass
class MatchResult:
    clean_matches: list[Match]
    files_not_in_csv: list[Path]
    rows_skipped_blank: int
    conflicts: list[Conflict]
    groups_over_limit: bool
    unmatched_csv_ids: list[str]
    n_ids_with_group: int = 0
    # group → ids, and id → its candidate file matches, for the ID-centric preview.
    id_group: dict[str, str] = field(default_factory=dict)
    id_candidates: dict[str, list[Match]] = field(default_factory=dict)
    error: str = ""


# ── Matching logic ────────────────────────────────────────────────────────────


def compute_matches(
    rows: list[dict],
    match_col: str,
    group_cols: list[str],
    files: list[Path],
    *,
    nonalpha: bool,
    boundary_strings: list[str],
    case_sensitive: bool,
    tolerate_zeros: bool,
    ignore_containing: list[str],
    min_tokens: int,
    match_order: bool,
    match_uninterrupted: bool,
) -> MatchResult:
    if not rows or not files:
        return MatchResult([], list(files) if files else [], 0, [], False, [])

    rows_skipped_blank = 0
    candidate_matches: dict[Path, list[Match]] = defaultdict(list)
    id_candidates: dict[str, list[Match]] = defaultdict(list)
    id_group: dict[str, str] = {}
    ids_with_group: set[str] = set()

    stem_toks = {
        f: tokenize(
            f.stem, nonalpha, boundary_strings, case_sensitive, tolerate_zeros, ignore_containing
        )
        for f in files
    }

    for row in rows:
        id_val = str(row.get(match_col, "")).strip()
        if not id_val:
            continue
        group_name = _group_name_for(row, group_cols)
        if group_name is None:
            rows_skipped_blank += 1
            continue
        ids_with_group.add(id_val)
        id_group[id_val] = group_name
        handle_toks = tokenize(
            id_val, nonalpha, boundary_strings, case_sensitive, tolerate_zeros, ignore_containing
        )
        handle_vals = [v for v, _, _ in handle_toks]
        for f in files:
            file_toks = stem_toks[f]
            matched = find_match(
                handle_vals,
                [v for v, _, _ in file_toks],
                min_tokens,
                match_order,
                match_uninterrupted,
            )
            if matched is not None:
                h_idx, s_idx = matched
                match = Match(
                    path=f,
                    id_val=id_val,
                    id_spans=[handle_toks[i][1:] for i in h_idx],
                    name_spans=[file_toks[j][1:] for j in s_idx],
                    group_name=group_name,
                )
                candidate_matches[f].append(match)
                id_candidates[id_val].append(match)

    files_not_in_csv = [f for f in files if not candidate_matches[f]]
    ids_that_matched: set[str] = {m.id_val for ms in candidate_matches.values() for m in ms}
    unmatched_csv_ids = sorted(ids_with_group - ids_that_matched)
    conflicts: list[Conflict] = []

    for f, ms in candidate_matches.items():
        if len(ms) > 1:
            conflicts.append(Conflict(label=f"{f.name} matches {len(ms)} rows", options=ms))

    single: dict[Path, Match] = {f: ms[0] for f, ms in candidate_matches.items() if len(ms) == 1}
    id_to_matches: dict[str, list[Match]] = defaultdict(list)
    for m in single.values():
        id_to_matches[m.id_val].append(m)

    multi_file_ids = {iv for iv, ms in id_to_matches.items() if len(ms) > 1}
    clean_matches = [m for m in single.values() if m.id_val not in multi_file_ids]

    for id_val, ms in id_to_matches.items():
        if len(ms) > 1:
            conflicts.append(Conflict(label=f"Row '{id_val}' matches {len(ms)} files", options=ms))

    all_group_names = {m.group_name for m in clean_matches}
    for c in conflicts:
        for m in c.options:
            all_group_names.add(m.group_name)

    return MatchResult(
        clean_matches=clean_matches,
        files_not_in_csv=files_not_in_csv,
        rows_skipped_blank=rows_skipped_blank,
        conflicts=conflicts,
        groups_over_limit=len(all_group_names) > 12,
        unmatched_csv_ids=unmatched_csv_ids,
        n_ids_with_group=len(ids_with_group),
        id_group=dict(id_group),
        id_candidates=dict(id_candidates),
    )


def build_result_groups(
    clean_matches: list[Match],
    conflicts: list[Conflict],
) -> dict[str, list[Path]]:
    groups: dict[str, list[Path]] = {}
    for m in clean_matches:
        groups.setdefault(m.group_name, []).append(m.path)
    for c in conflicts:
        if not isinstance(c.selection, frozenset) or len(c.selection) == 0:
            continue  # unresolved or explicitly excluded
        for idx in c.selection:
            m = c.options[idx]
            groups.setdefault(m.group_name, []).append(m.path)
    return groups
