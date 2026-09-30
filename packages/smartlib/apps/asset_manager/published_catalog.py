"""Published Maya prop choices shared with Asset Assembly's card browser."""
from smartlib.core.asset_publish_resolver import AssetPublishResolver
from smartlib.core.path_resolver import AssetIdentity, configured_project_paths


def published_prop_cards(config, *, manager=None):
    if not config.project_root:
        raise ValueError('Project root is not configured. Open Asset Assembly from the project launcher.')
    if manager is None:
        from scripts.asset_manager import AssetManager
        manager = AssetManager(config.config_dir)
    paths = configured_project_paths(config.project_root, config)
    resolver = AssetPublishResolver(config)
    cards = []
    for asset in manager.list_assets(category='prop'):
        variants = {}
        for variant in manager.asset_variants(asset):
            identity = AssetIdentity('prop', asset.group, asset.name, variant)
            root = paths.asset_variant_root(identity)
            contexts = {}
            for context in resolver.list_published_contexts(root):
                versions = resolver.list_context_versions(root, context, formats=('mb', 'ma'))
                if versions:
                    contexts[context] = versions
            if contexts:
                variants[variant] = contexts
        if not variants:
            continue
        metadata = manager.load_asset_metadata(asset)
        cards.append(dict(category='prop', group=asset.group, asset=asset.name,
                          status=metadata.get('status', ''), description=metadata.get('description', ''),
                          thumbnail=str(manager.find_asset_thumbnail(asset) or ''), variants=variants))
    return cards
