"""Editable draft state for the Published Snapshot table."""
from pathlib import Path

from .composition_ui import QtCore, QtWidgets


def render(window):
    table = window.composition_table
    table.blockSignals(True)
    table.setRowCount(0)
    window.composition_open_btn.setEnabled(False)
    if hasattr(window, "composition_review_btn"):
        window.composition_review_btn.setEnabled(False)
    window.composition_save_btn.setEnabled(False)
    window._composition_edit_rows = []
    try:
        identity = window._composition_identity()
        if not identity:
            return
        service = window._composition_service()
        path = window.composition_versions.currentData()
        source = window._selected_review_source()
        rows = []
        if path:
            data = service.load(path, identity=identity)
            key = ("snapshot", path)
            candidates = []
            for candidate_path, _ in service.list_snapshots(identity, data["department"]):
                try:
                    candidate = service.load(candidate_path, identity=identity)
                    if all(candidate[k] == data[k] for k in ("profile", "fps", "frame_range")):
                        candidates.append((str(candidate_path), candidate))
                except (OSError, ValueError):
                    continue
            for member in data["members"]:
                target = member["instance_id"]
                options = []
                for candidate_path, candidate in candidates:
                    other = next((m for m in candidate["members"] if m["instance_id"] == target), None)
                    if other and all(other.get(k) == member.get(k) for k in ("asset", "variant")):
                        versions = ", ".join(k + " " + v["version"] for k, v in other["products"].items())
                        options.append((candidate["version"] + " / " + versions, candidate_path))
                rows.append(dict(target=target, type=" + ".join(member["products"]),
                    category=(data.get("cast", {}).get(target) or {}).get("category", ""),
                    context=data["department"], options=options, default=path))
            window.composition_open_btn.setEnabled(True)
            if hasattr(window, "composition_review_btn"):
                window.composition_review_btn.setEnabled(True)
        else:
            if not source or window.composition_department.currentText() != "animation":
                window.composition_state.setText("Select a submitted Construct or a published Snapshot.")
                return
            key = ("source", source["source_manifest"])
            profile = window.service.project_config.pipeline_profile
            if not profile:
                return
            shots = window.service.shots
            cast = shots.load_cast(identity).get("cast") or {}
            previews = {r.cast_key: r for r in shots.build_preview(identity, cast_contexts={k: "REND" for k in cast})} if profile.name == "maya_rend_atom" else {}
            for target, entry in cast.items():
                if not entry.get("animation_required", True):
                    continue
                options = [("Selected Construct " + source["build_version"], source["source_manifest"])]
                if profile.name == "maya_rend_atom":
                    preview = previews.get(target)
                    options = [(v["version"], v["path"]) for v in shots.asset_publish_resolver.list_context_versions(preview.variant_root, "REND")] if preview and preview.variant_root else []
                rows.append(dict(target=target, type=" + ".join(profile.products), category=entry.get("category", ""),
                    context="REND" if profile.name == "maya_rend_atom" else "Final Deform", options=options,
                    default=options[0][1] if options else ""))
        drafts = window.__dict__.setdefault("_composition_drafts", {})
        draft = drafts.setdefault(key, {})
        window._composition_draft_key = key
        window._composition_edit_rows = rows
        table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            choice = draft.setdefault(row["target"], {"use": True, "version": row["default"]})
            check = QtWidgets.QTableWidgetItem()
            check.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable | QtCore.Qt.ItemIsUserCheckable)
            check.setCheckState(QtCore.Qt.Checked if choice["use"] else QtCore.Qt.Unchecked)
            table.setItem(i, 0, check)
            for j, field in enumerate(("type", "target", "category", "context"), 1):
                table.setItem(i, j, QtWidgets.QTableWidgetItem(row[field]))
            combo = QtWidgets.QComboBox()
            for label, value in row["options"]:
                combo.addItem(label, value)
                combo.setItemData(combo.count()-1, value, QtCore.Qt.ToolTipRole)
            index = combo.findData(choice["version"])
            if index < 0:
                combo.addItem("Unavailable selection", choice["version"])
                index = combo.count()-1
            combo.setCurrentIndex(index)
            combo.currentIndexChanged.connect(lambda _index, i=i: changed(window, i))
            table.setCellWidget(i, 5, combo)
        for column in range(5):
            table.resizeColumnToContents(column)
        update_state(window)
    except Exception as exc:
        window.composition_state.setText(str(exc))
    finally:
        table.blockSignals(False)


def changed(window, row_index):
    rows = window._composition_edit_rows
    if not 0 <= row_index < len(rows):
        return
    row = rows[row_index]
    draft = window._composition_drafts[window._composition_draft_key]
    draft[row["target"]] = {"use": window.composition_table.item(row_index, 0).checkState() == QtCore.Qt.Checked,
                            "version": window.composition_table.cellWidget(row_index, 5).currentData()}
    update_state(window)


def update_state(window):
    draft = window._composition_drafts[window._composition_draft_key]
    blocked, modified, count = False, False, 0
    table = window.composition_table
    was = table.blockSignals(True)
    for i, row in enumerate(window._composition_edit_rows):
        choice = draft[row["target"]]
        ready = choice["version"] in [value for _, value in row["options"]] and bool(choice["version"])
        dirty = not choice["use"] or choice["version"] != row["default"]
        modified |= dirty
        count += int(choice["use"])
        blocked |= choice["use"] and not ready
        state = "EXCLUDED" if not choice["use"] else "MISSING" if not ready else "CHANGED" if dirty else "READY"
        table.setItem(i, 6, QtWidgets.QTableWidgetItem(state))
    table.blockSignals(was)
    valid = count > 0 and not blocked
    source_mode = window._composition_draft_key[0] == "source"
    busy = any(j.get("kind") == "animation_publish" and j.get("state") not in {"COMPLETE", "FAILED"} for j in getattr(window, "queue_jobs", []))
    source = window._selected_review_source()
    window.composition_build_btn.setEnabled(bool(source_mode and valid and source and source["state"] == "READY" and not busy))
    window.composition_save_btn.setEnabled(not source_mode and valid and modified and not busy)
    window.composition_state.setText(("Draft changed — publish as a new version." if modified else "Draft — choose Use and Version.") +
                                    (" Enable at least one member with an available version." if not valid else ""))


def source_payload(window):
    service = window._composition_service()
    if window._composition_draft_key[0] != "source":
        raise ValueError("Select New from selected Construct first")
    draft = window._composition_drafts[window._composition_draft_key]
    result = {}
    atom = window.service.project_config.pipeline_profile.name == "maya_rend_atom"
    for target, choice in draft.items():
        result[target] = {"use": choice["use"]}
        if choice["use"] and atom:
            result[target]["rend"] = service._fixed_dependency(choice["version"])
    return result


def publish(window):
    try:
        key = window._composition_draft_key
        draft = window._composition_drafts[key]
        path = window._composition_service().revise_members(window._composition_identity(), key[1],
            {target: choice["version"] if choice["use"] else None for target, choice in draft.items()})
        window._refresh_compositions()
        window.composition_versions.setCurrentIndex(window.composition_versions.findData(str(path)))
        window.footer_label.setText("Published Snapshot: " + str(path))
    except Exception as exc:
        QtWidgets.QMessageBox.critical(window, "Snapshot Publish Failed", str(exc))
        return
    try:
        from .composition_review import enqueue
        enqueue(window, path)
    except Exception as exc:
        QtWidgets.QMessageBox.warning(window, "Snapshot Published / Review Not Queued", str(exc))
