import ast
from pathlib import Path
from types import SimpleNamespace
import shutil
import sys
import pytest

from smartlib.core.usd_settings import usd_settings
from scripts.asset_manager import AssetManager


@pytest.mark.parametrize('value', [0, -1, float('inf'), float('nan'), True, 'bad'])
def test_invalid_units(value):
    with pytest.raises(ValueError): usd_settings({'usd':{'meters_per_unit':value}})


def test_usd_defaults_and_axis():
    assert usd_settings({}) == {'meters_per_unit':.01,'up_axis':'Y'}
    assert usd_settings({'usd':{'meters_per_unit':1,'up_axis':'z'}})['up_axis']=='Z'
    with pytest.raises(ValueError): usd_settings({'usd':{'up_axis':'X'}})


@pytest.mark.parametrize('department,subset,extension,expected', [
    ('model','proxy','mb',['mb','usd']), ('model','hires','mb',['mb']),
    ('rig','anim','mb',['mb']), ('model','proxy','hip',['hip']),
])
def test_proxy_maya_formats_required(department,subset,extension,expected):
    manager=SimpleNamespace(parse_work_file=lambda p:{'department':department,'variant':'A'},
                            publish_outputs={})
    result=AssetManager.publish_formats_for_work_file(manager,None,Path('asset.'+extension),subset=subset)
    assert result==expected


@pytest.mark.parametrize('failure', [False, True])
def test_proxy_publication_registers_only_after_valid_usd(tmp_path,monkeypatch,failure):
    from smartlib.dcc.maya import proxy_usd
    from smartlib.core.config_loader import ProjectConfig
    source=tmp_path/'source.mb'; source.write_bytes(b'maya saved scene')
    calls=[]
    manager=SimpleNamespace(config_dir=tmp_path,parse_work_file=lambda p:{'department':'model','variant':'A'},
        register_publish_files_for_work_file=lambda *a,**kw:calls.append(kw))
    monkeypatch.setitem(sys.modules,'maya',SimpleNamespace(cmds=SimpleNamespace(file=lambda **kw:False)))
    monkeypatch.setitem(sys.modules,'maya.cmds',sys.modules['maya'].cmds)
    monkeypatch.setattr(ProjectConfig,'load',lambda *a:{})
    monkeypatch.setattr(proxy_usd,'validate_proxy_scene',lambda settings:{})
    def export(target,settings):
        if failure: raise ValueError('invalid USD')
        target.write_bytes(b'validated usd')
        return {'status':'PASS','mesh_count':2}
    monkeypatch.setattr(proxy_usd,'export_proxy_usd',export)
    code=Path('scripts/asset_manager_ui.py').read_text(encoding='utf-8')
    tree=ast.parse(code)
    fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='publish_work_outputs')
    scope={'Path':Path,'Asset':object,'AssetManager':object,'shutil':shutil,
        'ensure_current_dcc_scene_matches':lambda p:None,
        'should_import_references_for_publish':lambda d:False}
    exec(compile(ast.Module(body=[fn],type_ignores=[]),'publish_test','exec'),scope)
    targets={'mb':tmp_path/'publish'/'asset.mb','usd':tmp_path/'publish'/'asset.usd'}
    kwargs=dict(overwrite=False,comment='test',subset='proxy',publish_version=1)
    if failure:
        with pytest.raises(ValueError,match='invalid USD'):
            scope['publish_work_outputs'](SimpleNamespace(name='Asset'),manager,source,targets,**kwargs)
        assert not calls
    else:
        scope['publish_work_outputs'](SimpleNamespace(name='Asset'),manager,source,targets,**kwargs)
        assert targets['mb'].read_bytes()==source.read_bytes()
        assert calls[0]['files']=={'mb':'asset.mb','usd':'asset.usd'}
        assert calls[0]['dependency_info']['usd_validation']['status']=='PASS'
