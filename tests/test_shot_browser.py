import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json
import time
from pathlib import Path

from PySide6 import QtCore, QtWidgets, QtTest

from smartlib.review.shot_catalog import ShotMovie, filter_catalog, is_new, scan_catalog
from smartlib.review.shot_browser import ShotBrowser


class Paths:
    def __init__(self, root):
        self.root = root
        (root / "identities" / "ep02" / "s027" / "c001").mkdir(parents=True)

    def shots_root(self):
        return self.root / "identities"

    def shot_review_movie_dir(self, ep, seq, shot, task):
        return self.root / "custom_movies" / shot / task

    def shot_review_output_root(self, ep, seq, shot, task, profile):
        return self.root / "custom_submissions" / shot / profile

    def shot_review_read_roots(self, ep, seq, shot, task, profile):
        return [self.shot_review_output_root(ep, seq, shot, task, profile)]

    def shot_review_publish_root(self, ep, seq, shot, task):
        return self.root / "custom_publish" / shot / task


def movie(paths, version):
    p = paths.shot_review_movie_dir("ep02", "s027", "c001", "anim") / ("show_ep02_s027_c001_anim_%s.mov" % version)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"test movie")
    return p


def test_resolver_inventory_versions_missing_and_filters(tmp_path):
    paths = Paths(tmp_path)
    movie(paths, "v002_t01")
    latest = movie(paths, "v010_t02")
    rows, errors = scan_catalog(paths, ["anim", "comp"], "working")
    assert not errors
    assert len(rows) == 3
    assert [r.movie for r in filter_catalog(rows, task="anim")] == [str(latest)]
    assert len(filter_catalog(rows, latest=False, task="anim")) == 2
    assert len(filter_catalog(rows, availability="Missing")) == 1
    assert not filter_catalog(rows, episode="ep01")
    assert not filter_catalog(rows, sequence="s028")
    row = filter_catalog(rows, task="anim")[0]
    assert not is_new(row, {})
    seen = {row.group: "v002_t01"}
    assert is_new(row, seen)
    assert filter_catalog(rows, availability="New versions", seen=seen) == [row]
    assert not filter_catalog(rows, availability="New versions", seen={row.group: row.version})
    assert len(filter_catalog(rows, latest=False, version="v002_t01")) == 1
    assert scan_catalog(paths, ["anim"], "working", lambda: True) == ([], [])


def test_formal_metadata_nested_takes_and_missing_latest(tmp_path):
    paths = Paths(tmp_path)
    base = paths.shot_review_output_root("ep02", "s027", "c001", "anim", "internal")
    for version in ("v009", "v010"):
        folder = base / version
        folder.mkdir(parents=True)
        (folder / "review.json").write_text(json.dumps({"department": "anim", "version": version, "movie": "review.mov"}))
    (base / "v009" / "review.mov").write_bytes(b"movie")
    rows, errors = scan_catalog(paths, ["anim", "comp"], "internal")
    assert not errors
    latest = filter_catalog(rows, task="anim")[0]
    assert latest.version == "v010" and not latest.movie
    assert not filter_catalog(rows, task="comp", availability="Has video")
    assert len(filter_catalog(rows, task="anim", latest=False, availability="Has video")) == 1
    folder = paths.shot_review_publish_root("ep02", "s027", "c001", "anim") / "v004" / "t002"
    (folder / "metadata").mkdir(parents=True)
    (folder / "review.mov").write_bytes(b"movie")
    (folder / "metadata" / "review.json").write_text(json.dumps({"movie": "review.mov"}))
    rows, errors = scan_catalog(paths, ["anim"], "publish")
    assert rows[0].version == "v004_t002"
    assert rows[0].movie == str(folder / "review.mov")
    (folder / "metadata" / "review.json").write_text("broken json")
    rows, errors = scan_catalog(paths, ["anim"], "publish")
    assert errors and not rows[0].movie


def wait_until(app, predicate):
    deadline = time.monotonic() + 5
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    assert predicate()


def test_browser_selection_exact_media_history_and_refresh(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    paths = Paths(tmp_path)
    old, latest = movie(paths, "v002_t01"), movie(paths, "v010_t01")
    loaded = []
    browser = ShotBrowser(["Test"], "Test", lambda _: paths,
                          lambda media, new: loaded.append((media, new)), ["anim"], tmp_path)
    browser.settings = QtCore.QSettings(str(tmp_path / "history.ini"), QtCore.QSettings.IniFormat)
    browser.thumbnails.executable = ""
    browser.show()
    wait_until(app, lambda: bool(browser.rows))
    browser.versions.setCurrentText("All versions")
    assert browser.grid.count() == 2
    browser.grid.item(0).setSelected(True)
    browser.push(True)
    assert loaded == [([str(old)], True)]
    assert browser.settings.value(browser._history_key(browser.rows[0].group)) == "v002_t01"
    browser.versions.setCurrentText("Latest")
    assert browser.grid.count() == 1
    assert is_new(browser.grid.item(0).data(QtCore.Qt.UserRole), browser.seen)
    browser.grid.selectAll()
    browser.push()
    assert loaded[-1] == ([str(latest)], False)
    assert not is_new(browser.grid.item(0).data(QtCore.Qt.UserRole), browser.seen)
    # Loading an old version cannot move the comparison baseline backwards.
    browser.versions.setCurrentText("v002_t01")
    browser.grid.selectAll()
    browser.push()
    assert browser.seen[browser.rows[0].group] == "v010_t01"
    # A disappeared movie is never sent to RV or recorded as loaded.
    old.unlink()
    browser.push()
    assert len(loaded) == 3
    browser.refresh()
    browser.refresh()
    wait_until(app, lambda: bool(browser.rows) and not browser.workers)
    assert len(browser.rows) == 1
    browser.close()


def test_drag_range_and_ctrl_selection(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    paths = Paths(tmp_path)
    browser = ShotBrowser(["DragTest"], "DragTest", lambda _: paths, lambda *_: None, ["anim"], tmp_path)
    browser.thumbnails.executable = ""
    browser.show()
    wait_until(app, lambda: bool(browser.rows))
    browser.rows = [ShotMovie("ep02", "s027", "c%03d" % i, "anim", "working", "v001") for i in range(8)]
    browser.apply_filters()
    app.processEvents()
    grid = browser.grid
    first = grid.visualItemRect(grid.item(0))
    second = grid.visualItemRect(grid.item(1))
    start = QtCore.QPoint(first.left() - 2, first.top() - 2)
    end = second.center()
    QtTest.QTest.mousePress(grid.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, start)
    QtTest.QTest.mouseMove(grid.viewport(), end, 50)
    QtTest.QTest.mouseRelease(grid.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, end)
    assert len(grid.selectedItems()) >= 2
    QtTest.QTest.mouseClick(grid.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.ControlModifier, grid.visualItemRect(grid.item(3)).center())
    assert grid.item(3).isSelected()
    QtTest.QTest.mouseClick(grid.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.ShiftModifier, grid.visualItemRect(grid.item(5)).center())
    assert grid.item(4).isSelected() and grid.item(5).isSelected()
    browser.close()


def test_configured_delivery_read_compatibility(tmp_path):
    from smartlib.core.path_resolver import configured_project_paths

    class Config:
        project_root = tmp_path
        templates = {"shots_root": "{project_root}/identities"}
        base = {}

        def load(self, filename):
            return {"delivery_profiles": {"internal": {
                "target_template": "{shot_root}/review/{department}/{profile}/{version}"
            }}}

    paths = configured_project_paths(tmp_path, Config())
    root = paths.shot_root("ep02", "s027", "c002")
    root.mkdir(parents=True)
    canonical, configured = paths.shot_review_read_roots("ep02", "s027", "c002", "anim", "internal")
    assert configured == root / "review" / "anim" / "internal"
    assert canonical == paths.shot_review_output_root("ep02", "s027", "c002", "anim", "internal")
    for version in ("v001", "v008"):
        folder = configured / version
        folder.mkdir(parents=True)
        (folder / "review.mov").write_bytes(b"movie")
        (folder / "review.json").write_text(json.dumps({"version": version, "department": "anim", "movie": "review.mov"}))
    rows, errors = scan_catalog(paths, ["anim"], "internal")
    assert not errors and len(rows) == 2
    assert filter_catalog(rows)[0].movie == str(configured / "v008" / "review.mov")
    assert len(filter_catalog(rows, latest=False)) == 2
    # Canonical copies win without duplicating the same version in the browser.
    folder = canonical / "v008"
    folder.mkdir(parents=True)
    (folder / "review.mov").write_bytes(b"canonical")
    (folder / "review.json").write_text(json.dumps({"version": "v008", "department": "anim", "movie": "review.mov"}))
    rows, errors = scan_catalog(paths, ["anim"], "internal")
    assert not errors and len(rows) == 2
    assert filter_catalog(rows)[0].movie == str(folder / "review.mov")


def test_multiple_sequences_selection_filter_and_rv_order(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    paths = Paths(tmp_path)
    loaded = []
    browser = ShotBrowser(["MultiSequence"], "MultiSequence", lambda _: paths,
                          lambda media, new: loaded.append(media), ["anim"], tmp_path)
    browser.settings = QtCore.QSettings(str(tmp_path / "multi.ini"), QtCore.QSettings.IniFormat)
    browser.thumbnails.executable = ""
    browser.show()
    wait_until(app, lambda: bool(browser.rows))
    rows = []
    for sequence in ("s029", "s028", "s027"):
        for shot in ("c002", "c001"):
            media = tmp_path / (sequence + shot + ".mov")
            media.write_bytes(b"movie")
            rows.append(ShotMovie("ep02", sequence, shot, "anim", "working", "v001", str(media)))
    browser.rows = rows
    browser._episode_changed()
    app.processEvents()
    seq = browser.sequences

    def click(index, modifier=QtCore.Qt.NoModifier):
        QtTest.QTest.mouseClick(seq.viewport(), QtCore.Qt.LeftButton, modifier,
                               seq.visualItemRect(seq.item(index)).center())

    click(1)
    click(3, QtCore.Qt.ControlModifier)
    assert browser._sequence_scope() == {"s027", "s029"}
    assert browser.grid.count() == 4
    assert {browser.grid.item(i).data(QtCore.Qt.UserRole).sequence for i in range(4)} == {"s027", "s029"}
    browser.grid.selectAll()
    browser.push()
    assert loaded[-1] == [str(tmp_path / (seq_name + shot + ".mov"))
                           for seq_name in ("s027", "s029") for shot in ("c001", "c002")]
    # Rebuilding the scope after refresh preserves every selected sequence.
    browser._episode_changed()
    assert browser._sequence_scope() == {"s027", "s029"}
    click(1)
    click(3, QtCore.Qt.ShiftModifier)
    assert browser._sequence_scope() == {"s027", "s028", "s029"}
    assert browser.grid.count() == 6
    click(0, QtCore.Qt.ControlModifier)
    assert browser._sequence_scope() == ""
    assert len(seq.selectedItems()) == 1
    click(2, QtCore.Qt.ControlModifier)
    assert browser._sequence_scope() == {"s028"}
    click(2, QtCore.Qt.ControlModifier)
    assert browser._sequence_scope() == ""
    assert not filter_catalog(rows, sequence=set())
    assert len(filter_catalog(rows, sequence={"s027", "s029"})) == 4
    browser.close()
