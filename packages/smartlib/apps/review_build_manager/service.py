from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import os
from pathlib import Path
import re
import shutil
from dataclasses import replace

from smartlib.apps.shot_manager import SequenceIdentity, ShotIdentity, ShotManagerService
from smartlib.core.config_loader import ProjectConfig, expand_config_tokens
from smartlib.core.metadata import read_json
from smartlib.core.versioning import format_version, parse_version
from smartlib.apps.review_build_manager.orchestrator import SceneBuildOrchestrator
from smartlib.apps.smart_sequence_builder.service import SmartSequenceBuilderService
from smartlib.review.workflow import ReviewProfileService, ReviewWorkflowService


MOV_EXTENSIONS = {".mov", ".mp4"}


@dataclass(frozen=True)
class ReviewOutput:
    version: str
    directory: str
    scene: str = ""
    movie: str = ""
    updated: str = ""
    state: str = "MISSING"


@dataclass(frozen=True)
class ReviewShotStatus:
    identity: ShotIdentity
    state: str
    output_version: str
    output_label: str
    last_review: str
    thumbnail: str
    comment: str
    source_version: str
    message: str
    outputs: tuple[ReviewOutput, ...] = field(default_factory=tuple)


class ReviewBuildManagerService:
    """Resolve review inputs, outputs, and the background Maya runtime."""

    def __init__(self, project_config: ProjectConfig):
        self.project_config = project_config
        self.shots = ShotManagerService(project_config)
        self.orchestrator = SceneBuildOrchestrator(self.shots)
        self.sequence_builder = SmartSequenceBuilderService(project_config)
        self.review_profiles = ReviewProfileService(project_config)

    def review_workflow(self, identity: ShotIdentity) -> ReviewWorkflowService:
        return ReviewWorkflowService(
            self.shots.shot_root(identity),
            self.shots.shot_build_root(identity).parent,
        )

    def review_profile_ids(self) -> list[str]:
        return self.review_profiles.review_profile_ids()

    def delivery_profile_ids(self) -> list[str]:
        return self.review_profiles.delivery_profile_ids()

    def asset_variants(self, asset: str) -> list[str]:
        root = self.shots.find_asset_root(str(asset or ""))
        if not root or not root.is_dir():
            return ["default"]
        variants = sorted(path.name for path in root.iterdir() if path.is_dir())
        return variants or ["default"]

    def assembly_definition(self, identity: ShotIdentity) -> dict:
        workflow = self.review_workflow(identity)
        current, _path = workflow.latest_composition()
        if current.get("members"):
            return current
        # Seed the declarative recipe from current Construct rows.  This does
        # not publish until the artist explicitly saves it.
        members = []
        for row in self.build_contents(identity):
            if str(row.get("type") or "") not in {"rig", "usd"}:
                continue
            name = str(row.get("cast_key") or row.get("name") or "")
            component = row.get("component") or {}
            source = component.get("source") or {}
            members.append({
                "uid": f"member-{name}",
                "name": name,
                "asset": str(source.get("asset") or row.get("asset") or name),
                "variant": str(row.get("variant") or "default"),
                "role": str(row.get("role") or "CHA"),
                "context": str(row.get("context") or "WORK"),
                "asset_version": str(row.get("official") or "latest"),
                "version_policy": "locked" if row.get("official") else "latest_approved",
                "behavior": "CURVE" if str(row.get("role") or "").upper() == "CHA" else "STATIC",
                "namespace": name,
                "enabled": bool(row.get("use", True)),
            })
        return workflow.normalize_assembly({"members": members})

    def publish_assembly_definition(
        self, identity: ShotIdentity, payload: dict, *, comment: str = ""
    ) -> Path:
        return self.review_workflow(identity).publish_composition(payload, comment)

    def layer_definition(self, identity: ShotIdentity, department: str = "anim") -> dict:
        workflow = self.review_workflow(identity)
        current, _path = workflow.latest_layer_definition()
        if current.get("layers"):
            return current
        # Convert the existing versioned review_spec to the dynamic schema.
        assembly = self.assembly_definition(identity)
        uid_by_name = {
            str(member.get("name")): str(member.get("uid"))
            for member in assembly.get("members") or []
        }
        layers = []
        for name, data in self.shots.review_layers(identity, department).items():
            layers.append({
                "name": name,
                "slug": name,
                "members": [uid_by_name.get(value, value) for value in data.get("members") or []],
                "camera": data.get("camera") or {},
                "order": data.get("order", len(layers) * 10),
                "precomp_placeholder": (data.get("ae") or {}).get("template_slot") or str(name).upper(),
                "enabled": True,
            })
        return workflow.normalize_layers(
            {"layers": layers},
            [str(row.get("uid")) for row in assembly.get("members") or []],
        )

    def publish_layer_definition(
        self, identity: ShotIdentity, payload: dict, *, comment: str = ""
    ) -> Path:
        return self.review_workflow(identity).publish_layer_definition(payload, comment)

    def review_definition_validation(
        self,
        identity: ShotIdentity,
        department: str = "anim",
        task: str = "main",
    ) -> dict:
        workflow = self.review_workflow(identity)
        _assembly, assembly_path = workflow.latest_assembly()
        layers, layers_path = workflow.latest_layer_definition()
        settings, settings_path = self.shots.latest_playblast_settings(
            identity, department, task
        )
        def normalized_id(value) -> str:
            text = str(value or "").strip()
            if text.lower().startswith("review_"):
                text = text[7:]
            return "".join(
                char if char.isalnum() or char in "_.-" else "_" for char in text
            ).strip("._-").lower()

        definition_ids = {
            normalized_id(layer.get("review_layer_id") or layer.get("slug") or layer.get("name"))
            for layer in (layers.get("layers") or [])
            if layer.get("enabled", True)
        }
        rows = [dict(row or {}) for row in (settings.get("rows") or []) if row.get("enabled", True)]
        setting_id_list = [
            normalized_id(row.get("review_layer_id") or row.get("layer") or row.get("display_layer"))
            for row in rows
        ]
        setting_ids = set(setting_id_list)
        errors = []
        if not assembly_path:
            errors.append("Shot Composition is not published.")
        if not layers_path:
            errors.append("Review Layer Definition is not published.")
        if not settings_path:
            errors.append(f"playblast_settings is not published for {department}/{task}.")
        if not definition_ids:
            errors.append("Review Layer Definition has no enabled layers.")
        if not setting_ids:
            errors.append("playblast_settings has no enabled rows.")
        duplicate_ids = sorted({value for value in setting_id_list if setting_id_list.count(value) > 1})
        for layer_id in duplicate_ids:
            errors.append(f"Duplicate playblast_settings Review Layer ID: {layer_id}")
        for layer_id in sorted(definition_ids - setting_ids):
            errors.append(f"playblast_settings row is missing: {layer_id}")
        for layer_id in sorted(setting_ids - definition_ids):
            errors.append(f"Review Layer Definition is missing: {layer_id}")
        for row in rows:
            layer_id = str(row.get("layer") or row.get("display_layer") or "<unknown>")
            if not str(row.get("display_layer") or "").strip():
                errors.append(f"Display Layer is missing: {layer_id}")
            if not str(row.get("camera") or "").strip():
                errors.append(f"Camera is missing: {layer_id}")
            if int(row.get("width") or 0) <= 0 or int(row.get("height") or 0) <= 0:
                errors.append(f"Resolution is invalid: {layer_id}")
            if int(row.get("end") or 0) < int(row.get("start") or 0):
                errors.append(f"Frame Range is invalid: {layer_id}")
            output_format = str(
                row.get("output_format") or row.get("image_format") or "png"
            ).lower().lstrip(".")
            if output_format not in {"png", "jpg", "jpeg", "exr"}:
                errors.append(f"Output Format is unsupported: {layer_id}={output_format}")
        return {
            "ready": not errors,
            "errors": errors,
            "assembly_path": assembly_path,
            "layer_definition_path": layers_path,
            "playblast_settings_path": settings_path,
            "playblast_settings": settings,
        }

    def asset_context_profiles(self) -> list[str]:
        try:
            from smartlib.apps.asset_manager.context import AssetContextService

            profiles = AssetContextService(self.project_config).quality_profiles("asset")
        except (FileNotFoundError, KeyError, TypeError, ValueError):
            profiles = []
        ordered = []
        for profile in [*profiles, "LO", "ANIM", "PROXY", "REND", "MCP"]:
            value = str(profile).strip().upper()
            if value and value not in ordered:
                ordered.append(value)
        return ordered

    @staticmethod
    def stage_profiles() -> list[str]:
        return ["FAST", "WORK", "REND"]

    def asset_context_profiles_for_root(self, asset_root: Path, variant: str = "default") -> list[str]:
        try:
            from smartlib.apps.asset_manager.context import AssetContextService
            from smartlib.core.path_resolver import AssetIdentity

            metadata = read_json(Path(asset_root) / "asset.json", {}) or {}
            identity = AssetIdentity(
                str(metadata.get("category") or Path(asset_root).parents[1].name),
                str(metadata.get("group") or Path(asset_root).parent.name),
                str(metadata.get("asset") or metadata.get("name") or Path(asset_root).name),
                variant or "default",
            )
            return [
                str(value).upper()
                for value in AssetContextService(self.project_config).quality_profiles_for_asset(identity)
            ]
        except (FileNotFoundError, KeyError, TypeError, ValueError, IndexError):
            return self.asset_context_profiles()

    def default_asset_context(self, stage: str, asset_root: Path) -> str:
        metadata = read_json(Path(asset_root) / "asset.json", {}) or {}
        try:
            from smartlib.apps.asset_manager.context import AssetContextService
            from smartlib.core.path_resolver import AssetIdentity

            identity = AssetIdentity(
                str(metadata.get("category") or Path(asset_root).parents[1].name),
                str(metadata.get("group") or Path(asset_root).parent.name),
                str(metadata.get("asset") or metadata.get("name") or Path(asset_root).name),
                "default",
            )
            return AssetContextService(self.project_config).stage_context_for_asset(identity, stage)
        except (AttributeError, FileNotFoundError, KeyError, TypeError, ValueError, IndexError):
            asset_type = str(metadata.get("asset_type") or metadata.get("category") or "").lower()
            stage = "REND" if str(stage).upper() == "FINAL" else str(stage).upper()
            if asset_type in {"bg", "bga", "env", "environment", "set", "background"}:
                return "REND" if stage == "REND" else "PROXY"
            if asset_type in {"ch", "character", "characters"}:
                return "REND" if stage == "REND" else "LO" if stage == "FAST" else "ANIM"
            return "REND" if stage == "REND" else "LO"

    def build_plan(
        self,
        identity: ShotIdentity,
        *,
        mode: str = "WORK STAGE",
        department: str = "anim",
        task: str = "",
        input_policy: str = "GENERATE MISSING",
        overrides: dict | None = None,
    ):
        return self.orchestrator.plan(
            identity,
            requested_mode=mode,
            department=department,
            task=task,
            input_policy=input_policy,
            overrides=overrides,
        )

    def ensure_stage_input(
        self,
        identity: ShotIdentity,
        *,
        policy: str = "GENERATE MISSING",
        overrides: dict | None = None,
        comment: str = "",
    ) -> Path:
        policy = str(policy or "GENERATE MISSING").strip().upper()
        current = self.shots.latest_anim_input(identity)
        if policy == "USE EXISTING":
            if not current:
                raise FileNotFoundError(
                    f"Animation Input Package was not found for {identity.code}."
                )
            return current
        if current and policy == "GENERATE MISSING":
            current_data = read_json(current, {}) or {}
            current_overrides = current_data.get("overrides") or {}
            requested_overrides = overrides or {}
            relevant_keys = {
                "context",
                "camera",
                "layout_overlay",
                "use_placements",
                "exclude_cast",
                "cast_contexts",
            }
            current_relevant = {
                key: current_overrides.get(key)
                for key in relevant_keys
                if key in current_overrides
            }
            requested_relevant = {
                key: requested_overrides.get(key)
                for key in relevant_keys
                if key in requested_overrides
            }
            if current_relevant == requested_relevant:
                return current
        result = self.shots.build_anim_input_package_for_shot(
            identity,
            comment=comment or "Generated by Review Build Manager",
            overrides=overrides,
        )
        return result.anim_input

    def sequence_build_plan(
        self,
        identity: SequenceIdentity,
        *,
        recipe: str = "",
        virtual_camera_take: str = "",
        enabled_inputs: dict[str, bool] | None = None,
        **kwargs,
    ):
        plan = self.orchestrator.plan_sequence(identity, **kwargs)
        recipe_plan = self.sequence_builder.plan(
            identity.episode,
            identity.sequence,
            recipe or self.default_sequence_recipe(),
            virtual_camera_take=virtual_camera_take,
            enabled=enabled_inputs,
        )
        from smartlib.apps.review_build_manager.orchestrator import BuildValidation

        recipe_validations = tuple(
            BuildValidation(
                "ERROR" if row.state == "ERROR" else "WARNING" if row.state == "WARNING" else "INFO",
                f"SEQUENCE_{row.key.upper()}",
                row.detail,
            )
            for row in recipe_plan.validation
            if row.state in {"ERROR", "WARNING"}
        )
        validations = tuple(plan.validations) + recipe_validations
        state = (
            "BLOCKED" if any(row.severity == "ERROR" for row in validations)
            else "WARNING" if any(row.severity == "WARNING" for row in validations)
            else plan.state
        )
        return replace(plan, state=state, validations=validations)

    def sequence_recipes(self) -> list[str]:
        return list(self.sequence_builder.recipes())

    def default_sequence_recipe(self) -> str:
        return self.sequence_builder.default_recipe()

    def sequence_recipe_plan(
        self,
        identity: SequenceIdentity,
        *,
        recipe: str = "",
        virtual_camera_take: str = "",
        enabled_inputs: dict[str, bool] | None = None,
    ):
        return self.sequence_builder.plan(
            identity.episode,
            identity.sequence,
            recipe or self.default_sequence_recipe(),
            virtual_camera_take=virtual_camera_take,
            enabled=enabled_inputs,
        )

    def ensure_sequence_stage_input(
        self,
        identity: SequenceIdentity,
        *,
        policy: str = "GENERATE MISSING",
        department: str = "layout",
        overrides: dict | None = None,
        comment: str = "",
    ) -> Path:
        policy = str(policy or "GENERATE MISSING").strip().upper()
        current = self.shots.latest_sequence_stage_input(identity, department)
        if policy == "USE EXISTING":
            if not current:
                raise FileNotFoundError(
                    f"Sequence Input Package was not found for {identity.code}."
                )
            return current
        if current and policy == "GENERATE MISSING":
            return current
        return self.shots.build_sequence_stage_input(
            identity,
            department=department,
            comment=comment or "Generated by Review Build Manager",
            overrides=overrides,
        )

    @property
    def project_name(self) -> str:
        return str(
            getattr(self.project_config, "project_name", "")
            or self.project_config.config_dir.name
        )

    def scan(
        self,
        *,
        mode: str = "WORK STAGE",
        department: str = "anim",
        task: str = "main",
        generate_review: bool = False,
        overrides: dict | None = None,
    ) -> list[ReviewShotStatus]:
        return [
            self.shot_status(
                identity,
                mode=mode,
                department=department,
                task=task,
                generate_review=generate_review,
                overrides=overrides,
            )
            for identity in self.shots.list_shots()
        ]

    @staticmethod
    def apply_generate_review_requirement(
        status: ReviewShotStatus,
        enabled: bool,
    ) -> ReviewShotStatus:
        """Update only the optional MOV requirement from cached scan data."""

        if status.state in {"MISSING", "DIRTY"}:
            return status
        matching_review = next(
            (
                output for output in status.outputs
                if output.version == status.output_version
                and output.movie
                and Path(output.movie).is_file()
            ),
            None,
        )
        missing_message = "Construct is current; Generate Review MOV is not available."
        if enabled and not matching_review:
            if status.state == "UP TO DATE":
                return replace(status, state="READY", message=missing_message)
            return status
        if status.message == missing_message:
            return replace(
                status,
                state="UP TO DATE",
                message=(
                    "Construct and Animation Curves are current; review was generated."
                    if enabled and matching_review
                    else "Construct and Animation Curves are current."
                ),
            )
        return status

    def build_contents(
        self,
        identity: ShotIdentity,
        *,
        default_context: str = "WORK",
        cast_contexts: dict[str, str] | None = None,
        excluded_cast: list[str] | None = None,
        representation: str = "project",
    ) -> list[dict]:
        context_map = {
            str(key): str(value).upper()
            for key, value in (cast_contexts or {}).items()
        }
        load_construct = getattr(self.shots, "load_construct", None)
        persisted_construct = load_construct(identity) if load_construct else {}
        for component in persisted_construct.get("components") or []:
            if not isinstance(component, dict):
                continue
            name = str(component.get("name") or "")
            saved_source = component.get("source") or {}
            saved_context = str(saved_source.get("context") or "").upper()
            if (
                name and saved_context and bool(saved_source.get("context_override"))
                and name not in context_map
            ):
                context_map[name] = saved_context
        load_cast = getattr(self.shots, "load_cast", None)
        cast_data = load_cast(identity) if load_cast else {}
        cast_rows = cast_data.get("cast") or {}
        if not cast_rows:
            load_sequence_cast = getattr(self.shots, "load_sequence_cast", None)
            sequence_cast = (
                load_sequence_cast(identity.episode, identity.sequence)
                if load_sequence_cast else {}
            )
            cast_rows = sequence_cast.get("cast") or {}
        for cast_key, entry in cast_rows.items():
            if not isinstance(entry, dict):
                continue
            asset_root = self.shots.find_asset_root(str(entry.get("asset") or ""))
            if asset_root:
                current = str(context_map.get(str(cast_key)) or "").upper()
                if current in {"", "FAST", "WORK", "FINAL"}:
                    context_map[str(cast_key)] = self.default_asset_context(
                        current or default_context, asset_root
                    )
        construct = self.shots.resolved_construct(
            identity,
            cast_contexts=context_map,
            exclude_cast=excluded_cast,
            representation=representation,
        )
        rows = []
        for component in construct.get("components") or []:
            source = dict(component.get("source") or {})
            name = str(component.get("name") or "")
            component_type = str(component.get("component_type") or "rig")
            is_virtual_camera_dependency = (
                component_type == "camera"
                and str(source.get("kind") or "") == "shot_dependency"
                and str(source.get("dependency_type") or "") == "virtual_camera"
            )
            is_cast = component_type == "rig"
            is_usd = component_type == "usd"
            asset = str(source.get("asset") or "")
            variant = str(source.get("variant") or "default")
            asset_root = self.shots.find_asset_root(asset)
            variant_root = asset_root / variant if asset_root else None
            is_asset_component = bool(asset_root and variant_root) and (is_cast or is_usd)
            context = (
                context_map.get(
                    name,
                    str(source.get("context") or default_context or "WORK").upper(),
                )
                if (is_cast or is_asset_component)
                else "payload" if is_usd else ""
            )
            latest_path = None
            if is_asset_component and variant_root and variant_root.is_dir():
                latest_path = self.shots.asset_publish_resolver.resolve_context(
                    variant_root,
                    context,
                    version="latest",
                )
            path = Path(str(component.get("path") or ""))
            official = str(component.get("version") or "")
            latest = latest_path.parent.name if latest_path else (
                path.parent.name if is_usd and path.is_file() else ""
            )
            enabled = bool(component.get("enabled", True))
            state = "EXCLUDED" if not enabled else "MISSING"
            if enabled and (path.is_file() or path.is_dir()):
                state = "UPDATE AVAILABLE" if latest and latest != official else "READY"
            note = str(component.get("note") or "")
            if is_virtual_camera_dependency:
                dependency_note = (
                    "from dependencies.json: "
                    + str(source.get("dependency_id") or name)
                )
                note = f"{dependency_note}; {note}" if note else dependency_note
            rows.append(
                {
                    "cast_key": name,
                    "component_key": [component_type, name, str(source.get("field") or "")],
                    "type": (
                        "rig" if is_cast
                        else "virtual_camera" if is_virtual_camera_dependency
                        else component_type
                    ),
                    "asset": asset,
                    "role": str(source.get("role") or ""),
                    "variant": variant,
                    "context": context,
                    "context_options": (
                        self.asset_context_profiles_for_root(asset_root, variant)
                        if is_asset_component and asset_root else []
                    ),
                    "official": official,
                    "latest": latest,
                    "state": state,
                    "required": bool(component.get("required", True)),
                    "enabled": enabled,
                    "note": note,
                    "component": dict(component),
                }
            )
        return rows

    def save_build_contents(self, identity: ShotIdentity, rows: list[dict]) -> Path:
        return self.shots.write_construct(
            identity,
            {"components": [dict(row.get("component") or {}) for row in rows]},
        )

    def construct_diff(
        self,
        identity: ShotIdentity,
        *,
        current: dict | None = None,
        desired: dict | None = None,
        cast_contexts: dict[str, str] | None = None,
        excluded_cast: list[str] | None = None,
        representation: str = "project",
    ) -> list[dict]:
        """Compare a saved Construct with freshly resolved published inputs."""
        current = current or self.shots.load_construct(identity)
        desired = desired or self.shots.construct_from_stage_inputs(
            identity,
            cast_contexts=cast_contexts,
            exclude_cast=excluded_cast,
            representation=representation,
        )

        def key(component: dict) -> tuple[str, str, str]:
            source = component.get("source") or {}
            return (
                str(component.get("component_type") or "").lower(),
                str(component.get("name") or ""),
                str(source.get("field") or source.get("kind") or ""),
            )

        old = {key(row): dict(row) for row in current.get("components") or []}
        new = {key(row): dict(row) for row in desired.get("components") or []}
        changes = []
        for component_key in sorted(set(old) | set(new)):
            before = old.get(component_key)
            after = new.get(component_key)
            change = "UNCHANGED"
            if before is None:
                change = "ADDED"
            elif after is None:
                change = "REMOVED"
            else:
                fields = [
                    name for name in ("version", "path", "enabled", "mode")
                    if before.get(name) != after.get(name)
                ]
                if str((before.get("source") or {}).get("context") or "") != str(
                    (after.get("source") or {}).get("context") or ""
                ):
                    fields.append("context")
                if fields:
                    change = "UPDATED"
            component = after or before or {}
            asset_status = self._construct_asset_status(component)
            changes.append({
                "key": list(component_key),
                "change": change,
                "code": (
                    "TIMING_CHANGED"
                    if component_key[0] == "editorial_timing" and change != "UNCHANGED"
                    else ""
                ),
                "severity": "ERROR" if asset_status == "omit" else (
                    "INFO" if change == "UNCHANGED" else "WARNING"
                ),
                "asset_status": asset_status,
                "selected": change != "UNCHANGED" and asset_status != "omit",
                "before": before or {},
                "after": after or {},
            })
        return changes

    def _construct_asset_status(self, component: dict) -> str:
        if str(component.get("component_type") or "").lower() != "rig":
            return ""
        source = component.get("source") or {}
        asset_root = self.shots.find_asset_root(str(source.get("asset") or ""))
        if not asset_root:
            return "missing"
        metadata = read_json(asset_root / "asset.json", {}) or {}
        variant = str(source.get("variant") or "default")
        variant_data = read_json(asset_root / variant / "variant.json", {}) or {}
        return str(variant_data.get("status") or metadata.get("status") or "").strip().lower()

    def list_constructs(
        self, identity: ShotIdentity, department: str, task: str, dcc: str = "maya"
    ) -> list[dict]:
        rows = []
        department_name = str(department or "main").strip().lower()
        task_name = str(task or "main").strip().lower()
        dcc_name = str(dcc or "maya").strip().lower()
        roots = [
            self.shots.shot_build_root(identity, department_name) / department_name / dcc_name / task_name,
            self.shots.shot_build_root(identity, department_name) / department_name / task_name,
            self.shots.unpartitioned_shot_build_root(identity) / department_name / dcc_name / task_name,
            self.shots.unpartitioned_shot_build_root(identity) / department_name / task_name,
            self.shots.legacy_shot_build_root(identity) / department_name / task_name,
        ]
        roots = list(dict.fromkeys(roots))
        for root in roots:
            for version_dir in sorted(root.glob("v*"), reverse=True) if root.is_dir() else []:
                if not version_dir.is_dir():
                    continue
                manifest = read_json(version_dir / "build_manifest.json", {}) or {}
                validation = read_json(version_dir / "validation.json", {}) or {}
                scene = next(iter(sorted(version_dir.glob("*.m[ab]"))), None)
                state = str(validation.get("state") or validation.get("status") or "VERIFYING").upper()
                validation_results = [
                    dict(item)
                    for item in (validation.get("results") or [])
                    if isinstance(item, dict)
                ]
                rows.append(
                    {
                        "version": version_dir.name,
                        "directory": str(version_dir),
                        "state": state,
                        "scene": str(scene or ""),
                        "updated": datetime.fromtimestamp(version_dir.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
                        "components": len(manifest.get("components") or manifest.get("resolved_assets") or []),
                        "validation_results": validation_results,
                    }
                )
        return sorted(rows, key=lambda row: parse_version(row["version"]) or 0, reverse=True)

    def list_sequence_constructs(
        self, identity: SequenceIdentity, department: str, task: str, dcc: str = "maya"
    ) -> list[dict]:
        rows = []
        department_name = str(department or "main").strip().lower()
        task_name = str(task or "main").strip().lower()
        dcc_name = str(dcc or "maya").strip().lower()
        roots = [
            self.shots.sequence_build_root(identity, department_name) / department_name / dcc_name / task_name,
            self.shots.sequence_build_root(identity, department_name) / department_name / task_name,
            self.shots.unpartitioned_sequence_build_root(identity) / department_name / dcc_name / task_name,
            self.shots.unpartitioned_sequence_build_root(identity) / department_name / task_name,
            self.shots.legacy_sequence_build_root(identity) / department_name / task_name,
        ]
        roots = list(dict.fromkeys(roots))
        for version_dir in (
            version_dir
            for root in roots
            for version_dir in (sorted(root.glob("v*"), reverse=True) if root.is_dir() else [])
        ):
            if not version_dir.is_dir():
                continue
            manifest = read_json(version_dir / "build_manifest.json", {}) or {}
            validation = read_json(version_dir / "validation.json", {}) or {}
            scene = next(iter(sorted(version_dir.glob("*.m[ab]"))), None)
            rows.append({
                "version": version_dir.name,
                "state": str(validation.get("state") or validation.get("status") or "VERIFYING").upper(),
                "scene": str(scene or ""),
                "updated": datetime.fromtimestamp(version_dir.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
                "shots": list(manifest.get("shots") or []),
            })
        return sorted(rows, key=lambda row: parse_version(row["version"]) or 0, reverse=True)

    def next_output_version(self, identity: ShotIdentity) -> str:
        versions = self.list_outputs(identity)
        number = max((parse_version(row.version) or 0 for row in versions), default=0) + 1
        return format_version(number)

    def next_construct_version(
        self,
        identity: ShotIdentity,
        department: str,
        task: str,
        dcc: str = "maya",
    ) -> str:
        department_name = str(department or "main").strip().lower()
        task_name = str(task or "main").strip().lower()
        dcc_name = str(dcc or "maya").strip().lower()
        roots = (
            self.shots.shot_build_root(identity, department_name) / department_name / dcc_name / task_name,
            self.shots.shot_build_root(identity, department_name) / department_name / task_name,
            self.shots.unpartitioned_shot_build_root(identity) / department_name / dcc_name / task_name,
            self.shots.unpartitioned_shot_build_root(identity) / department_name / task_name,
            self.shots.legacy_shot_build_root(identity) / department_name / task_name,
        )
        roots = tuple(dict.fromkeys(roots))
        numbers = [
            parse_version(path.name) or 0
            for root in roots
            for path in root.glob("v*")
            if path.is_dir()
        ]
        return format_version(max(numbers, default=0) + 1)

    def next_sequence_output_version(self, identity: SequenceIdentity) -> str:
        root = (
            self.shots.sequence_workspace_root(identity.episode, identity.sequence)
            / "output"
            / "review"
            / "layout"
        )
        numbers = [
            parse_version(path.name) or 0
            for path in root.glob("v*")
            if path.is_dir()
        ] if root.is_dir() else []
        return format_version(max(numbers, default=0) + 1)

    def next_sequence_construct_version(
        self,
        identity: SequenceIdentity,
        department: str,
        task: str,
        dcc: str = "maya",
    ) -> str:
        department_name = str(department or "main").strip().lower()
        task_name = str(task or "main").strip().lower()
        dcc_name = str(dcc or "maya").strip().lower()
        roots = (
            self.shots.sequence_build_root(identity, department_name) / department_name / dcc_name / task_name,
            self.shots.sequence_build_root(identity, department_name) / department_name / task_name,
            self.shots.unpartitioned_sequence_build_root(identity) / department_name / dcc_name / task_name,
            self.shots.unpartitioned_sequence_build_root(identity) / department_name / task_name,
            self.shots.legacy_sequence_build_root(identity) / department_name / task_name,
        )
        roots = tuple(dict.fromkeys(roots))
        numbers = [
            parse_version(path.name) or 0
            for root in roots
            for path in root.glob("v*")
            if path.is_dir()
        ]
        return format_version(max(numbers, default=0) + 1)

    def maya_software_config_name(self) -> str:
        """Resolve the enabled Maya registration used by every build process.

        A project may contain multiple registrations (for example ``maya2024``
        and ``maya2024_2``).  Prefer an explicit build-worker registration,
        then the most recently enabled registration that has required build
        plug-ins, before falling back to the legacy maya2024 registration.
        """

        available = {
            path.stem.removeprefix("software_"): path
            for path in self.project_config.config_dir.glob("software_maya*.yml")
        }
        base = self.project_config.load("templates_base.yml") or {}
        enabled = [str(value) for value in (base.get("enabled_softwares") or [])]
        candidates = [available[name] for name in enabled if name in available]
        if not candidates:
            candidates = list(available.values())

        configured = str(
            (base.get("review_build") or {}).get("maya_software") or ""
        ).strip()
        configured_path = available.get(configured)
        if configured_path and (not enabled or configured in enabled):
            return configured_path.name

        for path in reversed(candidates):
            data = self.project_config.load(path.name) or {}
            if data.get("review_build_worker") or data.get("build_worker"):
                return path.name
        for path in reversed(candidates):
            data = self.project_config.load(path.name) or {}
            profiles = data.get("plugin_profiles") or {}
            if any((profile or {}).get("required") for profile in profiles.values()):
                return path.name

        preferred = self.project_config.config_dir / "software_maya2024.yml"
        if preferred.is_file():
            return preferred.name
        return candidates[-1].name if candidates else "software_maya2024.yml"

    def resolve_mayapy(self) -> Path:
        configured = os.environ.get("SMARTPIPELINE_MAYAPY")
        if configured and Path(configured).is_file():
            return Path(configured)
        selected = self.project_config.config_dir / self.maya_software_config_name()
        candidates = [selected]
        candidates.extend(path for path in sorted(
            self.project_config.config_dir.glob("software_maya*.yml")
        ) if path not in candidates)
        for config_path in candidates:
            data = self.project_config.load(config_path.name)
            maya_path = Path(str(data.get("path") or ""))
            if maya_path.suffix.lower() in {".bat", ".cmd"} and maya_path.is_file():
                text = maya_path.read_text(encoding="utf-8-sig", errors="ignore")
                match = re.search(
                    r"^\s*set\s+MAYAINSTPATH\s*=\s*(.+?)\s*$",
                    text,
                    flags=re.IGNORECASE | re.MULTILINE,
                )
                if match:
                    install_root = Path(match.group(1).strip().strip('"'))
                    mayapy = install_root / "bin" / "mayapy.exe"
                    if mayapy.is_file():
                        return mayapy
            mayapy = maya_path.with_name("mayapy.exe")
            if mayapy.is_file():
                return mayapy
        raise FileNotFoundError(
            "mayapy.exe was not resolved. Set SMARTPIPELINE_MAYAPY or configure software_maya*.yml."
        )

    def maya_software_config(self) -> dict:
        """Return the same merged Maya configuration used to resolve mayapy."""

        return self.project_config.load(self.maya_software_config_name())

    def maya_process_environment(self) -> tuple[dict[str, str], dict[str, list[str]]]:
        """Resolve scalar and path-list environment values for a Maya worker."""

        config = self.maya_software_config()
        # Older software configs stored Maya process switches as top-level
        # Software Settings. Keep those projects working while allowing an
        # explicit Env Variable entry to take precedence.
        configured_env = {
            str(key): value
            for key, value in config.items()
            if str(key).upper().startswith("MAYA_")
        }
        configured_env.update(config.get("env_vars") or {})
        env_vars = {
            str(key): expand_config_tokens(str(value), self.project_config)
            for key, value in configured_env.items()
            if str(key).strip()
        }
        paths = {}
        for key, values in (config.get("paths") or {}).items():
            if isinstance(values, str):
                text = values.strip()
                values = [] if text in {"", "[]", "null", "None"} else [text]
            paths[str(key)] = [
                expand_config_tokens(str(value), self.project_config)
                for value in (values or [])
                if str(value).strip()
            ]
        return env_vars, paths

    def shot_status(
        self,
        identity: ShotIdentity,
        *,
        mode: str = "WORK STAGE",
        department: str = "anim",
        task: str = "main",
        generate_review: bool = False,
        overrides: dict | None = None,
    ) -> ReviewShotStatus:
        if str(mode or "WORK STAGE").upper() == "WORK STAGE":
            return self._work_stage_status(
                identity,
                department=department,
                task=task,
                generate_review=generate_review,
                overrides=overrides or {},
            )
        source = self.shots.latest_animation_package_path(identity)
        source_version = source.parent.name if source else ""
        outputs = tuple(self.list_outputs(identity))
        latest_output = outputs[0] if outputs else None
        shot_data = self.shots.load_shot(identity)
        thumbnail = self.shots.shot_root(identity) / "thumbnail.jpg"

        if not source:
            state = "MISSING"
            message = "Animation Package is missing."
        elif not latest_output:
            state = "READY"
            message = "No review output has been generated."
        elif self._is_dirty(source, latest_output):
            state = "DIRTY"
            message = "Published inputs are newer than the last review build."
        elif not latest_output.movie:
            state = "READY"
            message = "Review scene exists, but the output movie is missing."
        else:
            state = "UP TO DATE"
            message = "Output movie matches the current Animation Package."

        output_version = latest_output.version if latest_output else ""
        if latest_output and latest_output.movie:
            output_label = output_version
        elif latest_output:
            output_label = f"{output_version} / MOV missing"
        else:
            output_label = "-"
        return ReviewShotStatus(
            identity=identity,
            state=state,
            output_version=output_version,
            output_label=output_label,
            last_review=latest_output.updated if latest_output else "-",
            thumbnail=str(thumbnail) if thumbnail.is_file() else "",
            comment=str(shot_data.get("status") or ""),
            source_version=source_version,
            message=message,
            outputs=outputs,
        )

    def _work_stage_status(
        self,
        identity: ShotIdentity,
        *,
        department: str,
        task: str,
        generate_review: bool,
        overrides: dict,
    ) -> ReviewShotStatus:
        """Evaluate WORK STAGE from Construct inputs, never Animation Package."""

        desired = self.shots.resolved_construct(
            identity,
            cast_contexts=dict(overrides.get("cast_contexts") or {}),
            exclude_cast=list(overrides.get("exclude_cast") or []),
            representation=str(overrides.get("representation") or "project"),
        )
        components = [
            row for row in (desired.get("components") or [])
            if isinstance(row, dict) and bool(row.get("enabled", True))
        ]
        def component_available(row: dict) -> bool:
            value = str(row.get("path") or "").strip()
            return bool(value) and Path(value).exists()

        missing = [
            str(row.get("name") or row.get("component_type") or "input")
            for row in components
            if bool(row.get("required", True))
            and not component_available(row)
        ]
        missing_curves = []
        curve_names = {
            str(row.get("name") or "")
            for row in components
            if str(row.get("component_type") or "").lower() == "animation_curve"
        }
        if str(department or "").lower() == "anim":
            cast_data = self.shots.load_cast(identity)
            if not (cast_data.get("cast") or {}):
                cast_data = self.shots.load_sequence_cast(
                    identity.episode, identity.sequence
                )
            excluded = {str(value) for value in overrides.get("exclude_cast") or []}
            placement_motion: dict[str, set[str]] = {}
            placement_paths = [
                Path(str(component.get("path") or ""))
                for component in components
                if str(component.get("component_type") or "").lower() == "placement"
                and str(component.get("path") or "").strip()
            ]
            # Motion intent belongs to the published Placement instance, not
            # to the transient "Use Placements" build toggle. Consult the
            # latest declaration even when placement application is disabled.
            list_placement_versions = getattr(self.shots, "list_placement_publish_versions", None)
            if callable(list_placement_versions):
                for row in list_placement_versions(identity):
                    if row.latest:
                        path = Path(str(row.path or ""))
                        if path not in placement_paths:
                            placement_paths.append(path)
            for placements_path in placement_paths:
                members = read_json(placements_path.parent / "placement_members.json", {}) or {}
                for row in members.get("placements") or []:
                    if not isinstance(row, dict):
                        continue
                    member = str(row.get("member") or "").strip()
                    if not member:
                        continue
                    motion = str(row.get("motion") or "STATIC").strip().upper()
                    placement_motion.setdefault(member, set()).add(
                        motion if motion in {"STATIC", "CURVE"} else "STATIC"
                    )
            missing_curves = [
                str(cast_key)
                for cast_key, entry in (cast_data.get("cast") or {}).items()
                if bool((entry or {}).get("required", True))
                and str((entry or {}).get("role") or "").strip().upper()
                not in {"BG", "BGA", "ENV", "BACKGROUND", "SET"}
                and str(cast_key) not in excluded
                and str(cast_key) not in curve_names
                and (
                    str(cast_key) not in placement_motion
                    or "CURVE" in placement_motion[str(cast_key)]
                )
            ]

        constructs = self.list_constructs(identity, department, task)
        latest = constructs[0] if constructs else None
        manifest = (
            read_json(
                Path(str(latest.get("directory") or "")) / "build_manifest.json",
                {},
            ) or {}
            if latest else {}
        )
        recorded = manifest.get("construct") or {"components": []}
        changes = self.construct_diff(identity, current=recorded, desired=desired)
        changed = [row for row in changes if row.get("change") != "UNCHANGED"]

        curve_versions = sorted({
            str(row.get("version") or "")
            for row in components
            if str(row.get("component_type") or "").lower() == "animation_curve"
            and str(row.get("version") or "")
        })
        source_version = ", ".join(curve_versions)
        if missing or missing_curves:
            state = "MISSING"
            details = []
            if missing:
                details.append("required inputs: " + ", ".join(missing))
            if missing_curves:
                details.append("Animation Curves: " + ", ".join(missing_curves))
            message = "Missing " + "; ".join(details) + "."
        elif not latest:
            state = "READY"
            message = "Construct inputs and Animation Curves are ready."
        elif changed:
            state = "DIRTY"
            names = [
                str((row.get("after") or row.get("before") or {}).get("name") or "input")
                for row in changed
            ]
            message = "Construct inputs changed: " + ", ".join(dict.fromkeys(names)) + "."
        elif generate_review and not Path(
            str(manifest.get("review_movie") or "")
        ).is_file():
            state = "READY"
            message = "Construct is current; Generate Review MOV is not available."
        else:
            state = "UP TO DATE"
            message = (
                "Construct and Animation Curves are current; review was generated."
                if generate_review
                else "Construct and Animation Curves are current."
            )

        shot_data = self.shots.load_shot(identity)
        thumbnail = self.shots.shot_root(identity) / "thumbnail.jpg"
        version = str(latest.get("version") or "") if latest else ""
        outputs = tuple(self.list_outputs(identity))
        latest_review = outputs[0] if outputs else None
        return ReviewShotStatus(
            identity=identity,
            state=state,
            output_version=version,
            output_label=version or "-",
            last_review=(
                latest_review.updated
                if latest_review else str(latest.get("updated") or "-") if latest else "-"
            ),
            thumbnail=str(thumbnail) if thumbnail.is_file() else "",
            comment=str(shot_data.get("status") or ""),
            source_version=source_version,
            message=message,
            outputs=outputs,
        )

    def list_outputs(self, identity: ShotIdentity) -> list[ReviewOutput]:
        rows: list[ReviewOutput] = []
        formal_root = self.shots.shot_root(identity) / "review"
        for review_json in formal_root.glob("*/*/v*/review.json") if formal_root.is_dir() else []:
            version_dir = review_json.parent
            data = read_json(review_json, {}) or {}
            movie = self._first_file(version_dir, MOV_EXTENSIONS)
            timestamps = [
                path.stat().st_mtime
                for path in (movie, review_json)
                if path and path.is_file()
            ]
            profile = version_dir.parent.name
            rows.append(
                ReviewOutput(
                    version=f"{profile}/{version_dir.name}",
                    directory=str(version_dir),
                    movie=str(movie) if movie else "",
                    updated=(
                        datetime.fromtimestamp(max(timestamps)).strftime("%Y-%m-%d %H:%M")
                        if timestamps else ""
                    ),
                    state=str(data.get("state") or "SUBMITTED"),
                )
            )
        root = (
            self.shots.shot_root(identity)
            / "output"
            / "review"
            / "animation"
        )
        for version_dir in root.glob("v*") if root.is_dir() else []:
            if not version_dir.is_dir() or parse_version(version_dir.name) is None:
                continue
            scene = self._first_file(version_dir, {".ma", ".mb"})
            movie = self._first_file(version_dir, MOV_EXTENSIONS)
            manifest = version_dir / "build_manifest.json"
            manifest_data = read_json(manifest, {}) or {}
            recorded_scene = Path(str(manifest_data.get("scene") or ""))
            if not scene and recorded_scene.is_file():
                scene = recorded_scene
            timestamps = [
                path.stat().st_mtime
                for path in (scene, movie, manifest)
                if path and path.is_file()
            ]
            updated = (
                datetime.fromtimestamp(max(timestamps)).strftime("%Y-%m-%d %H:%M")
                if timestamps
                else ""
            )
            state = "COMPLETE" if movie else ("SCENE ONLY" if scene else "MISSING")
            rows.append(
                ReviewOutput(
                    version=version_dir.name,
                    directory=str(version_dir),
                    scene=str(scene) if scene else "",
                    movie=str(movie) if movie else "",
                    updated=updated,
                    state=state,
                )
            )
        return sorted(rows, key=lambda row: (row.updated, row.version), reverse=True)

    def accept_output_to_work(
        self,
        identity: ShotIdentity,
        output: ReviewOutput,
        *,
        department: str = "anim",
        task: str = "",
        option: str = "main",
    ) -> Path:
        source = Path(output.scene)
        if not source.is_file():
            raise FileNotFoundError("The selected output has no generated scene to accept.")
        task = task or self.shots.shot_tasks(department)[0]
        current_rows = self.shots.list_shot_work_files(
            identity,
            department=department,
            option=option,
            tool_name="maya",
            task=task,
        )
        current = current_rows[0].path if current_rows else None
        destination = self.shots.next_shot_work_path(
            identity,
            department,
            current_path=current,
            next_version=bool(current),
            option=option,
            tool_name="maya",
            ext=source.suffix.lstrip("."),
            task=task,
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        self.shots.write_shot_work_metadata(
            destination,
            identity,
            department,
            option=option,
            task=task,
            comment=f"Accepted from Review Build {output.version}",
        )
        return destination

    @staticmethod
    def _first_file(root: Path, extensions: set[str]) -> Path | None:
        matches = sorted(
            (
                path
                for path in root.rglob("*")
                if path.is_file() and path.suffix.lower() in extensions
            ),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        return matches[0] if matches else None

    @staticmethod
    def _is_dirty(source: Path, output: ReviewOutput) -> bool:
        output_dir = Path(output.directory)
        build_manifest = read_json(output_dir / "build_manifest.json", {}) or {}
        recorded = Path(str(build_manifest.get("animation_manifest") or ""))
        if recorded and recorded.as_posix().lower() != source.as_posix().lower():
            return True
        comparison = Path(output.movie or output.scene)
        if not comparison.is_file():
            return True
        if source.stat().st_mtime > comparison.stat().st_mtime:
            return True
        review_spec = Path(str(build_manifest.get("review_spec") or ""))
        return review_spec.is_file() and review_spec.stat().st_mtime > comparison.stat().st_mtime
