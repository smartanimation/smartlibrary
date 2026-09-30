"""Durable Publish client: submit detached supervisors and reconnect to receipts."""
import hashlib
import os
from pathlib import Path
import signal
import subprocess
import time

from smartlib.core.metadata import read_json
from .publish_queue import PublishQueue
from .publish_runner import (acquire_lock, atomic_json, process_alive,
                             process_receipt, receipt_alive, TERMINAL_STATES)


def terminate_process(pid):
    """Terminate a detached supervisor after recovery has proven no worker started."""
    pid = int(pid)
    if pid == os.getpid():
        raise RuntimeError("Refusing to terminate the current process.")
    if os.name != 'nt':
        os.kill(pid, signal.SIGTERM)
        return
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel.OpenProcess(0x0001 | 0x00100000, False, pid)
    if not handle:
        raise RuntimeError(f"Could not open queued runner PID {pid} for recovery.")
    try:
        if not kernel.TerminateProcess(handle, 1):
            raise RuntimeError(f"Could not stop queued runner PID {pid}.")
    finally:
        kernel.CloseHandle(handle)


class Elapsed:
    def __init__(self, started, finished=None):
        self.started, self.finished = started, finished
    def isValid(self):
        return True
    def elapsed(self):
        return int(((self.finished or time.time()) - self.started) * 1000)


class DetachedPublishQueue(PublishQueue):
    def __init__(self, shots, parent=None):
        super().__init__(shots, parent)
        self.registry = self.service.paths.publish_job_registry_dir()
        self.registry.mkdir(parents=True, exist_ok=True)
        self.lease_file = self.service.paths.artifact_file(self.registry, 'execution.lock')
        self._lease_handle = None
        self.poll()
        self.timer.start()

    def start_next(self):
        # Independent supervisors own execution, not this QObject.
        pass

    def acquire(self, owner):
        if self.lease is not None:
            return False
        self._lease_handle = acquire_lock(self.lease_file)
        if self._lease_handle is None:
            return False
        self.lease = owner
        return True

    def release(self, owner):
        if self.lease == owner:
            if self._lease_handle:
                self._lease_handle.close()
            self._lease_handle = None
            super().release(owner)

    def recover_local_lease(self, owner):
        """Release an orphaned lease owned by an idle in-process client.

        Detached runners own their leases in another process and cannot be
        unlocked here.  This deliberately handles only the failure mode where
        Review Build Manager acquired the shared lease for a normal Build, but
        no longer has an active Job to release it on the usual completion path.
        """
        if self.lease is None or self._lease_handle is None:
            return False
        if self.lease is not owner:
            raise RuntimeError("The execution lock is owned by another active client.")
        if getattr(owner, "active_job", None) is not None:
            raise RuntimeError("A Build Job is still active; its execution lock cannot be recovered.")
        process = getattr(owner, "worker_process", None)
        if process is not None:
            try:
                process_state = process.state()
                state_value = getattr(process_state, "value", process_state)
                if int(state_value) != 0:
                    raise RuntimeError("A Build worker is still running; its execution lock cannot be recovered.")
            except (AttributeError, TypeError, ValueError):
                raise RuntimeError("A Build worker is still attached; its execution lock cannot be recovered.")
        self.release(owner)
        return True

    def recover_execution_queue(self, owner, selected_job_id):
        """Recover an idle local lease or restart stalled queued supervisors."""
        if self.recover_local_lease(owner):
            return "local", 1

        selected_path = self.service.paths.artifact_file(
            self.registry, str(selected_job_id) + '.json'
        )
        selected = read_json(selected_path, {})
        if not selected:
            raise RuntimeError("The selected Publish registration was not found.")
        candidates = []
        # Recovery is scoped to the user's selection, never all older Jobs.
        # Older failures may belong to another shot or contain a reused PID.
        for path, record in [(selected_path, selected)]:
            if os.path.normcase(str(record.get('config') or '')) != os.path.normcase(str(self.service.config.config_dir)):
                continue
            status = read_json(record.get('status_file'), {})
            state = status.get('state')
            runner_unavailable = (
                state == 'FAILED' and status.get('task') == 'Runner unavailable'
            )
            if state in ('COMPLETE', 'CANCELLED') or (state == 'FAILED' and not runner_unavailable):
                continue
            if state != 'QUEUED' and not runner_unavailable:
                raise RuntimeError(
                    f"Selected Job {record.get('id', path.stem)} is {state or 'UNKNOWN'}; "
                    "the Queue cannot be recovered safely."
                )
            execution = read_json(record.get('execution_file'), {})
            if execution:
                raise RuntimeError(
                    f"Job {record.get('id', path.stem)} has already started a worker; "
                    "its execution lock cannot be recovered."
                )
            acceptance = read_json(record.get('accepted_file'), {})
            pid = acceptance.get('pid')
            if not pid or not process_alive(pid):
                raise RuntimeError(
                    f"Queued runner {record.get('id', path.stem)} is unavailable. "
                    "Remove it or submit a new Job."
                )
            candidates.append((float(record['submitted_at']), path, record, int(pid)))
        if not candidates or not any(record.get('id') == selected_job_id for _, _, record, _ in candidates):
            raise RuntimeError("The selected Job is not a recoverable queued Runner.")

        candidates.sort(key=lambda item: item[0])
        for _, _, _, pid in candidates:
            terminate_process(pid)
        deadline = time.time() + 5
        while any(process_alive(pid) for _, _, _, pid in candidates):
            if time.time() >= deadline:
                raise RuntimeError("A queued runner did not stop; no Job was relaunched.")
            time.sleep(.05)
        for _, path, record, _ in candidates:
            accepted_file = Path(record['accepted_file'])
            try:
                accepted_file.unlink()
            except FileNotFoundError:
                pass
            atomic_json(record['status_file'], dict(
                state='QUEUED', progress=0, task=record.get('task') or 'Publish'
            ))
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            process = self.launch_detached(record, path, digest)
            launched_file = record.get('launched_file') or str(
                self.service.paths.artifact_file(
                    Path(record['request_file']).parent, 'launched.json'
                )
            )
            atomic_json(launched_file, dict(**process_receipt(process.pid), launched_at=time.time()))
        return "runners", 1

    def submit(self, identity, **kwargs):
        key = super().submit(identity, **kwargs)
        self.pending.remove(key)
        job = self.jobs[key]
        environment = dict(os.environ)
        for name, value in job['env_vars'].items():
            environment[name] = os.path.expandvars(value)
        for name, values in job['path_vars'].items():
            environment[name] = os.pathsep.join([os.path.expandvars(v) for v in values] + [environment.get(name, '')])
        environment['PROJECT_CONFIG_DIR'] = str(self.service.config.config_dir)
        environment['PYTHONPATH'] = str(Path(__file__).resolve().parents[3]) + os.pathsep + environment.get('PYTHONPATH', '')
        record = {k: v for k, v in job.items() if k != 'elapsed'}
        # Persist only explicit runtime overrides, not unrelated login tokens.
        runtime_keys = set(job['env_vars']) | set(job['path_vars']) | {'PROJECT_CONFIG_DIR', 'PYTHONPATH'}
        record.update(config=str(self.service.config.config_dir),
                      environment={name: environment[name] for name in runtime_keys},
                      submitted_at=time.time(), lease_file=str(self.lease_file),
                      execution_file=str(self.service.paths.artifact_file(Path(job['request_file']).parent, 'execution.json')),
                      launched_file=str(self.service.paths.artifact_file(Path(job['request_file']).parent, 'launched.json')),
                      runner_log_file=str(self.service.paths.artifact_file(Path(job['request_file']).parent, 'runner.log')),
                      accepted_file=str(self.service.paths.artifact_file(Path(job['request_file']).parent, 'accepted.json')))
        path = self.service.paths.artifact_file(self.registry, key + '.json')
        atomic_json(path, record)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        try:
            process = self.launch_detached(record, path, digest)
            atomic_json(record['launched_file'], dict(
                **process_receipt(process.pid), launched_at=time.time()
            ))
        except Exception as exc:
            atomic_json(job['status_file'], dict(state='FAILED', task='Launch failed',
                        progress=0, message=str(exc), finished_at=time.time()))
            self.poll()
            raise
        # Acceptance is shown only after the independent process writes its receipt.
        job['message'] = 'Starting independent runner; wait for Accepted before closing Maya.'
        self.changed.emit(key)
        return key

    def launch_detached(self, record, path, digest):
        args = [record['program'], '-m', 'smartlib.apps.review_build_manager.publish_runner',
                '--registration', str(path), '--sha256', digest]
        options = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, env=dict(os.environ, **record['environment']), close_fds=True)
        if os.name == 'nt':
            # Refuse a host job object that disallows breakaway instead of falsely
            # promising that its child will survive host termination.
            options['creationflags'] = (subprocess.DETACHED_PROCESS |
                subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_BREAKAWAY_FROM_JOB)
        else:
            options['start_new_session'] = True
        with open(record['runner_log_file'], 'ab') as log:
            options.update(stdout=log, stderr=subprocess.STDOUT)
            return subprocess.Popen(args, **options)

    def poll(self):
        if not hasattr(self, 'registry'):
            return
        for path in self.registry.glob('PUB-*.json'):
            try:
                record = read_json(path, {})
                if os.path.normcase(record['config']) != os.path.normcase(str(self.service.config.config_dir)):
                    continue
                key = record['id']
                is_new = key not in self.jobs
                job = self.jobs.setdefault(key, dict(record))
                previous = (job.get('state'), job.get('task'), job.get('progress'), job.get('message'), job.get('stderr'))
                state = read_json(record['status_file'], {})
                acceptance = read_json(record['accepted_file'], {})
                if state.get('state') not in TERMINAL_STATES:
                    dead = acceptance and not receipt_alive(acceptance)
                    launched = (
                        read_json(record.get('launched_file'), {})
                        if record.get('launched_file') else {}
                    )
                    launch_alive = bool(
                        launched and receipt_alive(launched)
                    )
                    timeout = (
                        not acceptance and not launch_alive
                        and time.time() - record['submitted_at'] > 120
                    )
                    if dead or timeout:
                        state = dict(state='FAILED', task='Runner unavailable', progress=0,
                            message='Independent runner exited or did not accept. Inspect runner.log; no automatic retry.',
                            finished_at=time.time())
                        atomic_json(record['status_file'], state)
                job.update(state)
                job['elapsed'] = Elapsed(record['submitted_at'], state.get('finished_at'))
                accepted = bool(acceptance)
                if accepted and job['state'] not in ('COMPLETE', 'FAILED'):
                    job['message'] = 'Accepted by independent runner. Maya can be closed.'
                log = Path(record['log_file'])
                if log.is_file():
                    with log.open('rb') as stream:
                        stream.seek(max(0, log.stat().st_size - 65536))
                        job['stderr'] = stream.read().decode('utf8', 'replace')
                current = (job.get('state'), job.get('task'), job.get('progress'), job.get('message'), job.get('stderr'))
                if is_new or current != previous:
                    self.changed.emit(key)
                if previous[0] not in ('COMPLETE', 'FAILED') and job['state'] in ('COMPLETE', 'FAILED'):
                    self.available.emit()
            except (OSError, ValueError, KeyError):
                continue
