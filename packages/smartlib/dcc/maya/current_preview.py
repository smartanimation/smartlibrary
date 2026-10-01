"""Publish or preview the active Maya scene without reopening or saving it."""
from pathlib import Path


def validate_work_scene(paths, identity, source):
    """Accept any department, but only the selected asset's Maya Work area."""
    source = Path(source).resolve()
    if source.suffix.lower() not in {'.ma', '.mb'} or not source.is_file():
        raise ValueError('Choose an existing Maya Work Scene')
    # Candidate tokens are validated and resolved by the existing shared resolver.
    # No department allow-list: custom departments and partitions work as well.
    for candidate in source.parts:
        try:
            department = paths.pipeline_token(candidate)
            root = paths.asset_work_dir(identity, department, 'maya').resolve()
        except ValueError:
            continue
        if source.is_relative_to(root):
            return source
    raise ValueError('Scene must exist in this Asset/Variant Maya Work area (any department)')


def publish_scene(job, *, current_scene=False, preview=False):
    import maya.cmds as cmds
    from pxr import Usd, UsdGeom
    from smartlib.core.config_loader import ProjectConfig
    from smartlib.core.path_resolver import AssetIdentity
    from smartlib.core.metadata import read_json
    from smartlib.apps.asset_manager.preview_publish import PreviewPublishService
    from smartlib.dcc.maya.preview_geometry import export_geometry
    from smartlib.dcc.maya.preview_materials import extract_recipe
    from smartlib.core.preview_look import write_preview_look, mesh_fingerprint
    from smartlib.core.maya_runtime import software_config_name
    from smartlib.apps.review_build_manager.worker import _load_build_plugins
    config = ProjectConfig(job['config'])
    service = PreviewPublishService(config)
    identity = AssetIdentity(**job['identity'])
    source = validate_work_scene(service.paths, identity, job['scene'])
    source_ref = service.pin(source)
    if current_scene:
        active = cmds.file(query=True, sceneName=True)
        if not active or Path(active).resolve() != source:
            raise ValueError('The active Maya scene changed; refresh the Publish panel')
    else:
        _load_build_plugins(cmds, config, 'WORK STAGE', software_config_name(config))
        cmds.file(str(source), open=True, force=True, prompt=False, executeScriptNodes=False)
    scene_state = dict(mode='current_scene' if current_scene else 'saved_scene',
        modified=bool(cmds.file(query=True, modified=True)), frame=cmds.currentTime(query=True))
    work = Path(job['staging'])
    work.mkdir(parents=True, exist_ok=True)
    if job['kind'] == 'rig':
        from smartlib.dcc.maya.usd_skel import publish_usd_skel_package
        from smartlib.dcc.maya.rig_metadata import collect_rig_metadata
        import shutil
        metadata = collect_rig_metadata(asset_name=identity.name, subset=job['subset'], source_workfile=source)
        outputs = publish_usd_skel_package(work, rig_metadata=metadata,
            contract=config.usd_skel_contract, paths=service.paths)
        if preview:
            return outputs['entry_usd']
        if service.pin(source) != source_ref:
            raise ValueError('Source scene changed during export')
        version, directory = service.reserve(identity, 'rig', job['subset'])
        files = dict(usd='usdSkel.usd', geometry_usd='geo.usd', rig_usd='rig.usd', validation='validation.json')
        for name in files.values():
            shutil.copy2(service.paths.artifact_file(work, name), service.paths.artifact_file(directory, name))
        return service.commit(identity, 'rig', job['subset'], version, directory, files,
            source_scene=source_ref, scene_state=scene_state, dcc='maya',
            usd_skel=dict(schema='smartpipeline.usd_skel.v2', entry='usdSkel.usd', geometry='geo.usd',
                rig='rig.usd', validation='validation.json', root_joint=metadata.get('root_joint', ''),
                capabilities=['linear_skin', 'joint_animation'],
                limitations=['BlendShape targets are not exported. Shot publish verifies deformation and falls back to deform cache.']))
    geo = export_geometry(work / 'geo.usd', config.load('project_settings.yml'))
    if service.pin(source) != source_ref:
        raise ValueError('Source scene changed during export')
    if job['kind'] == 'geometry':
        if preview:
            return geo
        return service.publish_geometry(identity, geo, source, subset=job['subset'], scene_state=scene_state)
    geometry_manifest, texture_manifest = job['geometry_manifest'], job['texture_manifest']
    for manifest, kind in [(geometry_manifest, 'model'), (texture_manifest, 'texture')]:
        if kind == 'texture' and not manifest:
            continue
        record = read_json(manifest, {})
        if any(record.get(k) != v for k, v in dict(asset=identity.name, category=identity.category,
                group=identity.group, variant=identity.variant, subset=job['subset'], publish_type=kind).items()):
            raise ValueError('Publish dependency belongs to another asset/subset: ' + manifest)
    published_geo = read_json(geometry_manifest, {})['artifacts']['usd']['path']
    def contract(path):
        stage = Usd.Stage.Open(str(path))
        return {str(p.GetPath()): mesh_fingerprint(p) for p in stage.Traverse() if p.IsA(UsdGeom.Mesh)}
    if contract(geo) != contract(published_geo):
        raise ValueError('Scene topology/UV differs from selected Geometry Publish; publish Geometry first')
    recipe = extract_recipe(geo, read_json(texture_manifest, {}) if texture_manifest else {})
    look = work / 'look.usd'
    write_preview_look(look, published_geo, recipe)
    if preview:
        stage = Usd.Stage.CreateNew(str(work / 'preview.usda'))
        stage.GetRootLayer().subLayerPaths = [look.as_posix(), geo.as_posix()]
        stage.SetDefaultPrim(stage.GetPrimAtPath('/Geometry'))
        geometry = Usd.Stage.Open(str(geo))
        UsdGeom.SetStageUpAxis(stage, UsdGeom.GetStageUpAxis(geometry))
        UsdGeom.SetStageMetersPerUnit(stage, UsdGeom.GetStageMetersPerUnit(geometry))
        stage.GetRootLayer().Save()
        return work / 'preview.usda'
    manifest = service.publish_look(identity, look, geometry_manifest, texture_manifest, recipe,
                                    dcc='maya', subset=job['subset'], source_scene=source, scene_state=scene_state)
    return manifest
