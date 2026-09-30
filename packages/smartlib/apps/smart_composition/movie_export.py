"""Frozen inputs and resolver-owned destinations for USD working review exports."""
from datetime import datetime, timezone
from pathlib import Path
import json
import math
import re

from smartlib.apps.shot_manager.usd_handoff import UsdHandoffService
from smartlib.apps.shot_manager.animation_publish import file_hash
from smartlib.core.metadata import read_json, write_json
from smartlib.review.playblast_package import find_ffmpeg


def prepare_export(session, camera, department='anim'):
    service = session.handoff
    data = service.load_handoff(session.source)
    if not camera:
        raise ValueError('Select a published camera before creating a review movie.')
    resolution = (service.config.base.get('anchors') or {}).get('resolution')
    if not isinstance(resolution, (list, tuple)) or len(resolution) != 2:
        raise ValueError('Set the project resolution before exporting.')
    width, height = map(int, resolution)
    if min(width, height) < 2 or width % 2 or height % 2:
        raise ValueError('Movie resolution must contain positive even dimensions.')
    start, end = data['frame_range']
    if any(not float(v).is_integer() for v in (start, end)) or end < start:
        raise ValueError('USD review requires an integer frame range.')
    fps = float(data['fps'])
    if not math.isfinite(fps) or fps <= 0:
        raise ValueError('Invalid FPS')
    ffmpeg = find_ffmpeg(service.config)
    if not ffmpeg:
        raise ValueError('FFmpeg is not configured for this project.')
    from pxr import Usd, UsdGeom
    stage = Usd.Stage.Open(data['entrypoint']['path'])
    prim = stage.GetPrimAtPath(camera)
    if not prim or not prim.IsA(UsdGeom.Camera):
        raise ValueError('The selected camera is not present in the saved composition.')
    identity = session.identity
    directory = service.paths.shot_review_movie_dir(identity.episode, identity.sequence,
        identity.shot, department, review_kind='usd')
    directory.mkdir(parents=True, exist_ok=True)
    tokens = [service.config.project_name, identity.episode, identity.sequence, identity.shot, 'usd']
    prefix = '_'.join(service.paths.pipeline_token(t) for t in tokens)
    pattern = re.compile(re.escape(prefix) + r'_v(\d+)_t\d+')
    version = max((int(m.group(1)) for p in directory.iterdir()
                   if (m := pattern.match(p.name))), default=0) + 1
    while True:
        stem = f'{prefix}_v{version:03d}_t01'
        lock = service.paths.artifact_file(directory, stem + '.lock')
        try:
            with lock.open('x', encoding='utf8') as stream:
                stream.write(datetime.now(timezone.utc).isoformat())
            break
        except FileExistsError:
            version += 1
    try:
        _, work = service._reserve(service.paths.usd_handoff_build_dir(*service._identity(identity)))
        files = {key: service.paths.artifact_file(directory, stem + suffix).as_posix()
                 for key, suffix in [('movie', '.mov'), ('report', '_report.pdf'), ('receipt', '.json')]}
        job = dict(schema='smartpipeline.usd_review_job.v1', config=str(service.config.config_dir.resolve()),
            composition=service.pin(session.source), snapshot=data, camera=camera,
            resolution=[width, height], frame_range=[int(start), int(end)], fps=fps,
            ffmpeg=ffmpeg, files=files, work=work.as_posix(), lock=lock.as_posix(),
            cancel=service.paths.artifact_file(work, 'cancel').as_posix(),
            created_at=datetime.now(timezone.utc).isoformat(),
            products=[dict(manifest=ref, data=service.load_handoff(ref['path'])) for ref in data['products']])
        path = service.paths.artifact_file(work, 'review_job.json')
        write_json(path, job)
        return path
    except Exception:
        lock.unlink(missing_ok=True)
        raise


def verify_inputs(service, job):
    service.check(job['composition'])
    service.load_handoff(job['composition']['path'])


def make_receipt(job):
    return dict(schema='smartpipeline.usd_working_review.v1', status='complete', approval='not_reviewed',
        composition=job['composition'], camera=job['camera'], resolution=job['resolution'],
        frame_range=job['frame_range'], fps=job['fps'], sections=job['snapshot'].get('sections', {}),
        products=job['snapshot']['products'], dependencies=job['snapshot'].get('dependencies', []),
        created_at=job['created_at'],
        files={key: dict(path=job['files'][key], sha256=file_hash(Path(job['files'][key])))
               for key in ('movie', 'report')})
