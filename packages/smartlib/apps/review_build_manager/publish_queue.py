"""Application-owned Publish queue, independent of authoring/monitor windows."""
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import uuid

try:
    from PySide6 import QtCore
except ImportError:
    from PySide2 import QtCore

from smartlib.core.metadata import read_json, write_json
from smartlib.apps.shot_manager.usd_handoff import UsdHandoffService
from .service import ReviewBuildManagerService

_QUEUES = {}


def get_queue(shots):
    key = os.path.normcase(str(shots.project_config.config_dir.resolve()))
    if key not in _QUEUES:
        from .detached_publish import DetachedPublishQueue
        _QUEUES[key] = DetachedPublishQueue(shots, parent=QtCore.QCoreApplication.instance())
    return _QUEUES[key]


def show_queue(shots):
    from .window import show
    window = show(shots.project_config.config_dir)
    window.main_tabs.setCurrentWidget(window.job_queue_page)
    return window


class PublishQueue(QtCore.QObject):
    changed = QtCore.Signal(str)
    available = QtCore.Signal()

    def __init__(self, shots, parent=None):
        super().__init__(parent)
        self.service = UsdHandoffService(shots)
        self.jobs, self.pending = {}, []
        self.active = None
        self.lease = None
        self.process = None
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self.poll)

    def acquire(self, owner):
        if self.lease is not None:
            return False
        self.lease = owner
        return True

    def release(self, owner):
        if self.lease == owner:
            self.lease = None
            QtCore.QTimer.singleShot(0, self.start_next)
            QtCore.QTimer.singleShot(0, self.available.emit)

    def submit(self, identity, *, kind, plan=None, snapshot=None, frame_range=None, base=None,
               scene_input=None, scene_options=None, merge_animation=False):
        if kind not in ('animation_usd', 'primary_camera_usd', 'camera_batch_usd', 'layout_usd', 'assets_usd'):
            raise ValueError('Unsupported Publish job kind')
        runtime = ReviewBuildManagerService(self.service.config)
        mayapy = runtime.resolve_mayapy()
        env_vars, path_vars = runtime.maya_process_environment()
        request = dict(schema='smartpipeline.publish_job.v1', kind=kind,
            shot=dict(zip(('episode', 'sequence', 'shot'), self.service._identity(identity))),
            config=str(self.service.config.config_dir), maya_config_name=runtime.maya_software_config_name(),
            maya_config=runtime.maya_software_config(), base_composition=deepcopy(base))
        if merge_animation:
            if kind != 'animation_usd' or base is not None:
                raise ValueError('Animation merge cannot have an explicit base')
            request['merge_animation'] = True
        if scene_input is not None:
            if kind not in ('animation_usd', 'primary_camera_usd', 'camera_batch_usd', 'layout_usd', 'assets_usd'):
                raise ValueError('This Publish kind does not use a Maya scene')
            from smartlib.dcc.maya.publish_scene_input import validate_scene_input
            validate_scene_input(scene_input)
            request.update(scene_input=deepcopy(scene_input), scene_options=deepcopy(scene_options),
                           frame_range=list(frame_range))
            if base:
                self.service.load_handoff(self.service.check(base))
            if kind == 'assets_usd':
                self.service.validate_plan(plan, identity)
                if not plan.get('replace_assets'):
                    raise ValueError('Assets Publish requires a complete registration snapshot')
                self.service.composition_inputs(identity, plan, base)
                request['plan'] = deepcopy(plan)
        elif kind in ('camera_batch_usd', 'layout_usd'):
            raise ValueError('This Publish requires a saved Maya scene')
        elif kind in ('animation_usd', 'assets_usd'):
            self.service.validate_plan(plan, identity)
            if kind == 'assets_usd' and not plan.get('replace_assets'):
                raise ValueError('Assets Publish requires a complete registration snapshot')
            self.service.composition_inputs(identity, plan, base)
            request['plan'] = deepcopy(plan)
        else:
            request['snapshot'] = self.service.pin(snapshot)
            data = read_json(snapshot, {})
            if data.get('schema') != 'smartpipeline.primary_camera.v1':
                raise ValueError('Expected a Primary Camera snapshot')
            request['native'] = self.service.pin(self.service.paths.manifest_source(snapshot, data['files']['ma']))
            request['frame_range'] = list(frame_range)
            if data['frame_range'] != request['frame_range']:
                raise ValueError('Camera snapshot frame range changed')
            if base:
                self.service.load_handoff(self.service.check(base))
        _, directory = self.service._reserve(self.service.paths.usd_handoff_build_dir(*self.service._identity(identity)))
        request_file = self.service._file(directory, 'request.json')
        write_json(request_file, request)
        job_id = 'PUB-' + uuid.uuid4().hex[:10]
        job = dict(id=job_id, kind=kind, identity=self.service._identity(identity), scope='shot',
            state='QUEUED', task={'animation_usd': 'Animation USD', 'assets_usd': 'Assets USD',
                                'camera_batch_usd': 'Camera Batch USD', 'layout_usd': 'Layout USD',
                                'primary_camera_usd': 'Primary Camera USD'}[kind],
            progress=0, message='', stderr='', request_file=str(request_file),
            status_file=str(self.service._file(directory, 'status.json')),
            result_file=str(self.service._file(directory, 'result.json')),
            log_file=str(self.service._file(directory, 'worker.log')), elapsed=QtCore.QElapsedTimer(),
            program=str(mayapy), env_vars=deepcopy(env_vars), path_vars=deepcopy(path_vars),
            request_sha256=hashlib.sha256(request_file.read_bytes()).hexdigest())
        self.jobs[job_id] = job
        self.pending.append(job_id)
        write_json(job['status_file'], dict(state='QUEUED', progress=0, task=job['task']))
        self.changed.emit(job_id)
        QtCore.QTimer.singleShot(0, self.start_next)
        return job_id

    def start_next(self):
        if self.active or not self.pending or not self.acquire(self):
            return
        job = self.jobs[self.pending.pop(0)]
        self.active = job['id']
        job.update(state='STARTING', task='Start project Maya')
        job['elapsed'].start()
        self.changed.emit(job['id'])
        try:
            self._launch(job)
        except Exception as exc:
            self.finish(False, str(exc))

    def _launch(self, job):
        process = QtCore.QProcess(self)
        self.process = process
        env = QtCore.QProcessEnvironment.systemEnvironment()
        for key, value in job['env_vars'].items():
            env.insert(key, os.path.expandvars(value))
        for key, values in job['path_vars'].items():
            env.insert(key, os.pathsep.join([os.path.expandvars(v) for v in values] + [env.value(key)]))
        env.insert('PROJECT_CONFIG_DIR', str(self.service.config.config_dir))
        env.insert('PYTHONPATH', str(Path(__file__).resolve().parents[3]) + os.pathsep + env.value('PYTHONPATH'))
        process.setProcessEnvironment(env)
        process.setProcessChannelMode(QtCore.QProcess.MergedChannels)
        process.readyReadStandardOutput.connect(self.read_output)
        process.finished.connect(self.worker_finished)
        process.errorOccurred.connect(self.process_error)
        process.start(job['program'], ['-m', 'smartlib.apps.review_build_manager.publish_worker',
            '--request', job['request_file'], '--sha256', job['request_sha256']])
        self.timer.start()

    def read_output(self):
        if not self.active or not self.process:
            return
        if self.sender() is not None and self.sender() is not self.process:
            return
        raw = bytes(self.process.readAllStandardOutput())
        job = self.jobs[self.active]
        with Path(job['log_file']).open('ab') as stream:
            stream.write(raw)
        job['stderr'] = (job['stderr'] + raw.decode('utf8', 'replace'))[-65536:]
        self.changed.emit(job['id'])

    def poll(self):
        if self.active:
            job = self.jobs[self.active]
            try:
                data = read_json(job['status_file'], {})
                job.update({k: data[k] for k in ('task', 'progress') if k in data})
                if data.get('state') == 'BUILDING':
                    job['state'] = 'BUILDING'
            except (OSError, ValueError):
                pass
            self.changed.emit(job['id'])

    def process_error(self, error):
        if self.sender() is not self.process:
            return
        if self.active and error == QtCore.QProcess.FailedToStart:
            self.finish(False, self.process.errorString())

    def worker_finished(self, code, status):
        if self.sender() is not self.process:
            return
        if not self.active:
            return
        self.read_output()
        job = self.jobs[self.active]
        try:
            result = read_json(job['result_file'], {})
            if code or not result.get('ok'):
                raise ValueError(result.get('error') or f'Worker exited without a successful result ({code})')
            self.service.load_handoff(result['manifest'])
            self.finish(True, result['manifest'])
        except Exception as exc:
            self.finish(False, str(exc))

    def finish(self, success, message):
        if not self.active:
            return
        job = self.jobs[self.active]
        job.update(state='COMPLETE' if success else 'FAILED', progress=100 if success else job['progress'],
            task='Complete' if success else 'Failed', message=message)
        write_json(job['status_file'], {k: job[k] for k in ('state', 'task', 'progress', 'message')})
        self.timer.stop()
        if self.process:
            self.process.deleteLater()
        self.process = None
        self.active = None
        self.changed.emit(job['id'])
        self.release(self)
