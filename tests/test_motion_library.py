from pathlib import Path
import pytest
from smartlib.core.path_resolver import AssetIdentity
from smartlib.core.config_loader import ProjectConfig
from smartlib.apps.motion_library.service import MotionLibraryService

@pytest.fixture
def service(tmp_path):
    config=tmp_path/'config';config.mkdir()
    (config/'templates_base.yml').write_text(f"anchors:\n  project_name: ELCD\n  project_root: '{tmp_path.as_posix()}/project'\n",encoding='utf-8')
    return MotionLibraryService(ProjectConfig(config))

ID=AssetIdentity('character','mob','MBGm','default')

def test_paths_and_registration(service,tmp_path):
    source=tmp_path/'input.mb';source.write_bytes(b'maya')
    target=service.register_work(ID,'sit_idle_01',source,take='t02')
    assert target.name=='ELCD_MBGm_default_anim_sit_idle_01_v001_t02.mb'
    assert '/motion/character/mob/MBGm/default/work/anim/maya/sit_idle_01/' in target.as_posix()
    assert source.read_bytes()==target.read_bytes()
    with pytest.raises(FileExistsError):service.register_work(ID,'sit_idle_01',source,take='t02')
    with pytest.raises(ValueError):service.paths.motion_work_dir(ID,'../escape')

def test_publish_is_versioned_and_failed_export_is_not_latest(service,tmp_path):
    source=tmp_path/'input.mb';source.write_bytes(b'maya')
    work=service.register_work(ID,'walk_01',source)
    metadata=dict(frame_range=[1001,1024],fps=24,root_motion='in_place',forward_axis='+Z',skeleton_root='root',placement_anchor='root')
    def export(path,meta):path.write_bytes(b'fbx');return {'joint_count':1}
    first=service.publish(ID,'walk_01',work,metadata,export)
    assert first.parent.name=='v001'
    assert service.resolve_clip(ID,'walk_01')['files']['fbx'].endswith('MBGm_default_walk_01.fbx')
    def fail(*args):raise RuntimeError('export failure')
    with pytest.raises(RuntimeError):service.publish(ID,'walk_01',work,metadata,fail)
    assert service.resolve_clip(ID,'walk_01')['version']=='v001'
    assert service.publish(ID,'walk_01',work,metadata,export).parent.name=='v003'
    Path(service.resolve_clip(ID,'walk_01')['files']['fbx']).write_bytes(b'changed')
    with pytest.raises(ValueError,match='modified'):service.resolve_clip(ID,'walk_01')
