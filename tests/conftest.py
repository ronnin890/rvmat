import os
import tempfile

os.environ["RVMAT_STUDIO_HOME"] = tempfile.mkdtemp(prefix="rvmat_home_")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest


@pytest.fixture(scope="session", autouse=True)
def _qt_cleanup_at_end():
    """Тесты создают десятки окон: перед завершением процесса закрываем их и собираем мусор, пока жив QApplication
    (иначе PySide6 может упасть при разрушении интерпретатора — только в тестах, само приложение выходит штатно)."""
    yield
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        return
    app = QApplication.instance()
    if app is None:
        return
    import gc
    for _ in range(2):
        for w in app.topLevelWidgets():
            try:
                w.close()
                w.deleteLater()
            except RuntimeError:
                pass
        app.processEvents()
        gc.collect()
    from PySide6.QtCore import QCoreApplication, QEvent
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


def pytest_sessionfinish(session, exitstatus):
    """RVMAT_REQUIRE_ALL=1 (в CI): пропущенные тесты считаются ошибкой — кроме symlink-тестов на Windows.
    Локальные пропуски допустимы только из-за окружения (нет PySide6/moderngl/OpenGL/hypothesis)."""
    if not os.environ.get("RVMAT_REQUIRE_ALL"):
        return
    tr = session.config.pluginmanager.get_plugin("terminalreporter")
    skipped = tr.stats.get("skipped", []) if tr else []
    bad = [r for r in skipped if "symlink" not in str(r.longrepr)]
    if bad:
        print(f"\nRVMAT_REQUIRE_ALL: пропущено тестов: {len(bad)} (например: {bad[0].longrepr})")
        session.exitstatus = 1
