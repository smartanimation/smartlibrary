import math

import pytest

from smartlib.dcc.maya.camera_output import film_gate, fit_frustum, output_frustum
from smartlib.dcc.maya import smart_menu


@pytest.mark.parametrize("policy", ["horizontal", "vertical", "fit", "fill"])
def test_same_aspect_preserves_full_frustum(policy):
    assert fit_frustum((-2, 6, -1, 3), 2, policy) == pytest.approx((-2, 6, -1, 3))


def test_horizontal_preserves_width_and_off_axis_center():
    assert fit_frustum((-2, 6, -1, 3), 1, "horizontal") == pytest.approx((-2, 6, -3, 5))


def test_vertical_preserves_height():
    assert fit_frustum((-2, 6, -1, 3), 1, "vertical") == pytest.approx((0, 4, -1, 3))


def test_fit_includes_source_fill_crops_source():
    assert fit_frustum((-2, 2, -1, 1), 1, "fit") == (-2, 2, -2, 2)
    assert fit_frustum((-2, 2, -1, 1), 1, "fill") == (-1, 1, -1, 1)
    assert fit_frustum((-2, 2, -1, 1), 4, "fit") == (-4, 4, -1, 1)
    assert fit_frustum((-2, 2, -1, 1), 4, "fill") == (-2, 2, -0.5, 0.5)


def test_film_gate_round_trip_in_inches():
    frustum = (-0.1, 0.3, -0.15, 0.25)
    gate = film_gate(frustum, 50, 0.1)
    scale = 0.1 * 25.4 / 50
    assert (gate["horizontalFilmOffset"] - gate["horizontalFilmAperture"] / 2) * scale == pytest.approx(frustum[0])
    assert (gate["verticalFilmOffset"] + gate["verticalFilmAperture"] / 2) * scale == pytest.approx(frustum[3])


def test_pixel_scale_expands_canvas_without_changing_pixel_density():
    source = (-2, 2, -1, 1)
    target = output_frustum(source, (2000, 1000), (3000, 2000), "pixel_scale")
    assert target == (-3, 3, -2, 2)
    assert 2000 / (source[1] - source[0]) == 3000 / (target[1] - target[0])
    assert 1000 / (source[3] - source[2]) == 2000 / (target[3] - target[2])


@pytest.mark.parametrize("aspect,policy", [(0, "fit"), (-1, "fit"), (math.nan, "fit"), (1, "bad")])
def test_invalid_inputs_fail(aspect, policy):
    with pytest.raises(ValueError):
        fit_frustum((-1, 1, -1, 1), aspect, policy)


@pytest.mark.parametrize("items", [[], {}])
def test_experimental_menu_entry_is_added_once(items):
    data = {"maya_menu": {"categories": {"Render": items}}}
    smart_menu._ensure_camera_playblast_entry(data)
    smart_menu._ensure_camera_playblast_entry(data)
    entries = smart_menu._menu_items_from_config(data["maya_menu"]["categories"]["Render"])
    assert len(entries) == 1
    assert entries[0]["command"].endswith(".show_smart_camera_playblast")
