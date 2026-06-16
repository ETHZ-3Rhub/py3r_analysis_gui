from pathlib import Path

from app.naming import assign_handles


def test_empty_groups():
    assert assign_handles({}) == []


def test_simple_unique_stems():
    result = assign_handles(
        {
            "Control": [Path("/data/dayA/oft.mp4"), Path("/data/dayB/ept.mp4")],
        }
    )
    handles = [h for h, _, _ in result]
    assert handles == ["oft", "ept"]
    assert [g for _, g, _ in result] == ["Control", "Control"]


def test_colliding_stems_widen_to_parent_dir():
    result = assign_handles(
        {
            "Control": [Path("/data/dayA/oft.mp4"), Path("/data/dayB/oft.mp4")],
        }
    )
    handles = [h for h, _, _ in result]
    assert handles == ["dayA_oft", "dayB_oft"]
    assert len(set(handles)) == 2


def test_same_file_in_multiple_groups_falls_back_to_group_name():
    shared = Path("/data/shared/oft.mp4")
    result = assign_handles(
        {
            "Control": [shared],
            "Treatment": [shared],
        }
    )
    handles = [h for h, _, _ in result]
    assert handles == ["oft_Control", "oft_Treatment"]


def test_sanitization_of_unsafe_characters():
    result = assign_handles(
        {
            "Group A/B": [Path("/data/dayA/oft.mp4"), Path("/data/dayA/oft 2.mp4")],
        }
    )
    handles = [h for h, _, _ in result]
    for h in handles:
        assert all(c.isalnum() or c in "._-" for c in h)
    assert len(set(handles)) == 2


def test_residual_collisions_get_numeric_suffix():
    # "oft#" and "oft!" both sanitize to "oft_" -> collision broken with suffix
    result = assign_handles(
        {
            "Group": [Path("/data/dayA/oft#.mp4"), Path("/data/dayA/oft!.mp4")],
        }
    )
    handles = [h for h, _, _ in result]
    assert len(set(handles)) == 2
    assert handles[0] != handles[1]
