"""Static geometry for portable Preview Look work, exported from cache_geo_set."""
from pathlib import Path


def export_geometry(target, settings, *, geometry_set='cache_geo_set'):
    import maya.cmds as cmds
    from pxr import Usd, UsdGeom, Tf
    from smartlib.core.usd_settings import usd_settings
    from .assembly_replacement import collect_meshes
    from .usd_skel import _ensure_maya_usd_plugin
    meshes = collect_meshes(cmds,geometry_set)
    roots = sorted({cmds.listRelatives(m,parent=True,fullPath=True)[0] for m in meshes})
    names = [Tf.MakeValidIdentifier(p.rsplit('|',1)[-1].rsplit(':',1)[-1]) for p in roots]
    if len(names) != len(set(names)):
        raise ValueError('Geometry export requires unique mesh transform names after removing namespaces')
    conventions = usd_settings(settings)
    previous = cmds.ls(selection=True,long=True) or []
    try:
        _ensure_maya_usd_plugin(cmds)
        cmds.select(roots,replace=True,noExpand=True)
        cmds.mayaUSDExport(file=Path(target).as_posix(),selection=True,exportRoots=roots,
            worldspace=True,parentScope='Geometry',mergeTransformAndShape=True,stripNamespaces=True,
            exportSkels='none',exportSkin='none',exportBlendShapes=False,shadingMode='none')
    finally:
        cmds.select(previous,replace=True) if previous else cmds.select(clear=True)
    stage = Usd.Stage.Open(str(target))
    if not stage or sum(p.IsA(UsdGeom.Mesh) for p in stage.Traverse()) != len(meshes):
        raise ValueError('Geometry export did not preserve the selected mesh count')
    stage.SetDefaultPrim(stage.GetPrimAtPath('/Geometry'))
    UsdGeom.SetStageMetersPerUnit(stage,conventions['meters_per_unit'])
    UsdGeom.SetStageUpAxis(stage,conventions['up_axis'])
    stage.GetRootLayer().Save()
    return Path(target)
