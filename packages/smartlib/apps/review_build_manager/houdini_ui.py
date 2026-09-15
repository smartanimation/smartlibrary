"""Houdini jobs use the existing Build Manager queue and status presentation."""
import json
import os
from pathlib import Path
try:
    from PySide6 import QtCore
except ImportError:
    from PySide2 import QtCore
from smartlib.core.metadata import write_json
from .houdini_build import runtime, snapshot
from .orchestrator import SceneBuildPlan, BuildValidation


def inputs(window, identity):
    overrides = window._stage_input_overrides(identity)
    construct = window.service.shots.resolved_construct(identity,
        cast_contexts=overrides.get('cast_contexts') or {}, exclude_cast=overrides.get('exclude_cast') or [])
    return snapshot(window.service, identity, construct)


def plan(window, identity):
    mode = window.mode_combo.currentText()
    validations = []
    summary = 'Build pinned environment USD in Solaris'
    try:
        if window.scope_combo.currentText() != 'Shot' or mode != 'WORK STAGE':
            raise ValueError('Houdini currently supports Shot / WORK STAGE only.')
        runtime(window.service.project_config)
        data = inputs(window, identity)
        summary = '; '.join(f"{a['name']}: Pack {a['pack_version']}, Release {a['release_version']}, "
                           f"{a['variant_selections']}" for a in data['assets'])
    except Exception as exc:
        validations.append(BuildValidation('ERROR', 'HOUDINI_INPUT', str(exc)))
    return SceneBuildPlan(identity, mode, mode, window.department_combo.currentText(),
        window.task_combo.currentText(), state='BLOCKED' if validations else 'READY',
        summary=summary, validations=tuple(validations))


def enqueue(window, identities):
    from smartlib.apps.shot_manager import ShotIdentity
    for values in identities:
        if any(tuple(j['identity']) == tuple(values) for j in [*window.pending_jobs, *([window.active_job] if window.active_job else [])]):
            continue
        identity = ShotIdentity(*values)
        build_plan = plan(window, identity)
        if not build_plan.buildable:
            window.footer_label.setText('; '.join(v.message for v in build_plan.validations))
            continue
        try:
            data = inputs(window, identity)
            version = window.service.next_construct_version(identity, build_plan.department, build_plan.task, 'houdini')
            paths = window.service.shots.paths
            directory = window.service.shots.shot_build_dir(identity, build_plan.department, 'houdini', build_plan.task, version)
            directory.mkdir(parents=True, exist_ok=False)
            status = paths.artifact_file(directory, 'status.json')
            request = dict(snapshot=data, status_file=str(status),
                scene=str(paths.artifact_file(directory,
                    f'{identity.shot}_{build_plan.department}_{build_plan.task}_{version}.hip')),
                stage=str(paths.artifact_file(directory, 'shot.usda')),
                manifest=str(paths.artifact_file(directory, 'build_manifest.json')))
            request_file = paths.artifact_file(directory, 'job.json')
            write_json(request_file, request)
            window.job_counter += 1
            job = dict(id=f'#{window.job_counter:04d}', identity=values, scope='shot', dcc='houdini',
                version=version, mode='WORK STAGE', department=build_plan.department, task_name=build_plan.task,
                status_file=str(status), request_file=str(request_file), state='QUEUED', progress=0,
                task='Queued', elapsed=QtCore.QElapsedTimer(), row=window.queue_table.rowCount(), stderr='',
                open_after_build=False)
            window.pending_jobs.append(job); window.queue_jobs.append(job); window._append_queue_row(job)
        except Exception as exc:
            window.footer_label.setText(f'Houdini Build: {exc}')
    if window.pending_jobs and not window.active_job:
        window._start_next_job()
    window._update_build_buttons()


def start(window, job):
    try:
        executable, config = runtime(window.service.project_config)
        process = QtCore.QProcess(window)
        env = QtCore.QProcessEnvironment.systemEnvironment()
        for key, value in (config.get('env_vars') or {}).items():
            env.insert(key, os.path.expandvars(str(value)))
        for key, values in (config.get('paths') or {}).items():
            if isinstance(values, str): values = [values]
            env.insert(key, os.pathsep.join([*(os.path.expandvars(v) for v in values), env.value(key)]))
        env.insert('PYTHONPATH', str(Path(__file__).resolve().parents[3]) + os.pathsep + env.value('PYTHONPATH'))
        process.setProcessEnvironment(env)
        process.setProgram(str(executable))
        process.setArguments(['-m', 'smartlib.apps.review_build_manager.houdini_worker', job['request_file']])
        process.setWorkingDirectory(str(Path(__file__).resolve().parents[4]))
        process.readyReadStandardError.connect(window._read_worker_stderr)
        process.readyReadStandardOutput.connect(window._read_worker_stdout)
        process.started.connect(window._worker_started)
        process.errorOccurred.connect(window._worker_process_error)
        process.finished.connect(window._worker_finished)
        window.worker_process = process
        job['launch_details'] = window._worker_launch_details(process)
        job['task'] = 'Start hython'
        window.job_timer.start()
        process.start()
    except Exception as exc:
        job['message'] = str(exc)
        window._finish_active_job(False)
