"""Save gate and immutable scene copy for detached Publish workers."""
from pathlib import Path
import shutil

from smartlib.apps.shot_manager.animation_publish import file_hash


def file_ref(path):
    path = Path(path).resolve()
    if not path.is_file():
        raise ValueError('Missing scene dependency: ' + str(path))
    return dict(path=path.as_posix(), sha256=file_hash(path))


def validate_scene_input(data):
    for ref in [data['scene']] + data.get('dependencies', []):
        if file_ref(ref['path']) != ref:
            raise ValueError('Saved scene input changed: ' + ref['path'])


def capture_saved_scene(service, identity, cmds=None):
    if cmds is None:
        import maya.cmds as cmds
    source = cmds.file(query=True, sceneName=True) or ''
    if not source:
        raise ValueError('Save the Maya scene before submitting Publish.')
    if cmds.file(query=True, modified=True):
        answer = cmds.confirmDialog(title='Save before Publish',
            message='The scene has unsaved changes. Save and submit?',
            button=['Save and Submit', 'Cancel'], defaultButton='Save and Submit',
            cancelButton='Cancel', dismissString='Cancel')
        if answer != 'Save and Submit':
            raise ValueError('Publish cancelled. The scene was not saved.')
        cmds.file(save=True)
    if cmds.file(query=True, modified=True):
        raise ValueError('The Maya scene still has unsaved changes.')
    source = Path(cmds.file(query=True, sceneName=True)).resolve()
    if source.suffix.lower() not in ('.ma', '.mb'):
        raise ValueError('Save a Maya .ma or .mb scene before Publish.')
    original = file_ref(source)
    dependencies = {}
    # Maya's file list includes scene references and registered file textures.
    for value in cmds.file(query=True, list=True, withoutCopyNumber=True) or []:
        if Path(value).resolve() == source:
            continue
        for path in service.paths.dependency_files(value):
            ref = file_ref(path)
            dependencies[ref['path']] = ref
    _, directory = service._reserve(service.paths.usd_handoff_build_dir(*service._identity(identity)))
    destination = service.paths.artifact_file(directory, 'source_scene' + source.suffix.lower())
    shutil.copy2(source, destination)
    copied = file_ref(destination)
    if copied['sha256'] != original['sha256'] or file_ref(source) != original:
        raise ValueError('Scene changed while preparing its Publish snapshot.')
    result = dict(scene=copied, source=original, dependencies=list(dependencies.values()),
                  workspace=cmds.workspace(query=True, rootDirectory=True))
    validate_scene_input(result)
    return result
