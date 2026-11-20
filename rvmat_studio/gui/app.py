import sys

from PySide6.QtWidgets import QApplication

from .main_window import MainWindow
from .theme import DARK


def selftest() -> int:
    """Проверка собранного приложения: окно, рендер, экспорт. Код 0 = всё работает."""
    import os, tempfile
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["RVMAT_STUDIO_HOME"] = tempfile.mkdtemp()
    from pathlib import Path
    from ..core import export as ex
    app = QApplication([])
    w = MainWindow()
    w.show()
    img = w.preview.render_sync()
    assert img.shape[2] == 4 and img[..., :3].std() > 2, "рендер пуст"
    out = Path(tempfile.mkdtemp())
    assert len(ex.execute(ex.plan_kit(w.proj, out))) == 6, "экспорт комплекта"
    w.preview.shutdown()
    from .preview import close_offscreen
    close_offscreen()
    w.proj.dirty = False
    print(f"SELFTEST OK (рендер: {w.preview.backend})")
    return 0


def main(argv=None) -> int:
    argv = list(argv or sys.argv)
    if "--selftest" in argv:
        return selftest()
    from ..core.gl_render import surface_format
    from PySide6.QtGui import QSurfaceFormat
    QSurfaceFormat.setDefaultFormat(surface_format())
    app = QApplication(argv)
    from .preview import close_offscreen
    app.aboutToQuit.connect(close_offscreen)
    app.setStyle("Fusion")
    app.setStyleSheet(DARK)
    w = MainWindow()
    w.show()
    w.check_recovery()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
