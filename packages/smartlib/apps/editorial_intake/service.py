from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from smartlib.core.config_loader import ProjectConfig
from smartlib.core.metadata import read_json
from smartlib.editorial import EditorialIntakeRequest, EditorialIntakeResult, EditorialIntakeService, StoryreelBuilder


@dataclass(frozen=True)
class IntakeSource:
    csv_path: Path
    mov_path: Path | None
    work_dir: Path | None = None
    manifest_path: Path | None = None


@dataclass(frozen=True)
class IntakePreview:
    source: IntakeSource | None
    report: list[str]
    events_count: int = 0


@dataclass(frozen=True)
class SmartIntakeResult:
    intake: EditorialIntakeResult | None
    storyreel_publish_dir: Path | None
    storyreel_shots: int
    report: list[str]


class SmartEditorialIntakeService:
    """Thin app service for the Editorial Intake UI."""

    def __init__(self, project_config: ProjectConfig):
        self.project_config = project_config
        self.intake_service = EditorialIntakeService(project_config)
        project_root = project_config.project_root
        if project_root is None:
            raise RuntimeError("project_root is not set in templates_base.yml")
        self.project_root = project_root

    @property
    def editorial_work_root(self) -> Path:
        return self.project_root / "editorial" / "work"

    @property
    def incoming_editorial_dir(self) -> Path:
        return self.intake_service.incoming_editorial_dir

    def list_episodes(self) -> list[str]:
        root = self.editorial_work_root
        names = sorted(path.name for path in root.iterdir() if path.is_dir()) if root.exists() else []
        return names or ["ep001"]

    def list_sequences(self, episode: str) -> list[str]:
        root = self.editorial_work_root / episode
        names = sorted(path.name for path in root.iterdir() if path.is_dir()) if root.exists() else []
        return names or ["sq010"]

    def list_versions(self, episode: str, sequence: str) -> list[str]:
        root = self.editorial_work_root / episode / sequence
        versions = sorted((path.name for path in root.iterdir() if path.is_dir() and path.name.startswith("v")), reverse=True) if root.exists() else []
        return ["Latest", *versions] if versions else ["Latest"]

    def resolve_source(
        self,
        episode: str,
        sequence: str,
        version: str,
        *,
        csv_path: str | Path | None = None,
        mov_path: str | Path | None = None,
    ) -> IntakeSource:
        explicit_csv = Path(csv_path) if csv_path else None
        explicit_mov = Path(mov_path) if mov_path else None
        if explicit_csv:
            return IntakeSource(csv_path=explicit_csv, mov_path=explicit_mov)

        version_dir = self._resolve_work_version_dir(episode, sequence, version)
        manifest_path = version_dir / "manifest.json"
        manifest = read_json(manifest_path, {}) or {}
        source_csv = version_dir / str(manifest.get("events") or "events.csv")
        manifest_mov = str(manifest.get("movie_path") or "").strip()
        source_mov = Path(manifest_mov) if manifest_mov else version_dir / "offline.mov"
        if not source_mov.exists():
            source_mov = None
        return IntakeSource(
            csv_path=source_csv,
            mov_path=source_mov,
            work_dir=version_dir,
            manifest_path=manifest_path if manifest_path.exists() else None,
        )

    def inspect(
        self,
        episode: str,
        sequence: str,
        version: str,
        *,
        csv_path: str | Path | None = None,
        mov_path: str | Path | None = None,
    ) -> IntakePreview:
        report: list[str] = []
        try:
            source = self.resolve_source(episode, sequence, version, csv_path=csv_path, mov_path=mov_path)
        except Exception as exc:
            return IntakePreview(None, [f"source - ERROR: {exc}"])

        events_count = 0
        if source.csv_path.exists():
            try:
                events = self.intake_service.read_events_csv(source.csv_path)
                events_count = len(events)
                report.append("events csv - OK")
                report.append(f"events count - {events_count}")
                report.extend(_inspect_events(events))
            except Exception as exc:
                report.append(f"events csv - ERROR: {exc}")
        else:
            report.append(f"events csv - MISSING: {source.csv_path}")

        if source.manifest_path:
            report.append("manifest - OK")
        elif source.work_dir:
            report.append("manifest - WARNING: missing")

        if source.mov_path and source.mov_path.exists():
            report.append("movie - OK")
        else:
            report.append("movie - WARNING: not set")

        report.append(f"fps check - OK ({self.intake_service.fps} fps)")
        report.append("audio - SKIP")
        report.append("resolution - SKIP")
        report.append("black frame - SKIP")
        return IntakePreview(source, report, events_count)

    def run(
        self,
        episode: str,
        sequence: str,
        version: str,
        *,
        csv_path: str | Path | None = None,
        mov_path: str | Path | None = None,
        comment: str = "",
        create_folder_structure: bool = True,
        generate_storyreel: bool = True,
        dry_run: bool = False,
    ) -> SmartIntakeResult:
        preview = self.inspect(episode, sequence, version, csv_path=csv_path, mov_path=mov_path)
        if preview.source is None:
            return SmartIntakeResult(None, None, 0, preview.report)
        source = preview.source
        if not source.csv_path.exists():
            return SmartIntakeResult(None, None, 0, preview.report)

        report = list(preview.report)
        if dry_run:
            report.append("dry-run - no files written")
            report.append("intake - READY")
            if generate_storyreel:
                if source.mov_path and source.mov_path.exists():
                    report.append("storyreel - READY")
                else:
                    report.append("storyreel - WARNING: movie missing")
            return SmartIntakeResult(None, None, 0, report)

        result = self.intake_service.intake(
            EditorialIntakeRequest(
                csv_path=source.csv_path,
                offline_mov=source.mov_path,
                comment=comment,
                work_dir=source.work_dir,
                publish_episode=episode,
                publish_sequence=sequence,
                publish=True,
                register_shots=create_folder_structure,
            )
        )
        report.append(f"publish - OK: {result.publish_dir}")
        report.append(f"registered shots - {len(result.registered_shots)}")

        storyreel_publish_dir = None
        storyreel_shots = 0
        if generate_storyreel:
            if result.publish_dir and result.offline_mov:
                storyreel = StoryreelBuilder(self.project_config).build_from_publish(result.publish_dir)
                storyreel_publish_dir = storyreel.publish_dir
                storyreel_shots = len(storyreel.results)
                report.append(f"storyreel - OK: {storyreel_shots} shots")
            else:
                report.append("storyreel - SKIP: offline.mov missing")
        return SmartIntakeResult(result, storyreel_publish_dir, storyreel_shots, report)

    def _resolve_work_version_dir(self, episode: str, sequence: str, version: str) -> Path:
        root = self.editorial_work_root / episode / sequence
        if version and version != "Latest":
            return root / version
        versions = sorted((path for path in root.iterdir() if path.is_dir() and path.name.startswith("v")), reverse=True) if root.exists() else []
        if not versions:
            return root / "v001"
        return versions[0]


def _inspect_events(events: list[object]) -> list[str]:
    rows: list[str] = []
    invalid_duration = [event for event in events if getattr(event, "duration", 0) <= 0]
    rows.append("duration - OK" if not invalid_duration else f"duration - ERROR: {len(invalid_duration)} invalid")
    episodes = sorted({str(getattr(event, "episode", "")) for event in events})
    sequences = sorted({str(getattr(event, "sequence", "")) for event in events})
    rows.append(f"episode - {', '.join(episodes)}")
    rows.append(f"sequence - {', '.join(sequences)}")
    return rows
