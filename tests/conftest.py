import os
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["RVMAT_STUDIO_HOME"] = tempfile.mkdtemp(prefix="rvmat_home_")


import pytest


@pytest.fixture(scope="session", autouse=True)
def _release_gl_at_end():
    yield
    try:
        from rvmat_studio.gui.preview import close_offscreen
        close_offscreen()
    except Exception:
        pass
