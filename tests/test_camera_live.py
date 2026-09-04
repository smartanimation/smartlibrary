import json

import pytest

from smartlib.dcc.maya import camera_output
from smartlib.dcc.maya.camera_live import (
    LIVE_ATTR,
    _rebindable_named_output,
    output_size,
)


class _NamedOutputCmds:
    def __init__(self, *, layer='CHA', owner=camera_output.OWNER, live=True, referenced=False):
        self.node = '|oldPrimary|smartCam_CHA'
        self.values = {
            self.node + '.' + camera_output.OWNER_ATTR: owner,
            self.node + '.' + camera_output.SPEC_ATTR: json.dumps({'layer': layer}),
        }
        if live:
            self.values[self.node + '.' + LIVE_ATTR] = None
        self.referenced = referenced

    def ls(self, *_args, **_kwargs):
        return [self.node]

    def objExists(self, plug):
        return plug in self.values

    def getAttr(self, plug):
        return self.values[plug]

    def referenceQuery(self, _node, **_kwargs):
        return self.referenced


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


def test_owned_live_camera_can_be_rebound_to_a_new_primary():
    cmds = _NamedOutputCmds()

    assert _rebindable_named_output(cmds, 'smartCam_CHA', 'CHA') == cmds.node


@pytest.mark.parametrize('kwargs', [
    {'owner': 'other.tool'},
    {'layer': 'CHB'},
    {'live': False},
])
def test_unowned_or_incompatible_camera_cannot_be_rebound(kwargs):
    assert _rebindable_named_output(
        _NamedOutputCmds(**kwargs), 'smartCam_CHA', 'CHA'
    ) is None


def test_referenced_live_camera_cannot_be_rebound():
    with pytest.raises(ValueError, match='referenced output camera'):
        _rebindable_named_output(
            _NamedOutputCmds(referenced=True), 'smartCam_CHA', 'CHA'
        )
