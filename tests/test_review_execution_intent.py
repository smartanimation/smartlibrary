from types import SimpleNamespace
import ast
from pathlib import Path

import pytest

from smartlib.apps.review_build_manager.window import ReviewBuildManagerWindow as Window


def harness(tmp_path):
    text = lambda value: SimpleNamespace(currentText=lambda: value)
    return SimpleNamespace(
        dcc_combo=text('Maya'), scope_combo=text('Sequence'),
        generate_review_check=SimpleNamespace(isChecked=lambda: True),
        pending_jobs=[], queue_jobs=[], active_job=None, job_counter=0,
        _review_submission_profiles={('ep', 'sq', ''): {'planned_snapshot': {'stale': True}}},
        _build_plan=lambda _: SimpleNamespace(buildable=True, resolved_mode='TEST', department='layout', task='main'),
        service=SimpleNamespace(next_sequence_construct_version=lambda *a: 'v001',
                                shots=SimpleNamespace(sequence_build_root=lambda *a: tmp_path)),
        queue_table=SimpleNamespace(rowCount=lambda: 0),
        _sequence_options=lambda _: {}, _stage_input_overrides=lambda _: {},
        _open_after_build_identity=None, _append_queue_row=lambda _: None,
        _start_next_job=lambda: None, _update_build_buttons=lambda: None,
    )


def test_build_ignores_stale_submit_options_and_ui_flag(tmp_path):
    window = harness(tmp_path)
    Window._enqueue_builds(window, [('ep', 'sq', 'c001')])
    job = window.queue_jobs[0]
    assert job['generate_review'] is False
    assert job['execution_kind'] == 'scene_build'
    assert 'planned_snapshot' not in job
    assert job['task'] == 'Scene Build / Queued'


def test_submit_intent_is_captured_and_drafts_are_copied(tmp_path):
    window = harness(tmp_path)
    window.generate_review_check.isChecked = lambda: False
    options = {('ep', 'sq', ''): {'planned_snapshot': {'inputs': ['original']}}}
    Window._enqueue_builds(window, [('ep', 'sq', 'c001')], generate_review=True,
                           submission_profiles=options)
    options[('ep', 'sq', '')]['planned_snapshot']['inputs'].clear()
    job = window.queue_jobs[0]
    assert job['generate_review'] is True
    assert job['execution_kind'] == 'review_submit'
    assert job['planned_snapshot']['inputs'] == ['original']
    assert job['task'] == 'Review Submit / Queued'


@pytest.mark.parametrize('requested,exists', [(False, False), (True, False), (True, True)])
def test_worker_completion_requires_movie_for_review_only(tmp_path, requested, exists):
    path = Path(__file__).parents[1] / 'packages/smartlib/apps/review_build_manager/worker.py'
    module = ast.parse(path.read_text(encoding='utf-8-sig'))
    function = next(n for n in module.body if isinstance(n, ast.FunctionDef) and n.name == '_run_scene_construction')
    index = next(i for i, n in enumerate(function.body) if isinstance(n, ast.If)
                 and 'Review Submit did not produce a movie' in ast.unparse(n))
    code = compile(ast.Module(body=function.body[index:index + 2], type_ignores=[]), str(path), 'exec')
    movie = tmp_path / 'check.mov'
    if exists:
        movie.write_bytes(b'movie')
    statuses = []
    context = dict(review_requested=requested, review_movie=movie, Path=Path,
                   status_path=tmp_path / 'status.json', scene_path=tmp_path / 'scene.ma',
                   _write_status=lambda path, **data: statuses.append(data))
    if requested and not exists:
        with pytest.raises(RuntimeError, match='scene Build alone'):
            exec(code, context)
        assert not statuses
    else:
        exec(code, context)
        assert statuses[0]['task'] == ('Review Submit Complete' if requested else 'Scene Build Complete')
