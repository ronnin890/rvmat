import sys

from PySide6.QtWidgets import QApplication

from ..core import logs
from .main_window import MainWindow
from .theme import DARK


def selftest() -> int:
    """Проверка собранного приложения: окно, рендер, экспорт. Код 0 = всё работает."""
    import os
    import tempfile
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["RVMAT_STUDIO_HOME"] = tempfile.mkdtemp()
    from pathlib import Path
    from ..core import export as ex
    app = QApplication([])
    w = MainWindow()
    w.show()
    img = w.preview.render_sync()
    assert img.shape[2] >= 3 and img[..., :3].std() > 2, "рендер пуст"
    out = Path(tempfile.mkdtemp())
    res = ex.run_export(ex.plan_export(w.proj, {"out_root": str(out), "mesh_name": "selftest", "textures": False, "fbx": False,
                                                "states": ["base", "damage", "destruct"]}))
    assert not res.errors and len(res.written) == 3, f"экспорт: {res.errors}"
    backend = w.preview.backend_info
    w.preview.shutdown()
    w.proj.dirty = False
    print(f"SELFTEST OK (рендер: {backend})")
    return 0


def main(argv=None) -> int:
    argv = list(argv if argv is not None else sys.argv)
    if "--selftest" in argv:
        return selftest()
    logs.setup()
    app = QApplication(argv)
    app.setStyle("Fusion")
    app.setStyleSheet(DARK)
    w = MainWindow()
    sys.excepthook = w.on_unhandled_error
    w.show()
    w.check_recovery()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
