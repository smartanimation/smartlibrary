"""Display the actual fixed Asset dependencies separately from animation."""
from smartlib.core.metadata import read_json
from smartlib.core.path_resolver import AssetIdentity
from .looks import look_versions, selected_look


def rebuild(window):
    from .main import QtCore, QtWidgets
    tree = window.tree
    blocked = tree.blockSignals(True)
    try:
        previous = dict(getattr(window, 'preserved_looks', {}))
        previous.update({p: c.currentData() for p, c in window.look_rows.items()})
        window.look_rows = {}
        for item in getattr(window, '_asset_detail_virtual', []):
            parent = item.parent()
            if parent:
                parent.takeChild(parent.indexOfChild(item))
        window._asset_detail_virtual = []
        assets, animations = {}, {}
        for item, combo, key in window.rows:
            if key[0] not in ('assets', 'animation'):
                continue
            item.takeChildren()
            data = read_json(combo.currentData(), {})
            if key[0] == 'assets':
                item.setText(0, key[1])
                assets[key[1]] = (item, combo.currentData(), data)
            elif item.checkState(0) == QtCore.Qt.Checked:
                item.setText(0, key[1] + (' (deform)' if data.get('usd_kind') != 'usd_skel_animation' else ' (animation)'))
                animations[key[1]] = (combo.currentData(), data)
        group = next((tree.topLevelItem(i) for i in range(tree.topLevelItemCount())
                      if tree.topLevelItem(i).text(0).startswith('Assets')), None)
        if group is None and animations:
            group = QtWidgets.QTreeWidgetItem(tree, ['Assets'])
        for target in sorted(set(assets) | set(animations)):
            if target in assets:
                item, path, data = assets[target]
            else:
                item = QtWidgets.QTreeWidgetItem(group, [target])
                window._asset_detail_virtual.append(item)
                path, data = animations[target]
            if target in animations:
                path, data = animations[target]
            inputs = data.get('inputs', {})
            release_ref = inputs.get('asset_release')
            release = read_json(release_ref['path'], {}) if release_ref else {}
            description = (release.get('quality', 'proxy') + ' / ' + release.get('version', '')) if release else 'Individual Publish references'
            item.setToolTip(0, description)
            if target not in assets:
                item.setText(1, description)
            info = QtWidgets.QTreeWidgetItem(item, ['USD Release', description])
            info.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
            for key, label in [('geometry', 'Geometry'), ('look', 'Look'), ('rig', 'Rig')]:
                ref = release.get(key)
                if key == 'rig' and not ref and not inputs.get('skel'):
                    continue
                if not ref and key == 'rig':
                    ref = inputs.get('skel')
                if not ref and key == 'geometry':
                    ref = inputs.get('skel')
                record = read_json(ref['path'], {}) if ref else {}
                child = QtWidgets.QTreeWidgetItem(item, [label, (record.get('subset', '') + ' / ' + record.get('version', '')).strip(' /') or 'Not recorded'])
                child.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
                if key == 'geometry' and data.get('kind') == 'animation' and data.get('usd_kind') != 'usd_skel_animation':
                    child.setText(1, 'deform / ' + data['version'])
                    child.setToolTip(1, data.get('validation', {}).get('fallback_reason', 'Evaluated deformation cache'))
                if ref:
                    child.setData(0, QtCore.Qt.UserRole, ref['path'])
                    child.setToolTip(0, ref['path'])
                if record.get('publish_type'):
                    identity = AssetIdentity(record['category'], record['group'], record['asset'], record['variant'])
                    root = window.session.paths.asset_publish_dir(identity, record['publish_type'], record['subset'])
                    latest = read_json(window.session.paths.artifact_file(root, 'latest.json'), {})
                    child.setText(2, latest.get('version', '-'))
                if key == 'look' and data.get('entrypoint'):
                    choices = look_versions(window.session.handoff, data)
                    if not choices and not selected_look(data):
                        child.setText(1, 'Embedded / Legacy')
                        continue
                    combo = QtWidgets.QComboBox()
                    combo.addItem('Off', '')
                    for choice in choices:
                        combo.addItem(choice['label'], choice['path'])
                    selected = previous.get(path, selected_look(data))
                    if selected and combo.findData(selected) < 0:
                        combo.addItem('Pinned Look', selected)
                    combo.setCurrentIndex(max(0, combo.findData(selected)))
                    tree.setItemWidget(child, 1, combo)
                    child.setText(2, ', '.join(c['label'] for c in choices if c['latest']) or '-')
                    window.look_rows[path] = combo
                    standard = record.get('artifacts', {}).get('usd', {}).get('path', selected_look(data))
                    def mark_override(_=None, row=child, widget=combo, default=standard):
                        from pathlib import Path
                        selected = widget.currentData() or ''
                        changed = bool(selected or default) and (not selected or not default or Path(selected) != Path(default))
                        was_blocked = tree.blockSignals(True)
                        try:
                            row.setText(0, 'Look *' if changed else 'Look')
                            row.setToolTip(1, 'Composition override' if changed else 'Published Look')
                        finally:
                            tree.blockSignals(was_blocked)
                    mark_override()
                    combo.currentIndexChanged.connect(mark_override)
                    combo.currentIndexChanged.connect(window.schedule_preview)
            item.setExpanded(True)
    finally:
        tree.blockSignals(blocked)
