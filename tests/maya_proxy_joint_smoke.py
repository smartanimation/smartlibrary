"""Joint-ancestor static export regression; synthetic files only under .tmp."""
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'packages'))


def main():
    import maya.standalone
    maya.standalone.initialize(name='python')
    try:
        import maya.cmds as cmds
        from pxr import Usd, UsdGeom, Gf
        from smartlib.dcc.maya.proxy_usd import export_proxy_usd
        cmds.file(new=True, force=True)
        cmds.currentUnit(linear='cm')
        root = cmds.group(empty=True, name='Root')
        joint = cmds.createNode('joint', name='layoutJoint', parent=root)
        meshes = []
        for i in range(5):
            mesh = cmds.polyCube(name='part_' + str(i))[0]
            cmds.parent(mesh, joint)
            cmds.setAttr(mesh + '.translateX', i * 2)
            meshes.append(mesh)
        cmds.sets(meshes, name='cache_geo_set')
        cmds.parent(cmds.circle(name='control')[0], joint)
        cmds.setAttr(root + '.translate', 17, 3, -5)
        cmds.setAttr(root + '.scale', 2, 1, 3)
        cmds.setAttr(joint + '.translateY', 4)
        cmds.select(joint)
        before = cmds.ls(selection=True, long=True)
        directory = ROOT / '.tmp' / ('proxy-joints-' + uuid.uuid4().hex)
        directory.mkdir(parents=True)
        target = directory / 'proxy.usda'
        receipt = export_proxy_usd(target, {})
        assert receipt['mesh_count'] == 5
        assert cmds.ls(selection=True, long=True) == before
        assert cmds.nodeType(joint) == 'joint'
        stage = Usd.Stage.Open(str(target))
        assert stage.GetDefaultPrim().GetName() == 'Root'
        assert not any(p.GetTypeName() in ('Skeleton', 'SkelAnimation', 'BasisCurves', 'NurbsCurves') for p in stage.Traverse())
        cache = UsdGeom.XformCache()
        for mesh in meshes:
            prim = stage.GetPrimAtPath('/Root/' + mesh)
            points = UsdGeom.Mesh(prim).GetPointsAttr().Get()
            matrix = cache.GetLocalToWorldTransform(prim)
            for index, point in enumerate(points):
                actual = matrix.Transform(Gf.Vec3d(*point))
                expected = cmds.pointPosition(mesh + '.vtx[%d]' % index, world=True)
                assert max(abs(a-b) for a,b in zip(actual, expected)) < 1e-5
        # Removing the source root matrix should leave root-local geometry for
        # Shot placements, not points with an extra world-space root offset.
        UsdGeom.Xformable(stage.GetDefaultPrim()).MakeMatrixXform().Set(Gf.Matrix4d(1))
        cache.Clear()
        local = cache.GetLocalToWorldTransform(stage.GetPrimAtPath('/Root/part_0')).ExtractTranslation()
        assert abs(local[0]) < 1e-6 and abs(local[1] - 4) < 1e-6
        print('PASS: joint descendants, mesh-only export, world points, root-local placement, selection restoration')
    finally:
        maya.standalone.uninitialize()


if __name__ == '__main__':
    main()
