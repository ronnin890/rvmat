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
FORMAT_VERSION = 2


class Slot:
    """Один материал проекта (комплект RVMAT) — назначается частям (селекшенам) модели."""

    def __init__(self, name: str, material: Optional[Material] = None):
        self.spec = KitSpec(name, material or super_template())
        self.baseline = self.spec.shared.clone()     # для «сброса» общих параметров
        self.base_color = ""                          # физический файл _co для предпросмотра (в RVMAT НЕ пишется)
        self.normal_strength = 1.0                    # усиление нормали — только предпросмотр
        self.preset_tag = ""                          # id/имя применённого пресета (идёт в имя RVMAT при экспорте)
        self.label = ""                               # имя селекшена для имён файлов (пусто — по назначенной части)

    @property
    def name(self) -> str:
        return self.spec.name

    def to_dict(self) -> dict:
        return {"name": self.name, "rvmat": self.spec.shared.to_text(), "decorative": self.spec.decorative,
                "overrides": {s.value: v for s, v in self.spec.overrides.items() if v}, "base_color": self.base_color,
                "normal_strength": self.normal_strength, "preset_tag": self.preset_tag, "label": self.label}

    @classmethod
    def from_dict(cls, d: dict) -> "Slot":
        sl = cls(d["name"], Material.from_text(d["rvmat"]))
        sl.spec.decorative = d.get("decorative", {})
        sl.spec.overrides = {State(k): v for k, v in d.get("overrides", {}).items()}
        sl.base_color = d.get("base_color", "")
        sl.normal_strength = float(d.get("normal_strength", 1.0))
        sl.preset_tag, sl.label = d.get("preset_tag", ""), d.get("label", "")
        sl.baseline = sl.spec.shared.clone()
        return sl


def _safe_name(n: str) -> str:
    import re
    n = re.sub(r"[^\w\-.]+", "_", n.strip(), flags=re.UNICODE).strip("._")
    if not n:
        raise ValueError("Пустое имя материала")
    return n


class Project:
    def __init__(self, name="newmaterial", material: Optional[Material] = None):
        self.title = name                # имя проекта (файл .rvproj, заголовок окна)
        self.slots: list[Slot] = [Slot(name, material)]
        self.active = 0                  # редактируемый материал
        self.part_map: dict[str, str] = {}   # часть (селекшен) модели -> имя материала; нет записи = первый материал
        self.model_path = ""             # пользовательская модель для предпросмотра
        self.mesh_name = ""              # имя меша для экспорта (пусто — по файлу модели/проекту)
        self.export_opts: dict = {}      # последние настройки окна экспорта
        self.enabled: set[State] = set(ALL_STATES)
        self.project_root = ""           # P:\ или корень проекта: для проверки/пересчёта путей
        self.export_dir = ""
        self.textures: list[str] = []    # библиотека текстур (физические пути)
        self.tex_map: dict[str, str] = {}   # игровой путь -> физический файл (вне корня проекта)
        self._last_change = ("", 0.0)
        self.path: Optional[Path] = None
        self.dirty = False
        self._undo: list[tuple[str, str]] = []
        self._redo: list[tuple[str, str]] = []
        self.history: list[str] = []
        self.listeners: list = []

    # --- активный материал (совместимый API: proj.spec / proj.name / proj.baseline / proj.base_color)
    @property
    def slot(self) -> Slot:
        return self.slots[self.active]

    @property
    def spec(self) -> KitSpec:
        return self.slot.spec

    @spec.setter
    def spec(self, v):
        self.slot.spec = v

    @property
    def name(self) -> str:
        return self.slot.spec.name

    @name.setter
    def name(self, v):
        self.slot.spec.name = v

    @property
    def baseline(self) -> Material:
        return self.slot.baseline

    @baseline.setter
    def baseline(self, v):
        self.slot.baseline = v

    @property
    def base_color(self) -> str:
        return self.slot.base_color

    @base_color.setter
    def base_color(self, v):
        self.slot.base_color = v

    # --- сериализация
    def to_dict(self) -> dict:
        return {"version": FORMAT_VERSION, "title": self.title, "slots": [s.to_dict() for s in self.slots],
                "active": self.active, "part_map": self.part_map, "model_path": self.model_path,
                "mesh_name": self.mesh_name, "export_opts": self.export_opts,
                "enabled": sorted(s.value for s in self.enabled), "project_root": self.project_root,
                "export_dir": self.export_dir, "textures": self.textures, "tex_map": self.tex_map}

    @classmethod
    def from_dict(cls, d: dict) -> "Project":
        if "slots" in d:
            p = cls(d.get("title", "newmaterial"))
            p.slots = [Slot.from_dict(x) for x in d["slots"]]
            p.active = min(int(d.get("active", 0)), len(p.slots) - 1)
            p.part_map = dict(d.get("part_map", {}))
            p.model_path = d.get("model_path", "")
            p.mesh_name, p.export_opts = d.get("mesh_name", ""), dict(d.get("export_opts", {}))
        else:                                           # формат v1: один материал
            p = cls(d["name"], Material.from_text(d["rvmat"]))
            p.slots = [Slot.from_dict({"name": d["name"], "rvmat": d["rvmat"], "decorative": d.get("decorative", {}),
                                       "overrides": d.get("overrides", {}), "base_color": d.get("base_color", "")})]
        p.enabled = {State(s) for s in d.get("enabled", [s.value for s in ALL_STATES])}
        p.project_root, p.export_dir = d.get("project_root", ""), d.get("export_dir", "")
        p.textures = d.get("textures", [])
        p.tex_map = d.get("tex_map", {})
        return p

    def _snap(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    def _restore(self, s: str):
        d = json.loads(s)
        np_ = Project.from_dict(d)
        self.title, self.slots, self.active, self.part_map = np_.title, np_.slots, np_.active, np_.part_map
        self.model_path, self.enabled = np_.model_path, np_.enabled
        self.mesh_name, self.export_opts = np_.mesh_name, np_.export_opts
        self.project_root, self.export_dir, self.textures = np_.project_root, np_.export_dir, np_.textures
        self.tex_map = np_.tex_map

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
        return d / f"{self.title}.autosave.rvproj"

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
        key = (label, self.active)                    # серия правок сливается только внутри ОДНОГО материала
        if key == self._last_change[0] and now - self._last_change[1] < 1.0 and self._undo:
            self._last_change = (key, now)            # слияние серии правок (перетаскивание ползунка)
            self.dirty = True
            return
        self._last_change = (key, now)
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

    def set_normal_strength(self, v: float):
        """Сила нормали в предпросмотре активного материала (в RVMAT не записывается)."""
        self._change("Сила нормали (превью)")
        self.slot.normal_strength = max(0.0, float(v))
        self._notify()

    def rename(self, name: str):
        """Переименовать активный материал (меняются имена его файлов)."""
        name = _safe_name(name)
        if any(i != self.active and sl.name == name for i, sl in enumerate(self.slots)):
            raise ValueError(f"Материал «{name}» уже существует")
        self._change(f"Имя материала → {name}")
        old = self.slot.name
        self.slot.spec.name = name
        self.part_map = {k: (name if v == old else v) for k, v in self.part_map.items()}
        if len(self.slots) == 1:
            self.title = name
        self._notify()

    # --- материалы (слоты) и части (селекшены)
    def slot_index(self, name: str) -> int:
        return next((i for i, s in enumerate(self.slots) if s.name == name), -1)

    def set_active(self, i: int):
        if 0 <= i < len(self.slots) and i != self.active:
            self.active = i
            self._last_change = ("", 0.0)             # смена материала обрывает серию правок
            self._notify()

    def add_slot(self, name: str, copy_from: Optional[int] = None) -> int:
        name = _safe_name(name)
        if self.slot_index(name) >= 0:
            raise ValueError(f"Материал «{name}» уже существует")
        self._change(f"Новый материал: {name}")
        if copy_from is None:
            sl = Slot(name)
        else:
            src = self.slots[copy_from]
            sl = Slot.from_dict({**src.to_dict(), "name": name})
        self.slots.append(sl)
        self.active = len(self.slots) - 1
        self._notify()
        return self.active

    def remove_slot(self, i: int):
        if len(self.slots) <= 1:
            raise ValueError("Нельзя удалить единственный материал")
        name = self.slots[i].name
        self._change(f"Удалён материал: {name}")
        del self.slots[i]
        self.part_map = {k: v for k, v in self.part_map.items() if v != name}
        self.active = min(self.active, len(self.slots) - 1)
        self._notify()

    def assign_part(self, part: str, slot_name: Optional[str]):
        """Назначить материал части (None — вернуть на основной материал)."""
        if slot_name is not None and self.slot_index(slot_name) < 0:
            raise KeyError(slot_name)
        self._change(f"Часть «{part}» → {slot_name or 'основной'}")
        if slot_name is None or slot_name == self.slots[0].name:
            self.part_map.pop(part, None)
        else:
            self.part_map[part] = slot_name
        self._notify()

    def slot_for_part(self, part: str) -> Slot:
        i = self.slot_index(self.part_map.get(part, ""))
        return self.slots[i if i >= 0 else 0]

    def slot_effective(self, slot: Slot, state: State) -> Material:
        return slot.spec.render(state)

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

    def reset_to_baseline(self, target: str, path: str):
        """Кнопка ↺: для общего материала — исходное значение, для состояния/ветки — снять переопределение."""
        if target == TARGET_SHARED:
            base = flatten_values(self.baseline).get(path)
            if base is not None:
                self.set_value(target, path, base, f"Сброс {path}")
        else:
            self.reset_value(target, path)

    def is_overridden(self, target: str, path: str) -> bool:
        if target == TARGET_DECOR:
            return path in self.spec.decorative
        if target == TARGET_SHARED:
            return False
        return path in self.spec.overrides.get(State(target), {})

    def apply_material(self, m: Material, label="Применён материал/пресет", keep_textures=True, preset_tag=""):
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
        self.slot.preset_tag = preset_tag
        self._notify()

    def effective(self, state: State) -> Material:
        return self.spec.render(state)

    def slot_kit(self, slot: Slot) -> dict[str, Material]:
        return slot.spec.render_all([s for s in ALL_STATES if s in self.enabled])

    def kit(self) -> dict[str, Material]:
        """Комплект активного материала."""
        return self.slot_kit(self.slot)

    def kit_all(self) -> dict[str, Material]:
        """Комплекты всех материалов проекта."""
        out: dict[str, Material] = {}
        for sl in self.slots:
            out.update(self.slot_kit(sl))
        return out

    def set_enabled(self, state: State, on: bool):
        self._change(f"{'Включено' if on else 'Отключено'}: {state.value}")
        (self.enabled.add if on else self.enabled.discard)(state)
        self._notify()

    def validate(self):
        root = Path(self.project_root) if self.project_root else None
        out = []
        for sl in self.slots:
            out += validate_kit(self.slot_kit(sl), sl.name, [s for s in ALL_STATES if s in self.enabled], root)
        return sorted(out, key=lambda i: -i.severity)

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
