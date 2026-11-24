"""Пользовательские настройки (JSON)."""
from __future__ import annotations

import json
import re
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import limits, safeio


def app_dir() -> Path:
    if os.environ.get("RVMAT_STUDIO_HOME"):
        p = Path(os.environ["RVMAT_STUDIO_HOME"])
    elif sys.platform == "win32":
        p = Path(os.environ.get("APPDATA", Path.home())) / "DayZRvmatStudio"
    else:
        p = Path.home() / ".config" / "DayZRvmatStudio"
    p.mkdir(parents=True, exist_ok=True)
    return p


@dataclass
class Settings:
    dayz_tools: str = ""
    workbench: str = ""
    image_to_paa: str = ""
    gproj: str = ""               # путь к dayz.gproj для Workbench (пусто = искать автоматически)
    p_drive: str = "P:\\"
    project_root: str = ""
    autosave_seconds: int = 60
    recent: list = field(default_factory=list)
    last_dir: str = ""
    geometry: str = ""            # Qt: base64 QMainWindow.saveGeometry
    window_state: str = ""        # Qt: base64 QMainWindow.saveState
    reflect_gain: float = 1.0     # сила отражений окружения в превью (калибровка)
    renderer: str = "auto"        # auto | gl | cpu
    full_drive_paths: bool = True  # писать в RVMAT полный путь с диском (P:\\MyMod\\...)
    trusted_exes: list = field(default_factory=list)   # exe, запуск которых пользователь подтвердил
    texture_dirs: list = field(default_factory=list)   # папки с текстурами, добавленные пользователем явно
    version: int = 1
    _warnings: list = field(default_factory=list, repr=False, compare=False)

    @classmethod
    def path(cls) -> Path:
        return app_dir() / "settings.json"

    @classmethod
    def load(cls) -> "Settings":
        """Неверные поля пропускаются (остаются значения по умолчанию), причины — в .warnings; файл не падает."""
        st = cls()
        try:
            d = safeio.read_json(cls.path(), "settings.json")
        except (OSError, safeio.DataError) as e:
            if cls.path().exists():
                st._warnings.append(f"settings.json не прочитан: {e}")
            return st
        if not isinstance(d, dict):
            st._warnings.append("settings.json: ожидался объект")
            return st
        ver = d.get("version")
        if isinstance(ver, int) and not isinstance(ver, bool) and ver > 1:
            st._warnings.append("settings.json создан более новой версией программы")
        for k, v in d.items():
            if k not in cls.__dataclass_fields__ or k.startswith("_") or k == "version":
                continue
            default = getattr(st, k)
            try:
                if isinstance(default, str):
                    v = safeio.as_str(v, k)
                elif isinstance(default, int) and not isinstance(default, bool):
                    v = safeio.as_int(v, k, default, 5, 86400)
                elif isinstance(default, bool):
                    v = safeio.as_bool(v, k, default)
                elif isinstance(default, float):
                    v = safeio.as_float(v, k, default, 0.0, 8.0)
                elif isinstance(default, list):
                    v = safeio.str_list(v, k, 200)
                else:
                    continue
            except safeio.SchemaError as e:
                st._warnings.append(str(e))
                continue
            setattr(st, k, v)
        for k in ("geometry", "window_state"):           # base64 (QByteArray); мусор и чрезмерная длина отбрасываются
            if not re.fullmatch(r"[A-Za-z0-9+/=]{0,16384}", getattr(st, k)):
                st._warnings.append(f"{k}: повреждённое значение сброшено")
                setattr(st, k, "")
        if st.renderer not in ("auto", "gl", "cpu"):
            st._warnings.append(f"renderer={st.renderer!r} недопустим — использовано auto")
            st.renderer = "auto"
        return st

    @property
    def warnings(self) -> list:
        return self._warnings

    def save(self):
        d = {k: v for k, v in asdict(self).items() if not k.startswith("_")}
        safeio.atomic_write(self.path(), json.dumps(d, ensure_ascii=False, indent=1))

    def add_recent(self, p: str):
        self.recent = [p] + [r for r in self.recent if r != p][:9]

    def find_tools(self) -> bool:
        """Поиск DayZ Tools в стандартных местах Steam."""
        for c in (r"C:\Program Files (x86)\Steam\steamapps\common\DayZ Tools", r"D:\SteamLibrary\steamapps\common\DayZ Tools"):
            if Path(c).is_dir():
                self.dayz_tools = c
                i = Path(c) / "Bin" / "ImageToPAA" / "ImageToPAA.exe"
                w = Path(c) / "Bin" / "Workbench" / "workbenchApp.exe"
                if i.exists():
                    self.image_to_paa = str(i)
                if w.exists():
                    self.workbench = str(w)
                return True
        return False
