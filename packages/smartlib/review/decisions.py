from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from smartlib.core.metadata import read_json


DECISIONS = {"APPROVED", "CHANGES_REQUESTED", "REVOKED"}


@dataclass(frozen=True)
class ReviewDecision:
    decision_id: str
    decision: str
    version: str
    review_json: Path
    author: str
    created_at: str
    comment: str = ""

    def to_dict(self, base: Path) -> dict[str, Any]:
        return {
            "schema": "smartpipeline.review_decision.v1",
            "decision_id": self.decision_id,
            "decision": self.decision,
            "version": self.version,
            "review_json": self.review_json.relative_to(base).as_posix(),
            "source_manifest": (self.review_json.parent / "source_manifest.json").relative_to(base).as_posix(),
            "author": self.author,
            "created_at": self.created_at,
            "comment": self.comment,
        }


class ReviewDecisionService:
    """Append review decisions and maintain a separate approved pointer."""

    def decide(
        self,
        review_json: str | Path,
        decision: str,
        *,
        author: str,
        comment: str = "",
    ) -> ReviewDecision:
        review_path = Path(review_json).resolve()
        if not review_path.is_file():
            raise FileNotFoundError(f"Review package was not found: {review_path}")
        decision = str(decision or "").strip().upper()
        if decision not in DECISIONS:
            raise ValueError(f"Unsupported review decision: {decision}")
        data = read_json(review_path, {}) or {}
        version = str(data.get("version") or review_path.parent.name)
        base = review_path.parent.parent
        if not version.startswith("v") or review_path.parent.parent != base:
            raise ValueError(f"Review version could not be resolved: {review_path}")
        now = datetime.now(timezone.utc)
        decision_id = f"{now.strftime('%Y%m%d_%H%M%S_%f')}_{uuid.uuid4().hex[:8]}"
        record = ReviewDecision(
            decision_id=decision_id,
            decision=decision,
            version=version,
            review_json=review_path,
            author=str(author or "unknown"),
            created_at=now.isoformat(),
            comment=str(comment or ""),
        )
        payload = record.to_dict(base)
        _atomic_json(base / "decisions" / f"{decision_id}.json", payload)

        approved = read_json(base / "approved.json", {}) or {}
        if decision == "APPROVED":
            _atomic_json(base / "approved.json", {**payload, "active": True})
        elif str(approved.get("version") or "") == version:
            _atomic_json(
                base / "approved.json",
                {
                    "schema": "smartpipeline.review_approval_pointer.v1",
                    "active": False,
                    "decision": decision,
                    "version": "",
                    "previous_version": version,
                    "decision_id": decision_id,
                    "author": record.author,
                    "created_at": record.created_at,
                    "comment": record.comment,
                },
            )
        return record

    @staticmethod
    def approved_review(base: str | Path) -> Path | None:
        base = Path(base)
        approved = read_json(base / "approved.json", {}) or {}
        if not approved.get("active") or approved.get("decision") != "APPROVED":
            return None
        path = base / str(approved.get("review_json") or approved.get("path") or "")
        return path if path.is_file() else None

    @staticmethod
    def approval(base: str | Path) -> dict[str, Any]:
        return read_json(Path(base) / "approved.json", {}) or {}


def _atomic_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
