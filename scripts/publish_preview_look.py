"""Run with mayapy or hython; publish an explicit portable Preview Look recipe.

This authors a USD shader network directly. It does not translate arbitrary
Maya shading networks or Karma materials, and does not use a REND Publish.
"""
import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dcc', choices=['maya','houdini'],required=True)
    parser.add_argument('--config',required=True)
    parser.add_argument('--category',required=True)
    parser.add_argument('--group',default='main')
    parser.add_argument('--asset',required=True)
    parser.add_argument('--variant',default='default')
    parser.add_argument('--subset',default='low')
    parser.add_argument('--geometry-manifest',required=True)
    parser.add_argument('--texture-manifest',required=True)
    parser.add_argument('--recipe',required=True,help='Recipe JSON, or an earlier Look publish.json containing a recipe')
    parser.add_argument('--staging-dir',required=True,help='Temporary output directory, outside Production')
    args = parser.parse_args()
    if args.dcc == 'maya':
        import maya.standalone
        maya.standalone.initialize(name='python')
    else:
        import hou
        # Require the Houdini runtime, not just a standalone USD Python.
        hou.applicationVersion()
    try:
        from smartlib.apps.asset_manager.preview_publish import PreviewPublishService
        from smartlib.core.config_loader import ProjectConfig
        from smartlib.core.path_resolver import AssetIdentity
        from smartlib.core.metadata import read_json
        from smartlib.core.preview_look import write_preview_look
        service = PreviewPublishService(ProjectConfig(args.config))
        identity = AssetIdentity(args.category,args.group,args.asset,args.variant)
        recipe = read_json(args.recipe,{})
        recipe = recipe.get('recipe',recipe)
        geometry = read_json(args.geometry_manifest,{})['artifacts']['usd']['path']
        work = Path(args.staging_dir).resolve()
        if work.is_relative_to(service.paths.production_root().resolve()):
            raise ValueError('Use a temporary staging directory outside Production')
        work.mkdir(parents=True,exist_ok=True)
        target = service.paths.artifact_file(work,'look.usd')
        write_preview_look(target,geometry,recipe)
        manifest = service.publish_look(identity,target,args.geometry_manifest,args.texture_manifest,
                                        recipe,dcc=args.dcc,subset=args.subset)
        print(manifest)
    finally:
        if args.dcc == 'maya':
            maya.standalone.uninitialize()


if __name__ == '__main__':
    main()
