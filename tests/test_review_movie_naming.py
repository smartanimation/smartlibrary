from pathlib import Path

import pytest

from smartlib.core.path_resolver import ProjectPaths
from smartlib.core.metadata import read_json
from smartlib.review.workflow import ReviewWorkflowService


@pytest.mark.parametrize('task,extension', [('preComp', '.mov'), ('main', '.mp4')])
def test_named_submission_and_manifest(tmp_path, task, extension):
    paths = ProjectPaths(tmp_path / 'ELCD', project_name='ELCD')
    workflow = ReviewWorkflowService(tmp_path / 'shot', tmp_path / 'work',
                                    paths=paths, identity=('ep02', 's027', 'c001'))
    movie = tmp_path / ('review' + extension)
    movie.write_bytes(b'movie')
    report = tmp_path / 'report.pdf'
    report.write_bytes(b'%PDF-1.4\n%%EOF')
    destination = workflow.submit_review(
        department='anim', delivery_profile='internal', movie=movie, report=report,
        review_data={'task': task}, source_manifest={}, version='v004',
    )
    expected = f'ELCD_ep02_s027_c001_anim_{task}_v004{extension}'
    assert read_json(destination / 'review.json')['movie'] == expected
    assert (destination / expected).read_bytes() == b'movie'
    assert not (destination / ('review' + extension)).exists()


def test_resolver_rejects_path_tokens(tmp_path):
    paths = ProjectPaths(tmp_path / 'ELCD')
    with pytest.raises(ValueError):
        paths.shot_review_movie_filename('ep02', 's027', 'c001', 'anim', '../oops', 'v004')


def test_worker_reads_receipt_instead_of_fixed_movie_glob():
    worker = Path(__file__).parents[1] / 'packages/smartlib/apps/review_build_manager/worker.py'
    source = worker.read_text(encoding='utf-8-sig')
    assert 'submitted_dir.glob("review.*")' not in source
    assert 'submitted_metadata["movie"]' in source
