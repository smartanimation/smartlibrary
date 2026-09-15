"""Static Proxy Release export from the shared cache_geo_set contract."""
import math
from pathlib import Path

from smartlib.core.usd_settings import usd_settings

_METERS = {'mm': .001, 'cm': .01, 'm': 1., 'km': 1000.,
           'in': .0254, 'ft': .3048, 'yd': .9144, 'mi': 1609.344}


def validate_proxy_scene(settings):
    import maya.cmds as cmds
    conventions = usd_settings(settings)
    unit = cmds.currentUnit(query=True, linear=True)
    axis = cmds.upAxis(query=True, axis=True).upper()
    if not math.isclose(_METERS.get(unit, -1), conventions['meters_per_unit'], rel_tol=1e-9):
        raise ValueError(f"Maya unit {unit} differs from project USD meters_per_unit={conventions['meters_per_unit']}.")
    if axis != conventions['up_axis']:
        raise ValueError(f"Maya up axis {axis} differs from project USD up_axis={conventions['up_axis']}.")
    sets = [n for n in (cmds.ls(type='objectSet') or []) if n.split(':')[-1] == 'cache_geo_set']
    if len(sets) != 1:
        raise ValueError('Proxy Release requires exactly one cache_geo_set in the asset scene.')
    meshes = set()
    def collect(name, ancestry):
        if name in ancestry:
            raise ValueError('cache_geo_set contains a nested set cycle.')
        members = cmds.sets(name, query=True) or []
        for member in members:
            if '.' in member:
                raise ValueError('cache_geo_set requires whole geometry, not components: ' + member)
            for node in cmds.ls(member, long=True) or []:
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
                    raise ValueError('Unsupported cache_geo_set member: ' + member)
    collect(sets[0], [])
    if not meshes:
        raise ValueError('cache_geo_set contains no exportable Mesh shapes.')
    return dict(conventions, meshes=sorted(meshes), geometry_set=sets[0])


def export_proxy_usd(target, settings):
    import maya.cmds as cmds
    from smartlib.dcc.maya.usd_skel import _ensure_maya_usd_plugin
    contract = validate_proxy_scene(settings)
    target = Path(target)
    previous = cmds.ls(selection=True, long=True) or []
    bbox = cmds.exactWorldBoundingBox(contract['meshes'])
    try:
        _ensure_maya_usd_plugin(cmds)
        cmds.select(contract['meshes'], replace=True, noExpand=True)
        cmds.mayaUSDExport(file=target.as_posix(), selection=True,
                           exportSkels='none', exportSkin='none', exportBlendShapes=False,
                           exportInstances=True, mergeTransformAndShape=True, stripNamespaces=False,
                           defaultMeshScheme='none')
    finally:
        cmds.select(previous, replace=True) if previous else cmds.select(clear=True)
    from pxr import Usd, UsdGeom
    stage = Usd.Stage.Open(str(target))
    if not stage:
        raise RuntimeError('Cannot open exported Proxy USD.')
    roots = [p for p in stage.GetPseudoRoot().GetChildren() if p.IsA(UsdGeom.Xform)]
    if len(roots) != 1:
        raise ValueError('Proxy geometry must share one scene root for USD referencing.')
    stage.SetDefaultPrim(roots[0])
    UsdGeom.SetStageMetersPerUnit(stage, contract['meters_per_unit'])
    UsdGeom.SetStageUpAxis(stage, contract['up_axis'])
    prims = list(Usd.PrimRange.Stage(stage, Usd.TraverseInstanceProxies()))
    count = sum(p.IsA(UsdGeom.Mesh) for p in prims)
    if count != len(contract['meshes']):
        raise ValueError(f'Proxy USD mesh count mismatch: {count} / {len(contract["meshes"])}.')
    if any(p.IsA(UsdGeom.Curves) or p.GetTypeName() in {'Skeleton','SkelAnimation'} for p in prims):
        raise ValueError('Proxy USD contains unexpected curves or skeletons.')
    box = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ['default','proxy','render']).ComputeWorldBound(stage.GetPseudoRoot()).ComputeAlignedRange()
    actual = list(box.GetMin()) + list(box.GetMax())
    if any(not math.isclose(a,b,abs_tol=.001,rel_tol=1e-6) for a,b in zip(bbox,actual)):
        raise ValueError('Proxy USD world bounds differ from cache_geo_set.')
    stage.GetRootLayer().Save()
    return {'status':'PASS', 'geometry_set':contract['geometry_set'], 'mesh_count':count,
            'meters_per_unit':contract['meters_per_unit'], 'up_axis':contract['up_axis'],
            'default_prim':str(stage.GetDefaultPrim().GetPath()), 'bounds':actual}
