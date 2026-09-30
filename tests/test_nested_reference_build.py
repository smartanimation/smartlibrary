from pathlib import Path

import pytest

from smartlib.dcc.maya import shot_builder


class ReferenceCmds:
    def __init__(self, actual='Room'):
        self.loaded = False
        self.actual = actual
        self.edits = []

    def ls(self, *args, **kwargs):
        if args:
            return []
        return ['existingRN', 'Room:ChairRN', 'RoomRN'] if self.loaded else ['existingRN']

    def namespace(self, **kwargs):
        return False

    def file(self, path, **kwargs):
        if kwargs.get('reference'):
            self.loaded = True
            return str(path) + '{1}'
        self.edits.append(path)
        assert path == 'room.mb{1}', 'Never rename a nested reference or an earlier copy'
        self.actual = kwargs['namespace']

    def referenceQuery(self, node, **kwargs):
        if kwargs.get('topReference'):
            return 'RoomRN'
        if kwargs.get('namespace'):
            return ':' + (self.actual if node == 'RoomRN' else 'Room:Chair')
        if kwargs.get('filename'):
            return 'room.mb{1}' if node == 'RoomRN' else 'chair.mb'
        raise AssertionError(kwargs)


@pytest.mark.parametrize('actual, expected_edits', [('Room', []), ('Temporary', ['room.mb{1}'])])
def test_build_identifies_outer_reference_not_alphabetically_first_child(actual, expected_edits):
    cmds = ReferenceCmds(actual)
    assert shot_builder._reference_file(cmds, Path('room.mb'), 'Room') == 'Room'
    assert cmds.edits == expected_edits
