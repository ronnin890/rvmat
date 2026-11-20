import os
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["RVMAT_STUDIO_HOME"] = tempfile.mkdtemp(prefix="rvmat_home_")
