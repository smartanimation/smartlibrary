import time
from pathlib import Path

import pytest
from test_usd_handoff import service
from test_publish_queue import queue
from smartlib.core.metadata import read_json
from smartlib.dcc.maya.publish_scene_input import capture_saved_scene, validate_scene_input
from smartlib.apps.review_build_manager.publish_runner import acquire_lock, atomic_json


class Commands:
    def __init__(self, path, modified=False, answer='Save and Submit', dependencies=()):
        self.path, self.modified, self.answer = str(path), modified, answer
        self.dependencies = list(dependencies)
        self.saved = False
    def file(self, **kwargs):
        if kwargs.get('sceneName'):
            return self.path
        if kwargs.get('modified'):
            return self.modified
        if kwargs.get('save'):
            self.modified = False
            self.saved = True
        if kwargs.get('list'):
            return [self.path] + self.dependencies
    def confirmDialog(self, **kwargs):
        return self.answer
    def workspace(self, **kwargs):
        return str(Path(self.path).parent)


def test_save_gate_and_dependency_pinning(service, tmp_path):
    svc, identity = service
    source = tmp_path / 'work.ma'
    dep = tmp_path / 'rig.mb'
    source.write_text('scene')
    dep.write_text('rig')
    cmds = Commands(source, modified=True, dependencies=[str(dep)])
    data = capture_saved_scene(svc, identity, cmds)
    assert cmds.saved
    assert Path(data['scene']['path']).read_text() == 'scene'
    source.write_text('later work')
    validate_scene_input(data)  # Editing the original work scene is safe.
    dep.write_text('changed')
    with pytest.raises(ValueError, match='changed'):
        validate_scene_input(data)


def test_cancel_or_unnamed_scene_never_submits(service, tmp_path):
    svc, identity = service
    with pytest.raises(ValueError, match='Save'):
        capture_saved_scene(svc, identity, Commands(''))
    source = tmp_path / 'work.ma'
    source.write_text('scene')
    cmds = Commands(source, modified=True, answer='Cancel')
    with pytest.raises(ValueError, match='cancelled'):
        capture_saved_scene(svc, identity, cmds)
    assert not cmds.saved


def test_os_lease_is_exclusive_and_released(tmp_path):
    first = acquire_lock(tmp_path / 'execution.lock')
    assert first
    assert acquire_lock(tmp_path / 'execution.lock') is None
    first.close()
    second = acquire_lock(tmp_path / 'execution.lock')
    assert second
    second.close()


def test_directory_lease_reclaims_dead_owner(tmp_path, monkeypatch):
    import smartlib.apps.review_build_manager.publish_runner as module

    lock_path = tmp_path / "execution.lock"
    lock_dir = tmp_path / "execution.lock.d"
    lock_dir.mkdir()
    atomic_json(lock_dir / "owner.json", {"pid": 999999, "token": "dead"})
    monkeypatch.setattr(module, "process_alive", lambda _pid: False)

    lease = module.acquire_lock(lock_path)

    assert lease
    assert (lock_dir / "owner.json").is_file()
    lease.close()
    assert not lock_dir.exists()


def test_recover_local_lease_only_when_owner_is_idle(queue):
    from types import SimpleNamespace
    from smartlib.apps.review_build_manager.detached_publish import DetachedPublishQueue

    q, _identity, app = queue
    detached = DetachedPublishQueue(q.service.shots, app)
    detached.timer.stop()
    owner = SimpleNamespace(active_job=None, worker_process=None)
    assert detached.acquire(owner)
    assert detached.recover_local_lease(owner)
    assert detached.lease is None
    assert detached._lease_handle is None


def test_recover_local_lease_rejects_active_owner(queue):
    from types import SimpleNamespace
    from smartlib.apps.review_build_manager.detached_publish import DetachedPublishQueue

    q, _identity, app = queue
    detached = DetachedPublishQueue(q.service.shots, app)
    detached.timer.stop()
    owner = SimpleNamespace(active_job={"id": "active"}, worker_process=None)
    assert detached.acquire(owner)
    with pytest.raises(RuntimeError, match="still active"):
        detached.recover_local_lease(owner)
    assert detached.lease is owner
    detached.release(owner)


def test_recover_local_lease_rejects_running_worker(queue):
    from types import SimpleNamespace
    from smartlib.apps.review_build_manager.detached_publish import DetachedPublishQueue

    q, _identity, app = queue
    detached = DetachedPublishQueue(q.service.shots, app)
    detached.timer.stop()
    process = SimpleNamespace(state=lambda: 2)
    owner = SimpleNamespace(active_job=None, worker_process=process)
    assert detached.acquire(owner)
    with pytest.raises(RuntimeError, match="still running"):
        detached.recover_local_lease(owner)
    assert detached.lease is owner
    detached.release(owner)


@pytest.mark.parametrize('old_alive', [False, True])
def test_recovery_ignores_unrelated_older_jobs(queue, tmp_path, monkeypatch, old_alive):
    from types import SimpleNamespace
    import smartlib.apps.review_build_manager.detached_publish as module
    from smartlib.apps.review_build_manager.detached_publish import DetachedPublishQueue

    q, _identity, app = queue
    detached = DetachedPublishQueue(q.service.shots, app)
    detached.timer.stop()
    detached.registry = tmp_path / "registry"
    detached.registry.mkdir()
    alive = {101, 102} if old_alive else {102}
    launched = []
    monkeypatch.setattr(module, "process_alive", lambda pid: int(pid) in alive)
    monkeypatch.setattr(module, "terminate_process", lambda pid: alive.discard(int(pid)))
    monkeypatch.setattr(
        detached,
        "launch_detached",
        lambda record, path, digest: (
            launched.append(record["id"]) or SimpleNamespace(pid=200 + len(launched))
        ),
    )
    for index, job_id in enumerate(("PUB-old", "PUB-new"), start=1):
        job_dir = tmp_path / job_id
        job_dir.mkdir()
        record = {
            "id": job_id,
            "config": str(detached.service.config.config_dir),
            "submitted_at": float(index),
            "status_file": str(job_dir / "status.json"),
            "accepted_file": str(job_dir / "accepted.json"),
            "execution_file": str(job_dir / "execution.json"),
            "launched_file": str(job_dir / "launched.json"),
            "task": "Assets USD",
        }
        atomic_json(detached.registry / f"{job_id}.json", record)
        status = (
            {"state": "FAILED", "task": "Runner unavailable", "progress": 0}
            if job_id == "PUB-old"
            else {"state": "QUEUED", "task": "Assets USD", "progress": 0}
        )
        atomic_json(record["status_file"], status)
        atomic_json(record["accepted_file"], {"pid": 100 + index})

    result = detached.recover_execution_queue(
        SimpleNamespace(active_job=None, worker_process=None), "PUB-new"
    )

    assert result == ("runners", 1)
    assert launched == ["PUB-new"]
    assert alive == ({101} if old_alive else set())
    old_status = read_json(tmp_path / "PUB-old" / "status.json", {})
    assert old_status["state"] == "FAILED"


def test_detached_receipt_reconnect_and_terminal_signal_once(queue, monkeypatch):
    from types import SimpleNamespace
    from smartlib.apps.review_build_manager.detached_publish import DetachedPublishQueue
    q, identity, app = queue
    first = DetachedPublishQueue(q.service.shots, app)
    monkeypatch.setattr(first, 'launch_detached', lambda *args: SimpleNamespace(pid=12345))
    plan = first.service.plan(identity, [], frame_range=[1, 2], replace_assets=True)
    key = first.submit(identity, kind='assets_usd', plan=plan)
    atomic_json(first.jobs[key]['status_file'], dict(state='COMPLETE', task='Complete', progress=100, message='test'))
    first.timer.stop()
    second = DetachedPublishQueue(q.service.shots, app)
    assert second.jobs[key]['state'] == 'COMPLETE'
    events = []
    second.changed.connect(events.append)
    second.poll()
    second.poll()
    assert events == []
    second.timer.stop()
