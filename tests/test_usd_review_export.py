import json
from pathlib import Path
import pytest
from test_usd_handoff import service, asset, plan
from smartlib.apps.smart_composition.service import CompositionSession
from smartlib.apps.smart_composition.movie_export import prepare_export, verify_inputs


def setup_review(service, tmp_path, monkeypatch):
    svc, identity = service
    config = svc.config.config_dir / 'templates_base.yml'
    config.write_text(config.read_text() + '  resolution: [640, 480]\n')
    monkeypatch.setattr('smartlib.apps.smart_composition.movie_export.find_ffmpeg', lambda config: 'ffmpeg')
    bg, cam = asset(tmp_path), asset(tmp_path, 'camera')
    manifest = svc.publish(identity, plan(svc, identity, [
        dict(kind='assets', target='BG', source=str(bg)),
        dict(kind='camera', target='primary', source=str(cam))]))
    return CompositionSession(svc.shots, manifest), bg


def test_review_reservation_inputs_and_no_overwrite(service, tmp_path, monkeypatch):
    session, bg = setup_review(service, tmp_path, monkeypatch)
    first = json.loads(prepare_export(session, '/Shot/Camera/primary/cam').read_text())
    second = json.loads(prepare_export(session, '/Shot/Camera/primary/cam').read_text())
    assert first['files']['movie'].endswith('_usd_v001_t01.mov')
    assert second['files']['movie'].endswith('_usd_v002_t01.mov')
    assert '/review/anim/usd/mov/' in first['files']['movie']
    assert first['files']['report'].endswith('_usd_v001_t01_report.pdf')
    assert first['resolution'] == [640,480]
    assert first['frame_range'] == [1,2]
    assert first['composition']['sha256']
    assert first['products']
    assert not Path(first['files']['movie']).exists()
    verify_inputs(session.handoff, first)
    bg.write_text(bg.read_text() + '\n# changed\n')
    with pytest.raises(ValueError, match='changed'):
        verify_inputs(session.handoff, first)


def test_review_requires_saved_camera(service, tmp_path, monkeypatch):
    session, _ = setup_review(service, tmp_path, monkeypatch)
    with pytest.raises(ValueError, match='camera'):
        prepare_export(session, '')
    with pytest.raises(ValueError, match='camera'):
        prepare_export(session, '/Shot/Assets/BG')
