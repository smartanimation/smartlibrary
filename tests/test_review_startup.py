import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from smartlib.apps.review_build_manager.startup import startup_splash, QtWidgets


@pytest.mark.parametrize('fail', [False, True])
def test_startup_feedback_closes_on_success_and_failure(fail):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    captured = []
    try:
        with startup_splash() as splash:
            captured.append(splash)
            assert splash.isVisible()
            assert not splash.pixmap().isNull()
            if fail:
                raise RuntimeError('startup failed')
    except RuntimeError as exc:
        assert fail and str(exc) == 'startup failed'
    assert not captured[0].isVisible()
