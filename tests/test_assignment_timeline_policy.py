import pytest

from smartlib.editorial.cut_assignment import restore_assignment_rows


@pytest.mark.parametrize("editorial", [True, False])
def test_restored_maya_ranges_follow_policy_without_losing_mapping(editorial):
    defaults = [
        {"signature": str(i), "record_in": start, "record_out": end, "maya_in": start, "maya_out": end}
        for i, (start, end) in enumerate([(278, 411), (412, 631), (632, 822)])
    ]
    saved = {"rows": [
        {**row, "maya_in": 1001, "maya_out": 1001 + row["record_out"] - row["record_in"],
         "production_sequence": "s027", "work_shot": "custom", "enabled": False}
        for row in defaults
    ]}
    rows, restored = restore_assignment_rows(defaults, saved, editorial=editorial)
    assert restored
    assert [(r["maya_in"], r["maya_out"]) for r in rows] == (
        [(278, 411), (412, 631), (632, 822)] if editorial else [(1001, 1134), (1001, 1220), (1001, 1191)]
    )
    assert all(r["production_sequence"] == "s027" and r["work_shot"] == "custom" and not r["enabled"] for r in rows)
    assert saved["rows"][0]["maya_in"] == 1001


def test_changed_events_do_not_restore_old_mapping():
    defaults = [{"signature": "new", "record_in": 278, "record_out": 411}]
    rows, restored = restore_assignment_rows(defaults, {"rows": [{"signature": "old"}]}, editorial=True)
    assert not restored
    assert rows[0]["maya_in"] == 278
    assert rows[0]["maya_out"] == 411
