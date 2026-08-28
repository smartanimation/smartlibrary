# Path Resolver Audit

Generated: 2026-08-25

## Summary

- P0 hardcoded absolute runtime paths: 42
- P1 production paths assembled outside resolvers: 10
- P2 review candidates/fallbacks: 107

This is a static audit. Each finding must be confirmed against call flow and configuration ownership.

## Findings

### P0 `bat/run_editorial_intake.bat:17`

- Kind: `absolute-path`
- Reason: Runtime code contains an absolute drive/UNC path.
- Code: `echo   %~nx0 template D:\Projects\STKB\incoming\editorial\events_template.csv`

### P0 `bat/run_editorial_intake.bat:18`

- Kind: `absolute-path`
- Reason: Runtime code contains an absolute drive/UNC path.
- Code: `echo   %~nx0 intake --csv D:\Projects\STKB\incoming\editorial\events.csv --mov D:\Projects\STKB\incoming\editorial\offline.mov --comment "first editorial publish"`

### P0 `packages/smartlib/apps/asset_assembly/ui.py:489`

- Kind: `absolute-path`
- Reason: Runtime code contains an absolute drive/UNC path.
- Code: `QtWidgets.QMessageBox.information(self, "Capture Viewport Thumbnail", f"Updated thumbnail:\n{path}")`

### P0 `packages/smartlib/apps/asset_assembly/ui.py:685`

- Kind: `absolute-path`
- Reason: Runtime code contains an absolute drive/UNC path.
- Code: `QtWidgets.QMessageBox.information(self, "Create Thumbnail", f"Created thumbnail:\n{path}")`

### P0 `packages/smartlib/apps/editorial_intake/service.py:216`

- Kind: `absolute-path`
- Reason: Runtime code contains an absolute drive/UNC path.
- Code: `raise ValueError("Editorial preflight failed:\n- " + "\n- ".join(errors))`

### P0 `packages/smartlib/apps/launcher/main.py:555`

- Kind: `absolute-path`
- Reason: Runtime code contains an absolute drive/UNC path.
- Code: `lines.append(f"ROOT: <a href='file:///{self.projectroot}' style='color: #55aaff; text-decoration: none;'>{self.projectroot}</a>")`

### P0 `packages/smartlib/apps/review_build_manager/window.py:1279`

- Kind: `absolute-path`
- Reason: Runtime code contains an absolute drive/UNC path.
- Code: `"Publish the following in Shot Manager before submitting:\n\n"`

### P0 `packages/smartlib/apps/review_build_manager/window.py:1391`

- Kind: `absolute-path`
- Reason: Runtime code contains an absolute drive/UNC path.
- Code: `f"Created a new Work version:\n{path}",`

### P0 `packages/smartlib/apps/review_build_manager/worker.py:486`

- Kind: `absolute-path`
- Reason: Runtime code contains an absolute drive/UNC path.
- Code: `"  function normalizedName(value) { return String(value || '').replace(/[\\u200B-\\u200D\\uFEFF]/g, '').replace(/\\s+/g, ' ').replace(/^\\s+|\\s+$/g, '').toLowerCase(); }",`

### P0 `packages/smartlib/apps/set_dress/ui.py:397`

- Kind: `absolute-path`
- Reason: Runtime code contains an absolute drive/UNC path.
- Code: `self, "Publish Set Dress", f"Published:\n{published}"`

### P0 `packages/smartlib/apps/shot_manager/service.py:635`

- Kind: `absolute-path`
- Reason: Runtime code contains an absolute drive/UNC path.
- Code: `if not source or source.lower().startswith("package://"):`

### P0 `packages/smartlib/apps/shot_manager/service.py:3222`

- Kind: `absolute-path`
- Reason: Runtime code contains an absolute drive/UNC path.
- Code: `"USD Skel animation is incompatible with the Asset USD:\n- "`

### P0 `packages/smartlib/apps/shot_manager/service.py:5284`

- Kind: `absolute-path`
- Reason: Runtime code contains an absolute drive/UNC path.
- Code: `return f"shot://{rel}"`

### P0 `packages/smartlib/apps/smart_casting/service.py:222`

- Kind: `absolute-path`
- Reason: Runtime code contains an absolute drive/UNC path.
- Code: `return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?{urlencode(query)}"`

### P0 `packages/smartlib/apps/smart_casting/ui.py:886`

- Kind: `absolute-path`
- Reason: Runtime code contains an absolute drive/UNC path.
- Code: `QtWidgets.QMessageBox.information(self, "Add Selected to Cast", f"Added {len(rows)} cast rows:\n{path}")`

### P0 `packages/smartlib/apps/smart_delivery/window.py:132`

- Kind: `absolute-path`
- Reason: Runtime code contains an absolute drive/UNC path.
- Code: `f"Resolved from source manifest:\n{manifest}\n\nConfirm every source, then run Dry Run."`

### P0 `packages/smartlib/apps/smart_ingest/main.py:503`

- Kind: `absolute-path`
- Reason: Runtime code contains an absolute drive/UNC path.
- Code: `QtWidgets.QMessageBox.warning(self, label, f"Path was not found:\n{path}")`

### P0 `packages/smartlib/apps/smart_ingest/main.py:620`

- Kind: `absolute-path`
- Reason: Runtime code contains an absolute drive/UNC path.
- Code: `QtWidgets.QMessageBox.warning(self, label, f"Folder was not found:\n{folder}")`

### P0 `packages/smartlib/apps/smart_ingest/main.py:661`

- Kind: `absolute-path`
- Reason: Runtime code contains an absolute drive/UNC path.
- Code: `message += "\n\nErrors:\n" + "\n".join(errors)`

### P0 `packages/smartlib/apps/smart_playblast/ui.py:1029`

- Kind: `absolute-path`
- Reason: Runtime code contains an absolute drive/UNC path.
- Code: `f"Image sequence exported:\n{output_text}\n\n{summary}",`

### P0 `packages/smartlib/apps/smart_playblast/ui.py:1133`

- Kind: `absolute-path`
- Reason: Runtime code contains an absolute drive/UNC path.
- Code: `f"Manifest:\n{path}",`

### P0 `packages/smartlib/apps/smart_playblast/ui.py:1158`

- Kind: `absolute-path`
- Reason: Runtime code contains an absolute drive/UNC path.
- Code: `QtWidgets.QMessageBox.warning(self, "Open Output Folder", f"Folder was not found:\n{folder}")`

### P0 `packages/smartlib/apps/viewer/service.py:318`

- Kind: `absolute-path`
- Reason: Runtime code contains an absolute drive/UNC path.
- Code: `Path("C:/Program Files/ShotGrid/RV-2023.0.2/src/python"),`

### P0 `packages/smartlib/dcc/houdini/crowd_kinefx.py:2423`

- Kind: `absolute-path`
- Reason: Runtime code contains an absolute drive/UNC path.
- Code: `_add_note(parent, "character_fbx", f"Import character FBX with KineFX:\n{_as_posix(files.character_fbx)}")`

### P0 `packages/smartlib/dcc/houdini/crowd_kinefx.py:2451`

- Kind: `absolute-path`
- Reason: Runtime code contains an absolute drive/UNC path.
- Code: `f"A File SOP was created so the FBX path is still visible:\n{_as_posix(path)}",`

### P0 `packages/smartlib/dcc/houdini/crowd_kinefx.py:2454`

- Kind: `absolute-path`
- Reason: Runtime code contains an absolute drive/UNC path.
- Code: `_add_note(parent, f"{label}_fbx", f"Import animation FBX with KineFX:\n{_as_posix(path)}")`

### P0 `packages/smartlib/dcc/houdini/crowd_kinefx.py:2740`

- Kind: `absolute-path`
- Reason: Runtime code contains an absolute drive/UNC path.
- Code: `"Agent/Crowd migration path:\n"`

### P0 `packages/smartlib/dcc/houdini/crowd_kinefx.py:2900`

- Kind: `absolute-path`
- Reason: Runtime code contains an absolute drive/UNC path.
- Code: `"OUT_BEHAVIOR_AGENT_POINTS contains only agent points with normalized attrs:\n"`

### P0 `packages/smartlib/dcc/houdini/crowd_kinefx.py:2946`

- Kind: `absolute-path`
- Reason: Runtime code contains an absolute drive/UNC path.
- Code: `"It preserves behavior attributes and adds crowd-friendly aliases:\n"`

### P0 `packages/smartlib/dcc/houdini/crowd_kinefx.py:3352`

- Kind: `absolute-path`
- Reason: Runtime code contains an absolute drive/UNC path.
- Code: `"The node reads agent_crowd_pipeline/OUT_CROWD_CLIP_STATE_DRIVER and uses the first agent point:\n"`

### P0 `packages/smartlib/dcc/maya/asset_assembly.py:1959`

- Kind: `absolute-path`
- Reason: Runtime code contains an absolute drive/UNC path.
- Code: `f"asset://{component.get('category')}/{component.get('group')}/{component.get('asset')}/"`

### P0 `packages/smartlib/dcc/maya/asset_context.py:68`

- Kind: `absolute-path`
- Reason: Runtime code contains an absolute drive/UNC path.
- Code: `f"Context snapshot was written, but Maya could not restore the previous scene:\n"`

### P0 `packages/smartlib/dcc/maya/asset_context.py:158`

- Kind: `absolute-path`
- Reason: Runtime code contains an absolute drive/UNC path.
- Code: `raise RuntimeError("Context USD Skel validation failed:\n- " + "\n- ".join(issues))`

### P0 `packages/smartlib/dcc/maya/asset_context.py:189`

- Kind: `absolute-path`
- Reason: Runtime code contains an absolute drive/UNC path.
- Code: `"Context payload validation failed:\n- " + "\n- ".join(validation["issues"])`

### P0 `packages/smartlib/dcc/maya/startup/userSetup.py:13`

- Kind: `absolute-path`
- Reason: Runtime code contains an absolute drive/UNC path.
- Code: `or "P:/dev/smartlibrary"`

### P0 `packages/smartlib/dcc/maya/usd_skel.py:25`

- Kind: `absolute-path`
- Reason: Runtime code contains an absolute drive/UNC path.
- Code: `raise RuntimeError("USD Skel validation failed:\n- " + "\n- ".join(issues))`

### P0 `packages/smartlib/dcc/maya/usd_skel.py:72`

- Kind: `absolute-path`
- Reason: Runtime code contains an absolute drive/UNC path.
- Code: `"Published USD Skel validation failed:\n- "`

### P0 `packages/smartlib/dcc/resolve/export_timeline_ui.py:340`

- Kind: `absolute-path`
- Reason: Runtime code contains an absolute drive/UNC path.
- Code: `f"Reference:\n{reference}\n\n"`

### P0 `packages/smartlib/dcc/resolve/export_timeline_ui.py:341`

- Kind: `absolute-path`
- Reason: Runtime code contains an absolute drive/UNC path.
- Code: `f"Offline:\n{movie}",`

### P0 `packages/smartlib/dcc/resolve/export_timeline_ui.py:360`

- Kind: `absolute-path`
- Reason: Runtime code contains an absolute drive/UNC path.
- Code: `f"Expected:\neditorial/data/{episode}/{sequence}/edit_source/v###/*.{reference_type}",`

### P0 `packages/smartlib/dcc/resolve/menu/Smart_Editorial_Export.py:13`

- Kind: `absolute-path`
- Reason: Runtime code contains an absolute drive/UNC path.
- Code: `return Path("P:/dev/smartlibrary")`

### P0 `packages/smartlib/delivery/after_effects.py:128`

- Kind: `absolute-path`
- Reason: Runtime code contains an absolute drive/UNC path.
- Code: `"  function clean(value) { return String(value || '').replace(/[\\u200B-\\u200D\\uFEFF]/g, '').replace(/\\s+/g, '').toLowerCase(); }",`

### P1 `packages/smartlib/apps/asset_assembly/ui.py:1062`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `assets_root = root / "assets"`

### P1 `packages/smartlib/apps/editorial_intake/service.py:47`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `return self.project_root / "editorial" / "work"`

### P1 `packages/smartlib/apps/shot_manager/service.py:4173`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `self.paths.project_root / "editorial" / "publish" / identity.episode / identity.sequence,`

### P1 `packages/smartlib/apps/shot_manager/service.py:4676`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `"editorial": self._relative_to_project(self.paths.project_root / "editorial" / "publish" / identity.episode / identity.sequence / "latest.json"),`

### P1 `packages/smartlib/apps/smart_sequence_builder/service.py:289`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `self.project_root / "editorial" / identity.episode / identity.sequence,`

### P1 `packages/smartlib/dcc/maya/shot_builder.py:1704`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `project_root / "workspace" / "editorial" / "publish" / episode / sequence,`

### P1 `packages/smartlib/dcc/maya/shot_builder.py:1705`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `project_root / "editorial" / "publish" / episode / sequence,`

### P1 `packages/smartlib/dcc/maya/smart_shot.py:283`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `latest = project_root / "editorial" / "publish" / episode / sequence / "latest.json"`

### P1 `packages/smartlib/dcc/maya/smart_shot.py:522`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `editorial_latest = project_root / "editorial" / "publish" / episode / sequence / "latest.json"`

### P1 `packages/smartlib/editorial/storyreel.py:166`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `publish_root = self.project_root / "editorial" / "publish"`

### P2 `packages/smartlib/apps/asset_assembly/ui.py:1106`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `variant_dir / "publish" / "model" / "render" / "latest.json",`

### P2 `packages/smartlib/apps/asset_assembly/ui.py:1112`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `variant_dir / "publish" / "usd",`

### P2 `packages/smartlib/apps/asset_assembly/ui.py:1113`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `variant_dir / "publish" / "model" / "proxy",`

### P2 `packages/smartlib/apps/asset_assembly/ui.py:1114`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `variant_dir / "publish" / "model" / "render",`

### P2 `packages/smartlib/apps/asset_manager/construct.py:62`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `self.data_root = self.variant_root / "data"`

### P2 `packages/smartlib/apps/asset_manager/retarget_publish.py:47`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `publish_root = Path(asset_root) / variant / "publish" / "asset"`

### P2 `packages/smartlib/apps/asset_manager/retarget_publish.py:124`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `asset_root / variant / "work" / "rig" / "retarget" / f"{asset_root.name}_retarget.json"`

### P2 `packages/smartlib/apps/asset_manager/retarget_publish.py:155`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `return Path(asset_root) / variant / "publish" / "retarget"`

### P2 `packages/smartlib/apps/asset_manager/retarget_publish.py:159`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `return Path(asset_root) / variant / "data" / "retarget"`

### P2 `packages/smartlib/apps/asset_manager/retarget_publish.py:210`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `write_json(root / "latest.json", {"version": version, "profile": f"{version}/{target.name}", "data": f"{version}/data.json"})`

### P2 `packages/smartlib/apps/asset_manager/retarget_publish.py:338`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `write_json(root / "latest.json", {"version": version, "publish": f"{version}/publish.json", "profile": f"{version}/{target_profile.name}"})`

### P2 `packages/smartlib/apps/review_build_manager/service.py:1104`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `formal_root = self.shots.shot_root(identity) / "review"`

### P2 `packages/smartlib/apps/review_build_manager/worker.py:1218`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `review_movie = review_output_dir / "output" / "review.mov"`

### P2 `packages/smartlib/apps/review_build_manager/worker.py:1270`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `target=review_output_dir / "output" / "thumbnail.jpg",`

### P2 `packages/smartlib/apps/shot_manager/service.py:326`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `return self.sequence_workspace_root(identity.episode, identity.sequence) / "output" / "scene_build"`

### P2 `packages/smartlib/apps/shot_manager/service.py:660`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `data_root = self.sequence_workspace_root(identity.episode, identity.sequence) / "data"`

### P2 `packages/smartlib/apps/shot_manager/service.py:1795`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `_pipeline_root() / "templates" / "ae" / "review" / "review_base.aep",`

### P2 `packages/smartlib/apps/shot_manager/service.py:2197`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `placements_root = sequence_root / "publish" / "layout" / "placements"`

### P2 `packages/smartlib/apps/shot_manager/service.py:2211`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `base_dir = sequence_root / "publish" / "stage_input" / department`

### P2 `packages/smartlib/apps/shot_manager/service.py:3364`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `asset_base = variant_root / "publish" / "asset" / context_subset`

### P2 `packages/smartlib/apps/shot_manager/service.py:3384`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `base_dir = variant_root / "publish" / "rig" / (subset or "anim")`

### P2 `packages/smartlib/apps/shot_manager/service.py:3873`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `/ clean_department / "data" / clean_type / clean_target / clean_subset`

### P2 `packages/smartlib/apps/shot_manager/service.py:4117`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `return self._list_data_versions(self.sequence_workspace_root(identity.episode, identity.sequence) / department / "data")`

### P2 `packages/smartlib/apps/shot_manager/service.py:4169`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `self._status_from_latest("placements", root / "publish" / "layout" / "placements"),`

### P2 `packages/smartlib/apps/shot_manager/service.py:4170`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `self._status_from_camera_publishes(root / "publish" / "camera"),`

### P2 `packages/smartlib/apps/shot_manager/service.py:4322`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `base_dir = self.sequence_workspace_root(episode, sequence) / "publish" / "cast" / "main"`

### P2 `packages/smartlib/apps/shot_manager/service.py:4346`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `source_base = sequence_root / "publish" / "layout" / "placements"`

### P2 `packages/smartlib/apps/shot_manager/service.py:4963`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `asset_root = variant_root / "publish" / "asset"`

### P2 `packages/smartlib/apps/shot_manager/service.py:5078`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `base_dir = self.sequence_workspace_root(identity.episode, identity.sequence) / "publish" / "camera" / identity.shot / camera_option`

### P2 `packages/smartlib/apps/shot_manager/service.py:5166`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `self.sequence_workspace_root(identity.episode, identity.sequence) / "publish" / "usd" / identity.shot / "layout.usda",`

### P2 `packages/smartlib/apps/shot_manager/service.py:5167`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `self.sequence_workspace_root(identity.episode, identity.sequence) / "publish" / "usd" / identity.shot / "layout.usd",`

### P2 `packages/smartlib/apps/shot_manager/service.py:5177`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `self.sequence_workspace_root(identity.episode, identity.sequence) / "publish" / "layout" / identity.shot / "usd",`

### P2 `packages/smartlib/apps/shot_manager/service.py:5178`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `self.sequence_workspace_root(identity.episode, identity.sequence) / "publish" / "layout" / identity.shot / "main",`

### P2 `packages/smartlib/apps/shot_manager/service.py:5179`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `self.sequence_workspace_root(identity.episode, identity.sequence) / "publish" / "layout" / identity.shot / "proxy",`

### P2 `packages/smartlib/apps/shot_manager/service.py:5445`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `context_root = variant_root / "publish" / "asset" / "work"`

### P2 `packages/smartlib/apps/shot_manager/service.py:5446`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `legacy_context_root = variant_root / "publish" / "asset" / "asset_work"`

### P2 `packages/smartlib/apps/shot_manager/service.py:6038`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `take = next_review_take(self.shot_publish_root(identity) / "review" / department / version_label)`

### P2 `packages/smartlib/apps/smart_casting/service.py:399`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `context_root = variant_root / "publish" / "asset" / context.lower()`

### P2 `packages/smartlib/apps/smart_delivery/service.py:36`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `root = self.shots.shot_root(identity) / "review" / department / profile`

### P2 `packages/smartlib/apps/smart_ingest/service.py:871`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `self._editorial_data_root() / "data" / episode / sequence`

### P2 `packages/smartlib/apps/smart_ingest/service.py:1266`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `return self._next_version(self._editorial_data_root() / "data" / episode / sequence / subset)`

### P2 `packages/smartlib/apps/smart_playblast/ui.py:826`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `version_dir = publish_root / "review" / department / f"v{version:03d}"`

### P2 `packages/smartlib/apps/smart_sequence_builder/service.py:200`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `mocap_root = self._first_existing(workspace / "data" / "mocap", sequence_root / "data" / "mocap")`

### P2 `packages/smartlib/apps/smart_sequence_builder/service.py:202`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `workspace / "data" / "virtual_camera",`

### P2 `packages/smartlib/apps/smart_sequence_builder/service.py:203`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `sequence_root / "data" / "virtual_camera",`

### P2 `packages/smartlib/apps/smart_sequence_builder/service.py:204`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `workspace / "publish" / "camera",`

### P2 `packages/smartlib/apps/smart_sequence_builder/service.py:205`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `sequence_root / "publish" / "camera",`

### P2 `packages/smartlib/apps/smart_sequence_builder/service.py:213`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `workspace / "data" / "light",`

### P2 `packages/smartlib/apps/smart_sequence_builder/service.py:214`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `sequence_root / "data" / "light",`

### P2 `packages/smartlib/apps/smart_sequence_builder/service.py:283`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `return root / "layout" / "work" / "maya" / "main" / f"{identity.code}_layout_v001_01.ma"`

### P2 `packages/smartlib/core/folder_structure.py:100`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `work_source = source / "work"`

### P2 `packages/smartlib/dcc/blender/smart_asset_panel.py:126`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `base = asset.variant_root(variant) / "data" / "model"`

### P2 `packages/smartlib/dcc/maya/asset_assembly.py:219`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `base_dir = paths.asset_variant_root(context.identity) / "data" / "assembly" / "placements"`

### P2 `packages/smartlib/dcc/maya/asset_assembly.py:562`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `base_dir = paths.asset_variant_root(context.identity) / "publish" / "assembly" / "render"`

### P2 `packages/smartlib/dcc/maya/asset_assembly.py:637`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `base_dir = paths.asset_variant_root(context.identity) / "data" / "assembly" / "placements"`

### P2 `packages/smartlib/dcc/maya/asset_assembly.py:661`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `preview_dir = paths.asset_variant_root(context.identity) / "data" / "assembly" / "preview"`

### P2 `packages/smartlib/dcc/maya/asset_assembly.py:704`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `base_dir = paths.asset_variant_root(context.identity) / "data" / "assembly" / "saved"`

### P2 `packages/smartlib/dcc/maya/asset_assembly.py:951`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `base_dir = paths.asset_variant_root(identity) / "publish" / "usd"`

### P2 `packages/smartlib/dcc/maya/asset_assembly.py:1115`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `assembly_base = paths.asset_variant_root(context.identity) / "publish" / "assembly" / "render"`

### P2 `packages/smartlib/dcc/maya/asset_assembly.py:1123`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `preview_path = paths.asset_variant_root(context.identity) / "data" / "assembly" / "preview" / "assembly.usda"`

### P2 `packages/smartlib/dcc/maya/asset_assembly.py:1127`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `base_dir = paths.asset_variant_root(context.identity) / "publish" / "usd"`

### P2 `packages/smartlib/dcc/maya/asset_assembly.py:1155`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `base_dir = paths.asset_variant_root(identity) / "data" / "assembly" / "saved"`

### P2 `packages/smartlib/dcc/maya/asset_assembly.py:1189`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `base_dir = paths.asset_variant_root(identity) / "publish" / "usd"`

### P2 `packages/smartlib/dcc/maya/asset_assembly.py:2265`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `return paths.asset_variant_root(identity) / "work" / department / "maya" / subset / filename`

### P2 `packages/smartlib/dcc/maya/asset_assembly.py:2299`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `base_dir = paths.asset_variant_root(identity) / "publish" / "usd"`

### P2 `packages/smartlib/dcc/maya/placement.py:500`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `candidates.extend(_latest_metadata_candidates(variant_root / "publish" / "rig"))`

### P2 `packages/smartlib/dcc/maya/placement.py:501`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `candidates.extend(_latest_metadata_candidates(variant_root / "publish" / "asset"))`

### P2 `packages/smartlib/dcc/maya/placement.py:568`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `return root / "layout" / "data" / "placements"`

### P2 `packages/smartlib/dcc/maya/placement.py:569`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `return root / "data" / "placements"`

### P2 `packages/smartlib/dcc/maya/placement.py:579`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `return root / "publish" / "layout" / "placements"`

### P2 `packages/smartlib/dcc/maya/placement.py:580`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `return root / "publish" / "layout" / "placements"`

### P2 `packages/smartlib/dcc/maya/set_dress.py:304`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `return Path(explicit_shot_root) / "data" / "setdress" / f"{package}.setdress.json"`

### P2 `packages/smartlib/dcc/maya/shot_validation_package.py:103`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `package_root = shot_root / "publish" / "anim" / "package"`

### P2 `packages/smartlib/dcc/maya/shot_validation_package.py:221`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `base = service.shot_root(identity) / "publish" / "layout" / "placements"`

### P2 `packages/smartlib/dcc/maya/shot_validation_package.py:295`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `base = service.shot_root(identity) / "publish" / "camera" / target / "main"`

### P2 `packages/smartlib/dcc/maya/shot_validation_package.py:430`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `base = service.shot_root(identity) / "publish" / "layout" / "setdress"`

### P2 `packages/smartlib/dcc/maya/smart_shot.py:407`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `write_json(version_dir / "output.json", {"record_type": "output", "output_type": "review", "subset": dept, "version": version_dir.name})`

### P2 `packages/smartlib/dcc/resolve/export_timeline_csv.py:595`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `return Path(editorial_root) / "work" / episode / sequence`

### P2 `packages/smartlib/delivery/engine.py:56`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `movies = [plan.package_root / row.destination for row in plan.items if row.kind == "review" or row.destination.suffix.lower() == ".mov"]`

### P2 `packages/smartlib/editorial/intake.py:287`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `base_dir = self.editorial_root / "publish" / publish_episode / publish_sequence`

### P2 `packages/smartlib/editorial/intake.py:384`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `version_dir = shot_root / "data" / "audio" / publish_dir.name`

### P2 `packages/smartlib/editorial/intake.py:406`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `shot_root / "data" / "audio" / "latest.json",`

### P2 `packages/smartlib/editorial/intake.py:452`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `sequence_root / "data" / "audio" / "latest.json",`

### P2 `packages/smartlib/editorial/intake.py:597`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `audio_manifest = read_json(shot_root / "data" / "audio" / "latest.json", {}) or {}`

### P2 `packages/smartlib/editorial/intake.py:612`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `base_dir = self.editorial_root / "work"`

### P2 `packages/smartlib/editorial/intake.py:861`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `root / "layout" / "work" / "maya",`

### P2 `packages/smartlib/editorial/intake.py:862`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `root / "layout" / "work" / "houdini",`

### P2 `packages/smartlib/editorial/intake.py:864`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `paths.extend(root / "publish" / publish_type for publish_type in ("camera", "blocking", "staging", "layout", "review"))`

### P2 `packages/smartlib/review/ae.py:49`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `shot_template_root = shot_root / "review" / "templates"`

### P2 `packages/smartlib/review/ae.py:81`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `project_template_root = project_root / "settings" / "templates" / "ae" / "review"`

### P2 `packages/smartlib/review/ae.py:85`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `pipeline_template_root = pipeline_root / "templates" / "ae" / "review"`

### P2 `packages/smartlib/review/ae.py:169`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `manifest_path = Path(manifest_path) if manifest_path else publish_root / "ae" / "data" / "review_build.json"`

### P2 `packages/smartlib/review/ae.py:170`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `log_path = Path(log_path) if log_path else publish_root / "ae" / "data" / "build_review.log"`

### P2 `packages/smartlib/review/package.py:38`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `base_dir = Path(publish_root) / "review" / department if publish_root else Path(shot_root) / "publish" / "review" / department`

### P2 `packages/smartlib/review/package.py:52`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `base_dir = Path(publish_root) / "review" / department if publish_root else Path(shot_root) / "publish" / "review" / department`

### P2 `packages/smartlib/review/workflow.py:189`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `return self.shot_root / "data" / "shot_composition"`

### P2 `packages/smartlib/review/workflow.py:198`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `return self.shot_root / "data" / "review_layers"`

### P2 `packages/smartlib/review/workflow.py:202`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `return self.shot_root / "publish" / "precomp"`

### P2 `packages/smartlib/review/workflow.py:206`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `self.workspace_shot_root / "build" / "review" / safe_slug(department)`

### P2 `packages/smartlib/review/workflow.py:261`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `return self.workspace_shot_root / "jobs" / "review"`

### P2 `packages/smartlib/review/workflow.py:267`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `return self.shot_root / "review" / safe_slug(department) / safe_slug(profile)`

### P2 `packages/smartlib/setdress/service.py:73`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `return self.paths.sequence_workspace_root(identity.episode, identity.sequence) / "data" / "setdress" / filename`

### P2 `packages/smartlib/setdress/service.py:76`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `return self.paths.shot_root(identity.episode, identity.sequence, identity.shot) / "data" / "setdress" / filename`

### P2 `packages/smartlib/setdress/service.py:95`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `base = self.paths.sequence_workspace_root(identity.episode, identity.sequence) / "publish" / "setdress" / clean_package`

### P2 `packages/smartlib/setdress/service.py:99`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `base = self.paths.shot_root(identity.episode, identity.sequence, identity.shot) / "publish" / "setdress" / clean_package`

### P2 `packages/smartlib/setdress/service.py:129`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `root = self.paths.sequence_workspace_root(identity.episode, identity.sequence) / "publish" / "setdress"`

### P2 `packages/smartlib/setdress/service.py:133`

- Kind: `manual-production-path`
- Reason: Production hierarchy is assembled outside a resolver module.
- Code: `root = self.paths.shot_root(identity.episode, identity.sequence, identity.shot) / "publish" / "setdress"`

## Resolver API Inventory

- `packages/smartlib/core/asset_publish_resolver.py:30` `__init__()`
- `packages/smartlib/core/asset_publish_resolver.py:33` `rules()`
- `packages/smartlib/core/asset_publish_resolver.py:58` `rule_for()`
- `packages/smartlib/core/asset_publish_resolver.py:70` `resolve()`
- `packages/smartlib/core/asset_publish_resolver.py:94` `_asset_context_for_stage()`
- `packages/smartlib/core/asset_publish_resolver.py:118` `resolve_context()`
- `packages/smartlib/core/asset_publish_resolver.py:142` `_resolve_context()`
- `packages/smartlib/core/asset_publish_resolver.py:175` `_preferred_file()`
- `packages/smartlib/core/asset_publish_resolver.py:186` `_read_json()`
- `packages/smartlib/core/output_resolver.py:22` `path()`
- `packages/smartlib/core/output_resolver.py:29` `__init__()`
- `packages/smartlib/core/output_resolver.py:33` `definitions()`
- `packages/smartlib/core/output_resolver.py:37` `resolve()`
- `packages/smartlib/core/output_resolver.py:60` `_values()`
- `packages/smartlib/core/output_resolver.py:84` `_expand()`
- `packages/smartlib/core/path_resolver.py:33` `production_root()`
- `packages/smartlib/core/path_resolver.py:37` `incoming_root()`
- `packages/smartlib/core/path_resolver.py:41` `delivery_root()`
- `packages/smartlib/core/path_resolver.py:45` `delivery_staging_root()`
- `packages/smartlib/core/path_resolver.py:49` `editorial_data_root()`
- `packages/smartlib/core/path_resolver.py:53` `workspace_partition()`
- `packages/smartlib/core/path_resolver.py:58` `assets_root()`
- `packages/smartlib/core/path_resolver.py:67` `shots_root()`
- `packages/smartlib/core/path_resolver.py:76` `sequences_root()`
- `packages/smartlib/core/path_resolver.py:82` `assemblies_root()`
- `packages/smartlib/core/path_resolver.py:86` `workspace_root()`
- `packages/smartlib/core/path_resolver.py:94` `asset_root()`
- `packages/smartlib/core/path_resolver.py:108` `asset_variant_root()`
- `packages/smartlib/core/path_resolver.py:111` `asset_work_dir()`
- `packages/smartlib/core/path_resolver.py:131` `assembly_root()`
- `packages/smartlib/core/path_resolver.py:141` `assembly_variant_root()`
- `packages/smartlib/core/path_resolver.py:144` `assembly_work_root()`
- `packages/smartlib/core/path_resolver.py:156` `assembly_work_dir()`
- `packages/smartlib/core/path_resolver.py:171` `assembly_data_root()`
- `packages/smartlib/core/path_resolver.py:179` `assembly_publish_root()`
- `packages/smartlib/core/path_resolver.py:187` `asset_work_root()`
- `packages/smartlib/core/path_resolver.py:203` `asset_data_root()`
- `packages/smartlib/core/path_resolver.py:206` `asset_publish_root()`
- `packages/smartlib/core/path_resolver.py:209` `asset_reference_root()`
- `packages/smartlib/core/path_resolver.py:212` `_asset_area_root()`
- `packages/smartlib/core/path_resolver.py:225` `asset_data_dir()`
- `packages/smartlib/core/path_resolver.py:228` `asset_publish_dir()`
- `packages/smartlib/core/path_resolver.py:231` `asset_work_scene_dir()`
- `packages/smartlib/core/path_resolver.py:236` `legacy_asset_work_dir()`
- `packages/smartlib/core/path_resolver.py:239` `asset_data_version_dir()`
- `packages/smartlib/core/path_resolver.py:242` `asset_publish_version_dir()`
- `packages/smartlib/core/path_resolver.py:251` `shot_root()`
- `packages/smartlib/core/path_resolver.py:263` `sequence_root()`
- `packages/smartlib/core/path_resolver.py:274` `sequence_workspace_root()`
- `packages/smartlib/core/path_resolver.py:277` `sequence_build_root()`
- `packages/smartlib/core/path_resolver.py:287` `sequence_build_dir()`
- `packages/smartlib/core/path_resolver.py:299` `sequence_work_dir()`
- `packages/smartlib/core/path_resolver.py:317` `sequence_publish_dir()`
- `packages/smartlib/core/path_resolver.py:320` `sequence_publish_version_dir()`
- `packages/smartlib/core/path_resolver.py:323` `shot_work_dir()`
- `packages/smartlib/core/path_resolver.py:340` `shot_work_root()`
- `packages/smartlib/core/path_resolver.py:352` `shot_build_root()`
- `packages/smartlib/core/path_resolver.py:364` `shot_build_dir()`
- `packages/smartlib/core/path_resolver.py:377` `shot_data_root()`
- `packages/smartlib/core/path_resolver.py:380` `shot_publish_root()`
- `packages/smartlib/core/path_resolver.py:383` `shot_output_root()`
- `packages/smartlib/core/path_resolver.py:386` `shot_render_root()`
- `packages/smartlib/core/path_resolver.py:389` `_shot_area_root()`
- `packages/smartlib/core/path_resolver.py:399` `legacy_shot_work_dir()`
- `packages/smartlib/core/path_resolver.py:402` `legacy_shot_tool_work_dir()`
- `packages/smartlib/core/path_resolver.py:412` `shot_data_dir()`
- `packages/smartlib/core/path_resolver.py:415` `shot_data_version_dir()`
- `packages/smartlib/core/path_resolver.py:427` `shot_publish_dir()`
- `packages/smartlib/core/path_resolver.py:430` `shot_publish_version_dir()`
- `packages/smartlib/core/path_resolver.py:441` `_template()`
- `packages/smartlib/core/path_resolver.py:444` `_template_fields()`
- `packages/smartlib/core/path_resolver.py:477` `_expand_template()`
- `packages/smartlib/core/path_resolver.py:486` `_path_from_template()`
- `packages/smartlib/core/path_resolver.py:493` `configured_project_paths()`
- `packages/smartlib/core/resolver.py:31` `__init__()`
- `packages/smartlib/core/resolver.py:34` `resolve()`
- `packages/smartlib/core/resolver.py:56` `_scheme_root()`
- `packages/smartlib/core/resolver.py:65` `_resolve_version_alias()`
- `packages/smartlib/core/resolver.py:80` `_latest_version()`
- `packages/smartlib/core/resolver.py:93` `resolve()`
- `packages/smartlib/dcc/houdini/scripts/create_car_path_locators_hda.py:839` `_set_if_exists()`
- `packages/smartlib/dcc/houdini/scripts/create_car_path_locators_hda.py:917` `_menu_with_callback()`
- `packages/smartlib/dcc/houdini/scripts/create_car_path_locators_hda.py:927` `_axis_menu()`
- `packages/smartlib/dcc/houdini/scripts/create_car_path_locators_hda.py:934` `_direction_menu()`
- `packages/smartlib/dcc/houdini/scripts/create_car_path_locators_hda.py:941` `_hidden_string()`
- `packages/smartlib/dcc/houdini/scripts/create_car_path_locators_hda.py:949` `_hidden_float()`
- `packages/smartlib/dcc/houdini/scripts/create_car_path_locators_hda.py:957` `_preset_button()`
- `packages/smartlib/dcc/houdini/scripts/create_car_path_locators_hda.py:967` `_build_parm_template_group()`
- `packages/smartlib/dcc/houdini/scripts/create_car_path_locators_hda.py:1182` `_apply_parm_template_group()`
- `packages/smartlib/dcc/houdini/scripts/create_car_path_locators_hda.py:1189` `_set_runtime_defaults()`
- `packages/smartlib/dcc/houdini/scripts/create_car_path_locators_hda.py:1195` `_build_network()`
- `packages/smartlib/dcc/houdini/scripts/create_car_path_locators_hda.py:1227` `create_hda()`
