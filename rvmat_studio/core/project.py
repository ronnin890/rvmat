"""Проект: общий материал + переопределения по состояниям, undo/redo, автосохранение."""
from __future__ import annotations

import copy
import json
import time
from pathlib import Path
from typing import Any, Optional

from ..backend import ALL_STATES, KitSpec, Material, State, validate_kit
from ..backend.kit import apply_override, flatten_values
from .presets import super_template
from .settings import app_dir

TARGET_SHARED = "shared"
TARGET_DECOR = "decorative"
FORMAT_VERSION = 1


class Project:
    def __init__(self, name="newmaterial", material: Optional[Material] = None):
        self.name = name
        self.spec = KitSpec(name, material or super_template())
        self.enabled: set[State] = set(ALL_STATES)
        self.project_root = ""           # P:\ или корень проекта: для проверки/пересчёта путей
        self.export_dir = ""
        self.textures: list[str] = []    # библиотека текстур (физические пути)
        self.tex_map: dict[str, str] = {}   # игровой путь -> физический файл (вне корня проекта)
        self.base_color = ""             # физический файл _co для предпросмотра (в RVMAT НЕ пишется)
        self.baseline = self.spec.shared.clone()   # для «сброса» общих параметров
        self._last_change = ("", 0.0)
        self.path: Optional[Path] = None
        self.dirty = False
        self._undo: list[tuple[str, str]] = []
        self._redo: list[tuple[str, str]] = []
        self.history: list[str] = []
        self.listeners: list = []

    # --- сериализация
    def to_dict(self) -> dict:
        return {"version": FORMAT_VERSION, "name": self.name, "rvmat": self.spec.shared.to_text(),
                "decorative": self.spec.decorative,
                "overrides": {s.value: v for s, v in self.spec.overrides.items() if v},
                "enabled": sorted(s.value for s in self.enabled), "project_root": self.project_root,
                "export_dir": self.export_dir, "textures": self.textures, "tex_map": self.tex_map, "base_color": self.base_color}

    @classmethod
    def from_dict(cls, d: dict) -> "Project":
        p = cls(d["name"], Material.from_text(d["rvmat"]))
        p.spec.decorative = d.get("decorative", {})
        p.spec.overrides = {State(k): v for k, v in d.get("overrides", {}).items()}
        p.enabled = {State(s) for s in d.get("enabled", [s.value for s in ALL_STATES])}
        p.project_root, p.export_dir = d.get("project_root", ""), d.get("export_dir", "")
        p.textures = d.get("textures", [])
        p.tex_map = d.get("tex_map", {})
        p.base_color = d.get("base_color", "")
        p.baseline = p.spec.shared.clone()
        return p

    def _snap(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    def _restore(self, s: str):
        d = json.loads(s)
        np_ = Project.from_dict(d)
        self.name, self.spec, self.enabled = np_.name, np_.spec, np_.enabled
        self.project_root, self.export_dir, self.textures = np_.project_root, np_.export_dir, np_.textures
        self.tex_map = np_.tex_map
        self.base_color = np_.base_color

    def save(self, path=None):
        path = Path(path or self.path)
        if path is None:
            raise ValueError("Не указан путь")
        if path.exists():
            path.with_suffix(".rvproj.bak").write_bytes(path.read_bytes())
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=1), "utf-8")
        self.path, self.dirty = path, False

    @classmethod
    def load(cls, path) -> "Project":
        p = cls.from_dict(json.loads(Path(path).read_text("utf-8")))
        p.path = Path(path)
        return p

    # --- автосохранение / восстановление
    def autosave_path(self) -> Path:
        d = app_dir() / "recovery"
        d.mkdir(exist_ok=True)
        return d / f"{self.name}.autosave.rvproj"

    def autosave(self):
        if self.dirty:
            self.autosave_path().write_text(json.dumps(self.to_dict(), ensure_ascii=False), "utf-8")

    @staticmethod
    def recovery_files() -> list[Path]:
        d = app_dir() / "recovery"
        return sorted(d.glob("*.autosave.rvproj")) if d.exists() else []

    def clear_autosave(self):
        self.autosave_path().unlink(missing_ok=True)

    # --- изменения (все через _change для undo)
    def _change(self, label: str):
        now = time.monotonic()
        if label == self._last_change[0] and now - self._last_change[1] < 1.0 and self._undo:
            self._last_change = (label, now)          # слияние серии правок (перетаскивание ползунка)
            self.dirty = True
            return
        self._last_change = (label, now)
        self._undo.append((label, self._snap()))
        del self._undo[:-200]
        self._redo.clear()
        self.history.append(f"{time.strftime('%H:%M:%S')}  {label}")
        self.dirty = True

    def _notify(self):
        for cb in self.listeners:
            cb()

    def undo(self) -> bool:
        if not self._undo:
            return False
        label, snap = self._undo.pop()
        self._redo.append((label, self._snap()))
        self._restore(snap)
        self.history.append(f"{time.strftime('%H:%M:%S')}  Отмена: {label}")
        self.dirty = True
        self._notify()
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        label, snap = self._redo.pop()
        self._undo.append((label, self._snap()))
        self._restore(snap)
        self.history.append(f"{time.strftime('%H:%M:%S')}  Повтор: {label}")
        self.dirty = True
        self._notify()
        return True

    @property
    def can_undo(self): return bool(self._undo)
    @property
    def can_redo(self): return bool(self._redo)

    def set_base_color(self, path: str):
        """Базовый цвет (_co) для предпросмотра. В DayZ он приходит с текстуры грани модели, а не из RVMAT."""
        self._change("Base Color (превью): " + (Path(path).name if path else "—"))
        self.base_color = str(path or "")
        if path and str(path) not in self.textures:
            self.textures.append(str(path))
        self._notify()

    def rename(self, name: str):
        self._change(f"Имя проекта → {name}")
        self.name = self.spec.name = name
        self._notify()

    def set_value(self, target: str, path: str, value: Any, label: str = ""):
        """target: 'shared' | 'decorative' | значение State."""
        self._change(label or f"{target}: {path}")
        if target == TARGET_SHARED:
            apply_override(self.spec.shared, path, value)
        elif target == TARGET_DECOR:
            self.spec.decorative[path] = value
        else:
            self.spec.overrides.setdefault(State(target), {})[path] = value
        self._notify()

    def reset_value(self, target: str, path: str):
        """Снять переопределение (для shared не применимо)."""
        if target == TARGET_DECOR:
            d = self.spec.decorative
        elif target == TARGET_SHARED:
            return
        else:
            d = self.spec.overrides.get(State(target), {})
        if path in d:
            self._change(f"Сброс {target}: {path}")
            del d[path]
            self._notify()

    def is_overridden(self, target: str, path: str) -> bool:
        if target == TARGET_DECOR:
            return path in self.spec.decorative
        if target == TARGET_SHARED:
            return False
        return path in self.spec.overrides.get(State(target), {})

    def apply_material(self, m: Material, label="Применён материал/пресет", keep_textures=True):
        """Заменить общий материал (например, пресетом), сохранив назначенные текстуры."""
        self._change(label)
        if keep_textures:
            old = self.spec.shared
            for i in (1, 4, 5):
                s_old, s_new = old.stage(i), m.stage(i)
                if s_old and s_new and s_old.texture:
                    s_new.texture = s_old.texture
        self.spec.shared = m.clone()
        self.baseline = m.clone()
        self._notify()

    def effective(self, state: State) -> Material:
        return self.spec.render(state)

    def kit(self) -> dict[str, Material]:
        return self.spec.render_all([s for s in ALL_STATES if s in self.enabled])

    def set_enabled(self, state: State, on: bool):
        self._change(f"{'Включено' if on else 'Отключено'}: {state.value}")
        (self.enabled.add if on else self.enabled.discard)(state)
        self._notify()

    def validate(self):
        root = Path(self.project_root) if self.project_root else None
        return validate_kit(self.kit(), self.name, [s for s in ALL_STATES if s in self.enabled], root)

    def copy_stage(self, src_state: State, dst_state: State, stage: int):
        """Скопировать текстуру/настройки стадии одного состояния в другое (как переопределения)."""
        s = self.effective(src_state).stage(stage)
        if not s:
            return
        self._change(f"Копия Stage{stage}: {src_state.value} → {dst_state.value}")
        o = self.spec.overrides.setdefault(dst_state, {})
        o[f"Stage{stage}.texture"] = s.texture
        for k in ("aside", "up", "dir", "pos"):
            v = s.uv(k)
            if v:
                o[f"Stage{stage}.uvTransform.{k}"] = v
        self._notify()

    def import_files(self, paths) -> list[str]:
        """Импорт готовых RVMAT. Определяет состояние по суффиксу имени; decorative-отличия
        записываются как переопределения ветки. Возвращает сообщения для журнала."""
        from ..backend import kit_filename
        found: dict[State, Material] = {}
        stem0 = None
        for p in map(Path, paths):
            st, stem = State.BASE, p.stem
            for s in sorted(State, key=lambda s: -len(s.suffix)):
                if s.suffix and stem.lower().endswith(s.suffix):
                    st, stem = s, stem[:-len(s.suffix)]
                    break
            stem0 = stem0 or stem
            found[st] = Material.load(p)
        if not found:
            return ["Нет файлов"]
        self._change("Импорт RVMAT")
        pick = lambda order: next((found[s] for s in order if s in found), None)
        shared = pick([State.BASE, State.DAMAGE, State.DESTRUCT]) or pick(
            [State.DECORATIVE, State.DECORATIVE_DAMAGE, State.DECORATIVE_DESTRUCT])
        self.spec.shared = shared.clone()
        self.baseline = shared.clone()
        self.spec.decorative, self.spec.overrides = {}, {}
        msgs = [f"Импортировано {len(found)} файл(ов); основа: {'-'.join(s.value for s in found if found[s] is shared)}"]
        dec = pick([State.DECORATIVE, State.DECORATIVE_DAMAGE, State.DECORATIVE_DESTRUCT])
        if dec is not None and dec is not shared:
            a, b = flatten_values(shared), flatten_values(dec)
            for k, v in b.items():
                if k != "Stage3.texture" and a.get(k) != v:
                    self.spec.decorative[k] = v
            msgs.append(f"Decorative: {len(self.spec.decorative)} отличий записано как переопределения ветки")
        self.name = self.spec.name = stem0 or self.name
        self._notify()
        return msgs
