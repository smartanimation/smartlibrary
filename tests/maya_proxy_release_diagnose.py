"""Read a failed Release without saving it; all diagnostics stay in .tmp."""
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'packages'))


def main(source):
    import maya.standalone
    maya.standalone.initialize(name='python')
    try:
        import maya.cmds as cmds
        from pxr import Usd, UsdGeom
        from smartlib.dcc.maya.proxy_usd import validate_proxy_scene
        from smartlib.dcc.maya.usd_skel import _ensure_maya_usd_plugin
        cmds.file(source, open=True, force=True, executeScriptNodes=False)
        _ensure_maya_usd_plugin(cmds)
        contract = validate_proxy_scene({})
        print('CONTRACT', contract)
        parents = sorted({p for mesh in contract['meshes']
                          for p in cmds.listRelatives(mesh, parent=True, fullPath=True) or []})
        for mesh in contract['meshes']:
            print('MESH', mesh, 'history', cmds.listHistory(mesh),
                  'visibility', cmds.getAttr(mesh + '.visibility'))
            print('USER ATTRS', [(a, str(cmds.getAttr(mesh + '.' + a)))
                                for a in cmds.listAttr(mesh, userDefined=True) or []])
        directory = ROOT / '.tmp' / ('proxy-diagnose-' + uuid.uuid4().hex)
        directory.mkdir(parents=True)
        for name, selection in [('shapes', contract['meshes']), ('transforms', parents), ('explicit_roots', parents)]:
            path = directory / (name + '.usda')
            cmds.select(selection, replace=True, noExpand=True)
            options = dict(exportRoots=parents, worldspace=True, parentScope='Root') if name == 'explicit_roots' else {}
            cmds.mayaUSDExport(file=str(path), selection=True, exportSkels='none', exportSkin='none',
                exportBlendShapes=False, exportInstances=True, mergeTransformAndShape=True,
                stripNamespaces=False, defaultMeshScheme='none', **options)
            stage = Usd.Stage.Open(str(path))
            print(name, 'PRIMS', [(str(p.GetPath()), p.GetTypeName()) for p in stage.TraverseAll()])
        print('OUTPUT', directory)
        from smartlib.dcc.maya.proxy_usd import export_proxy_usd
        print('FIXED EXPORT', export_proxy_usd(directory / 'fixed.usda', {}))
    finally:
        maya.standalone.uninitialize()


if __name__ == '__main__':
    main(sys.argv[1])
