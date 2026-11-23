import os
import tempfile

os.environ["RVMAT_STUDIO_HOME"] = tempfile.mkdtemp(prefix="rvmat_home_")


def pytest_sessionfinish(session, exitstatus):
    """RVMAT_REQUIRE_ALL=1 (в CI): пропущенные тесты считаются ошибкой — кроме symlink-тестов на Windows.
    Локальные пропуски допустимы только из-за окружения (нет tkinter/дисплея/moderngl/hypothesis)."""
    if not os.environ.get("RVMAT_REQUIRE_ALL"):
        return
    tr = session.config.pluginmanager.get_plugin("terminalreporter")
    skipped = tr.stats.get("skipped", []) if tr else []
    bad = [r for r in skipped if "symlink" not in str(r.longrepr)]
    if bad:
        print(f"\nRVMAT_REQUIRE_ALL: пропущено тестов: {len(bad)} (например: {bad[0].longrepr})")
        session.exitstatus = 1
