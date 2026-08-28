from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class EntityType(str, Enum):
    ASSET = "asset"
    ASSEMBLY = "assembly"
    SEQUENCE = "sequence"
    SHOT = "shot"


REFERENCEABLE_ENTITY_TYPES = frozenset({EntityType.ASSET, EntityType.ASSEMBLY})


@dataclass(frozen=True)
class EntityReference:
    uid: str
    entity_type: EntityType
    entity_id: str
    variant: str = "default"
    version: str = ""
    namespace: str = ""

    def __post_init__(self) -> None:
        if self.entity_type not in REFERENCEABLE_ENTITY_TYPES:
            raise ValueError(f"Entity is not referenceable: {self.entity_type.value}")
        if not self.uid.strip() or not self.entity_id.strip():
            raise ValueError("uid and entity_id are required")
