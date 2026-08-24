from __future__ import annotations

import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Iterable

from smartlib.apps.shot_manager import ShotIdentity, ShotManagerService
from smartlib.core.config_loader import ProjectConfig
from smartlib.core.metadata import read_json
from smartlib.delivery import DeliveryEngine, DeliveryInput, DeliveryPlanner, DeliveryProfile, ShotContext
from smartlib.review.decisions import ReviewDecisionService
from smartlib.delivery.after_effects import AfterEffectsDeliveryAdapter


FRAME_RE = re.compile(r"(?P<frame>\d{4,})(?=\.[^.]+$)")


class SmartDeliveryService:
    def __init__(self, config_dir: str | Path):
        self.config = ProjectConfig(config_dir)
        self.shots = ShotManagerService(self.config)
        self.profile_path = Path(config_dir) / "delivery" / "clients" / "dandelione_v003.yml"
        if not self.profile_path.is_file():
            raise FileNotFoundError(f"Client Delivery Profile was not found: {self.profile_path}")
        self.profile = DeliveryProfile.load(self.profile_path)

    def list_shots(self) -> list[ShotIdentity]:
        return self.shots.list_shots()

    def review_layers(self, identity: ShotIdentity) -> list[str]:
        return list(self.shots.review_layers(identity).keys())

    def review_source(self, identity: ShotIdentity, department: str = "anim", profile: str = "internal") -> dict:
        root = self.shots.shot_root(identity) / "review" / department / profile
        approval = ReviewDecisionService.approval(root)
        latest = read_json(root / "latest.json", {}) or {}
        latest_path = root / str(latest.get("path") or "")
        latest_review = read_json(latest_path, {}) if latest_path.is_file() else {}
        latest_version = str((latest_review or {}).get("version") or latest.get("version") or "")
        review_path = ReviewDecisionService.approved_review(root)
        if review_path is None:
            return {
                "approved": False,
                "version": "",
                "state": "NOT_APPROVED",
                "latest_version": latest_version,
                "approval": approval,
            }
        if not review_path.is_file():
            return {}
        version_root = review_path.parent
        review = read_json(review_path, {}) or {}
        manifest_path = version_root / "source_manifest.json"
        manifest = read_json(manifest_path, {}) or {}
        layers = {
            str(layer): str((data or {}).get("sequence") or "")
            for layer, data in (manifest.get("layers") or {}).items()
            if str((data or {}).get("sequence") or "")
        }
        movie = version_root / str(review.get("movie") or "review.mov")
        return {
            "approved": True,
            "version": str(review.get("version") or version_root.name),
            "state": "APPROVED",
            "review": str(movie) if movie.is_file() else "",
            "maya": str(manifest.get("construct") or ""),
            "aep": str(manifest.get("precomp") or ""),
            "image_sequences": layers,
            "manifest": str(manifest_path),
            "approval": approval,
            "latest_version": latest_version,
        }

    def review_status(self, identity: ShotIdentity, department: str = "anim") -> str:
        source = self.review_source(identity, department)
        if source.get("approved"):
            approved = source.get("version") or "-"
            latest = source.get("latest_version") or "-"
            return f"APPROVED {approved} | LATEST {latest}"
        version = source.get("latest_version") or "no review"
        return f"NOT APPROVED | LATEST {version}"

    def suggested_sources(self, identity: ShotIdentity, department: str = "anim") -> dict[str, str]:
        review_source = self.review_source(identity, department)
        if review_source.get("approved"):
            return {kind: str(review_source.get(kind) or "") for kind in ("maya", "aep", "review")}
        if review_source:
            return {kind: "" for kind in ("maya", "aep", "review")}
        root = self.shots.shot_root(identity)
        return {
            "maya": _latest(root, {".ma", ".mb"}),
            "aep": _latest(root, {".aep"}),
            "review": _latest(root, {".mov", ".mp4"}, prefer=("review", "finalimage", "precomp")),
        }

    def suggested_image_sequences(self, identity: ShotIdentity, department: str = "anim") -> dict[str, str]:
        review_source = self.review_source(identity, department)
        if review_source.get("approved") and review_source.get("image_sequences"):
            return dict(review_source["image_sequences"])
        if review_source:
            return {}
        outputs = self.shots.latest_preview_render_outputs(identity, department=department)
        result = {}
        for layer, data in outputs.items():
            pattern = str(data.get("pattern") or data.get("first_file") or "")
            if pattern:
                result[str(layer)] = pattern
        return result

    def build_plan(
        self,
        identity: ShotIdentity,
        *,
        task: str,
        version: int,
        package_root: str | Path,
        sources: dict[str, str],
        layer_sequences: dict[str, str],
    ):
        review_source = self.review_source(identity)
        if not review_source.get("approved"):
            raise RuntimeError(f"{identity.code} has no approved Internal Review. Approve a version in Smart Review first.")
        context = ShotContext(identity.episode, identity.sequence, identity.shot, task, int(version))
        job_id = f"DLV-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8].upper()}"
        staging_root = self.shots.paths.delivery_staging_root() / job_id
        delivery_batch = self._next_delivery_batch()
        delivery_id = f"{job_id}-{identity.code}"
        archive_root = self.shots.paths.delivery_root() / "client" / self.profile.client / delivery_batch
        inputs = []
        for kind in ("maya", "aep", "review"):
            value = str(sources.get(kind) or "").strip()
            if value:
                inputs.append(DeliveryInput(f"{kind}.primary", kind if kind != "maya" else "maya_scene", Path(value), kind))
        for layer, pattern in layer_sequences.items():
            for frame_path in expand_sequence(pattern):
                match = FRAME_RE.search(frame_path.name)
                frame = match.group("frame") if match else ""
                inputs.append(
                    DeliveryInput(
                        f"image_sequence.{layer}.{frame or frame_path.name}",
                        "image_sequence",
                        frame_path,
                        "image_sequence",
                        metadata={"review_layer": layer, "frame": frame},
                    )
                )
        return DeliveryPlanner(self.profile).plan(
            context,
            inputs,
            staging_root,
            job_id=job_id,
            metadata={
                "review_approval": dict(review_source.get("approval") or {}),
                "client": self.profile.client,
                "client_shot_root": self.profile.root,
                "deployment_root": str(Path(package_root)),
                "archive_root": str(archive_root),
                "delivery_batch": delivery_batch,
                "delivery_id": delivery_id,
            },
        )

    def execute(self, plan, *, ffmpeg: str = ""):
        return DeliveryEngine().construct(
            plan,
            ffmpeg=ffmpeg,
            after_effects_adapter=AfterEffectsDeliveryAdapter(self.config),
        )

    def _next_delivery_batch(self) -> str:
        date = datetime.now().strftime("%Y%m%d")
        root = self.shots.paths.delivery_root() / "client" / self.profile.client
        numbers = []
        for path in root.glob(f"{date}_*") if root.is_dir() else []:
            suffix = path.name.rsplit("_", 1)[-1]
            if suffix.isdigit():
                numbers.append(int(suffix))
        return f"{date}_{max(numbers, default=0) + 1:02d}"


def expand_sequence(value: str) -> list[Path]:
    text = str(value or "").strip().replace("\\", "/")
    if not text:
        return []
    path = Path(text)
    if path.is_dir():
        return sorted(item for item in path.iterdir() if item.is_file())
    if "####" in text:
        path = Path(text)
        regex = re.compile("^" + re.escape(path.name).replace(r"\#\#\#\#", r"\d{4}") + "$")
        return sorted(item for item in path.parent.iterdir() if item.is_file() and regex.match(item.name)) if path.parent.is_dir() else []
    if "%04d" in text:
        path = Path(text)
        regex = re.compile("^" + re.escape(path.name).replace("%04d", r"\d{4}") + "$")
        return sorted(item for item in path.parent.iterdir() if item.is_file() and regex.match(item.name)) if path.parent.is_dir() else []
    return [path]


def _latest(root: Path, extensions: set[str], prefer: Iterable[str] = ()) -> str:
    if not root.is_dir():
        return ""
    rows = [path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in extensions]
    if not rows:
        return ""
    preferred = [path for path in rows if any(token in path.as_posix().lower() for token in prefer)]
    rows = preferred or rows
    return str(max(rows, key=lambda path: path.stat().st_mtime))
