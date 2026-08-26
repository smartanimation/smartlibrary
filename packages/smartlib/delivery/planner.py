from __future__ import annotations

import uuid
from pathlib import Path

from .models import AssetContext, DeliveryInput, DeliveryItem, DeliveryPlan, ShotContext
from .profile import DeliveryProfile


class DeliveryPlanner:
    def __init__(self, profile: DeliveryProfile):
        self.profile = profile

    def plan(
        self,
        context: ShotContext | AssetContext,
        inputs: list[DeliveryInput],
        package_root: str | Path,
        *,
        job_id: str = "",
        metadata: dict | None = None,
    ) -> DeliveryPlan:
        items = []
        destinations: dict[str, str] = {}
        for source in inputs:
            tokens = context.tokens()
            tokens.update(source.metadata)
            destination_text = self.profile.render(source.template, tokens).as_posix()
            frame = str(source.metadata.get("frame") or "")
            if frame:
                destination_text = destination_text.replace("####", frame.zfill(4))
            destination = Path(destination_text)
            case_key = destination.as_posix()
            folded = case_key.casefold()
            if folded in destinations:
                previous = destinations[folded]
                detail = "case-only collision" if previous != case_key else "duplicate destination"
                raise ValueError(f"{detail}: {previous} / {case_key}")
            destinations[folded] = case_key
            items.append(
                DeliveryItem(
                    id=source.id,
                    kind=source.kind,
                    source=Path(source.source),
                    destination=destination,
                    required=source.required,
                    metadata=source.metadata,
                )
            )
        return DeliveryPlan(
            job_id=job_id or f"DLV-{uuid.uuid4().hex[:8].upper()}",
            profile_id=self.profile.id,
            profile_version=self.profile.version,
            context=context,
            items=items,
            package_root=Path(package_root),
            metadata=dict(metadata or {}),
        )
