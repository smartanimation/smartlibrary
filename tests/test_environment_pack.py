from pathlib import Path
import pytest
from pxr import Usd, UsdGeom
from smartlib.core.config_loader import ProjectConfig
from smartlib.core.path_resolver import AssetIdentity
from smartlib.core.metadata import read_json
from smartlib.apps.asset_manager.context import AssetContextService,AssetContextAssembly,AssetContextEntry
from smartlib.core.asset_publish_resolver import AssetPublishResolver


@pytest.fixture
def service(tmp_path):
    config=tmp_path/'config';config.mkdir()
    (config/'templates_base.yml').write_text(f"anchors:\n  project_name: TEST\n  project_root: '{tmp_path.as_posix()}/project'\n",encoding='utf-8')
    return AssetContextService(ProjectConfig(config))


def release(service,variant='A',quality='proxy',version='v001',offset=0):
    identity=AssetIdentity('environment','school','schoolRoom',variant)
    directory=service.paths.asset_publish_version_dir(identity,'model',quality,version)
    directory.mkdir(parents=True)
    maya=service.paths.artifact_file(directory,'model.mb');maya.write_bytes(b'saved maya')
    usd=service.paths.artifact_file(directory,'model.usd')
    stage=Usd.Stage.CreateNew(str(usd));root=UsdGeom.Xform.Define(stage,'/Root').GetPrim();stage.SetDefaultPrim(root)
    UsdGeom.SetStageUpAxis(stage,'Y');UsdGeom.SetStageMetersPerUnit(stage,.01)
    mesh=UsdGeom.Mesh.Define(stage,'/Root/body');mesh.GetPointsAttr().Set([(offset,0,0),(1,0,0),(0,1,0)])
    mesh.GetFaceVertexCountsAttr().Set([3]);mesh.GetFaceVertexIndicesAttr().Set([0,1,2]);stage.GetRootLayer().Save()
    entry=AssetContextEntry('model',quality,quality,version,'RESOLVED',str(directory),{'mb':str(maya),'usd':str(usd)})
    return AssetContextAssembly(identity,'asset','v002',quality.upper(),[entry],[],{'context':{'asset_class':'environment'},'resolved_representations':[]})


def test_pack_layout_and_default_selection(service):
    assembly=release(service)
    pack=service.pack(assembly)
    assert pack.scene_path.name=='schoolRoom_A.mb'
    assert pack.version_dir==service.paths.asset_publish_version_dir(assembly.identity,'asset','proxy','v001')
    assert (pack.version_dir/'schoolRoom_A_payload.usd').is_file()
    assert pack.usd_path==service.paths.asset_usd_version_dir(assembly.identity,'v001')/'schoolRoom.usda'
    stage=Usd.Stage.Open(str(pack.usd_path));root=stage.GetDefaultPrim()
    assert root.GetVariantSet('variant').GetVariantSelection()=='A'
    assert root.GetVariantSet('quality').GetVariantSelection()=='proxy'
    assert stage.GetPrimAtPath('/schoolRoom/body')
    unloaded=Usd.Stage.Open(str(pack.usd_path),load=Usd.Stage.LoadNone)
    assert not unloaded.GetPrimAtPath('/schoolRoom/body')
    resolved=AssetPublishResolver(service.project_config).resolve_usd_entry(assembly.identity)
    assert resolved['pack_version']=='v001'
    assert resolved['variant_selections']=={'variant':'A','quality':'proxy'}
    with pytest.raises(ValueError,match='already packed'): service.pack(assembly)


def test_new_pack_preserves_old_entry_and_other_variants(service):
    first=service.pack(release(service))
    original=first.usd_path.read_bytes()
    service.pack(release(service,'B'))
    third=service.pack(release(service,'A','render'))
    stage=Usd.Stage.Open(str(third.usd_path));root=stage.GetDefaultPrim()
    assert root.GetVariantSet('variant').GetVariantNames()==['A','B']
    root.GetVariantSet('variant').SetVariantSelection('A')
    assert root.GetVariantSet('quality').GetVariantNames()==['proxy','render']
    assert first.usd_path.read_bytes()==original
    updated=service.pack(release(service,'A','proxy','v002',offset=.5))
    manifest=read_json(updated.usd_path.parent/'manifest.json',{})
    assert manifest['members']['A']['proxy']['pack_version']=='v002'
    assert manifest['members']['B']['proxy']['pack_version']=='v001'
    assert manifest['members']['A']['render']['pack_version']=='v001'


def test_mutated_previous_pack_is_rejected(service):
    first=service.pack(release(service))
    first.scene_path.write_bytes(b'changed')
    with pytest.raises(ValueError,match='modified'): service.pack(release(service,'B'))


def test_missing_usd_blocks_pack(service):
    assembly=release(service)
    Path(assembly.entries[0].files['usd']).unlink()
    with pytest.raises(ValueError,match='both'): service.pack(assembly)


def test_wrong_units_block_pack(service):
    assembly=release(service)
    stage=Usd.Stage.Open(assembly.entries[0].files['usd']);UsdGeom.SetStageMetersPerUnit(stage,1);stage.GetRootLayer().Save()
    with pytest.raises(ValueError,match='conventions'): service.pack(assembly)


def test_workspace_subset_uses_resolver_for_dcc_layouts(tmp_path):
    from scripts.asset_manager import AssetManager
    from types import SimpleNamespace
    for prefix,tail in [('model','maya/proxy'),('model/maya','proxy')]:
        root=tmp_path/prefix
        manager=SimpleNamespace(parse_work_file=lambda p:{'dcc':'maya'},_asset_identity=lambda a,v:None,
            paths=SimpleNamespace(asset_work_dir=lambda *args:root,pipeline_token=lambda value:value))
        assert AssetManager.work_subset_for_path(manager,None,root/tail/'test.mb','model','A')=='proxy'


def test_failed_entry_build_is_not_discoverable(service,monkeypatch):
    from smartlib.apps.asset_manager import environment_pack
    from smartlib.core.asset_publish_resolver import _preferred_file
    assembly=release(service)
    def fail(*args):
        raise RuntimeError('simulated entry failure')
    monkeypatch.setattr(environment_pack,'_write_entry',fail)
    with pytest.raises(RuntimeError,match='simulated'):
        service.pack(assembly)
    directory=service.paths.asset_publish_version_dir(assembly.identity,'asset','proxy','v001')
    assert _preferred_file(directory,('mb','usd')) is None
    assert not service.list_packs(assembly.identity,quality_profile='PROXY')
    assert not (service.paths.asset_usd_root(assembly.identity)/'latest.json').exists()
    assert not (service.paths.asset_usd_root(assembly.identity)/'_pack.lock').exists()


def test_environment_current_scene_registration_is_rejected(service):
    assembly = release(service)
    with pytest.raises(ValueError, match="Context > Assemble"):
        service.write_current_scene_assembly(assembly, Path(assembly.entries[0].files['mb']))
    assert not service.paths.asset_publish_dir(assembly.identity, 'asset', 'proxy').exists()


@pytest.mark.parametrize('invalid', ['current_scene', 'missing_usd', 'missing_mb'])
def test_ui_blocks_invalid_environment_release(service, invalid):
    import ast
    from types import SimpleNamespace
    assembly = release(service)
    if invalid == 'current_scene':
        assembly.manifest['source_policy'] = 'current_scene'
    else:
        Path(assembly.entries[0].files['usd' if invalid == 'missing_usd' else 'mb']).unlink()
    tree = ast.parse(Path('scripts/asset_manager_ui.py').read_text(encoding='utf-8'))
    method = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == '_populate_context_state')
    namespace = {}
    exec(compile(ast.Module(body=[method], type_ignores=[]), '<ui-method>', 'exec'), namespace)
    state = {}
    def button(key):
        return SimpleNamespace(setEnabled=lambda enabled: state.update({key: enabled}), setToolTip=lambda text: None, setText=lambda text: None)
    ui = SimpleNamespace(
        _context_official_versions=lambda *args: {},
        _populate_context_entries=lambda *args, **kwargs: None,
        _context_source_scene=lambda *args: '',
        _set_context_readiness=lambda status, text: state.update(status=status, text=text),
        context_use_scene_btn=button('current_scene'), context_pack_btn=button('pack'),
        status_label=SimpleNamespace(setText=lambda text: None))
    namespace['_populate_context_state'](ui, assembly, service)
    assert state['status'] == 'BLOCKED'
    assert state['pack'] is False
    assert state['current_scene'] is True
    with pytest.raises(ValueError, match='Release'):
        service.pack(assembly)


def test_current_release_and_pack_are_separate_and_retryable(service, monkeypatch):
    import shutil
    from smartlib.apps.asset_manager.environment_release import release_current_scene, latest_release
    from smartlib.apps.asset_manager import environment_pack
    assembly = release(service)
    source = service.paths.asset_work_root(assembly.identity) / 'test.mb'
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b'current saved scene')
    def exporter(target):
        shutil.copy2(assembly.entries[0].files['usd'], target)
        return {'status': 'PASS'}
    released = release_current_scene(service, assembly, source, exporter)
    entry = released.entries[0]
    assert entry.publish_type == 'asset'
    assert Path(entry.files['mb']).name == 'schoolRoom_A.mb'
    assert Path(entry.files['usd']).name == 'schoolRoom_A_payload.usd'
    assert not service.paths.asset_usd_root(assembly.identity).exists()
    record = service.paths.artifact_file(Path(entry.path), 'publish.json')
    before = record.read_bytes()
    original = environment_pack._write_entry
    def fail(*args):
        raise RuntimeError('Pack failure')
    monkeypatch.setattr(environment_pack, '_write_entry', fail)
    with pytest.raises(RuntimeError, match='Pack failure'):
        service.pack(released)
    monkeypatch.setattr(service, 'load_context', lambda *args: {'_version_label': 'v002', 'quality_profiles': {}})
    monkeypatch.setattr(service, '_profiles_for_identity', lambda *args: ('environment', {'PROXY': {'model': 'none'}}))
    recovered = service.assemble(assembly.identity, quality_profile='PROXY')
    assert recovered.entries[0].version == entry.version
    assert service.has_pack_changes(recovered)
    monkeypatch.setattr(environment_pack, '_write_entry', original)
    packed = service.pack(recovered)
    assert packed.scene_path == Path(entry.files['mb'])
    assert record.read_bytes() == before
    assert not service.has_pack_changes(recovered)
    assert len([p for p in service.paths.asset_publish_dir(assembly.identity, 'asset', 'proxy').glob('v*') if p.is_dir()]) == 1
    stage = Usd.Stage.Open(str(packed.usd_path))
    assert stage.GetPrimAtPath('/schoolRoom/body')
    with pytest.raises(ValueError, match='already packed'):
        service.pack(recovered)


def test_failed_current_release_is_not_discovered(service):
    from smartlib.apps.asset_manager.environment_release import release_current_scene, latest_release
    assembly = release(service)
    source = service.paths.asset_work_root(assembly.identity) / 'test.mb'
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b'scene')
    def fail(target):
        raise RuntimeError('export failed')
    with pytest.raises(RuntimeError, match='export failed'):
        release_current_scene(service, assembly, source, fail)
    assert latest_release(service, assembly) is assembly
    assert not service.paths.asset_usd_root(assembly.identity).exists()


def test_resolve_common_pack_for_selected_release(service):
    first = service.pack(release(service))
    second = service.pack(release(service, version='v002', offset=.5))
    resolver = AssetPublishResolver(service.project_config)
    identity = AssetIdentity('environment','school','schoolRoom','A')
    assert resolver.resolve_usd_entry(identity, release_path=first.scene_path)['path'] == str(first.usd_path)
    assert resolver.resolve_usd_entry(identity, release_path=second.scene_path)['path'] == str(second.usd_path)
    assert resolver.resolve_usd_entry(identity)['path'] == str(second.usd_path)
    with pytest.raises(ValueError, match='No completed'):
        resolver.resolve_usd_entry(identity, release_path=first.scene_path.with_name('unpublished.mb'))
    with pytest.raises(ValueError, match='does not reference'):
        resolver.resolve_usd_entry(identity, version=second.usd_path.parent.name, release_path=first.scene_path)
    first.scene_path.write_bytes(b'changed')
    with pytest.raises(ValueError, match='modified'):
        resolver.resolve_usd_entry(identity, release_path=first.scene_path)
