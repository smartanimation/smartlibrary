from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from smartlib.crowd.schema import BehaviorSchema


@dataclass
class AnimationProperties:
    animation_type: str
    animation_style: str
    interaction: str = "none"
    duration: float = 0.0
    duration_frames: float = 0.0
    duration_seconds: float = 0.0
    fps: float = 24.0
    unit: str = ""
    start_frame: float = 0.0
    end_frame: float = 0.0
    root_motion: dict[str, Any] = field(default_factory=dict)
    speed: float = 0.0
    speed_houdini: float = 0.0
    loop: bool = False
    bounding_box: dict[str, Any] = field(default_factory=dict)
    foot_contact: list[dict[str, Any]] = field(default_factory=list)

    def validate(self, schema: BehaviorSchema) -> None:
        schema.require_option("animation_types", self.animation_type)
        schema.require_option("animation_styles", self.animation_style)
        if self.interaction not in {"", "none"}:
            schema.require_option("interaction_types", self.interaction)

    def to_yaml_data(self, schema: BehaviorSchema) -> dict[str, Any]:
        self.validate(schema)
        return {
            "animation": {
                "type": self.animation_type,
                "style": self.animation_style,
                "interaction": self.interaction or "none",
                "duration": self.duration,
                "durationFrames": self.duration_frames or self.duration,
                "durationSeconds": self.duration_seconds,
                "fps": self.fps,
                "unit": self.unit,
                "startFrame": self.start_frame,
                "endFrame": self.end_frame,
                "rootMotion": self.root_motion,
                "speed": self.speed,
                "speed_houdini": self.speed_houdini,
                "loop": self.loop,
                "boundingBox": self.bounding_box,
                "foot_contact": self.foot_contact,
            }
        }
