"""Versioned project publish contracts, separate from asset quality profiles."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class PipelineProfile:
    name: str
    version: int
    representation: str
    products: tuple[str, ...]
    entrypoint: str
    description: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["products"] = list(self.products)
        return data


PROFILES = {
    "usd_animation": PipelineProfile(
        "usd_animation", 1, "usd", ("deform",), "shot.usda",
        "Final Deform USD; Look can be adopted independently. Skeleton is not required.",
    ),
    "alembic_cache": PipelineProfile(
        "alembic_cache", 1, "abc", ("deform",), "shot_manifest.json",
        "Final Deform ABC; the fixed composition manifest is the shot entrypoint.",
    ),
    "maya_rend_atom": PipelineProfile(
        "maya_rend_atom", 1, "maya", ("transfer", "rend"), "shot_render.ma",
        "ATOM transfer and validated applied REND scenes, assembled into a Maya shot.",
    ),
}


def profile_from_settings(settings: dict[str, Any]) -> PipelineProfile | None:
    name = settings.get("pipeline_profile")
    if name in (None, ""):
        return None  # Existing projects keep their legacy workflow until configured.
    if not isinstance(name, str) or name not in PROFILES:
        raise ValueError(f"Unknown pipeline_profile: {name!r}")
    version = settings.get("pipeline_profile_version", 1)
    if type(version) is not int or version != 1:
        raise ValueError(f"Unsupported pipeline_profile_version: {version!r}")
    return PROFILES[name]
