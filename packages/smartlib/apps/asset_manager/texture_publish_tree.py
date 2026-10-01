"""Show complete texture compositions and per-publish changes."""
from pathlib import Path
import re
from .texture_publish_ui import QtCore, QtWidgets
from smartlib.core.metadata import read_json


def populate_texture_tree(tree, service, identity):
    tree.clear()
    tree.setHeaderLabels(['Texture / Version', 'Source version', 'Subset', '', 'Latest', 'State', 'Updated', 'Changes'])
    root = service.paths.asset_publish_root(identity)
    texture_root = service.paths.artifact_file(root, 'texture')
    if not texture_root.exists():
        return
    for branch in sorted(p for p in texture_root.iterdir() if p.is_dir()):
        snapshot, manifest = service.texture_snapshot(identity, branch.name)
        if not manifest:
            continue
        record = read_json(manifest, {})
        parent = QtWidgets.QTreeWidgetItem([f'{branch.name} — {len(snapshot)} textures', '', branch.name, '',
                                          record['version'], 'CURRENT', '', ''])
        tree.addTopLevelItem(parent)
        for item in sorted(snapshot.values(), key=lambda item: item['name'].casefold()):
            child = QtWidgets.QTreeWidgetItem([item['name'], item['version'], branch.name, '', '', 'ACTIVE'])
            child.setToolTip(0, item['artifact']['path'])
            child.setData(0, QtCore.Qt.UserRole, item['artifact']['path'])
            parent.addChild(child)
        history = QtWidgets.QTreeWidgetItem(['Publish history'])
        parent.addChild(history)
        versions = sorted((p.name for p in branch.iterdir() if p.is_dir() and re.fullmatch(r'v\d+', p.name)), reverse=True)
        for version in versions:
            directory = service.paths.asset_publish_version_dir(identity, 'texture', branch.name, version)
            receipt = read_json(service.paths.artifact_file(directory, 'publish.json'), {}) or {}
            if receipt.get('status') != 'published':
                continue
            changed = receipt.get('changed', list(receipt.get('files', {}).values()))
            removed = receipt.get('removed', [])
            node = QtWidgets.QTreeWidgetItem([version, '', branch.name, '', '', 'PUBLISHED',
                receipt.get('created_at', ''), f'{len(changed)} updated / {len(removed)} removed'])
            history.addChild(node)
            for name in changed:
                node.addChild(QtWidgets.QTreeWidgetItem([name, version, branch.name, '', '', 'UPDATED']))
            for name in removed:
                node.addChild(QtWidgets.QTreeWidgetItem([name, '', branch.name, '', '', 'REMOVED']))
        parent.setExpanded(True)
