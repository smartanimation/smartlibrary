# Environment Release & Pack

The environment Context button **Release & Pack** publishes the saved current Maya scene and then creates a common USD entry. Character/prop USD Current Scene behavior is unchanged.

## Products

All pipeline paths come from the existing ProjectPaths resolver.

- Proxy/Render Release: `asset_publish_version_dir(identity, "asset", quality, version)` contains `{asset}_{variant}.mb` and `{asset}_{variant}_payload.usd`, with publish/build metadata.
- Pack: `asset_usd_version_dir(identity, version)` contains `{asset}.usda` and `manifest.json` (`smartpipeline.asset_usd_entry.v1`).

The entry exposes nested `variant` and `quality` selections. Payload arcs pin the selected Release versions. Other members retain the previous Pack selections. Existing versions are never overwritten.

## Operation and recovery

Save a binary Maya work scene belonging to the selected asset. Select PROXY or RENDER and click **Release & Pack**. The Maya scene is copied intact; static USD geometry comes from `cache_geo_set`, with project unit/up-axis, mesh count and bounds validation. The common Pack is published only after Release validation succeeds.

A failed Release retains `_building` and is excluded from discovery. A successful Release remains available if Pack fails. Click **Assemble**, then **Pack** to retry the latest completed asset Release without another export. Pack does not copy or mutate that Release. Latest asset Release takes precedence over legacy model input resolution.

The existing model/assembly Release-to-Pack route is retained for older data. Previously published variant files and common entries remain readable. New current-scene Releases use `smartpipeline.environment_release.v1` metadata.

Release publication is serialized per variant/quality; common Pack publication is serialized per asset. The usual UI adopts the latest completed Release; arbitrary older-Release selection is not added here.

AssetPublishResolver.resolve_usd_entry returns a fixed common entry path and explicit selections. Existing Maya consumers resolve the released `.mb` through the same resolver.

## Limits

The current scene must be a complete static environment. Future prop assemblies should retain their component references in a complete Release. Viewport shader equivalence is not certified by geometry export validation.
