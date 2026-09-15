"""Run with mayapy from the repository root. All fixtures stay under .tmp."""
from datetime import datetime
from pathlib import Path
import sys
sys.path.insert(0, str(Path.cwd() / 'packages'))
import maya.standalone
maya.standalone.initialize(name='python')
try:
    from maya import cmds
    from smartlib.dcc.maya.reference_editor import Operation, apply, snapshot, scan
    root = Path.cwd() / '.tmp' / ('reference-editor-maya-' + datetime.now().strftime('%Y%m%d%H%M%S'))
    root.mkdir(parents=True)
    old, new = root / 'old.ma', root / 'new.ma'
    for path in (old, new):
        cmds.file(new=True, force=True)
        cmds.createNode('transform', name='control')
        cmds.file(rename=str(path))
        cmds.file(save=True, type='mayaAscii', force=True)
    cmds.file(new=True, force=True)
    result = apply(cmds, snapshot(cmds), [Operation('add', 'hero', str(old)), Operation('add', 'hero2', str(old))])
    assert not result.errors, result.errors
    assert len(scan(cmds)) == 2
    cmds.setAttr('hero:control.tx', 12)
    cmds.setKeyframe('hero:control.ty', time=1, value=3)
    cmds.setKeyframe('hero:control.ty', time=10, value=9)
    ref = next(ref for ref in scan(cmds) if ref.namespace == 'hero')
    result = apply(cmds, snapshot(cmds), [Operation('replace', 'hero', str(new), ref)])
    assert not result.errors, result.errors
    assert cmds.getAttr('hero:control.tx') == 12
    assert cmds.keyframe('hero:control.ty', query=True, valueChange=True) == [3, 9]
    assert next(ref for ref in scan(cmds) if ref.namespace == 'hero2').path == str(old).replace('\\', '/')
    ref = next(ref for ref in scan(cmds) if ref.namespace == 'hero2')
    cmds.file(unloadReference=ref.node)
    ref = next(ref for ref in scan(cmds) if ref.namespace == 'hero2')
    result = apply(cmds, snapshot(cmds), [Operation('replace', 'hero2', str(new), ref)])
    assert not result.errors, result.errors
    assert not cmds.referenceQuery(ref.node, isLoaded=True)
    assert cmds.referenceQuery(ref.node, filename=True, withoutCopyNumber=True) == str(new).replace('\\', '/')
    # Nested references must be visible but not independently replaceable.
    nested = root / 'nested.ma'
    cmds.file(new=True, force=True)
    cmds.file(str(old), reference=True, namespace='inside')
    cmds.file(rename=str(nested))
    cmds.file(save=True, type='mayaAscii', force=True)
    cmds.file(new=True, force=True)
    result = apply(cmds, snapshot(cmds), [Operation('add', 'outer', str(nested))])
    assert not result.errors, result.errors
    refs = scan(cmds)
    assert len(refs) == 2 and sum(ref.nested for ref in refs) == 1
    child = next(ref for ref in refs if ref.nested)
    try:
        apply(cmds, snapshot(cmds), [Operation('replace', child.namespace, str(new), child)])
    except RuntimeError:
        pass
    else:
        raise AssertionError('Nested replacement was accepted')
    print('REFERENCE_EDITOR_MAYA_SMOKE_OK', root, flush=True)
finally:
    maya.standalone.uninitialize()
