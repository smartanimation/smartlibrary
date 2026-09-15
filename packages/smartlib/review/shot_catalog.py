"""Read-only review movie inventory; all pipeline roots come from ProjectPaths."""
import json
import re
from dataclasses import dataclass
from pathlib import Path

MOVIES = {".mov", ".mp4", ".m4v", ".avi", ".mxf"}


def version_key(value):
    return tuple(int(n) for n in re.findall(r"\d+", str(value))) or (0,)


def movie_version(path):
    match = re.search(r"(?:^|_)(v\d+(?:_t\d+)?)(?:_|$)", Path(path).stem, re.I)
    return match.group(1).lower() if match else "unversioned"


@dataclass(frozen=True)
class ShotMovie:
    episode: str
    sequence: str
    shot: str
    task: str
    profile: str
    version: str = ""
    movie: str = ""
    modified: float = 0

    @property
    def group(self):
        return (self.episode, self.sequence, self.shot, self.task, self.profile)

    @property
    def identity(self):
        return self.group + (self.version, self.movie)


def scan_catalog(paths, tasks, profile, cancelled=lambda: False):
    """Enumerate discovered identity directories, then ask the shared resolver for media roots."""
    rows, errors = [], []
    root = paths.shots_root()
    if not root.is_dir():
        raise FileNotFoundError("Shot root is unavailable: %s" % root)
    for episode in sorted(root.iterdir()):
        if not episode.is_dir():
            continue
        for sequence in sorted(episode.iterdir()):
            if not sequence.is_dir():
                continue
            for shot in sorted(sequence.iterdir()):
                if not shot.is_dir():
                    continue
                for task in tasks:
                    if cancelled():
                        return [], []
                    identity = (episode.name, sequence.name, shot.name, task, profile)
                    found = []
                    try:
                        if profile == "working":
                            base = paths.shot_review_movie_dir(*identity[:4])
                            for movie in sorted(base.glob("*")):
                                if movie.suffix.lower() in MOVIES and movie.is_file():
                                    found.append(ShotMovie(*identity, movie_version(movie), str(movie), movie.stat().st_mtime))
                        else:
                            roots = ([paths.shot_review_publish_root(*identity[:4])] if profile == "publish"
                                     else paths.shot_review_read_roots(*identity[:4], profile))
                            manifests = dict.fromkeys(manifest for base in roots for manifest in sorted(base.rglob("review.json")))
                            versions = set()
                            for manifest in manifests:
                                if cancelled():
                                    return [], []
                                try:
                                    data = json.loads(manifest.read_text(encoding="utf-8-sig"))
                                    if not isinstance(data, dict):
                                        raise ValueError("Expected an object")
                                    department = data.get("department") or data.get("dept")
                                    if department and department != task:
                                        continue
                                    parent = manifest.parent.parent if manifest.parent.name == "metadata" else manifest.parent
                                    version = str(data.get("version") or next((p.name for p in manifest.parents if re.fullmatch(r"v\d+", p.name)), "unversioned"))
                                    take = next((p.name for p in manifest.parents if re.fullmatch(r"t\d+", p.name)), "")
                                    if take:
                                        version += "_" + take
                                    if version in versions:
                                        continue
                                    versions.add(version)
                                    value = data.get("movie")
                                    movie = parent / str(value) if value else None
                                    available = movie is not None and movie.suffix.lower() in MOVIES and movie.is_file()
                                    found.append(ShotMovie(*identity, version, str(movie) if available else "", movie.stat().st_mtime if available else manifest.stat().st_mtime))
                                except (OSError, ValueError, TypeError) as exc:
                                    errors.append("%s: %s" % (manifest, exc))
                    except (OSError, ValueError) as exc:
                        errors.append("%s: %s" % ("/".join(identity), exc))
                    rows.extend(found or [ShotMovie(*identity)])
    return rows, errors


def filter_catalog(rows, episode="", sequence="", task="", latest=True,
                   version="", availability="All", search="", seen=None):
    seen = seen or {}
    sequences = None if sequence is None or sequence == "" else ({sequence} if isinstance(sequence, str) else set(sequence))
    candidates = [r for r in rows if (not episode or r.episode == episode)
                  and (sequences is None or r.sequence in sequences) and (not task or r.task == task)]
    if latest:
        groups = {}
        for row in candidates:
            previous = groups.get(row.group)
            if previous is None or (version_key(row.version), row.modified, row.movie) > (version_key(previous.version), previous.modified, previous.movie):
                groups[row.group] = row
        candidates = list(groups.values())
    result = []
    for row in candidates:
        if version and row.version != version:
            continue
        if search.lower() not in " ".join(row.group + (row.version,)).lower():
            continue
        if availability == "Has video" and not row.movie:
            continue
        if availability == "Missing" and row.movie:
            continue
        if availability == "New versions" and not is_new(row, seen):
            continue
        result.append(row)
    return sorted(result, key=lambda r: (r.group, version_key(r.version), r.movie))


def is_new(row, seen):
    """No history means unseen, not proof of a version update."""
    previous = seen.get(row.group)
    return bool(row.movie and previous and version_key(row.version) > version_key(previous))
