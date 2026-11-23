"""Логирование в файл с ротацией: <app_dir>/logs/rvmat_studio.log. Трейсбеки скрытых исключений пишутся сюда."""
from __future__ import annotations

import logging
import logging.handlers
import sys
import time

from .settings import app_dir

LOGGER = logging.getLogger("rvmat_studio")
MAX_BYTES = 1024 * 1024
BACKUPS = 3
_configured = False
_recent: dict = {}
DEDUPE_SECONDS = 30.0


def log_path():
    d = app_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d / "rvmat_studio.log"


def setup(level: int = logging.INFO) -> None:
    """Идемпотентно подключает файловый обработчик; при ошибке (нет прав на запись) логирование отключается тихо."""
    global _configured
    if _configured:
        return
    _configured = True
    LOGGER.setLevel(level)
    LOGGER.propagate = False
    try:
        h = logging.handlers.RotatingFileHandler(log_path(), maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        LOGGER.addHandler(h)
    except OSError:
        LOGGER.addHandler(logging.NullHandler())


def log_exc(where: str = "") -> None:
    """Записать текущее исключение (трейсбек) в журнал. Вызывать внутри except."""
    et, ev, _ = sys.exc_info()
    if et is None:
        return
    key = (where, et.__name__, str(ev)[:200])              # одинаковая ошибка (например при каждом кадре) пишется не чаще раза в 30 с
    now = time.monotonic()
    if now - _recent.get(key, -1e9) < DEDUPE_SECONDS:
        return
    _recent[key] = now
    if len(_recent) > 500:
        _recent.clear()
    if not LOGGER.handlers:
        setup()
    LOGGER.error("Исключение%s", f" в {where}" if where else "", exc_info=True)


def info(msg: str) -> None:
    if not LOGGER.handlers:
        setup()
    LOGGER.info(msg)
