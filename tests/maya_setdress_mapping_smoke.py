"""Synthetic Asset correspondence and two referenced instances, confined to .tmp."""
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
        from pxr import Usd, UsdGeom
        from smartlib.dcc.maya.proxy_usd import export_proxy_usd
        from smartlib.dcc.maya.setdress_mapping import save, load, scene_nodes
        from smartlib.apps.shot_manager.setdress_mapping import resolve, prim_record
        from smartlib.apps.shot_manager import ShotIdentity
        from smartlib.dcc.maya.set_dress import _node_id, _resolve_node
        directory = ROOT / '.tmp' / ('setdress-mapping-' + uuid.uuid4().hex)
        directory.mkdir(parents=True)
        cmds.file(new=True, force=True)
        cmds.currentUnit(linear='cm', time='film')
        cmds.upAxis(axis='y')
        root = cmds.group(empty=True, name='Root')
        mesh = cmds.polyCube(name='seat')[0]
        cmds.parent(mesh, root)
        cmds.sets(root, name='cache_geo_set')
        native = directory / 'asset.ma'
        cmds.file(rename=str(native))
        cmds.file(save=True, type='mayaAscii', force=True)
        usd = directory / 'asset.usda'
        export_proxy_usd(usd, {})
        asset = Usd.Stage.Open(str(usd))
        assert asset.GetPrimAtPath('/Root/seat').GetCustomDataByKey('smartpipeline:setdress:sourcePath') == '|Root|seat'
        cmds.file(new=True, force=True)
        nodes = {}
        stage = Usd.Stage.CreateInMemory()
        cast = {}
        for namespace in ('A', 'B'):
            cmds.file(str(native), reference=True, namespace=namespace)
            node = '|%s:Root|%s:seat' % (namespace, namespace)
            key = _node_id(cmds, node)
            nodes[key] = node
            prim = UsdGeom.Xform.Define(stage, '/Shot/Assets/' + namespace).GetPrim()
            prim.GetReferences().AddReference(str(usd), '/Root')
            prim.SetCustomDataByKey('smartpipeline', dict(cast_key=namespace, name='Chair', usd_path=str(usd)))
            cast[namespace] = dict(namespace=namespace)
        assert len(nodes) == 2
        for key, node in nodes.items():
            assert _resolve_node(cmds, key, '') == node
        current, source_ids = scene_nodes(nodes, cmds)
        matches = resolve(stage, current, cast, source_ids=source_ids)
        assert {row['selected'] for row in matches.values()} == {'/Shot/Assets/A/seat', '/Shot/Assets/B/seat'}, (nodes, current, matches)
        assert all(row['state'] == 'Asset mapping' for row in matches.values())
        records = {key: prim_record(stage, row['selected']) for key, row in matches.items()}
        identity = ShotIdentity('ep01', 'sq01', 'c001')
        save(identity, records, cmds)
        shot = directory / 'shot.ma'
        cmds.file(rename=str(shot))
        cmds.file(save=True, type='mayaAscii', force=True)
        cmds.file(str(shot), open=True, force=True)
        assert load(identity, cmds) == records
        assert load(ShotIdentity('ep01', 'sq01', 'c002'), cmds) == {}
        print('PASS: exported correspondence, duplicate referenced Asset disambiguation, saved-scene mapping roundtrip', flush=True)
        print(directory, flush=True)
    finally:
        maya.standalone.uninitialize()


if __name__ == '__main__':
    main()
