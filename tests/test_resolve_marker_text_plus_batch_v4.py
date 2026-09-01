from smartlib.dcc.resolve.marker_text_plus import MarkerCaption
from smartlib.dcc.resolve.marker_text_plus_batch_v4 import build_expression_with_filename


def test_filename_is_added_on_a_third_line():
    value = build_expression_with_filename(
        [MarkerCaption("c001", 0, 36)],
        fps=24,
        marker_origin=0,
        comp_origin=0,
        filename="ELCD_OP_c001.mov",
    )
    assert '(01 + 12)\\nELCD_OP_c001.mov' in value
