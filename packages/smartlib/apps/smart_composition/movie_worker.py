"""Isolated USD capture process. No DCC or published USD is modified."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from pxr import Usd, UsdGeom
from pxr.Usdviewq.qt import QtCore, QtWidgets, QtGui
from pxr.Usdviewq.stageView import StageView
from pxr.Usdviewq.common import CameraMaskModes
from smartlib.apps.shot_manager import ShotManagerService
from smartlib.apps.shot_manager.usd_handoff import UsdHandoffService
from smartlib.core.config_loader import ProjectConfig
from smartlib.core.metadata import read_json, write_json
from .movie_export import verify_inputs, make_receipt
from .movie_report import create_report


class Cancelled(Exception):
    pass


def main(job_file):
    job = read_json(job_file, {})
    service = UsdHandoffService(ShotManagerService(ProjectConfig(job['config'])))
    paths = service.paths
    work = Path(job['work'])
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    view = None
    published = []
    complete = False
    def check_cancel():
        if Path(job['cancel']).exists():
            raise Cancelled('Export cancelled')
    try:
        verify_inputs(service, job)
        stage = Usd.Stage.Open(job['snapshot']['entrypoint']['path'])
        camera = stage.GetPrimAtPath(job['camera'])
        if not camera or not camera.IsA(UsdGeom.Camera):
            raise ValueError('Camera missing from saved USD')
        model = StageView.DefaultDataModel()
        view = StageView(dataModel=model)
        # The first Hydra render must use a numeric time: initializing at
        # Default() can retain default-time values for animated camera/geometry.
        model.currentFrame = Usd.TimeCode(job['frame_range'][0])
        model.stage = stage
        model.viewSettings.cameraPrim = camera
        model.viewSettings.showHUD = False
        model.viewSettings.showBBoxes = False
        model.viewSettings.showAxis = False
        model.viewSettings.showCameras = False
        # PARTIAL uses the same evaluated camera matrices as the interactive view.
        model.viewSettings.cameraMaskMode = CameraMaskModes.PARTIAL
        model.viewSettings.cameraMaskColor = (0., 0., 0., 1.)
        model.viewSettings.showMask_Outline = False
        view.setAttribute(QtCore.Qt.WA_DontShowOnScreen, True)
        view.setFixedSize(*job['resolution'])
        view.show()
        app.processEvents()
        start, end = job['frame_range']
        frames = []
        for index, frame in enumerate(range(start, end + 1)):
            check_cancel()
            model.currentFrame = Usd.TimeCode(frame)
            view.updateView(forceComputeBBox=(index == 0))
            app.processEvents()
            # Hidden QOpenGLWidget updates can be coalesced; explicitly render
            # this time code before reading the framebuffer.
            view.makeCurrent()
            view.paintGL()
            # grabFrameBuffer synchronizes the GL framebuffer, not the desktop.
            image = view.grabFrameBuffer(cropToAspectRatio=True)
            width, height = job['resolution']
            if image.isNull():
                raise RuntimeError('USD renderer returned an empty image')
            if (image.width(), image.height()) != (width, height):
                fitted = image.scaled(width, height, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
                image = QtGui.QImage(width, height, QtGui.QImage.Format_RGB32)
                image.fill(QtCore.Qt.black)
                painter = QtGui.QPainter(image)
                painter.drawImage((width - fitted.width()) // 2, (height - fitted.height()) // 2, fitted)
                painter.end()
            path = paths.artifact_file(work, f'frame_{index:06d}.png')
            if not image.save(str(path)):
                raise RuntimeError('Could not save captured frame')
            frames.append(path)
            print(f'FRAME {index + 1} {end - start + 1}', flush=True)
        view.closeRenderer()
        view.close()
        view = None
        check_cancel()
        print('ENCODING', flush=True)
        movie = paths.artifact_file(work, 'review.mov')
        log = paths.artifact_file(work, 'ffmpeg.log')
        command = [job['ffmpeg'], '-hide_banner', '-loglevel', 'error', '-n',
            '-framerate', str(job['fps']), '-start_number', '0', '-i',
            str(frames[0]).replace('frame_000000.png', 'frame_%06d.png'), '-frames:v', str(len(frames)),
            '-c:v', 'libx264', '-preset', 'fast', '-crf', '18', '-pix_fmt', 'yuv420p',
            '-movflags', '+faststart', str(movie)]
        with log.open('w', encoding='utf8') as stream:
            encoder = subprocess.Popen(command, stdout=stream, stderr=stream,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            try:
                deadline = time.monotonic() + 1800
                while encoder.poll() is None:
                    check_cancel()
                    if time.monotonic() > deadline:
                        raise RuntimeError('FFmpeg timed out')
                    time.sleep(.1)
            finally:
                if encoder.poll() is None:
                    encoder.kill()
                    encoder.wait()
        if encoder.returncode:
            raise RuntimeError(log.read_text(encoding='utf8')[-2000:])
        probe = Path(job['ffmpeg']).with_name('ffprobe.exe' if os.name == 'nt' else 'ffprobe')
        if not probe.is_file():
            raise RuntimeError('ffprobe is required to validate the output movie')
        result = subprocess.run([str(probe), '-v', 'error', '-count_frames', '-select_streams', 'v:0',
            '-show_entries', 'stream=width,height,nb_read_frames,avg_frame_rate', '-of', 'json', str(movie)],
            capture_output=True, text=True, check=True, timeout=120,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        info = json.loads(result.stdout)['streams'][0]
        numerator, denominator = map(float, info['avg_frame_rate'].split('/'))
        if ([info['width'], info['height']] != job['resolution']
                or int(info['nb_read_frames']) != len(frames)
                or abs(numerator / denominator - job['fps']) > .001):
            raise RuntimeError('Movie resolution, frame count or FPS does not match the job')
        print('REPORT', flush=True)
        report = paths.artifact_file(work, 'report.pdf')
        focal = UsdGeom.Camera(camera).GetFocalLengthAttr().Get(Usd.TimeCode(start))
        create_report(report, job, frames[len(frames)//2], focal * UsdGeom.GetStageMetersPerUnit(stage) * 100)
        check_cancel()
        verify_inputs(service, job)
        for source, key in [(movie, 'movie'), (report, 'report')]:
            destination = Path(job['files'][key])
            with destination.open('xb') as output:
                published.append(destination)
                with source.open('rb') as stream:
                    shutil.copyfileobj(stream, output)
        receipt = Path(job['files']['receipt'])
        with receipt.open('x', encoding='utf8') as output:
            published.append(receipt)
            json.dump(make_receipt(job), output, ensure_ascii=False, indent=2)
        complete = True
        # Remove only the exact intermediate frames generated by this process.
        for frame in frames:
            frame.unlink()
        print('COMPLETE ' + job['files']['movie'], flush=True)
        return 0
    except Cancelled as exc:
        print(str(exc), flush=True)
        return 2
    except Exception as exc:
        import traceback
        traceback.print_exc()
        print('ERROR ' + str(exc), flush=True)
        return 1
    finally:
        if view:
            view.closeRenderer()
            view.close()
        if not complete:
            for path in published:
                path.unlink(missing_ok=True)
        Path(job['lock']).unlink(missing_ok=True)


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1]))
