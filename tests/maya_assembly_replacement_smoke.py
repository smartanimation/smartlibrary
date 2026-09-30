"""Synthetic end-to-end group replacement regression (run with mayapy)."""
from pathlib import Path
from types import SimpleNamespace
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'packages'))


def main():
    import maya.standalone
    maya.standalone.initialize(name='python')
    try:
        import maya.cmds as cmds
        import maya.api.OpenMaya as om
        from smartlib.dcc.maya.usd_skel import _ensure_maya_usd_plugin
        _ensure_maya_usd_plugin(cmds)
        from pxr import Usd, UsdGeom
        from smartlib.core.path_resolver import AssetIdentity, configured_project_paths
        from smartlib.dcc.maya.assembly_replacement import (
            replace_group, restore_group, list_replacements, replacement_contract, place_new_group)
        from smartlib.dcc.maya.proxy_usd import export_proxy_usd

        directory = ROOT / '.tmp' / 'assembly-replacement' / uuid.uuid4().hex
        directory.mkdir(parents=True)
        config = SimpleNamespace(project_root=directory, templates={}, project_name='Test', base={}, load=lambda _: {})
        paths = configured_project_paths(directory, config)
        identity = AssetIdentity('prop', 'main', 'DeleinChair', 'default')
        for version, names in [('v005', ['seat', 'legs']), ('v006', ['newSeat', 'newLegs', 'back'])]:
            cmds.file(new=True, force=True)
            root = cmds.createNode('transform', name='Root')
            joint = cmds.createNode('joint', name='rigJoint', parent=root)
            output = []
            shader = cmds.shadingNode('lambert', asShader=True, name='chairMaterial')
            sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name='chairMaterialSG')
            cmds.connectAttr(shader + '.outColor', sg + '.surfaceShader')
            for index, name in enumerate(names):
                mesh = cmds.polyCube(name=name, subdivisionsX=2 if version == 'v006' else 1)[0]
                cmds.parent(mesh, joint)
                cmds.setAttr(mesh + '.translate', index * 3, 2, 0)
                cmds.sets(mesh + '.f[0:2]', edit=True, forceElement=sg)
                if index == 0:
                    morph = cmds.duplicate(mesh, name='morphTarget')[0]
                    cmds.move(0, 1.5, 0, morph + '.vtx[0]', relative=True)
                    blend = cmds.blendShape(morph, mesh)[0]
                    cmds.setAttr(blend + '.weight[0]', .7)
                    cmds.delete(morph)
                output.append(mesh)
            cmds.parent(cmds.circle(name='control')[0], root)
            cmds.sets(output, name='cache_geo_set')
            destination = paths.asset_publish_version_dir(identity, 'asset', 'anim', version)
            destination.mkdir(parents=True)
            source = paths.artifact_file(destination, 'DeleinChair_default.mb')
            cmds.file(rename=str(source))
            cmds.file(save=True, type='mayaBinary')
        cmds.file(new=True, force=True)
        invalid = paths.asset_publish_version_dir(identity, 'asset', 'anim', 'v007')
        invalid.mkdir(parents=True)
        cmds.file(rename=str(paths.artifact_file(invalid, 'DeleinChair_default.mb')))
        cmds.file(save=True, type='mayaBinary')
        cmds.file(new=True, force=True)
        root = cmds.createNode('transform', name='Root')
        geo = cmds.createNode('transform', name='geo_grp', parent=root)
        props = cmds.createNode('transform', name='prop_geo_grp', parent=geo)
        chair = cmds.createNode('transform', name='chair_geo_grp', parent=props)
        old = cmds.polyCube(name='chair_1_geo')[0]
        cmds.parent(old, chair)
        wall = cmds.polyCube(name='wall_geo')[0]
        cmds.parent(wall, geo)
        cmds.setAttr(root + '.translate', 12, 4, -8)
        cmds.setAttr(root + '.rotateY', 23)
        cmds.setAttr(props + '.scale', 1, 2, 1)
        cmds.setAttr(chair + '.translate', 7, 0, 2)
        cmds.setAttr(chair + '.rotateY', 37)
        cmds.sets(geo, name='cache_geo_set')
        target = cmds.ls(chair, long=True)[0]
        original_matrix = cmds.xform(target, query=True, worldSpace=True, matrix=True)
        try:
            replace_group(config, target, identity, version='v007')
            raise AssertionError('Expected invalid prop rejection')
        except ValueError as exc:
            assert 'cache_geo_set' in str(exc)
        assert cmds.objExists(target + '|chair_1_geo')
        assert not list_replacements()
        # Fail after original children have been archived and metadata connected.
        from smartlib.dcc.maya import assembly_replacement as replacement_module
        original_plan = replacement_module.export_plan
        replacement_module.export_plan = lambda *args: (_ for _ in ()).throw(ValueError('injected plan failure'))
        try:
            try:
                replace_group(config, target, identity, version='v005')
                raise AssertionError('Expected late replacement failure')
            except ValueError as exc:
                assert 'injected' in str(exc)
            assert cmds.objExists(target + '|chair_1_geo')
            assert not list_replacements()
            assert not cmds.objExists(target + '|__assemblyOriginal')
        finally:
            replacement_module.export_plan = original_plan
        replace_group(config, target, identity, version='v005')
        assert cmds.namespace(exists=':DeleinChair')
        assert cmds.objExists(target + '|DeleinChair_GRP')
        # Migrate the previous UUID namespace without reloading or losing edits.
        reference = replacement_contract(cmds, list_replacements())['replacements'][0]['reference']
        fixed_id = list_replacements()[0]['id']
        cmds.setAttr('DeleinChair:control.translateZ', 3)
        filename = cmds.referenceQuery(reference, filename=True)
        cmds.file(filename, edit=True, namespace=':assemblyProp_legacy')
        before_nodes = cmds.ls(type='reference')
        migrated = replacement_module.rename_group_reference(target)
        assert migrated['namespace'] == 'DeleinChair'
        assert migrated['id'] == fixed_id
        assert cmds.getAttr('DeleinChair:control.translateZ') == 3
        assert cmds.ls(type='reference') == before_nodes
        from smartlib.dcc.maya.shot_builder import _matching_namespaces, _reference_file
        assert not _matching_namespaces(cmds, 'DeleinChair')
        try:
            _reference_file(cmds, Path(filename), 'DeleinChair')
            raise AssertionError('Expected conflicting Cast namespace rejection')
        except RuntimeError as exc:
            assert 'Assembly prop' in str(exc)
        assert cmds.xform(target, query=True, worldSpace=True, matrix=True) == original_matrix
        assert cmds.objExists(target + '|__assemblyOriginal|chair_1_geo')
        saved = directory / 'background.mb'
        cmds.file(rename=str(saved))
        cmds.file(save=True, type='mayaBinary')
        cmds.file(str(saved), open=True, force=True)
        assert len(list_replacements()) == 1
        cmds.select(target)
        before = cmds.ls(selection=True, long=True)
        reference = replacement_contract(cmds, list_replacements())['replacements'][0]['reference']
        edits_before = cmds.referenceQuery(reference, editStrings=True)
        usd = directory / 'background.usda'
        receipt = export_proxy_usd(usd, {})
        assert receipt['mesh_count'] == 3, receipt
        assert cmds.ls(selection=True, long=True) == before
        assert not cmds.file(query=True, modified=True)
        assert cmds.referenceQuery(reference, editStrings=True) == edits_before, 'Export modified source reference edits'
        stage = Usd.Stage.Open(str(usd))
        public = '/Root/geo_grp/prop_geo_grp/chair_geo_grp'
        assert stage.GetPrimAtPath(public)
        assert stage.GetPrimAtPath(public + '/seat')
        assert stage.GetPrimAtPath(public + '/legs')
        assert stage.GetPrimAtPath('/Root/geo_grp/wall_geo')
        assert not any('__assembly' in str(p.GetPath()) or 'rigJoint' in str(p.GetPath()) for p in stage.Traverse())
        contract = replacement_contract(cmds, list_replacements())
        from smartlib.dcc.maya.preflight import MayaPreflightAdapter
        assert not MayaPreflightAdapter().publish_geometry_visibility_issues('cache_geo_set')
        xforms = UsdGeom.XformCache()
        for source in contract['replacements'][0]['meshes']:
            name = source.rsplit('|', 2)[-2].split(':')[-1]
            mesh = UsdGeom.Mesh(stage.GetPrimAtPath(public + '/' + name))
            matrix = xforms.GetLocalToWorldTransform(mesh.GetPrim())
            from pxr import Gf
            for index, point in enumerate(mesh.GetPointsAttr().Get()):
                actual = matrix.Transform(Gf.Vec3d(*point))
                expected = cmds.pointPosition(source + '.vtx[%d]' % index, world=True)
                assert max(abs(a-b) for a,b in zip(actual, expected)) < 1e-4, (actual, expected)
            assert UsdGeom.PrimvarsAPI(mesh).GetPrimvar('st').Get()
            selection = om.MSelectionList()
            selection.add(source)
            source_fn = om.MFnMesh(selection.getDagPath(0))
            counts, indices = source_fn.getVertices()
            assert list(mesh.GetFaceVertexCountsAttr().Get()) == list(counts)
            assert list(mesh.GetFaceVertexIndicesAttr().Get()) == list(indices)
            uv_counts, uv_indices = source_fn.getAssignedUVs()
            u, v = source_fn.getUVs()
            actual_uvs = UsdGeom.PrimvarsAPI(mesh).GetPrimvar('st').ComputeFlattened()
            expected_uvs = [(u[index], v[index]) for index in uv_indices]
            assert len(actual_uvs) == len(expected_uvs)
            assert all(max(abs(a-b) for a, b in zip(actual, expected)) < 1e-6
                       for actual, expected in zip(actual_uvs, expected_uvs))
            subsets = UsdGeom.Subset.GetAllGeomSubsets(mesh)
            assert sorted(sorted(subset.GetIndicesAttr().Get()) for subset in subsets) == [[0, 1, 2], [3, 4, 5]]
            material_faces = next(subset for subset in subsets if 'chairMaterialSG' in subset.GetPrim().GetName())
            assert list(material_faces.GetIndicesAttr().Get()) == [0, 1, 2]
            assert material_faces.GetPrim().GetRelationship('material:binding').GetTargets()
        stable_id = list_replacements()[0]['id']
        refs_before = cmds.ls(type='reference')
        try:
            replace_group(config, target, identity, version='v007')
            raise AssertionError('Expected invalid update rejection')
        except ValueError:
            pass
        assert list_replacements()[0]['version'] == 'v005'
        assert cmds.ls(type='reference') == refs_before
        replace_group(config, target, identity, version='v006')
        assert list_replacements()[0]['id'] == stable_id
        receipt = export_proxy_usd(directory / 'updated.usda', {})
        assert receipt['mesh_count'] == 4
        second = cmds.createNode('transform', name='chairB_geo_grp', parent=props)
        cmds.setAttr(second + '.translateX', -12)
        second = cmds.ls(second, long=True)[0]
        replace_group(config, second, identity, version='v005')
        assert list_replacements()[1]['namespace'] in {'DeleinChair', 'DeleinChair_2'}
        assert cmds.namespace(exists=':DeleinChair') and cmds.namespace(exists=':DeleinChair_2')
        assert not _matching_namespaces(cmds, 'DeleinChair')
        receipt = export_proxy_usd(directory / 'two-chairs.usda', {})
        assert receipt['mesh_count'] == 6
        # An exporter failure must leave no temporary nodes, selection or edits.
        original_export = cmds.mayaUSDExport
        cmds.mayaUSDExport = lambda **_: (_ for _ in ()).throw(RuntimeError('injected export failure'))
        state = cmds.file(query=True, modified=True)
        try:
            try:
                export_proxy_usd(directory / 'failed.usda', {})
                raise AssertionError('Expected exporter failure')
            except RuntimeError as exc:
                assert 'injected' in str(exc)
            assert not cmds.ls('__assemblyPublish_*:*')
            assert cmds.file(query=True, modified=True) == state
            assert len(list_replacements()) == 2
        finally:
            cmds.mayaUSDExport = original_export
        # Moving a registered group must block publication rather than silently
        # changing a downstream public path.
        moved = cmds.rename(second, 'movedChair')
        try:
            export_proxy_usd(directory / 'renamed.usda', {})
            raise AssertionError('Expected renamed group rejection')
        except ValueError as exc:
            assert 'renamed or moved' in str(exc)
        second = cmds.rename(moved, 'chairB_geo_grp')
        second = cmds.ls(second, long=True)[0]
        restore_group(second)
        restore_group(target)
        assert not list_replacements()
        children = cmds.listRelatives(props, children=True, fullPath=True)
        try:
            place_new_group(config, props, identity, version='v007')
            raise AssertionError('Expected invalid new placement rejection')
        except ValueError:
            pass
        assert cmds.listRelatives(props, children=True, fullPath=True) == children
        placed = place_new_group(config, props, identity, version='v005')
        assert placed['target_path'].startswith(cmds.ls(props, long=True)[0] + '|')
        receipt = export_proxy_usd(directory / 'new-placement.usda', {})
        assert receipt['mesh_count'] == 4
        restore_group(placed['target_path'])
        # A real top-level Cast namespace and its nodes are never merged into
        # or renamed by a new Assembly placement.
        cmds.namespace(add='DeleinChair')
        cast_node = cmds.createNode('transform', name='DeleinChair:CastRoot')
        placed = place_new_group(config, props, identity, version='v005')
        assert placed['namespace'] == 'DeleinChair_2'
        assert cmds.objExists(cast_node)
        assert _matching_namespaces(cmds, 'DeleinChair') == ['DeleinChair']
        restore_group(placed['target_path'])
        assert cmds.objExists(target + '|chair_1_geo')
        assert cmds.xform(target, query=True, worldSpace=True, matrix=True) == original_matrix
        print('PASS: fixed references, save/reopen, group paths, world vertices, UVs, update, restore, source preservation')
    finally:
        maya.standalone.uninitialize()


if __name__ == '__main__':
    main()
