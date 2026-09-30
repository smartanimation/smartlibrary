"""Group-bound prop references and their static publish representation.

Only the target group and its ancestors are a public hierarchy contract. Meshes,
UVs and shading assignments are taken from the referenced cache_geo_set.
"""
from contextlib import contextmanager
from dataclasses import asdict
import json
from pathlib import Path
import uuid

from smartlib.core.asset_publish_resolver import AssetPublishResolver
from smartlib.core.path_resolver import configured_project_paths

ATTR = 'smartAssemblyReplacement'
REF_ATTR = 'smartAssemblyReplacementReference'
BACKUP_ATTR = 'smartAssemblyReplacementBackup'


def published_versions(config, identity, context='anim'):
    paths = configured_project_paths(config.project_root, config)
    return AssetPublishResolver(config).list_context_versions(
        paths.asset_variant_root(identity), context, formats=('mb', 'ma'))


def _set_record(cmds, node, record):
    if not cmds.attributeQuery(ATTR, node=node, exists=True):
        cmds.addAttr(node, longName=ATTR, dataType='string')
    cmds.setAttr(node + '.' + ATTR, json.dumps(record, sort_keys=True), type='string')


def _linked(cmds, node, attr):
    if not cmds.attributeQuery(attr, node=node, exists=True):
        return []
    return cmds.listConnections(node + '.' + attr, source=True, destination=False) or []


def _link(cmds, target, attr, source):
    if not cmds.attributeQuery(attr, node=target, exists=True):
        cmds.addAttr(target, longName=attr, attributeType='message')
    if not cmds.isConnected(source + '.message', target + '.' + attr):
        cmds.connectAttr(source + '.message', target + '.' + attr, force=True)


def _asset_namespace(cmds, asset, *, current=''):
    import re
    base = re.sub(r'[^A-Za-z0-9_]', '_', asset) or 'prop'
    if base[0].isdigit():
        base = 'n_' + base
    candidate, index = base, 2
    while candidate != current and cmds.namespace(exists=':' + candidate):
        candidate = f'{base}_{index}'
        index += 1
    return candidate


def assembly_reference_namespaces(cmds):
    """Ownership follows message connections, including nested background refs."""
    namespaces = set()
    for plug in cmds.ls('*.' + REF_ATTR, recursive=True) or []:
        for reference in cmds.listConnections(plug, source=True, destination=False) or []:
            if cmds.nodeType(reference) == 'reference':
                namespaces.add(cmds.referenceQuery(reference, namespace=True).strip(':'))
    return namespaces


def rename_group_reference(target):
    """Change display names without reloading the rig or changing its Assembly ID."""
    import maya.cmds as cmds
    matches = cmds.ls(target, long=True) or []
    if len(matches) != 1 or cmds.referenceQuery(matches[0], isNodeReferenced=True):
        raise ValueError('Select a local Assembly placement group.')
    target = matches[0]
    rows = [row for row in list_replacements() if row['target'] == target]
    if not rows:
        raise ValueError('Select a registered Assembly placement group.')
    row = validate_replacements(cmds, rows)[0]
    reference = row['reference']
    current = cmds.referenceQuery(reference, namespace=True).lstrip(':')
    namespace = _asset_namespace(cmds, row['asset']['name'], current=current)
    # The copy-number-qualified path identifies this reference even when the
    # same prop publish is placed more than once in the background.
    if namespace != current:
        filename = cmds.referenceQuery(reference, filename=True)
        cmds.file(filename, edit=True, namespace=':' + namespace)
    # Locate the reference wrapper by its owned output mesh, never its old name.
    _, meshes = _reference_geometry(cmds, reference)
    wrapper = target + '|' + meshes[0][len(target) + 1:].split('|')[0]
    if all(mesh.startswith(wrapper + '|') for mesh in meshes):
        cmds.rename(wrapper, ':' + namespace + '_GRP')
    record = {key: value for key, value in row.items()
              if key not in {'target', 'reference', 'geometry_set', 'meshes'}}
    record['namespace'] = cmds.referenceQuery(reference, namespace=True).lstrip(':')
    _set_record(cmds, target, record)
    return record


def list_replacements():
    import maya.cmds as cmds
    rows = []
    for plug in cmds.ls('*.' + ATTR, recursive=True) or []:
        node = (cmds.ls(plug.rsplit('.', 1)[0], long=True) or [''])[0]
        row = json.loads(cmds.getAttr(plug))
        rows.append(dict(row, target=node))
    return sorted(rows, key=lambda row: row['target'])


def collect_meshes(cmds, geometry_set):
    meshes = set()
    def collect(name, ancestry):
        if name in ancestry:
            raise ValueError('cache_geo_set contains a nested set cycle.')
        for member in cmds.sets(name, query=True) or []:
            if '.' in member:
                raise ValueError('cache_geo_set requires whole geometry: ' + member)
            matches = cmds.ls(member, long=True) or []
            if not matches:
                raise ValueError('Missing geometry: ' + member)
            for node in matches:
                kind = cmds.nodeType(node)
                if kind == 'objectSet':
                    collect(node, ancestry + [name])
                elif kind in {'transform', 'joint'}:
                    shapes = cmds.listRelatives(node, allDescendents=True, fullPath=True) or []
                    meshes.update(n for n in shapes if cmds.nodeType(n) == 'mesh'
                                  and not cmds.getAttr(n + '.intermediateObject'))
                elif kind == 'mesh' and not cmds.getAttr(node + '.intermediateObject'):
                    meshes.add(node)
                else:
                    raise ValueError('Unsupported geometry member: ' + member)
    collect(geometry_set, [])
    if not meshes:
        raise ValueError('No exportable meshes in ' + geometry_set)
    return sorted(meshes)


def _reference_geometry(cmds, reference):
    if not cmds.referenceQuery(reference, isLoaded=True):
        raise ValueError('Load the prop reference before publishing: ' + reference)
    namespace = cmds.referenceQuery(reference, namespace=True).lstrip(':')
    geometry_set = namespace + ':cache_geo_set'
    if not cmds.objExists(geometry_set) or cmds.nodeType(geometry_set) != 'objectSet':
        raise ValueError('Prop requires its own cache_geo_set: ' + geometry_set)
    meshes = collect_meshes(cmds, geometry_set)
    return geometry_set, meshes


def replace_group(config, target, identity, *, context='anim', version):
    """Resolve a fixed publish; keep the group and reversibly archive old children."""
    import maya.cmds as cmds
    from .usd_skel import _ensure_maya_usd_plugin
    if identity.category != 'prop':
        raise ValueError('Group replacement requires a prop asset.')
    paths = configured_project_paths(config.project_root, config)
    resolver = AssetPublishResolver(config)
    versions = published_versions(config, identity, context)
    if version not in {row['version'] for row in versions}:
        raise ValueError('Select a concrete published version.')
    source = resolver.resolve_context(paths.asset_variant_root(identity), context,
                                      version=version, formats=('mb', 'ma'))
    if source is None or not source.is_file():
        raise ValueError('Published prop was not found.')
    matches = cmds.ls(target, long=True) or []
    if len(matches) != 1 or cmds.nodeType(matches[0]) != 'transform':
        raise ValueError('Select exactly one background group.')
    target = matches[0]
    if cmds.referenceQuery(target, isNodeReferenced=True):
        raise ValueError('Open the background work scene; the target group must be local.')
    if cmds.listRelatives(target, shapes=True):
        raise ValueError('Select a group, not a mesh transform.')
    if cmds.lockNode(target, query=True, lock=True)[0]:
        raise ValueError('Unlock the target group before replacement.')
    rows = list_replacements()
    for row in rows:
        if row['target'] != target and (row['target'].startswith(target + '|')
                                      or target.startswith(row['target'] + '|')):
            raise ValueError('Replacement groups cannot contain another replacement.')
    old = next((row for row in rows if row['target'] == target), None)
    old_ref = None
    if old:
        validate_replacements(cmds, [old])
        old_ref = _linked(cmds, target, REF_ATTR)[0]
        if len(_linked(cmds, target, BACKUP_ATTR)) != 1:
            raise ValueError('Original geometry backup is missing.')
    children = cmds.listRelatives(target, children=True, fullPath=True) or []
    if not old and any(cmds.referenceQuery(child, isNodeReferenced=True)
                       or cmds.lockNode(child, query=True, lock=True)[0] for child in children):
        raise ValueError('Original group children must be local and unlocked.')
    # Load the plug-in before opening a referenced scene or using pxr. Maya's
    # asset resolver must be initialized before USD context data is created.
    _ensure_maya_usd_plugin(cmds)
    # Load and validate before changing any existing geometry or reference.
    namespace = _asset_namespace(cmds, identity.name)
    reference, backup, ref_group = None, None, None
    before_refs = set(cmds.ls(type='reference') or [])
    try:
        new_nodes = cmds.file(str(source), reference=True, namespace=':' + namespace,
                              groupReference=True, groupName=namespace + '_GRP',
                              returnNewNodes=True)
        if cmds.objExists(namespace + ':cache_geo_set'):
            reference = cmds.referenceQuery(namespace + ':cache_geo_set', referenceNode=True)
        if not reference:
            raise ValueError('Referenced prop has no cache_geo_set.')
        _, meshes = _reference_geometry(cmds, reference)
        roots = cmds.ls(new_nodes, assemblies=True, long=True) or []
        if len(roots) != 1:
            raise ValueError('Expected one grouped prop reference.')
        ref_group = roots[0]
        if not all(mesh.startswith(ref_group + '|') for mesh in meshes):
            raise ValueError('All prop output meshes must belong to the grouped reference.')
        # Relative parenting aligns the authored prop origin to the target pivot.
        ref_group = cmds.parent(ref_group, target, relative=True)[0]
        if not old:
            backup = cmds.createNode('transform', name='__assemblyOriginal', parent=target)
            for child in children:
                cmds.parent(child, backup, absolute=True)
            cmds.setAttr(backup + '.visibility', False)
            _link(cmds, target, BACKUP_ATTR, backup)
        record = dict(schema='smartpipeline.assembly_replacement.v1',
                      id=old['id'] if old else uuid.uuid4().hex,
                      target_path=old['target_path'] if old else target,
                      asset=asdict(identity), context=context, version=version,
                      source_path=str(source))
        _set_record(cmds, target, record)
        _link(cmds, target, REF_ATTR, reference)
        # Ensure names can be published before committing the replacement.
        current = next(row for row in list_replacements() if row['target'] == target)
        validated = validate_replacements(cmds, [current])
        export_plan(cmds, {'meshes': validated[0]['meshes'], 'replacements': validated})
        if old_ref:
            cmds.file(removeReference=True, referenceNode=old_ref)
    except Exception:
        added_refs = set(cmds.ls(type='reference') or []) - before_refs
        # Remove the parent reference first, including its nested references.
        ordered = ([reference] if reference else []) + sorted(added_refs - {reference})
        for added in ordered:
            if cmds.objExists(added):
                cmds.file(removeReference=True, referenceNode=added)
        if backup and cmds.objExists(backup):
            for child in cmds.listRelatives(backup, children=True, fullPath=True) or []:
                cmds.parent(child, target, absolute=True)
            cmds.delete(backup)
        if old:
            _set_record(cmds, target, {key: value for key, value in old.items() if key != 'target'})
            _link(cmds, target, REF_ATTR, old_ref)
        else:
            for attr in (ATTR, REF_ATTR, BACKUP_ATTR):
                if cmds.attributeQuery(attr, node=target, exists=True):
                    cmds.deleteAttr(target + '.' + attr)
        raise
    # The replacement is committed. A cosmetic rename must never roll back a
    # valid replacement after its previous reference has already been removed.
    try:
        record = rename_group_reference(target)
    except (RuntimeError, ValueError) as exc:
        cmds.warning('Assembly reference was replaced, but display naming failed: ' + str(exc))
    cmds.select(target, replace=True)
    return record


def place_new_group(config, parent, identity, *, context='anim', version):
    """Create a public placement group under a selected background parent."""
    import re
    import maya.cmds as cmds
    matches = cmds.ls(parent, long=True) or []
    if len(matches) != 1 or cmds.nodeType(matches[0]) != 'transform':
        raise ValueError('Select one background parent group for the new placement.')
    parent = matches[0]
    if cmds.referenceQuery(parent, isNodeReferenced=True) or cmds.listRelatives(parent, shapes=True):
        raise ValueError('The placement parent must be a local background group.')
    name = re.sub(r'[^A-Za-z0-9_]', '_', identity.name) + '_geo_grp'
    node = cmds.createNode('transform', name=name, parent=parent)
    node = cmds.ls(node, long=True)[0]
    try:
        return replace_group(config, node, identity, context=context, version=version)
    except Exception:
        if cmds.objExists(node):
            cmds.delete(node)
        raise


def restore_group(target):
    import maya.cmds as cmds
    rows = [row for row in list_replacements() if row['target'] == target]
    if len(rows) != 1:
        raise ValueError('Select a registered replacement group.')
    backups = _linked(cmds, target, BACKUP_ATTR)
    if len(backups) != 1:
        raise ValueError('Original geometry backup is missing.')
    for reference in _linked(cmds, target, REF_ATTR):
        cmds.file(removeReference=True, referenceNode=reference)
    backup = backups[0]
    for child in cmds.listRelatives(backup, children=True, fullPath=True) or []:
        cmds.parent(child, target, absolute=True)
    cmds.delete(backup)
    for attr in (ATTR, REF_ATTR, BACKUP_ATTR):
        cmds.deleteAttr(target + '.' + attr)
    cmds.select(target, replace=True)


def validate_replacements(cmds, rows):
    result = []
    for row in rows:
        target = row['target']
        if target != row['target_path']:
            raise ValueError('Public replacement group was renamed or moved: ' + target)
        references = _linked(cmds, target, REF_ATTR)
        if len(references) != 1:
            raise ValueError('Replacement reference is missing: ' + target)
        reference = references[0]
        actual = cmds.referenceQuery(reference, filename=True, withoutCopyNumber=True)
        if Path(actual).resolve() != Path(row['source_path']).resolve():
            raise ValueError('Reference changed outside Asset Assembly: ' + target)
        if not Path(actual).is_file():
            raise ValueError('Prop publish is missing: ' + actual)
        geometry_set, meshes = _reference_geometry(cmds, reference)
        if not all(mesh.startswith(target + '|') for mesh in meshes):
            raise ValueError('Prop geometry moved outside its replacement group: ' + target)
        result.append(dict(row, reference=reference, geometry_set=geometry_set, meshes=meshes))
    return result


def replacement_contract(cmds, rows):
    """Local background set owns the export; referenced prop sets are scoped inputs."""
    rows = validate_replacements(cmds, rows)
    ids = [row['id'] for row in rows]
    if len(set(ids)) != len(ids):
        raise ValueError('Replacement IDs are duplicated; register each group separately.')
    sets = [n for n in cmds.ls(type='objectSet') or []
            if n.split(':')[-1] == 'cache_geo_set' and not cmds.referenceQuery(n, isNodeReferenced=True)]
    if len(sets) != 1:
        raise ValueError('Background requires exactly one local cache_geo_set.')
    base = collect_meshes(cmds, sets[0])
    for row in rows:
        if not any(mesh.startswith(row['target'] + '|') for mesh in base):
            raise ValueError('Add the replacement group to the background cache_geo_set: ' + row['target'])
    meshes = [mesh for mesh in base if not any(mesh.startswith(row['target'] + '|') for row in rows)]
    meshes.extend(mesh for row in rows for mesh in row['meshes'])
    return dict(geometry_set=sets[0], meshes=sorted(set(meshes)), replacements=rows)


def export_plan(cmds, contract):
    """Map evaluated mesh shapes to public transform paths, fail on collisions."""
    from pxr import Tf
    plan, source_paths = {}, {}
    for mesh in contract['meshes']:
        parent = mesh.rsplit('|', 1)[0]
        row = next((r for r in contract['replacements'] if mesh in r['meshes']), None)
        output = row['target'] + '|' + parent.rsplit('|', 1)[-1].split(':')[-1] if row else parent
        public = '/' + '/'.join(Tf.MakeValidIdentifier(p) for p in output.split('|') if p)
        if public in plan:
            raise ValueError('Prop output mesh names must be unique within each group: ' + public)
        plan[public] = mesh
        source_paths[parent] = public
        ancestor = row['target'] if row else parent.rsplit('|', 1)[0]
        while ancestor:
            source_paths[ancestor] = '/' + '/'.join(Tf.MakeValidIdentifier(p) for p in ancestor.split('|') if p)
            ancestor = ancestor.rsplit('|', 1)[0]
    for path in plan:
        if any(other.startswith(path + '/') for other in plan):
            raise ValueError('Export mesh cannot also be a public group: ' + path)
    if len({path.split('/')[1] for path in plan}) != 1:
        raise ValueError('Background geometry must share one public root.')
    return plan, source_paths


@contextmanager
def static_geometry(contract):
    """Build disposable evaluated mesh copies; never import or modify source rigs."""
    import maya.cmds as cmds
    import maya.api.OpenMaya as om
    plan, mapping = export_plan(cmds, contract)
    namespace = '__assemblyPublish_' + uuid.uuid4().hex[:12]
    previous = cmds.ls(selection=True, long=True) or []
    modified = cmds.file(query=True, modified=True)
    previous_namespace = cmds.namespaceInfo(currentNamespace=True, absoluteName=True)
    undo_enabled = cmds.undoInfo(query=True, state=True)
    cmds.undoInfo(stateWithoutFlush=False)
    cmds.namespace(setNamespace=':')
    cmds.namespace(add=namespace)
    nodes, mesh_nodes = {}, []
    def dag(name):
        selection = om.MSelectionList()
        selection.add(name)
        return selection.getDagPath(0)
    try:
        paths = {path[:i] for path in plan for i, char in enumerate(path) if char == '/' and i > 0} | set(plan)
        inverse_mapping = {path: source for source, path in mapping.items()}
        for path in sorted(paths, key=lambda p: (p.count('/'), p)):
            parent = nodes.get(path.rsplit('/', 1)[0])
            kwargs = {'parent': parent} if parent else {}
            node = cmds.createNode('transform', name=namespace + ':' + path.rsplit('/', 1)[-1], **kwargs)
            node = cmds.ls(node, long=True)[0]
            nodes[path] = node
            source = inverse_mapping.get(path)
            if source:
                cmds.xform(node, worldSpace=True, matrix=cmds.xform(source, query=True, worldSpace=True, matrix=True))
            if path not in plan:
                continue
            source_dag = dag(plan[path])
            source_fn = om.MFnMesh(source_dag)
            # Reading outMesh forces skin/deformer evaluation before copying.
            om.MFnDependencyNode(source_dag.node()).findPlug('outMesh', False).asMObject()
            copied = om.MFnMesh().copy(source_dag.node(), dag(node).node())
            shape = om.MFnDagNode(copied).fullPathName()
            shaders, indices = source_fn.getConnectedShaders(source_dag.instanceNumber())
            for index, shader in enumerate(shaders):
                faces = [shape + '.f[%d]' % face for face, value in enumerate(indices) if value == index]
                if faces:
                    cmds.sets(faces, edit=True, forceElement=om.MFnDependencyNode(shader).name())
            mesh_nodes.append(shape)
        yield dict(root=nodes[min(nodes, key=lambda p: p.count('/'))], meshes=mesh_nodes,
                   mapping=mapping, plan=plan)
    finally:
        try:
            roots = [node for path, node in nodes.items() if path.count('/') == 1]
            if roots:
                cmds.delete(roots)
            cmds.namespace(removeNamespace=namespace)
        finally:
            cmds.namespace(setNamespace=previous_namespace)
            cmds.select(previous, replace=True) if previous else cmds.select(clear=True)
            cmds.file(modified=modified)
            cmds.undoInfo(stateWithoutFlush=undo_enabled)


def export_replacement_usd(target, contract):
    import math
    import maya.cmds as cmds
    from .usd_skel import _ensure_maya_usd_plugin
    from .setdress_mapping import stamp_asset
    _ensure_maya_usd_plugin(cmds)
    from pxr import Gf, Usd, UsdGeom
    expected_bounds = cmds.exactWorldBoundingBox(contract['meshes'])
    with static_geometry(contract) as prepared:
        cmds.select(prepared['root'], replace=True)
        cmds.mayaUSDExport(file=Path(target).as_posix(), selection=True,
                           exportSkels='none', exportSkin='none', exportBlendShapes=False,
                           exportInstances=False, mergeTransformAndShape=True,
                           stripNamespaces=True, defaultMeshScheme='none')
        mapping, plan = prepared['mapping'], prepared['plan']
    stage = Usd.Stage.Open(str(target))
    if not stage:
        raise ValueError('Cannot open replacement USD.')
    root_path = '/' + next(iter(plan)).split('/')[1]
    root = stage.GetPrimAtPath(root_path)
    if not root or not root.IsA(UsdGeom.Xform):
        raise ValueError('Replacement USD has no public root: ' + root_path)
    stage.SetDefaultPrim(root)
    UsdGeom.SetStageMetersPerUnit(stage, contract['meters_per_unit'])
    UsdGeom.SetStageUpAxis(stage, contract['up_axis'])
    meshes = {str(p.GetPath()): p for p in stage.Traverse() if p.IsA(UsdGeom.Mesh)}
    if set(meshes) != set(plan):
        raise ValueError('Replacement USD mesh paths differ from the publish plan.')
    for row in contract['replacements']:
        prim = stage.GetPrimAtPath(mapping[row['target']])
        if not prim or not prim.IsA(UsdGeom.Xform):
            raise ValueError('Missing public replacement group: ' + row['target'])
        prim.SetCustomDataByKey('smartpipeline:assembly:instanceId', row['id'])
        prim.SetCustomDataByKey('smartpipeline:assembly:sourcePath', row['source_path'])
        prim.SetCustomDataByKey('smartpipeline:assembly:version', row['version'])
    if any(p.GetTypeName() in {'Skeleton', 'SkelAnimation', 'BasisCurves', 'NurbsCurves'} for p in stage.Traverse()):
        raise ValueError('Replacement USD contains rig nodes.')
    world_range = Gf.Range3d()
    xforms = UsdGeom.XformCache()
    for path, source in plan.items():
        mesh = UsdGeom.Mesh(meshes[path])
        if len(mesh.GetPointsAttr().Get()) != cmds.polyEvaluate(source, vertex=True):
            raise ValueError('Replacement vertex count differs: ' + path)
        if len(mesh.GetFaceVertexCountsAttr().Get()) != cmds.polyEvaluate(source, face=True):
            raise ValueError('Replacement face count differs: ' + path)
        matrix = xforms.GetLocalToWorldTransform(mesh.GetPrim())
        for point in mesh.GetPointsAttr().Get():
            world_range.UnionWith(matrix.Transform(Gf.Vec3d(*point)))
    bounds = list(world_range.GetMin()) + list(world_range.GetMax())
    if any(not math.isclose(a, b, abs_tol=.001, rel_tol=1e-6) for a, b in zip(bounds, expected_bounds)):
        raise ValueError('Replacement USD world bounds differ from Maya geometry.')
    stamp_asset(stage, mapping, cmds, flattened=mapping)
    stage.GetRootLayer().Save()
    return dict(status='PASS', geometry_set=contract['geometry_set'], mesh_count=len(plan),
                meters_per_unit=contract['meters_per_unit'], up_axis=contract['up_axis'],
                default_prim=root_path, bounds=bounds,
                replacements=[{key: value for key, value in row.items()
                               if key not in {'meshes', 'reference', 'geometry_set'}}
                              for row in contract['replacements']])
