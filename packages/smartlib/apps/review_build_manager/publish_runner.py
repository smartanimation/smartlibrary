"""Detached one-job supervisor. No Qt objects or parent-Maya lifetime dependency."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import uuid

TERMINAL_STATES = {'COMPLETE', 'FAILED', 'CANCELLED'}


def atomic_json(path, data):
    path = Path(path)
    temporary = path.with_name(path.name + '.' + str(os.getpid()) + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf8')
    os.replace(temporary, path)


class DirectoryLease:
    def __init__(self, directory, token):
        self.directory = Path(directory)
        self.token = token
        self.closed = False

    def close(self):
        if self.closed:
            return
        self.closed = True
        owner_file = self.directory / 'owner.json'
        owner = _read_json(owner_file)
        if owner.get('token') != self.token:
            return
        try:
            owner_file.unlink()
        except FileNotFoundError:
            pass
        try:
            self.directory.rmdir()
        except (FileNotFoundError, OSError):
            pass


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf8'))
    except (OSError, ValueError, TypeError):
        return {}


def acquire_lock(path):
    """Acquire a PID-owned atomic directory lease and reclaim dead owners."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_dir = path.with_name(path.name + '.d')
    for _attempt in range(3):
        token = uuid.uuid4().hex
        try:
            lock_dir.mkdir()
        except FileExistsError:
            owner = _read_json(lock_dir / 'owner.json')
            owner_pid = owner.get('pid')
            if owner_pid and receipt_alive(owner):
                return None
            try:
                age = time.time() - lock_dir.stat().st_mtime
            except OSError:
                continue
            if not owner_pid and age < 30:
                return None
            stale = lock_dir.with_name(
                lock_dir.name + f'.stale.{os.getpid()}.{uuid.uuid4().hex}'
            )
            try:
                lock_dir.rename(stale)
            except (FileNotFoundError, OSError):
                continue
            try:
                for child in stale.iterdir():
                    child.unlink()
                stale.rmdir()
            except OSError:
                pass
            continue
        atomic_json(lock_dir / 'owner.json', dict(
            **process_receipt(os.getpid()), token=token, acquired_at=time.time()
        ))
        return DirectoryLease(lock_dir, token)
    return None


def process_alive(pid):
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel.OpenProcess(0x00100000, False, int(pid))
        if not handle:
            return ctypes.get_last_error() == 5  # Access denied is not proof of exit.
        try:
            return kernel.WaitForSingleObject(handle, 0) == 258
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def process_created_at(pid):
    """Return Windows process creation time, or None if it cannot be queried."""
    if os.name != 'nt':
        return None
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4
    kernel.GetProcessTimes.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x1000, False, int(pid))
    if not handle:
        return None
    try:
        values = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(v) for v in values)):
            return None
        created = values[0]
        return ((created.dwHighDateTime << 32) | created.dwLowDateTime) / 10000000 - 11644473600
    finally:
        kernel.CloseHandle(handle)


def process_receipt(pid):
    return dict(pid=pid, process_created_at=process_created_at(pid))


def receipt_alive(receipt):
    pid = receipt.get('pid')
    if not pid or not process_alive(pid):
        return False
    created = process_created_at(pid)
    if created is None:
        return True  # Unqueryable is not proof of exit.
    expected = receipt.get('process_created_at')
    if expected is not None:
        return abs(created - expected) < .001
    # Legacy receipts were written after process creation. A newer process
    # using the same PID cannot be the recorded worker.
    recorded = next((receipt[k] for k in
                     ('started_at', 'accepted_at', 'launched_at', 'acquired_at')
                     if receipt.get(k) is not None), None)
    return recorded is None or created <= recorded + .001


def predecessor_blocks(other, now=None):
    """A terminal job never reserves FIFO priority, even after PID reuse."""
    if _read_json(other['status_file']).get('state') in TERMINAL_STATES:
        return False
    accepted = _read_json(other['accepted_file'])
    execution = _read_json(other.get('execution_file'))
    if receipt_alive(execution) or receipt_alive(accepted):
        return True
    launched = _read_json(other.get('launched_file'))
    if receipt_alive(launched):
        return True
    return not accepted and (time.time() if now is None else now) - other['submitted_at'] < 120


def run(registration, expected_hash):
    from smartlib.core.metadata import read_json
    record = Path(registration)
    if hashlib.sha256(record.read_bytes()).hexdigest() != expected_hash:
        raise ValueError('Publish launch registration changed')
    job = read_json(record, {})
    status = job['status_file']
    atomic_json(job['accepted_file'], dict(**process_receipt(os.getpid()), accepted_at=time.time()))
    lease = None
    try:
        while lease is None:
            lease = acquire_lock(job['lease_file'])
            if lease:
                # FIFO among registered live runners. Do not retry orphaned jobs.
                for other_path in record.parent.glob('PUB-*.json'):
                    other = read_json(other_path, {})
                    if other.get('submitted_at', float('inf')) >= job['submitted_at']:
                        continue
                    if predecessor_blocks(other):
                        lease.close()
                        lease = None
                        break
            if lease is None:
                time.sleep(.25)
        if hashlib.sha256(Path(job['request_file']).read_bytes()).hexdigest() != job['request_sha256']:
            raise ValueError('Publish request changed after enqueue')
        atomic_json(status, dict(state='STARTING', task='Start project Maya', progress=0))
        with open(job['log_file'], 'ab', buffering=0) as log:
            child = subprocess.Popen([job['program'], '-m',
                'smartlib.apps.review_build_manager.publish_worker',
                '--request', job['request_file'], '--sha256', job['request_sha256']],
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                env=dict(os.environ, **job['environment']), creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            if job.get('execution_file'):
                atomic_json(job['execution_file'], dict(**process_receipt(child.pid), started_at=time.time()))
            code = child.wait()
        result = read_json(job['result_file'], {})
        if code or not result.get('ok'):
            raise ValueError(result.get('error') or f'Publish worker failed ({code})')
        from smartlib.core.config_loader import ProjectConfig
        from smartlib.apps.shot_manager import ShotManagerService
        from smartlib.apps.shot_manager.usd_handoff import UsdHandoffService
        service = UsdHandoffService(ShotManagerService(ProjectConfig(job['config'])))
        service.load_handoff(result['manifest'])
        atomic_json(status, dict(state='COMPLETE', task='Complete', progress=100,
                                 message=result['manifest'], finished_at=time.time()))
    except Exception as exc:
        atomic_json(status, dict(state='FAILED', task='Failed', progress=0,
                                 message=str(exc), finished_at=time.time()))
    finally:
        if lease:
            lease.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--registration', required=True)
    parser.add_argument('--sha256', required=True)
    args = parser.parse_args()
    run(args.registration, args.sha256)
