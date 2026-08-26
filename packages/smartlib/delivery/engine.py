from __future__ import annotations

import hashlib
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from smartlib.core.metadata import read_json, write_json

from .contact_sheet import generate_contact_sheet
from .models import DeliveryPlan, DeliveryResult, ValidationResult
from .validators import validate_constructed_package, validate_plan_sources


class DeliveryEngine:
    def construct(
        self,
        plan: DeliveryPlan,
        *,
        create_archive: bool = True,
        create_contact_sheet: bool = True,
        ffmpeg: str = "",
        after_effects_adapter=None,
    ) -> DeliveryResult:
        source_results = validate_plan_sources(plan)
        if _blocked(source_results):
            return self._write_failed_result(plan, source_results)
        plan.package_root.mkdir(parents=True, exist_ok=True)
        self._assert_deployment_available(plan)
        collisions = [
            plan.package_root / item.destination
            for item in plan.items
            if (plan.package_root / item.destination).exists()
        ]
        if collisions:
            preview = "\n".join(str(path) for path in collisions[:10])
            raise FileExistsError(f"Delivery would overwrite {len(collisions)} existing file(s):\n{preview}")
        metadata_root = plan.package_root / "_smart_delivery" / "jobs" / plan.job_id
        write_json(metadata_root / "delivery_plan.json", plan.to_dict())
        for item in plan.items:
            target = plan.package_root / item.destination
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item.source, target)
        results = list(source_results)
        predeployed: list[Path] = []
        if after_effects_adapter is not None:
            predeployed = self._deploy_client_tree(plan, exclude_kinds={"aep"})
            try:
                results.extend(after_effects_adapter.relink_and_validate(plan, metadata_root))
            except Exception as exc:
                results.append(ValidationResult("AE_DELIVERY_FAILED", "ERROR", str(exc)))
        results.extend(validate_constructed_package(plan))
        contact_sheet = None
        if create_contact_sheet:
            movies = [plan.package_root / row.destination for row in plan.items if row.kind == "review" or row.destination.suffix.lower() == ".mov"]
            contact_sheet, error = generate_contact_sheet(movies, metadata_root / "contact_sheet.jpg", ffmpeg=ffmpeg)
            results.append(
                ValidationResult(
                    "CONTACT_SHEET_CREATED" if contact_sheet else "CONTACT_SHEET_SKIPPED",
                    "PASS" if contact_sheet else "WARNING",
                    str(contact_sheet or error),
                )
            )
        manifest_path = metadata_root / "delivery_manifest.json"
        validation_path = metadata_root / "validation.json"
        write_json(validation_path, _validation_data(results))
        write_json(manifest_path, self._manifest(plan, results, contact_sheet))
        archive = None
        if create_archive and not _blocked(results):
            self._deploy_client_tree(plan, only_kinds={"aep"} if after_effects_adapter is not None else None)
            archive_root = Path(str(plan.metadata.get("archive_root") or plan.package_root / "_smart_delivery" / "archives"))
            delivery_id = str(plan.metadata.get("delivery_id") or plan.job_id)
            archive = archive_root / f"{delivery_id}.zip"
            archive.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as stream:
                for item in plan.items:
                    target = plan.package_root / item.destination
                    stream.write(target, item.destination.as_posix())
                for metadata in metadata_root.rglob("*"):
                    if metadata.is_file():
                        stream.write(metadata, metadata.relative_to(plan.package_root).as_posix())
            self._update_delivery_index(plan, archive)
        elif _blocked(results) and predeployed:
            self._cleanup_deployed(predeployed)
        return DeliveryResult(plan.package_root, manifest_path, validation_path, archive, contact_sheet, tuple(results))

    @staticmethod
    def _assert_deployment_available(plan: DeliveryPlan) -> None:
        deployment_value = str(plan.metadata.get("deployment_root") or "").strip()
        if not deployment_value:
            return
        deployment_root = Path(deployment_value)
        collisions = [
            deployment_root / item.destination
            for item in plan.items
            if (deployment_root / item.destination).exists()
        ]
        if collisions:
            preview = "\n".join(str(path) for path in collisions[:10])
            raise FileExistsError(f"Client Tree deployment would overwrite {len(collisions)} existing file(s):\n{preview}")

    @staticmethod
    def _deploy_client_tree(
        plan: DeliveryPlan,
        *,
        exclude_kinds: set[str] | None = None,
        only_kinds: set[str] | None = None,
    ) -> list[Path]:
        deployment_value = str(plan.metadata.get("deployment_root") or "").strip()
        if not deployment_value:
            return []
        deployment_root = Path(deployment_value)
        written = []
        for item in plan.items:
            if exclude_kinds and item.kind in exclude_kinds:
                continue
            if only_kinds and item.kind not in only_kinds:
                continue
            source = plan.package_root / item.destination
            target = deployment_root / item.destination
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            written.append(target)
        return written

    @staticmethod
    def _cleanup_deployed(paths: list[Path]) -> None:
        for path in reversed(paths):
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass

    @staticmethod
    def _update_delivery_index(plan: DeliveryPlan, archive: Path) -> None:
        delivery_root = archive.parents[3] if len(archive.parents) >= 4 else archive.parent
        index_path = delivery_root / "delivery_index.json"
        data = read_json(index_path, {}) or {}
        rows = list(data.get("deliveries") or [])
        entity_type = str(plan.metadata.get("entity_type") or "shot")
        entity_id = str(plan.metadata.get("entity_id") or getattr(plan.context, "code", ""))
        row = {
                "delivery_id": str(plan.metadata.get("delivery_id") or plan.job_id),
                "job_id": plan.job_id,
                "party_type": "client",
                "party": str(plan.metadata.get("client") or ""),
                "delivery_batch": str(plan.metadata.get("delivery_batch") or ""),
                "archive": archive.relative_to(delivery_root).as_posix(),
                "entity_type": entity_type,
                "entity_id": entity_id,
                "status": "READY",
            }
        if entity_type == "shot":
            row["shots"] = [entity_id]
            row["review_version"] = f"v{plan.context.version:03d}"
        else:
            row["assets"] = [entity_id]
            row["asset_version"] = f"v{plan.context.version:03d}"
        rows.append(row)
        write_json(index_path, {"schema": "smart_delivery_index/v1", "deliveries": rows})

    def _write_failed_result(self, plan: DeliveryPlan, results: list[ValidationResult]) -> DeliveryResult:
        report_root = plan.package_root / "_smart_delivery" / "jobs" / plan.job_id
        validation_path = report_root / "validation.json"
        manifest_path = report_root / "delivery_plan.json"
        write_json(manifest_path, plan.to_dict())
        write_json(validation_path, _validation_data(results))
        return DeliveryResult(plan.package_root, manifest_path, validation_path, None, None, tuple(results))

    @staticmethod
    def _manifest(plan: DeliveryPlan, results: list[ValidationResult], contact_sheet: Path | None) -> dict:
        items = []
        for item in plan.items:
            target = plan.package_root / item.destination
            row = item.to_dict()
            row.update({"size": target.stat().st_size, "sha256": _sha256(target)})
            items.append(row)
        return {
            "schema": "smart_delivery_manifest/v1",
            "job_id": plan.job_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "profile": {"id": plan.profile_id, "version": plan.profile_version},
            "context": plan.to_dict()["context"],
            "metadata": plan.metadata,
            "items": items,
            "validation": _validation_data(results),
            "contact_sheet": contact_sheet.relative_to(plan.package_root).as_posix() if contact_sheet else "",
        }


def _blocked(results: list[ValidationResult]) -> bool:
    return any(row.severity == "ERROR" for row in results)


def _validation_data(results: list[ValidationResult]) -> dict:
    return {
        "schema": "smart_delivery_validation/v1",
        "blocked": _blocked(results),
        "counts": {state: sum(row.severity == state for row in results) for state in ("PASS", "WARNING", "ERROR")},
        "results": [row.to_dict() for row in results],
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
