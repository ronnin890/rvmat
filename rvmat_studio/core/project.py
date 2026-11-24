"""Проект: общий материал + переопределения по состояниям, undo/redo, автосохранение."""
from __future__ import annotations

import copy
import json
import time
from pathlib import Path
from typing import Any, Optional

from ..backend import ALL_STATES, KitSpec, Material, State, validate_kit
from ..backend.kit import apply_override, flatten_values
from . import limits, safeio, texnames
from .presets import super_template
from .safeio import SchemaError
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
        d = safeio.as_dict(d, "slot")
        name = safeio.as_str(d.get("name"), "slot.name", maxlen=200).strip()
        if not name:
            raise SchemaError("Поле «slot.name»: пустое имя материала")
        try:
            mat = Material.from_text(safeio.as_str(d.get("rvmat"), "slot.rvmat", maxlen=limits.MAX_RVMAT_FILE))
        except ValueError as e:
            raise SchemaError(f"RVMAT материала «{name}» не разобран: {e}") from e
        sl = cls(name, mat)
        sl.spec.decorative = _flat_overrides(d.get("decorative"), "slot.decorative")
        ov = safeio.as_dict(d.get("overrides"), "slot.overrides")
        out = {}
        for k, v in ov.items():
            try:
                out[State(k)] = _flat_overrides(v, f"slot.overrides.{k}")
            except ValueError:
                if isinstance(v, dict):
                    raise SchemaError(f"Поле «slot.overrides»: неизвестное состояние {k!r}") from None
                raise
        sl.spec.overrides = out
        sl.base_color = safeio.as_str(d.get("base_color"), "slot.base_color")
        sl.normal_strength = safeio.as_float(d.get("normal_strength"), "slot.normal_strength", 1.0, 0.0, 10.0)
        sl.preset_tag = safeio.as_str(d.get("preset_tag"), "slot.preset_tag", maxlen=200)
        sl.label = safeio.as_str(d.get("label"), "slot.label", maxlen=200)
        sl.baseline = sl.spec.shared.clone()
        return sl


def _flat_overrides(v, name: str) -> dict:
    """{«Stage1.texture»: значение} — значения скаляры/списки скаляров ограниченной длины."""
    d = safeio.as_dict(v, name)
    if len(d) > 2000:
        raise SchemaError(f"Поле «{name}»: слишком много записей")
    out = {}
    for k, x in d.items():
        k = safeio.as_str(k, name + " (ключ)", maxlen=200)
        if isinstance(x, list):
            if len(x) > 64 or not all(isinstance(i, (str, int, float)) and not isinstance(i, bool) for i in x):
                raise SchemaError(f"Поле «{name}.{k}»: недопустимый массив")
        elif isinstance(x, str):
            safeio.as_str(x, f"{name}.{k}")
        elif isinstance(x, bool) or not isinstance(x, (int, float)):
            raise SchemaError(f"Поле «{name}.{k}»: недопустимое значение")
        out[k] = x
    return out


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
        self.load_warnings: list[str] = []              # предупреждения при открытии (новая версия формата и т. п.)
        self.trusted_dirs: list[str] = []               # папки текстур, явно добавленные пользователем (на время сессии)
        self.path_warnings: list[str] = []              # отклонённые небезопасные пути (для журнала)
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

    EXPORT_OPT_KEYS = {"out_root": str, "mesh_name": str, "prefix": str, "textures": bool, "fbx": bool, "drive_paths": bool, "converter": str}

    @classmethod
    def from_dict(cls, d: dict) -> "Project":
        """Проверяет типы, диапазоны и лимиты; неизвестные ключи игнорируются; неверные типы → SchemaError."""
        d = safeio.as_dict(d, "проект")
        warnings = []
        ver = d.get("version", 1)
        if isinstance(ver, int) and not isinstance(ver, bool) and ver > FORMAT_VERSION:
            warnings.append(f"Проект создан более новой версией программы (формат {ver}, поддерживается {FORMAT_VERSION}): часть данных может быть потеряна")
        if "slots" in d:
            p = cls(safeio.as_str(d.get("title"), "title", "newmaterial", 200) or "newmaterial")
            raw = safeio.as_list(d["slots"], "slots", limits.MAX_SLOTS)
            if not raw:
                raise SchemaError("В проекте нет ни одного материала")
            p.slots = [Slot.from_dict(x) for x in raw]
            p.active = safeio.as_int(d.get("active"), "active", 0, 0, len(p.slots) - 1) if (isinstance(d.get("active"), int) and 0 <= d.get("active") < len(p.slots)) else 0
            p.part_map = safeio.str_dict(d.get("part_map"), "part_map", limits.MAX_PARTS)
            p.model_path = safeio.as_str(d.get("model_path"), "model_path")
            p.mesh_name = safeio.as_str(d.get("mesh_name"), "mesh_name", maxlen=200)
            eo = safeio.as_dict(d.get("export_opts"), "export_opts")
            p.export_opts = {}
            for k, t in cls.EXPORT_OPT_KEYS.items():             # неизвестные ключи (в т. ч. пути к exe) отбрасываются
                if k in eo:
                    v = eo[k]
                    if not isinstance(v, t):
                        raise SchemaError(f"Поле «export_opts.{k}»: неверный тип")
                    p.export_opts[k] = safeio.as_str(v, f"export_opts.{k}") if t is str else v
            if "states" in eo:
                p.export_opts["states"] = [x for x in safeio.str_list(eo["states"], "export_opts.states", 16) if x in {s_.value for s_ in State}]
        else:                                           # формат v1: один материал
            name = safeio.as_str(d.get("name"), "name", maxlen=200)
            if not name:
                raise SchemaError("Не найдены данные проекта (поля «slots» или «name»)")
            p = cls(name)
            p.slots = [Slot.from_dict({"name": name, "rvmat": d.get("rvmat"), "decorative": d.get("decorative", {}),
                                       "overrides": d.get("overrides", {}), "base_color": d.get("base_color", "")})]
            p.title = name
        valid = {s_.value for s_ in State}
        en = safeio.str_list(d.get("enabled", [s_.value for s_ in ALL_STATES]), "enabled", 16)
        p.enabled = {State(x) for x in en if x in valid}
        p.project_root = safeio.as_str(d.get("project_root"), "project_root")
        p.export_dir = safeio.as_str(d.get("export_dir"), "export_dir")
        p.textures = safeio.str_list(d.get("textures"), "textures")
        p.tex_map = safeio.str_dict(d.get("tex_map"), "tex_map")
        p.load_warnings = warnings
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
        safeio.atomic_write(path, json.dumps(self.to_dict(), ensure_ascii=False, indent=1), backup=True)
        self.path, self.dirty = path, False

    @classmethod
    def load(cls, path) -> "Project":
        p = cls.from_dict(safeio.read_json(path, "Проект"))
        p.path = Path(path)
        return p

    # --- пути за пределами папки проекта
    def external_refs(self, base_dir=None) -> list[tuple[str, str]]:
        """Ссылки на файлы/папки вне папки самого проекта (base_dir; по умолчанию — папка файла .rvproj):
        [(поле, значение)]. Корень проекта, содержащий папку проекта (например P:\\), — норма и не считается внешним."""
        base = Path(base_dir) if base_dir else (self.path.parent if self.path else None)
        if base is None:
            return []
        inside = lambda x: bool(x) and safeio.is_within(x, base)
        root_ok = bool(self.project_root) and (inside(self.project_root) or safeio.is_within(base, self.project_root))
        zone = [str(base)] + ([self.project_root] if root_ok else [])
        ok = lambda x: any(safeio.is_within(x, z) for z in zone)
        out: list[tuple[str, str]] = []
        if self.project_root and not root_ok:
            out.append(("Корень проекта", self.project_root))
        for label, v in (("Папка экспорта", self.export_dir), ("Папка экспорта (настройки)", self.export_opts.get("out_root", "")),
                         ("Модель", self.model_path)):
            if v and not ok(v):
                out.append((label, v))
        out += [("Текстура", t) for t in self.textures if t and not ok(t)]
        out += [(f"Путь {k}", v) for k, v in self.tex_map.items() if v and not ok(v)]
        out += [(f"Base Color «{sl.name}»", sl.base_color) for sl in self.slots if sl.base_color and not ok(sl.base_color)]
        return out

    def drop_refs(self, refs) -> None:
        """Убрать перечисленные внешние ссылки из проекта (пользователь отказался им доверять)."""
        bad = {v for _, v in refs}
        if self.project_root in bad:
            self.project_root = ""
        if self.export_dir in bad:
            self.export_dir = ""
        if self.export_opts.get("out_root") in bad:
            self.export_opts.pop("out_root", None)
        if self.model_path in bad:
            self.model_path = ""
        self.textures = [t for t in self.textures if t not in bad]
        self.tex_map = {k: v for k, v in self.tex_map.items() if v not in bad}
        for sl in self.slots:
            if sl.base_color in bad:
                sl.base_color = ""

    def trust_dir(self, path) -> None:
        d = str(Path(path).parent if Path(path).suffix else Path(path))
        if d and d not in self.trusted_dirs:
            self.trusted_dirs.append(d)

    def allowed_roots(self, extra=()) -> list[str]:
        roots = [self.project_root, str(self.path.parent) if self.path else "", self.export_dir, *self.trusted_dirs, *extra]
        return [r for r in roots if r]

    def find_texture(self, tex: str, extra=()):
        """Игровой путь → физический файл с проверкой песочницы (см. texnames.find_texture)."""
        def warn(m):
            if m not in self.path_warnings:
                self.path_warnings.append(m)
        return texnames.find_texture(tex, self.project_root, self.tex_map, (), self.allowed_roots(extra), warn)

    # --- автосохранение / восстановление
    def autosave_path(self) -> Path:
        d = app_dir() / "recovery"
        d.mkdir(exist_ok=True)
        return safeio.ensure_within(d / f"{texnames.safe_name(self.title)}.autosave.rvproj", d)

    def autosave(self):
        if self.dirty:
            safeio.atomic_write(self.autosave_path(), json.dumps(self.to_dict(), ensure_ascii=False))

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
