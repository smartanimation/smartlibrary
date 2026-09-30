"""Resolve published character Retarget dependencies for sequence construction."""
from dataclasses import replace

from smartlib.apps.retarget_setup.service import RetargetService
from smartlib.core.path_resolver import AssetIdentity


def attach_retarget_inputs(plan, shots, identity):
    from smartlib.apps.smart_sequence_builder.service import ValidationResult
    cast = shots.load_sequence_cast(identity.episode, identity.sequence).get("cast") or {}
    validation = list(plan.validation)
    inputs = []
    for group in plan.inputs:
        if group.key != "mocap" or not group.enabled:
            inputs.append(group)
            continue
        children = []
        for row in group.children:
            try:
                matches = [(key, value) for key, value in cast.items()
                           if row.key.casefold() in {str(key).casefold(), str(value.get("asset") or key).casefold()}]
                if len(matches) != 1:
                    raise ValueError("Mocap target must identify exactly one sequence cast member")
                key, member = matches[0]
                asset_name = member.get("asset") or key
                asset_identity = AssetIdentity(
                    member.get("category") or "character", member.get("group") or "main",
                    asset_name, "")
                if not member.get("group"):
                    asset_root = shots.find_asset_root(asset_name)
                    registered = shots.asset_publish_resolver.identity_from_publish_path(
                        shots.paths.artifact_file(asset_root, "asset.json")
                    ) if asset_root else None
                    if registered is not None:
                        asset_identity = registered
                service = RetargetService(shots.paths, asset_identity)
                published = service.resolve_published()
                profile = service.import_profile(published["profile"])
                manifest = published["manifest"]
                if profile.get("input_mode") != "mcr_to_anim":
                    raise ValueError("Publish a received MCR → ANIM profile for Scene Build")
                errors = service.validate(profile)
                if errors:
                    raise ValueError("; ".join(errors))
                if (manifest.get("validation_status") != "passed"
                        or not manifest.get("test_motion", {}).get("reviewed")
                        or manifest.get("fingerprint") != service.fingerprint(profile)):
                    raise ValueError("Retarget Publish or ANIM dependency changed; retest and publish")
                data = dict(profile=profile, profile_path=str(published["profile"]),
                            version=published["version"], fingerprint=manifest["fingerprint"],
                            cast_key=key, namespace=member.get("namespace") or key,
                            rig=profile["animation_rig_scene"])
                children.append(replace(row, adapter="MCR → ANIM Bake", retarget=data))
                validation.append(ValidationResult("retarget_"+row.key, row.label+" Retarget", "READY",
                                                   f"{row.key}: Retarget {published['version']} / {data['rig']}"))
            except (ValueError, OSError, KeyError) as exc:
                children.append(replace(row, state="MISSING", adapter="Retarget Publish required"))
                validation.append(ValidationResult("retarget_"+row.key, row.label+" Retarget", "ERROR", f"{row.key}: {exc}"))
        inputs.append(replace(group, children=tuple(children), state="READY" if children and all(r.state == "READY" for r in children) else "MISSING"))
    return replace(plan, inputs=tuple(inputs), validation=tuple(validation))


def retarget_cast_preview(preview, inputs):
    """Use the ANIM representation pinned by the verified Retarget Publish."""
    bindings = {row.retarget["cast_key"]: row.retarget for group in inputs
                if group.key == "mocap" and group.enabled for row in group.children if row.retarget}
    result = []
    for item in preview:
        data = bindings.pop(item.cast_key, None)
        result.append(replace(item, publish_path=data["rig"], namespace=data["namespace"], status="resolved", message="") if data else item)
    if bindings:
        raise ValueError("Retarget cast is absent from build preview: " + ", ".join(bindings))
    return result
