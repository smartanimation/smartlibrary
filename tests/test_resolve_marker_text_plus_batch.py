from smartlib.dcc.resolve.marker_text_plus import MarkerCaption
from smartlib.dcc.resolve.marker_text_plus_batch import _overlaps_any_marker


def test_clip_marker_overlap_is_independent_of_clip_boundaries():
    markers = [MarkerCaption("c001", 0, 36), MarkerCaption("c002", 36, 24)]
    assert _overlaps_any_marker(0, 10, markers)
    assert _overlaps_any_marker(10, 50, markers)
    assert _overlaps_any_marker(50, 60, markers)
    assert not _overlaps_any_marker(60, 70, markers)
