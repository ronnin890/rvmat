"""Пользовательские настройки (JSON) и пути интеграции с DayZ Tools."""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path


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
    window_state: str = ""        # base64 QMainWindow.saveState
    geometry: str = ""
    recent: list = field(default_factory=list)
    env_gain: float = 2.5         # сила отражений окружения в превью (калибровка)
    renderer: str = "auto"       # auto | gl | cpu
    lighting: str = "Студия"
    last_dir: str = ""

    @classmethod
    def path(cls) -> Path:
        return app_dir() / "settings.json"

    @classmethod
    def load(cls) -> "Settings":
        try:
            d = json.loads(cls.path().read_text("utf-8"))
            return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
        except (OSError, ValueError, TypeError):
            return cls()

    def save(self):
        self.path().write_text(json.dumps(asdict(self), ensure_ascii=False, indent=1), "utf-8")

    def add_recent(self, p: str):
        self.recent = [p] + [r for r in self.recent if r != p][:9]

    def find_tools(self):
        """Попытка найти DayZ Tools в стандартных местах Steam."""
        cands = [r"C:\Program Files (x86)\Steam\steamapps\common\DayZ Tools",
                 r"D:\SteamLibrary\steamapps\common\DayZ Tools"]
        for c in cands:
            if Path(c).is_dir():
                self.dayz_tools = c
                i = Path(c) / "Bin" / "ImageToPAA" / "ImageToPAA.exe"
                if i.exists():
                    self.image_to_paa = str(i)
                w = Path(c) / "Bin" / "Workbench" / "workbenchApp.exe"
                if w.exists():
                    self.workbench = str(w)
                return True
        return False
