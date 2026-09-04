import pytest

from smartlib.dcc.maya.camera_live import output_size


@pytest.mark.parametrize('reference,rule,expected', [
    ([1920, 1080], {'mode': 'shared'}, (1920, 1080)),
    ([1920, 1080], {'mode': 'scale', 'scale': 1.1}, (2112, 1188)),
    ([2048, 858], {'mode': 'scale', 'scale': 1.1}, (2253, 944)),
    ([1920, 1080], {'mode': 'resolution', 'width': 2200, 'height': 1400}, (2200, 1400)),
])
def test_live_material_dimensions(reference, rule, expected):
    assert output_size(reference, rule) == expected


@pytest.mark.parametrize('rule', [
    {'mode': 'scale', 'scale': float('nan')}, {'mode': 'scale', 'scale': .9},
    {'mode': 'scale', 'scale': 11}, {'mode': 'other'},
    {'mode': 'resolution', 'width': 1280, 'height': 720},
])
def test_invalid_expansion_rejected(rule):
    with pytest.raises(ValueError):
        output_size([1920, 1080], rule)
