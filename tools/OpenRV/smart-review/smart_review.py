from __future__ import print_function

import json
import os
import re
import sys
import getpass
from pathlib import Path

from rv import commands, qtutils, rvtypes

try:
    from PySide2 import QtCore, QtWidgets
except ImportError:
    try:
        from PySide6 import QtCore, QtWidgets
    except ImportError:
        from PySide import QtCore, QtGui as QtWidgets


PROJECT_FALLBACK_ROOT = "P:/dev/smartlibrary"
SHOT_DEPTS = ("layout", "anim", "fx", "light", "comp")
SHOT_PROFILES = ("publish", "internal")
ASSET_DEPTS = ("model", "rig", "look")


def _repo_root():
    return Path(
        os.environ.get("SMARTLIBRARY_ROOT")
        or os.environ.get("SMARTPIPELINE_ROOT")
        or PROJECT_FALLBACK_ROOT
    )


def _project_names(repo_root):
    names = []
    for config_root in _project_config_roots(repo_root):
        if not config_root.exists():
            continue
        for path in sorted(config_root.iterdir()):
            if path.is_dir() and (path / "templates_base.yml").exists() and path.name not in names:
                names.append(path.name)
    return names or ["STKB"]


def _project_root(repo_root, project):
    for config_root in _project_config_roots(repo_root):
        path = config_root / project / "templates_base.yml"
        text = _read_text(path)
        match = re.search(r"(?m)^\s*project_root:\s*[\"']?([^\"'\r\n]+)", text)
        if match:
            return Path(match.group(1).strip())
    return Path(os.environ.get("SMART_REVIEW_PROJECT_ROOT") or "")


def _project_config_roots(repo_root):
    candidates = []
    for env_name in (
        "SMART_REVIEW_PROJECT_CONFIG_ROOT",
        "SMARTPIPELINE_STUDIO_CONFIG_DIR",
    ):
        value = os.environ.get(env_name)
        if not value:
            continue
        path = Path(value)
        candidates.extend([path / "config", path])
    candidates.extend(
        [
            repo_root.parent / "smartprojects" / "config",
            repo_root / "config",
        ]
    )
    result = []
    for path in candidates:
        normalized = Path(path)
        if normalized not in result:
            result.append(normalized)
    return result


def _production_root(project_root):
    project_root = Path(project_root)
    repo_root = _repo_root()
    for config_root in _project_config_roots(repo_root):
        if not config_root.exists():
            continue
        for config in config_root.glob("*/templates_base.yml"):
            text = _read_text(config)
            root_match = re.search(r"(?m)^\s*project_root:\s*[\"']?([^\"'\r\n]+)", text)
            if not root_match or Path(root_match.group(1).strip()) != project_root:
                continue
            match = re.search(r"(?m)^\s*production_root:\s*[\"']?([^\"'\r\n]+)", text)
            if match:
                value = match.group(1).strip().replace("{project_root}", str(project_root))
                return Path(value)
    return project_root / "production"


def _entity_root(project_root, name):
    return _production_root(project_root) / name


def _read_text(path):
    try:
        return Path(path).read_text(encoding="utf-8")
    except Exception:
        return ""


def _read_json(path):
    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return {}


def _latest_review_json(base_dir):
    latest = _read_json(Path(base_dir) / "latest.json")
    rel = latest.get("path") if isinstance(latest, dict) else ""
    review = Path(base_dir) / rel if rel else None
    return review if review and review.exists() else None


def _review_versions(base_dir):
    base = Path(base_dir)
    return sorted(
        [path.name for path in base.glob("v*") if path.is_dir() and (path / "review.json").is_file()],
        key=_version_number,
        reverse=True,
    )


def _decision_service():
    packages = _repo_root() / "packages"
    if str(packages) not in sys.path:
        sys.path.insert(0, str(packages))
    from smartlib.review.decisions import ReviewDecisionService

    return ReviewDecisionService()


def _sequence_pattern_from_first_file(path):
    path = Path(path)
    stem = path.stem
    if "_" not in stem:
        return path
    prefix, frame = stem.rsplit("_", 1)
    if not frame.isdigit():
        return path
    return path.with_name("%s_%%0%dd%s" % (prefix, len(frame), path.suffix))


def _rv_sequence_path(path):
    text = str(path).replace("\\", "/")
    match = re.search(r"(#+)", text)
    if match:
        hashes = match.group(1)
        return text.replace(hashes, "%%0%dd" % len(hashes), 1)
    return text


def _media_from_output_json(output_json):
    output_json = Path(output_json)
    data = _read_json(output_json)
    version_dir = output_json.parent
    files = data.get("files") or {}
    media = []
    for key in ("beauty", "movie", "review"):
        value = files.get(key) or data.get(key)
        if not value:
            continue
        media_path = Path(str(value))
        if not media_path.is_absolute():
            media_path = version_dir / media_path
        media.append(_rv_sequence_path(media_path))
    return _dedupe(media)


def _media_from_sequence_review(review_json, shot=None):
    review_json = Path(review_json)
    data = _read_json(review_json)
    version_dir = review_json.parent.parent if review_json.parent.name == "metadata" else review_json.parent
    shots = data.get("shots") or {}
    if not isinstance(shots, dict):
        return []
    if isinstance(shot, (list, tuple, set)):
        names = [str(item) for item in shot if str(item)]
    else:
        names = [shot] if shot else list(data.get("exported_shots") or shots.keys())
    media = []
    for name in names:
        row = shots.get(name) or {}
        value = row.get("file") or row.get("first_file")
        if value:
            media_path = Path(str(value))
            if not media_path.is_absolute():
                media_path = version_dir / media_path
            media.append(_rv_sequence_path(media_path))
    return _dedupe(media)


def _media_from_review(review_json, selected_shots=None):
    review_json = Path(review_json)
    data = _read_json(review_json)
    version_dir = review_json.parent.parent if review_json.parent.name == "metadata" else review_json.parent
    media = []

    if data.get("record_type") == "output" and isinstance(data.get("shots"), dict):
        return _media_from_sequence_review(review_json, selected_shots)

    if data.get("type") == "quick_preview":
        outputs = data.get("outputs") or {}
        for key in ("beauty", "wireframe", "bbox"):
            files = outputs.get(key) or []
            if not files:
                continue
            first = Path(files[0])
            if not first.is_absolute():
                first = version_dir / first
            if first.exists():
                media.append(str(_sequence_pattern_from_first_file(first)))
        return media

    if data.get("turntable_usd"):
        usd = version_dir / str(data.get("turntable_usd"))
        return [str(usd)] if usd.exists() else []

    movie = data.get("movie")
    if movie:
        movie_path = version_dir / str(movie)
        if movie_path.exists():
            media.append(str(movie_path))

    layers = data.get("layers") or {}
    layer_order = list((data.get("ae") or {}).get("layer_order") or layers.keys())
    for layer_name in layer_order:
        layer = layers.get(layer_name) or {}
        actual = (layer.get("actual_outputs") or {}).get("beauty") or {}
        first = actual.get("first_file")
        if first:
            first_path = version_dir / str(first)
            if first_path.exists():
                media.append(str(_sequence_pattern_from_first_file(first_path)))
                continue
        outputs = layer.get("outputs") or {}
        pattern = outputs.get("beauty")
        if pattern:
            media.append(str(version_dir / str(pattern)))
    return _dedupe(media)


def _dedupe(items):
    seen = set()
    result = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _grid_dimensions(count):
    if count <= 1:
        return 1, 1
    columns = 1
    while columns * columns < count:
        columns += 1
    rows = (count + columns - 1) // columns
    return rows, columns


def _env_flag(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return str(value).strip().lower() not in ("", "0", "false", "off", "no")


def _launch_review_json():
    value = os.environ.get("SMART_REVIEW_REVIEW_JSON") or os.environ.get("SMART_REVIEW_OPEN_REVIEW_JSON")
    if not value:
        return None
    path = Path(value)
    return path if path.exists() else None


def _launch_selected_shots():
    value = os.environ.get("SMART_REVIEW_SELECTED_SHOTS") or ""
    if not value:
        return []
    try:
        data = json.loads(value)
        if isinstance(data, list):
            return [str(item) for item in data if str(item)]
    except Exception:
        pass
    return [item.strip() for item in value.split(",") if item.strip()]


def _shot_dirs(project_root, episode, sequence):
    base = _entity_root(project_root, "shots") / episode / sequence
    if not base.exists():
        return []
    return [path for path in sorted(base.iterdir()) if path.is_dir()]


def _adjacent_sequences(project_root, episode, sequence):
    base = _entity_root(project_root, "shots") / episode
    if not base.exists():
        return [sequence]
    sequences = [path.name for path in sorted(base.iterdir()) if path.is_dir()]
    if sequence not in sequences:
        return sequences[:1] or [sequence]
    index = sequences.index(sequence)
    return sequences[max(0, index - 1) : min(len(sequences), index + 2)]


def _latest_sequence_review(project_root, episode, sequence, dept):
    base = _entity_root(project_root, "sequences") / episode / sequence / "output" / "review" / dept / "main"
    review = _latest_review_json(base)
    if review:
        return review
    for version_dir in sorted(base.glob("v*"), reverse=True):
        output = _read_json(version_dir / "output.json")
        rel = (output.get("files") or {}).get("review_json") if isinstance(output, dict) else ""
        candidate = version_dir / rel if rel else version_dir / "review.json"
        if candidate.exists():
            return candidate
    return None


def _latest_sequence_shot_output(project_root, episode, sequence, shot, dept):
    base = _entity_root(project_root, "sequences") / episode / sequence / "output" / "review" / dept / shot
    if not base.exists():
        return None
    for layer_dir in sorted([path for path in base.iterdir() if path.is_dir()]):
        latest = _read_json(layer_dir / "latest.json")
        rel = latest.get("path") if isinstance(latest, dict) else ""
        candidates = []
        if rel:
            direct = layer_dir / rel
            candidates.append(direct)
            if direct.name == "review.json":
                candidates.append(direct.with_name("output.json"))
        version = latest.get("version") if isinstance(latest, dict) else ""
        if version:
            candidates.append(layer_dir / str(version) / "output.json")
        candidates.extend(sorted(layer_dir.glob("v*/output.json"), reverse=True))
        for candidate in candidates:
            if candidate.exists():
                return candidate
    return None


def _sequence_shot_media(project_root, episode, sequence, shot, dept):
    output = _latest_sequence_shot_output(project_root, episode, sequence, shot, dept)
    if output:
        media = _media_from_output_json(output)
        if media:
            return media
    review = _latest_sequence_review(project_root, episode, sequence, dept)
    return _media_from_sequence_review(review, shot) if review else []


def _quick_check_media(project_root, episode, sequence, shot, dept):
    shot_root = _entity_root(project_root, "shots") / episode / sequence / shot
    preview_media = _latest_preview_render_media(shot_root, dept)
    if preview_media:
        return preview_media
    packages = []

    # Current Smart Playblast layout:
    # publish/review/{dept}/{version}/{take}/image_sequence/{layer}/...
    current_base = shot_root / "publish" / "review" / dept
    for version_dir in current_base.glob("v*"):
        if not version_dir.is_dir():
            continue
        for take_dir in version_dir.iterdir():
            if take_dir.is_dir() and _take_number(take_dir.name) is not None:
                image_root = take_dir / "image_sequence"
                if image_root.exists():
                    packages.append((_version_number(version_dir.name), _take_number(take_dir.name), image_root))

    # Quick Check target layout:
    # review/{dept}/{layer}/{version}/{take}/...
    quick_base = shot_root / "review" / dept
    for layer_dir in quick_base.iterdir() if quick_base.exists() else []:
        if not layer_dir.is_dir():
            continue
        for version_dir in layer_dir.glob("v*"):
            for take_dir in version_dir.iterdir() if version_dir.exists() else []:
                take = _take_number(take_dir.name)
                if take_dir.is_dir() and take is not None:
                    packages.append((_version_number(version_dir.name), take, take_dir))

    if not packages:
        return []
    latest_version = max(row[0] for row in packages)
    latest_take = max(row[1] for row in packages if row[0] == latest_version)
    roots = [row[2] for row in packages if row[0] == latest_version and row[1] == latest_take]
    media = []
    for root in roots:
        layer_dirs = [path for path in root.iterdir() if path.is_dir()] if root.exists() else []
        search_roots = layer_dirs or [root]
        for search_root in search_roots:
            frames = sorted(
                path for path in search_root.iterdir()
                if path.is_file() and path.suffix.lower() in (".jpg", ".jpeg", ".png")
            )
            if frames:
                media.append(_rv_sequence_path(_sequence_pattern_from_first_file(frames[0])))
    return _dedupe(media)


def _latest_preview_render_media(shot_root, dept):
    """Resolve the latest Smart Playblast take independently per layer group."""
    groups_root = (
        Path(shot_root)
        / "publish"
        / "preview_render"
        / str(dept)
        / "groups"
    )
    if not groups_root.exists():
        return []
    media = []
    for group_dir in sorted(
        (path for path in groups_root.iterdir() if path.is_dir()),
        key=lambda path: path.name.lower(),
    ):
        versions = [
            path
            for path in group_dir.glob("v*")
            if path.is_dir() and _version_number(path.name) > 0
        ]
        if not versions:
            continue
        version_dir = max(versions, key=lambda path: _version_number(path.name))
        takes = [
            path
            for path in version_dir.iterdir()
            if path.is_dir() and _take_number(path.name) is not None
        ]
        if not takes:
            continue
        take_dir = max(takes, key=lambda path: _take_number(path.name))
        frames = sorted(
            path
            for path in take_dir.iterdir()
            if path.is_file()
            and path.suffix.lower() in (".jpg", ".jpeg", ".png")
        )
        if frames:
            media.append(
                _rv_sequence_path(_sequence_pattern_from_first_file(frames[0]))
            )
    return _dedupe(media)


def _version_number(value):
    match = re.match(r"^v(\d+)$", str(value or ""), re.IGNORECASE)
    return int(match.group(1)) if match else 0


def _take_number(value):
    match = re.match(r"^(?:t|take)?(\d+)$", str(value or ""), re.IGNORECASE)
    return int(match.group(1)) if match else None


def _latest_editorial_dir(project_root):
    base = Path(project_root) / "editorial" / "publish" / "cut"
    latest = _read_json(base / "latest.json")
    rel = latest.get("path") if isinstance(latest, dict) else ""
    if rel:
        path = base / rel
        if path.exists():
            return path.parent.parent if path.parent.name == "metadata" else path.parent
    versions = sorted([path for path in base.glob("v*") if path.is_dir()], reverse=True)
    return versions[0] if versions else None


def _story_reel_name(project_root, episode, sequence, shot):
    editorial_dir = _latest_editorial_dir(project_root)
    if not editorial_dir:
        return ""
    editorial = _read_json(editorial_dir / "metadata" / "editorial.json")
    all_editorial_shots = list(editorial.get("shots") or [])
    editorial_shots = [
        row
        for row in all_editorial_shots
        if str(row.get("sequence") or "") == str(sequence)
    ]
    candidate_sets = [editorial_shots] if editorial_shots else []
    candidate_sets.append(all_editorial_shots)
    for row in editorial_shots:
        if str(row.get("shot") or "") == str(shot):
            return str(row.get("shot") or "")

    sequence_json = _read_json(_entity_root(project_root, "sequences") / episode / sequence / "sequence.json")
    sequence_shots = sequence_json.get("shots") or []
    shot_index = None
    for index, row in enumerate(sequence_shots):
        if str(row.get("shot") or "") == str(shot):
            shot_index = index
            break
    if shot_index is None:
        return ""

    durations = [int(row.get("duration") or 0) for row in sequence_shots]
    for candidates in candidate_sets:
        editorial_durations = [int(row.get("duration") or 0) for row in candidates]
        for start in range(0, len(editorial_durations) - len(durations) + 1):
            if editorial_durations[start : start + len(durations)] == durations:
                return str(candidates[start + shot_index].get("shot") or "")
    if editorial_shots and shot_index < len(editorial_shots):
        return str(editorial_shots[shot_index].get("shot") or "")
    return ""


def _story_reel_media(project_root, episode, sequence, shot):
    editorial_dir = _latest_editorial_dir(project_root)
    story_name = _story_reel_name(project_root, episode, sequence, shot)
    if not editorial_dir or not story_name:
        return []
    storyreel = _read_json(editorial_dir / "metadata" / "storyreel.json")
    row = (storyreel.get("shots") or {}).get(story_name) or {}
    value = row.get("image_sequence") or row.get("first_file")
    if not value:
        return []
    media_path = Path(str(value))
    if not media_path.is_absolute():
        media_path = editorial_dir / media_path
    return [_rv_sequence_path(media_path)]


def _shot_review_base(project_root, episode, sequence, shot, dept, profile):
    shot_root = _entity_root(project_root, "shots") / episode / sequence / shot
    if profile == "publish":
        return shot_root / "publish" / "review" / dept
    return shot_root / "review" / dept / profile


def _latest_shot_review(project_root, episode, sequence, shot, dept, profile="publish"):
    base = _shot_review_base(project_root, episode, sequence, shot, dept, profile)
    review = _latest_review_json(base)
    if review:
        return review
    nested = sorted(base.glob("*/latest.json"), reverse=True)
    for latest in nested:
        data = _read_json(latest)
        rel = data.get("path") if isinstance(data, dict) else ""
        candidate = latest.parent / rel if rel else None
        if candidate and candidate.exists():
            return candidate
    return None


def _asset_review_rows(project_root):
    roots = sorted(_entity_root(project_root, "assets").glob("*/*/*"))
    rows = []
    for asset_root in roots:
        if not asset_root.is_dir():
            continue
        parts = asset_root.parts[-3:]
        for latest in sorted(asset_root.glob("**/publish/review/**/latest.json"), reverse=True):
            review = _latest_review_json(latest.parent)
            if not review:
                continue
            data = _read_json(review)
            rows.append(
                {
                    "label": "_".join(parts),
                    "category": parts[0],
                    "group": parts[1],
                    "asset": parts[2],
                    "dept": str(data.get("department") or latest.parent.name),
                    "version": str(data.get("version") or ""),
                    "type": str(data.get("type") or "review"),
                    "review_json": str(review),
                }
            )
            break
    return rows


class SmartReviewWidget(QtWidgets.QWidget):
    def __init__(self, mode):
        QtWidgets.QWidget.__init__(self)
        self.mode = mode
        self.repo_root = _repo_root()
        self.project_root = Path()
        self._build_ui()
        self.refresh_projects()

    def _build_ui(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        header = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("Smart Review")
        title.setStyleSheet("font-weight: 700; font-size: 14px;")
        self.status = QtWidgets.QLabel("Connected to SmartLibrary")
        self.status.setStyleSheet("color: #8fbc8f;")
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.status)
        root.addLayout(header)

        context = QtWidgets.QGridLayout()
        self.project_combo = QtWidgets.QComboBox()
        self.refresh_btn = QtWidgets.QPushButton("Refresh")
        context.addWidget(QtWidgets.QLabel("Project"), 0, 0)
        context.addWidget(self.project_combo, 0, 1)
        context.addWidget(self.refresh_btn, 0, 2)
        root.addLayout(context)

        self.tabs = QtWidgets.QTabWidget()
        root.addWidget(self.tabs, 1)
        self.asset_tab = QtWidgets.QWidget()
        self.shot_tab = QtWidgets.QWidget()
        self.tabs.addTab(self.asset_tab, "Asset")
        self.tabs.addTab(self.shot_tab, "Shot")
        self._build_asset_tab()
        self._build_shot_tab()

        self.project_combo.currentTextChanged.connect(self.refresh_context)
        self.refresh_btn.clicked.connect(self.refresh_projects)

    def _build_asset_tab(self):
        layout = QtWidgets.QVBoxLayout(self.asset_tab)
        layout.setSpacing(5)
        self.asset_current_btn = QtWidgets.QPushButton("Current Shot/Sequence")
        layout.addWidget(self.asset_current_btn)
        self.asset_filter = QtWidgets.QComboBox()
        self.asset_filter.addItems(["Cast Assets", "All Review Assets"])
        self.asset_search = QtWidgets.QLineEdit()
        self.asset_search.setPlaceholderText("Search asset")
        layout.addWidget(self.asset_filter)
        layout.addWidget(self.asset_search)
        self.asset_list = QtWidgets.QListWidget()
        layout.addWidget(self.asset_list, 1)

        form = QtWidgets.QGridLayout()
        self.variant_combo = _combo(["default"])
        self.asset_dept_combo = _combo(ASSET_DEPTS)
        self.asset_version_combo = _combo(["latest"])
        self.asset_take_combo = _combo(["latest"])
        for row, (label, widget) in enumerate(
            [
                ("Variant", self.variant_combo),
                ("Department", self.asset_dept_combo),
                ("Version", self.asset_version_combo),
                ("Take", self.asset_take_combo),
            ]
        ):
            form.addWidget(QtWidgets.QLabel(label), row, 0)
            form.addWidget(widget, row, 1)
        layout.addLayout(form)

        self.asset_mode_combo = _combo(["Quick Check Grid", "Model Turntable", "Look Turntable"])
        layout.addWidget(self.asset_mode_combo)
        checks = QtWidgets.QHBoxLayout()
        self.beauty_check = QtWidgets.QCheckBox("Beauty")
        self.wire_check = QtWidgets.QCheckBox("Wireframe")
        self.bbox_check = QtWidgets.QCheckBox("BBox")
        for cb in (self.beauty_check, self.wire_check, self.bbox_check):
            cb.setChecked(True)
            checks.addWidget(cb)
        layout.addLayout(checks)
        self.asset_summary = QtWidgets.QPlainTextEdit()
        self.asset_summary.setReadOnly(True)
        self.asset_summary.setMaximumHeight(74)
        layout.addWidget(self.asset_summary)
        self._add_actions(layout)

        self.asset_search.textChanged.connect(self.refresh_assets)
        self.asset_current_btn.clicked.connect(self.current_context)
        self.asset_list.itemChanged.connect(lambda _item: self._update_asset_summary())

    def _build_shot_tab(self):
        layout = QtWidgets.QVBoxLayout(self.shot_tab)
        layout.setSpacing(5)
        self.shot_current_btn = QtWidgets.QPushButton("Current Shot/Sequence")
        layout.addWidget(self.shot_current_btn)
        top = QtWidgets.QGridLayout()
        self.episode_combo = QtWidgets.QComboBox()
        self.sequence_combo = QtWidgets.QComboBox()
        top.addWidget(QtWidgets.QLabel("Episode"), 0, 0)
        top.addWidget(self.episode_combo, 0, 1)
        top.addWidget(QtWidgets.QLabel("Sequence"), 1, 0)
        top.addWidget(self.sequence_combo, 1, 1)
        layout.addLayout(top)

        nav = QtWidgets.QHBoxLayout()
        self.prev_seq_btn = QtWidgets.QPushButton("< prev")
        self.current_seq_btn = QtWidgets.QPushButton("current")
        self.next_seq_btn = QtWidgets.QPushButton("next >")
        nav.addWidget(self.prev_seq_btn)
        nav.addWidget(self.current_seq_btn)
        nav.addWidget(self.next_seq_btn)
        layout.addLayout(nav)
        self.scope_combo = _combo(["Current Sequence Only", "Adjacent Sequences", "Custom Range"])
        self.scope_combo.setCurrentText("Current Sequence Only")
        layout.addWidget(self.scope_combo)
        self.shot_search = QtWidgets.QLineEdit()
        self.shot_search.setPlaceholderText("Search shot")
        layout.addWidget(self.shot_search)

        select_buttons = QtWidgets.QHBoxLayout()
        self.select_all_btn = QtWidgets.QPushButton("Select All")
        self.clear_selection_btn = QtWidgets.QPushButton("Clear Selection")
        self.invert_selection_btn = QtWidgets.QPushButton("Invert Selection")
        select_buttons.addWidget(self.select_all_btn)
        select_buttons.addWidget(self.clear_selection_btn)
        select_buttons.addWidget(self.invert_selection_btn)
        layout.addLayout(select_buttons)

        self.shot_list = QtWidgets.QListWidget()
        layout.addWidget(self.shot_list, 1)
        self.shot_mode_combo = _combo(
            [
                "Quick Check",
                "Sequence Playback",
                "Dept Compare Grid",
                "OTIO Replace",
                "Handle Trim Stitch",
                "AOV Grid",
                "Contact Sheet",
            ]
        )
        layout.addWidget(self.shot_mode_combo)
        options = QtWidgets.QGridLayout()
        self.shot_dept_combo = _combo(SHOT_DEPTS)
        self.shot_profile_combo = _combo(SHOT_PROFILES)
        self.version_mode_combo = _combo(["latest"])
        self.review_version_combo = _combo([])
        self.handle_in = QtWidgets.QSpinBox()
        self.handle_out = QtWidgets.QSpinBox()
        for spin in (self.handle_in, self.handle_out):
            spin.setRange(0, 999)
            spin.setValue(8)
        self.hud_check = QtWidgets.QCheckBox("Include HUD")
        self.trim_check = QtWidgets.QCheckBox("Trim Handles")
        self.sync_check = QtWidgets.QCheckBox("Sync Views")
        self.hud_check.setChecked(True)
        self.sync_check.setChecked(True)
        options.addWidget(QtWidgets.QLabel("Department"), 0, 0)
        options.addWidget(self.shot_dept_combo, 0, 1)
        options.addWidget(QtWidgets.QLabel("Profile"), 1, 0)
        options.addWidget(self.shot_profile_combo, 1, 1)
        options.addWidget(QtWidgets.QLabel("Version"), 2, 0)
        options.addWidget(self.version_mode_combo, 2, 1)
        options.addWidget(QtWidgets.QLabel("Review Version"), 3, 0)
        options.addWidget(self.review_version_combo, 3, 1)
        options.addWidget(QtWidgets.QLabel("Handle In"), 4, 0)
        options.addWidget(self.handle_in, 4, 1)
        options.addWidget(QtWidgets.QLabel("Handle Out"), 5, 0)
        options.addWidget(self.handle_out, 5, 1)
        options.addWidget(self.hud_check, 6, 0, 1, 2)
        options.addWidget(self.trim_check, 7, 0, 1, 2)
        options.addWidget(self.sync_check, 8, 0, 1, 2)
        layout.addLayout(options)
        decisions = QtWidgets.QGridLayout()
        self.approve_btn = QtWidgets.QPushButton("Approve for Delivery")
        self.changes_btn = QtWidgets.QPushButton("Request Changes")
        self.revoke_btn = QtWidgets.QPushButton("Revoke Approval")
        decisions.addWidget(self.approve_btn, 0, 0, 1, 2)
        decisions.addWidget(self.changes_btn, 1, 0)
        decisions.addWidget(self.revoke_btn, 1, 1)
        layout.addLayout(decisions)
        self.shot_summary = QtWidgets.QPlainTextEdit()
        self.shot_summary.setReadOnly(True)
        self.shot_summary.setMaximumHeight(92)
        layout.addWidget(self.shot_summary)
        self._add_actions(layout)

        self.episode_combo.currentTextChanged.connect(self.refresh_sequences)
        self.sequence_combo.currentTextChanged.connect(self.refresh_shots)
        self.scope_combo.currentTextChanged.connect(self.refresh_shots)
        self.shot_search.textChanged.connect(self.refresh_shots)
        self.select_all_btn.clicked.connect(lambda: self._set_shot_checks(QtCore.Qt.Checked))
        self.clear_selection_btn.clicked.connect(lambda: self._set_shot_checks(QtCore.Qt.Unchecked))
        self.invert_selection_btn.clicked.connect(self._invert_shot_checks)
        self.shot_current_btn.clicked.connect(self.current_context)
        self.shot_list.itemChanged.connect(lambda _item: self._update_shot_summary())
        self.shot_mode_combo.currentTextChanged.connect(lambda _text: self._update_shot_summary())
        self.shot_profile_combo.currentTextChanged.connect(self.refresh_shots)
        self.shot_dept_combo.currentTextChanged.connect(self._refresh_review_versions)
        self.shot_profile_combo.currentTextChanged.connect(self._refresh_review_versions)
        self.shot_list.itemChanged.connect(lambda _item: self._refresh_review_versions())
        self.approve_btn.clicked.connect(lambda: self._review_decision("APPROVED"))
        self.changes_btn.clicked.connect(lambda: self._review_decision("CHANGES_REQUESTED"))
        self.revoke_btn.clicked.connect(lambda: self._review_decision("REVOKED"))

    def _add_actions(self, layout):
        grid = QtWidgets.QGridLayout()
        buttons = [
            ("Load Into Current Session", self.load_current),
            ("Replace Current Sources", self.replace_sources),
            ("Open New Session", self.open_new_session),
            ("Build RV Session", self.build_session),
        ]
        for index, (label, slot) in enumerate(buttons):
            button = QtWidgets.QPushButton(label)
            button.clicked.connect(slot)
            grid.addWidget(button, index // 2, index % 2)
        layout.addLayout(grid)
        push = QtWidgets.QPushButton("Push to RV")
        push.setStyleSheet("font-weight: 700; padding: 6px;")
        push.clicked.connect(self.load_current)
        layout.addWidget(push)

    def refresh_projects(self):
        current = self.project_combo.currentText()
        self.project_combo.clear()
        self.project_combo.addItems(_project_names(self.repo_root))
        preferred = current or os.environ.get("SMART_REVIEW_PROJECT") or "STKB"
        index = self.project_combo.findText(preferred)
        if index >= 0:
            self.project_combo.setCurrentIndex(index)
        self.refresh_context()

    def refresh_context(self):
        self.project_root = _project_root(self.repo_root, self.project_combo.currentText())
        self.status.setText("Connected to SmartLibrary" if self.project_root.exists() else "Project root missing")
        self.refresh_episodes()
        self.refresh_assets()

    def refresh_episodes(self):
        current = self.episode_combo.currentText()
        self.episode_combo.blockSignals(True)
        self.episode_combo.clear()
        shots_root = _entity_root(self.project_root, "shots")
        episodes = [path.name for path in sorted(shots_root.iterdir()) if path.is_dir()] if shots_root.exists() else []
        self.episode_combo.addItems(episodes)
        if current:
            index = self.episode_combo.findText(current)
            if index >= 0:
                self.episode_combo.setCurrentIndex(index)
        self.episode_combo.blockSignals(False)
        self.refresh_sequences()

    def refresh_sequences(self):
        current = self.sequence_combo.currentText()
        self.sequence_combo.blockSignals(True)
        self.sequence_combo.clear()
        base = _entity_root(self.project_root, "shots") / self.episode_combo.currentText()
        sequences = [path.name for path in sorted(base.iterdir()) if path.is_dir()] if base.exists() else []
        self.sequence_combo.addItems(sequences)
        if current:
            index = self.sequence_combo.findText(current)
            if index >= 0:
                self.sequence_combo.setCurrentIndex(index)
        self.sequence_combo.blockSignals(False)
        self.refresh_shots()

    def refresh_shots(self):
        self.shot_list.clear()
        episode = self.episode_combo.currentText()
        sequence = self.sequence_combo.currentText()
        if not episode or not sequence:
            self._update_shot_summary()
            return
        sequences = [sequence]
        if self.scope_combo.currentText() in ("Adjacent Sequences", "Prev+Current+Next"):
            sequences = _adjacent_sequences(self.project_root, episode, sequence)
        search = self.shot_search.text().strip().lower()
        rows = []
        for seq in sequences:
            for shot_dir in _shot_dirs(self.project_root, episode, seq):
                if search and search not in shot_dir.name.lower() and search not in seq.lower():
                    continue
                rows.append(
                    {
                        "episode": episode,
                        "sequence": seq,
                        "shot": shot_dir.name,
                        "status": self._shot_status(episode, seq, shot_dir.name),
                    }
                )
        for row in sorted(rows, key=lambda item: (item["episode"], item["sequence"], item["shot"])):
            label = "%s / %s / %s  %s" % (row["episode"], row["sequence"], row["shot"], row["status"])
            item = QtWidgets.QListWidgetItem(label)
            item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
            item.setCheckState(QtCore.Qt.Unchecked)
            item.setData(
                QtCore.Qt.UserRole,
                {"episode": row["episode"], "sequence": row["sequence"], "shot": row["shot"]},
            )
            self.shot_list.addItem(item)
        self._update_shot_summary()
        self._refresh_review_versions()

    def refresh_assets(self):
        self.asset_list.clear()
        search = self.asset_search.text().strip().lower()
        for row in _asset_review_rows(self.project_root):
            label = "%s  %s %s  %s" % (row["label"], row["dept"], row["version"], row["type"])
            if search and search not in label.lower():
                continue
            item = QtWidgets.QListWidgetItem(label)
            item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
            item.setCheckState(QtCore.Qt.Unchecked)
            item.setData(QtCore.Qt.UserRole, row)
            self.asset_list.addItem(item)
        self._update_asset_summary()

    def _shot_status(self, episode, sequence, shot):
        chunks = []
        profile = self.shot_profile_combo.currentText() if hasattr(self, "shot_profile_combo") else "publish"
        for dept in ("layout", "anim", "comp"):
            review = _latest_shot_review(self.project_root, episode, sequence, shot, dept, profile)
            sequence_media = _sequence_shot_media(self.project_root, episode, sequence, shot, dept)
            quick_media = _quick_check_media(self.project_root, episode, sequence, shot, dept)
            chunks.append("%s %s" % (dept, "ready" if review or sequence_media or quick_media else "-"))
        return " | ".join(chunks)

    def _selected_shots(self):
        rows = []
        for index in range(self.shot_list.count()):
            item = self.shot_list.item(index)
            if item.checkState() == QtCore.Qt.Checked:
                rows.append(item.data(QtCore.Qt.UserRole))
        return rows

    def _selected_review_path(self):
        rows = self._selected_shots()
        if len(rows) != 1:
            raise RuntimeError("Select exactly one shot for a review decision.")
        row = rows[0]
        base = _shot_review_base(
            self.project_root,
            row["episode"], row["sequence"], row["shot"],
            self.shot_dept_combo.currentText(),
            self.shot_profile_combo.currentText(),
        )
        version = self.review_version_combo.currentText()
        review = base / version / "review.json"
        if not review.is_file():
            raise RuntimeError("Selected review version was not found: %s" % review)
        return review

    def _refresh_review_versions(self):
        current = self.review_version_combo.currentText()
        versions = []
        rows = self._selected_shots()
        if len(rows) == 1:
            row = rows[0]
            base = _shot_review_base(
                self.project_root,
                row["episode"], row["sequence"], row["shot"],
                self.shot_dept_combo.currentText(),
                self.shot_profile_combo.currentText(),
            )
            versions = _review_versions(base)
        self.review_version_combo.blockSignals(True)
        self.review_version_combo.clear()
        self.review_version_combo.addItems(versions)
        if current in versions:
            self.review_version_combo.setCurrentText(current)
        self.review_version_combo.blockSignals(False)

    def _review_decision(self, decision):
        try:
            review = self._selected_review_path()
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Smart Review", str(exc))
            return
        verb = {
            "APPROVED": "Approve for Client Delivery",
            "CHANGES_REQUESTED": "Request Changes",
            "REVOKED": "Revoke Approval",
        }[decision]
        answer = QtWidgets.QMessageBox.question(
            self,
            verb,
            "%s\n\n%s\n\nContinue?" % (verb, review),
        )
        if answer != QtWidgets.QMessageBox.Yes:
            return
        comment, accepted = QtWidgets.QInputDialog.getText(self, verb, "Comment")
        if not accepted:
            return
        try:
            record = _decision_service().decide(
                review,
                decision,
                author=os.environ.get("USERNAME") or os.environ.get("USER") or getpass.getuser(),
                comment=str(comment),
            )
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Smart Review", str(exc))
            return
        self.status.setText("%s: %s %s" % (record.decision, record.version, record.author))
        self.refresh_shots()

    def _selected_assets(self):
        rows = []
        for index in range(self.asset_list.count()):
            item = self.asset_list.item(index)
            if item.checkState() == QtCore.Qt.Checked:
                rows.append(item.data(QtCore.Qt.UserRole))
        return rows

    def _set_shot_checks(self, state):
        for index in range(self.shot_list.count()):
            self.shot_list.item(index).setCheckState(state)
        self._update_shot_summary()

    def _invert_shot_checks(self):
        for index in range(self.shot_list.count()):
            item = self.shot_list.item(index)
            item.setCheckState(QtCore.Qt.Unchecked if item.checkState() == QtCore.Qt.Checked else QtCore.Qt.Checked)
        self._update_shot_summary()

    def _update_shot_summary(self):
        rows = self._selected_shots()
        sequences = sorted({row["sequence"] for row in rows})
        output = "Dept 2x2: Story Reel | Layout / Animation | Comp" if self.shot_mode_combo.currentText() == "Dept Compare Grid" else "RV session stack"
        self.shot_summary.setPlainText(
            "Selected shots: %d\nSequences: %s\nMode: %s\nProfile: %s\nOutput: %s"
            % (
                len(rows),
                ", ".join(sequences) or "-",
                self.shot_mode_combo.currentText(),
                self.shot_profile_combo.currentText(),
                output,
            )
        )

    def _update_asset_summary(self):
        rows = self._selected_assets()
        self.asset_summary.setPlainText(
            "Selected assets: %d\nSources: beauty, wireframe, bbox\nContext: current shot/sequence\nOutput: RV asset review stack"
            % len(rows)
        )

    def _shot_media(self):
        if self.shot_mode_combo.currentText() == "Dept Compare Grid":
            return self._dept_compare_media()
        media = []
        dept = self.shot_dept_combo.currentText()
        for row in self._selected_shots():
            if self.shot_mode_combo.currentText() == "Quick Check":
                media.extend(
                    _quick_check_media(
                        self.project_root,
                        row["episode"],
                        row["sequence"],
                        row["shot"],
                        dept,
                    )
                )
                continue
            media.extend(self._department_media(row, dept))
        return _dedupe(media)

    def _department_media(self, row, dept):
        profile = self.shot_profile_combo.currentText()
        if profile != "publish":
            base = _shot_review_base(
                self.project_root, row["episode"], row["sequence"], row["shot"], dept, profile
            )
            selected_version = self.review_version_combo.currentText() if dept == self.shot_dept_combo.currentText() else ""
            selected_review = base / selected_version / "review.json" if selected_version else None
            review = selected_review if selected_review and selected_review.is_file() else _latest_shot_review(
                self.project_root, row["episode"], row["sequence"], row["shot"], dept, profile
            )
            if review:
                return _media_from_review(review)[:1]
        sequence_media = _sequence_shot_media(
            self.project_root,
            row["episode"],
            row["sequence"],
            row["shot"],
            dept,
        )
        if sequence_media:
            return sequence_media[:1]
        review = _latest_shot_review(
            self.project_root,
            row["episode"],
            row["sequence"],
            row["shot"],
            dept,
            "publish",
        )
        return _media_from_review(review)[:1] if review else []

    def _dept_compare_media(self):
        rows = self._selected_shots()
        if not rows:
            return []
        row = rows[0]
        media = []
        for resolver in (
            lambda: _story_reel_media(self.project_root, row["episode"], row["sequence"], row["shot"]),
            lambda: self._department_media(row, "layout"),
            lambda: self._department_media(row, "anim"),
            lambda: self._department_media(row, "comp"),
        ):
            resolved = resolver()
            media.extend(resolved[:1])
        return _dedupe(media)

    def _asset_media(self):
        media = []
        for row in self._selected_assets():
            media.extend(_media_from_review(row["review_json"]))
        return _dedupe(media)

    def _current_media(self):
        if self.tabs.currentWidget() == self.asset_tab:
            self._update_asset_summary()
            return self._asset_media()
        self._update_shot_summary()
        return self._shot_media()

    def _apply_view_mode(self, source_count):
        if self.tabs.currentWidget() != self.shot_tab:
            return
        mode = self.shot_mode_combo.currentText()
        if mode not in ("Contact Sheet", "Dept Compare Grid"):
            return
        rows, columns = (2, 2) if mode == "Dept Compare Grid" else _grid_dimensions(source_count)
        try:
            commands.setViewNode("defaultLayout")
            commands.setStringProperty("#RVLayoutGroup.layout.mode", ["grid"], True)
            commands.setIntProperty("#RVLayoutGroup.layout.gridRows", [rows], True)
            commands.setIntProperty("#RVLayoutGroup.layout.gridColumns", [columns], True)
            commands.setFloatProperty("#RVLayoutGroup.layout.spacing", [1.0], True)
            commands.redraw()
        except Exception as exc:
            self.status.setText("Loaded, but grid layout failed: %s" % exc)

    def _load_media(self, replace=False, new_session=False):
        media = self._current_media()
        if not media:
            self.status.setText("No media resolved")
            return
        if new_session:
            commands.newSession()
        elif replace:
            commands.clearSession()
        commands.addSources(media, "explicit", False, False)
        self._apply_view_mode(len(media))
        self.status.setText("Loaded %d source(s)" % len(media))

    def load_review_json(self, review_json, replace=True, selected_shots=None):
        review_json = Path(review_json)
        selected_shots = selected_shots or []
        self._set_context_from_review(review_json)
        if selected_shots:
            self._select_shot_names(selected_shots)
        media = _media_from_review(review_json, selected_shots=selected_shots)
        if not media:
            self.status.setText("No media resolved: %s" % review_json)
            return
        if replace:
            commands.clearSession()
        commands.addSources(media, "explicit", False, False)
        self.status.setText("Loaded %d source(s): %s" % (len(media), review_json.name))

    def _set_context_from_review(self, review_json):
        data = _read_json(review_json)
        episode = str(data.get("episode") or "")
        sequence = str(data.get("sequence") or "")
        if not episode or not sequence:
            return
        episode_index = self.episode_combo.findText(episode)
        if episode_index >= 0:
            self.episode_combo.setCurrentIndex(episode_index)
        sequence_index = self.sequence_combo.findText(sequence)
        if sequence_index >= 0:
            self.sequence_combo.setCurrentIndex(sequence_index)

    def _select_shot_names(self, shot_names):
        wanted = set(str(name) for name in shot_names if str(name))
        if not wanted:
            return
        for index in range(self.shot_list.count()):
            item = self.shot_list.item(index)
            row = item.data(QtCore.Qt.UserRole) or {}
            state = QtCore.Qt.Checked if row.get("shot") in wanted else QtCore.Qt.Unchecked
            item.setCheckState(state)
        self._update_shot_summary()

    def load_current(self):
        self._load_media()

    def replace_sources(self):
        self._load_media(replace=True)

    def open_new_session(self):
        self._load_media(new_session=True)

    def build_session(self):
        self._load_media()

    def current_context(self):
        self.status.setText("Current Shot/Sequence context requested")


def _combo(values):
    combo = QtWidgets.QComboBox()
    combo.addItems(list(values))
    return combo


class SmartReviewMode(rvtypes.MinorMode):
    def __init__(self):
        rvtypes.MinorMode.__init__(self)
        self.init(
            "smart_review",
            [],
            None,
            [
                (
                    "Smart Review",
                    [
                        ("Show Panel", self.show_panel, "", None),
                        ("Hide Panel", self.hide_panel, "", None),
                        ("Toggle Panel", self.toggle_panel, "", None),
                        ("Refresh", self.refresh_panel, "", None),
                    ],
                )
            ],
        )
        self.window = qtutils.sessionWindow()
        self.dock = QtWidgets.QDockWidget("Smart Review", self.window)
        self.panel = SmartReviewWidget(self)
        self.dock.setWidget(self.panel)
        self.window.addDockWidget(QtCore.Qt.RightDockWidgetArea, self.dock)
        self.dock.hide()
        QtCore.QTimer.singleShot(0, self.apply_launch_context)

    def toggle_panel(self, event=None):
        if self.dock.isVisible():
            self.dock.hide()
        else:
            self.dock.show()

    def show_panel(self, event=None):
        self.dock.show()

    def hide_panel(self, event=None):
        self.dock.hide()

    def refresh_panel(self, event=None):
        self.panel.refresh_context()
        self.dock.show()

    def apply_launch_context(self):
        review_json = _launch_review_json()
        selected_shots = _launch_selected_shots()
        if _env_flag("SMART_REVIEW_SHOW_PANEL", False) or review_json:
            self.dock.show()
        if review_json and _env_flag("SMART_REVIEW_AUTO_LOAD", True):
            self.panel.load_review_json(
                review_json,
                replace=_env_flag("SMART_REVIEW_REPLACE", True),
                selected_shots=selected_shots,
            )

    def activate(self):
        rvtypes.MinorMode.activate(self)
        self.dock.show()

    def deactivate(self):
        rvtypes.MinorMode.deactivate(self)
        self.dock.hide()


def createMode():
    return SmartReviewMode()
