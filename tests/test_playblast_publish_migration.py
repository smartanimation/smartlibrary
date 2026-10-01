import ast
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

SOURCE = Path(__file__).parents[1] / 'scripts' / 'shot_manager_ui.py'
TREE = ast.parse(SOURCE.read_text(encoding='utf-8-sig'))
CLASS = next(n for n in TREE.body if isinstance(n, ast.ClassDef) and n.name == 'ShotManagerWindow')

def method(name):
    scope = dict(Path=Path)
    node = next(n for n in CLASS.body if isinstance(n, ast.FunctionDef) and n.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), 'exec'), scope)
    return scope[name]

def test_ui_type_is_moved_not_duplicated():
    text = SOURCE.read_text(encoding='utf-8-sig')
    assert '("Playblast Settings", "render_manifest")' in text
    assert '("Preview Render", "preview_render")' not in text
    assert 'playblast_item = QtWidgets.QListWidgetItem' not in text
    assert '("render_manifest", "Render Manifest")' not in text
    assert '.clicked.connect(self.publish_playblast_settings)' in text

def test_publish_lists_existing_data_versions_for_department():
    @dataclass
    class Row:
        name: str
        path: str
    rows = [Row('render_manifest/anim/main', '/data/anim/v001'),
            Row('render_manifest/layout/main', '/data/layout/v001')]
    service = SimpleNamespace(list_shot_data_versions=lambda _: rows,
        paths=SimpleNamespace(artifact_file=lambda directory, name: Path(directory) / name))
    widget = SimpleNamespace(active_shot_identity='shot', active_sequence_identity=None,
        current_sequence_identity=lambda: None, _current_publish_type=lambda: 'render_manifest',
        _current_publish_target=lambda: 'anim', service=service)
    result = method('_publish_rows')(widget)
    assert [(r.name, r.path) for r in result] == [('render_manifest/anim/main', str(Path('/data/anim/v001/render_manifest.json')))]
    assert rows[0].path == '/data/anim/v001'

def test_publish_uses_publish_selection_not_data_selection():
    calls = []
    widget = SimpleNamespace(publish_target_list=SimpleNamespace(selectedItems=lambda: [
        SimpleNamespace(data=lambda role: {'target': 'anim'}, text=lambda: 'anim')]),
        _export_scene_component_data=lambda *a, **kw: calls.append((a, kw)),
        _populate_publish_targets=lambda: None, populate_publish_tree=lambda: None)
    node = next(n for n in CLASS.body if isinstance(n, ast.FunctionDef) and n.name == 'publish_playblast_settings')
    scope = dict(QtCore=SimpleNamespace(Qt=SimpleNamespace(UserRole=32)))
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), 'exec'), scope)
    scope['publish_playblast_settings'](widget)
    assert calls == [(('render_manifest',), {'targets': ['anim']})]
