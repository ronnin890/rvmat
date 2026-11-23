"""python -m rvmat_studio --selftest: окно, рендер и экспорт без участия пользователя (код 0 = всё работает)."""
import tempfile
import time
from pathlib import Path


def selftest() -> int:
    import os
    os.environ.setdefault("RVMAT_STUDIO_HOME", tempfile.mkdtemp())
    from ..core import export as ex
    from .app import StudioApp
    app = StudioApp()
    app.root.update()
    t = time.time()
    while time.time() - t < 3 and not app.preview.has_image():
        app.root.update(); time.sleep(0.02)
    img = app.preview.render_sync()
    assert img.shape[2] == 4 and img[..., :3].std() > 2, "рендер пуст"
    out = Path(tempfile.mkdtemp())
    plan = ex.plan_export(app.proj, {"out_root": str(out), "mesh_name": "selftest", "states": ["base", "damage", "destruct"], "fbx": False})
    res = ex.run_export(plan)
    assert not res.errors and len(res.written) == 3, res.errors
    app.proj.dirty = False
    app.close()
    print("SELFTEST OK")
    return 0
