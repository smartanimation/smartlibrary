from pathlib import Path
from types import SimpleNamespace

from smartlib.apps.review_build_manager import worker
from smartlib.core.config_loader import ProjectConfig
from smartlib.review import ae


def test_review_relink_creates_queue_from_configured_final_comp(
    tmp_path: Path, monkeypatch
) -> None:
    published = tmp_path / "published.aep"
    published.write_bytes(b"fake aep")
    working = tmp_path / "working.aep"
    executable = tmp_path / "AfterFX.exe"
    executable.write_bytes(b"")
    monkeypatch.setattr(ae, "find_after_effects_executable", lambda _config: str(executable))

    def fake_popen(*_args, **_kwargs):
        working.with_suffix(".relinked").write_text("ok", encoding="utf-8")
        return SimpleNamespace()

    monkeypatch.setattr(worker.subprocess, "Popen", fake_popen)
    config = SimpleNamespace(
        load=lambda _name: {
            "final_comp": {"name": "final", "fallback_names": "render_final,anim_final"},
            "render_queue": {},
        }
    )

    result = worker._prepare_review_project_copy(
        published, working, {}, config
    )

    script = working.with_suffix(".relink.jsx").read_text(encoding="utf-8")
    assert result == working
    assert 'var finalCompNames = ["final", "render_final", "anim_final"]' in script
    assert "projectItem instanceof CompItem" in script
    assert "queue.items.add(outputComp)" in script
    assert "queue.item(q).comp === outputComp" in script
    assert "currentName === normalizedName(requested[n])" in script
    assert "Published project has no Render Queue item" not in script


def test_after_effects_resolver_prefers_explicit_review_build_registration(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "config" / "SHOW"
    config_dir.mkdir(parents=True)
    ae_2024 = tmp_path / "Adobe After Effects 2024" / "AfterFX.exe"
    ae_2025 = tmp_path / "Adobe After Effects 2025" / "AfterFX.exe"
    ae_2024.parent.mkdir()
    ae_2025.parent.mkdir()
    ae_2024.write_bytes(b"")
    ae_2025.write_bytes(b"")
    (config_dir / "software_AfterEffects2024.yml").write_text(
        f"path: '{ae_2024.as_posix()}'\n", encoding="utf-8"
    )
    (config_dir / "software_AfterEffects2025.yml").write_text(
        f"path: '{ae_2025.as_posix()}'\n", encoding="utf-8"
    )
    (config_dir / "templates_base.yml").write_text(
        "enabled_softwares:\n"
        "  - AfterEffects2025\n"
        "review_build:\n"
        "  after_effects_software: AfterEffects2024\n",
        encoding="utf-8",
    )

    resolved = ae.find_after_effects_executable(ProjectConfig(config_dir))

    assert Path(resolved) == ae_2024
