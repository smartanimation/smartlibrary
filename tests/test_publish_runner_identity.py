import os

import pytest

from smartlib.apps.review_build_manager import publish_runner as runner


@pytest.mark.parametrize('state', ['COMPLETE', 'FAILED', 'CANCELLED'])
def test_terminal_predecessor_ignores_reused_pid(tmp_path, monkeypatch, state):
    status = tmp_path / 'status.json'
    runner.atomic_json(status, {'state': state})
    monkeypatch.setattr(runner, 'receipt_alive', lambda _: pytest.fail('Terminal job must be skipped'))
    assert not runner.predecessor_blocks({'status_file': str(status)})


@pytest.mark.parametrize('receipt', [
    {'pid': 31992, 'started_at': 100},
    {'pid': 31992, 'process_created_at': 90},
])
def test_reused_pid_does_not_match_receipt(monkeypatch, receipt):
    monkeypatch.setattr(runner, 'process_alive', lambda _: True)
    monkeypatch.setattr(runner, 'process_created_at', lambda _: 200)
    assert not runner.receipt_alive(receipt)


def test_live_orphan_worker_still_blocks(tmp_path, monkeypatch):
    status, accepted, execution = [tmp_path / name for name in ('status.json', 'accepted.json', 'execution.json')]
    runner.atomic_json(status, {'state': 'BUILDING'})
    runner.atomic_json(accepted, {'pid': 1, 'process_created_at': 10})
    runner.atomic_json(execution, {'pid': 2, 'process_created_at': 20})
    monkeypatch.setattr(runner, 'process_alive', lambda pid: pid == 2)
    monkeypatch.setattr(runner, 'process_created_at', lambda _: 20)
    assert runner.predecessor_blocks(dict(status_file=str(status), accepted_file=str(accepted),
                                         execution_file=str(execution), submitted_at=0))


@pytest.mark.skipif(os.name != 'nt', reason='Windows process identity')
def test_windows_current_process_receipt():
    receipt = runner.process_receipt(os.getpid())
    assert receipt['process_created_at'] is not None
    assert runner.receipt_alive(receipt)
    receipt['process_created_at'] -= 10
    assert not runner.receipt_alive(receipt)
