from smartlib.dcc.resolve.editorial_insert import InsertRequest, InsertShot, media_filename


def test_editorial_insert_filename_contains_cgid_and_occurrence():
    request = InsertRequest("op", "op01")
    shot = InsertShot(
        shot="c001",
        cg_shot_id="a84f921c-1234-5678-90ab-cdef12345678",
        marker_start=100,
        cut_duration=36,
        mark_in=92,
        mark_out=143,
        source_tc="01:00:03:20",
        occurrence=2,
    )
    assert media_filename(request, shot, "v001", "edit") == (
        "op_op01_c001_CGID-a84f921c_E002_edit_v001.mov"
    )
    assert media_filename(request, shot, "v001", "clean") == (
        "op_op01_c001_CGID-a84f921c_E002_clean_v001.mov"
    )
