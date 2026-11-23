"""Менеджер пресетов: встроенная библиотека + пользовательские (JSON)."""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from ..backend import Material
from ..backend.procedural import make_color, make_fresnel
from . import limits, safeio
from .settings import app_dir

CATEGORIES = ["Металл", "Пластик", "Дерево", "Ткань", "Кожа", "Стекло", "Резина", "Камень", "Бетон", "Другие материалы"]

DAYZ_ENV = r"dz\data\data\env_land_co.paa"


def super_template(ambient=0.9, diffuse=0.9, specular=0.8, power=15, fresnel=(0.88, 1.89),
                   emissive=(0, 0, 0, 0), forced=(0, 0, 0, 1)) -> Material:
    """Каноническая структура Super / 7 стадий (как в образцах eyemask_*)."""
    m = Material()
    m.set_color("ambient", [ambient] * 3 + [1])
    m.set_color("diffuse", [diffuse] * 3 + [1])
    m.set_color("forcedDiffuse", list(forced))
    m.set_color("emmisive", list(emissive))
    m.set_color("specular", [specular] * 3 + [1])
    m.set_scalar("specularPower", power)
    m.set_string("PixelShaderID", "Super")
    m.set_string("VertexShaderID", "Super")
    tex = ["", make_color(0.5, 0.5, 0.5, 1, "DT"), make_color(0, 0, 0, 0, "MC"), "", "",
           make_fresnel(*fresnel), DAYZ_ENV]
    for i, t in enumerate(tex, 1):
        st = m.add_stage(i, t)
        if i == 2:
            st.filter = "Trilinear"
    return m


PRESET_ID = re.compile(r"^[0-9a-f]{12}$")          # id пользовательских пресетов (имя файла строится только из него)
PRESET_VERSION = 1


def new_id() -> str:
    return uuid.uuid4().hex[:12]


@dataclass
class Preset:
    name: str
    category: str
    description: str
    rvmat: str                      # полный текст RVMAT
    id: str = field(default_factory=new_id)
    builtin: bool = False
    favorite: bool = False
    version: int = PRESET_VERSION

    @classmethod
    def from_dict(cls, d, require_id: bool = True) -> "Preset":
        """Проверка типов/длин/id; неизвестные ключи игнорируются. Ошибка → SchemaError."""
        d = safeio.as_dict(d, "пресет")
        name = safeio.as_str(d.get("name"), "name", maxlen=200).strip()
        if not name:
            raise safeio.SchemaError("Пресет без названия")
        pid = d.get("id")
        if pid is None and not require_id:
            pid = new_id()
        if not isinstance(pid, str) or not PRESET_ID.match(pid):
            raise safeio.SchemaError("Недопустимый id пресета (ожидается 12 hex-символов)")
        rv = safeio.as_str(d.get("rvmat"), "rvmat", maxlen=limits.MAX_RVMAT_FILE)
        try:
            Material.from_text(rv)
        except ValueError as e:
            raise safeio.SchemaError(f"RVMAT пресета «{name}» не разобран: {e}") from e
        ver = safeio.as_int(d.get("version"), "version", PRESET_VERSION, 0, 10 ** 6)
        p = cls(name, safeio.as_str(d.get("category"), "category", "Другие материалы", 100) or "Другие материалы",
                safeio.as_str(d.get("description"), "description", "", 2000), rv, pid, False,
                safeio.as_bool(d.get("favorite"), "favorite"), ver)
        return p

    def material(self) -> Material:
        return Material.from_text(self.rvmat)


def _b(id_, name, cat, desc, **kw) -> Preset:
    return Preset(name, cat, desc, super_template(**kw).to_text(), id=id_, builtin=True)


# Значения — стартовые ориентиры для типа материала (НЕ официальные значения BI).
# Структура (Super, 7 стадий, пути env/fresnel/DT) взята из образцов eyemask_*.
BUILTIN = [
    _b("metal_polished", "Металл полированный", "Металл", "Высокий specular, резкий блик", specular=1.0, power=40, fresnel=(0.6, 3.0), ambient=0.8, diffuse=0.8),
    _b("metal_worn", "Металл потёртый", "Металл", "Средний блик, шероховатая поверхность", specular=0.6, power=20, fresnel=(0.7, 2.5)),
    _b("plastic_glossy", "Пластик глянцевый", "Пластик", "Широкий светлый блик", specular=0.8, power=30, fresnel=(1.4, 0.0)),
    _b("plastic_matte", "Пластик матовый", "Пластик", "Слабый блик", specular=0.3, power=8, fresnel=(1.4, 0.0)),
    _b("wood", "Дерево", "Дерево", "Матовая поверхность, минимальный блик", specular=0.15, power=6, fresnel=(1.3, 0.0)),
    _b("fabric", "Ткань", "Ткань", "Почти без блика", specular=0.05, power=2, fresnel=(1.3, 0.0)),
    _b("leather", "Кожа", "Кожа", "Мягкий блик", specular=0.35, power=10, fresnel=(1.4, 0.0)),
    _b("glass", "Стекло", "Стекло", "Сильные отражения", specular=1.0, power=60, fresnel=(1.5, 0.0), emissive=(0, 0, 0, 0)),
    _b("rubber", "Резина", "Резина", "Тёмный матовый материал", specular=0.1, power=4, fresnel=(1.4, 0.0)),
    _b("stone", "Камень", "Камень", "Шероховатый, слабый блик", specular=0.2, power=5, fresnel=(1.5, 0.0)),
    _b("concrete", "Бетон", "Бетон", "Матовый", specular=0.1, power=3, fresnel=(1.5, 0.0)),
    _b("generic", "Универсальный (как eyemask)", "Другие материалы", "Значения из образцов: specular 0.8, power 15, fresnel 0.88/1.89", ),
]


class PresetManager:
    def __init__(self, user_dir: Optional[Path] = None):
        self.dir = Path(user_dir) if user_dir else app_dir() / "presets"
        self.dir.mkdir(parents=True, exist_ok=True)
        self._fav_file = self.dir / "_favorites.json"
        self.items: dict[str, Preset] = {}
        self.reload()

    def reload(self):
        self.items = {p.id: Preset(**asdict(p)) for p in BUILTIN}
        self.warnings: list[str] = []
        favs = set()
        try:
            raw = safeio.read_json(self._fav_file, "Избранное")
            favs = set(safeio.str_list(raw, "favorites", limits.MAX_PRESETS))
        except (OSError, safeio.DataError):
            pass
        for n, f in enumerate(sorted(self.dir.glob("*.rvpreset"))):
            if n >= limits.MAX_PRESETS:
                self.warnings.append(f"Загружено не более {limits.MAX_PRESETS} пресетов")
                break
            try:
                p = Preset.from_dict(safeio.read_json(f, f.name))
                if f.stem != p.id:
                    raise safeio.SchemaError("имя файла не совпадает с id пресета")
                if p.version > PRESET_VERSION:
                    self.warnings.append(f"{f.name}: создан более новой версией программы")
                if p.id in {b.id for b in BUILTIN}:
                    raise safeio.SchemaError("id совпадает со встроенным пресетом")
                self.items[p.id] = p
            except (OSError, ValueError) as e:                  # DataError/SchemaError — ValueError
                self.warnings.append(f"{f.name}: пропущен ({e})")
                continue
        for i in favs:
            if i in self.items:
                self.items[i].favorite = True

    def _path(self, pid: str) -> Path:
        """Путь файла пресета — только из валидного id, строго внутри папки пресетов."""
        if not PRESET_ID.match(pid):
            raise safeio.PathError(f"Недопустимый id пресета: {pid!r}")
        return safeio.ensure_within(self.dir / f"{pid}.rvpreset", self.dir)

    def _persist(self, p: Preset):
        safeio.atomic_write(self._path(p.id), json.dumps(asdict(p), ensure_ascii=False, indent=1))

    def _save_favs(self):
        safeio.atomic_write(self._fav_file, json.dumps([i for i, p in self.items.items() if p.favorite]))

    def create(self, name, category, description, material: Material) -> Preset:
        p = Preset(name, category if category in CATEGORIES else "Другие материалы", description, material.to_text())
        self.items[p.id] = p
        self._persist(p)
        return p

    def update(self, pid: str, **kw):
        p = self.items[pid]
        if p.builtin:
            raise PermissionError("Встроенный пресет нельзя изменить; используйте копию")
        for k, v in kw.items():
            if isinstance(v, Material):
                k, v = "rvmat", v.to_text()
            setattr(p, k, v)
        self._persist(p)

    def duplicate(self, pid: str) -> Preset:
        src = self.items[pid]
        p = Preset(src.name[:190] + " (копия)", src.category, src.description, src.rvmat)
        self.items[p.id] = p
        self._persist(p)
        return p

    def delete(self, pid: str):
        p = self.items[pid]
        if p.builtin:
            raise PermissionError("Встроенный пресет нельзя удалить")
        del self.items[pid]
        self._path(pid).unlink(missing_ok=True)

    def set_favorite(self, pid: str, fav: bool):
        self.items[pid].favorite = fav
        self._save_favs()

    def search(self, text: str = "", category: str = "", favorites_only=False) -> list[Preset]:
        t = text.lower().strip()
        out = [p for p in self.items.values()
               if (not category or p.category == category) and (not favorites_only or p.favorite)
               and (not t or t in p.name.lower() or t in p.description.lower())]
        return sorted(out, key=lambda p: (CATEGORIES.index(p.category) if p.category in CATEGORIES else 99, p.name))

    def export_to(self, pids: list[str], path):
        data = [asdict(self.items[i]) for i in pids]
        safeio.atomic_write(path, json.dumps({"rvmat_studio_presets": PRESET_VERSION, "items": data}, ensure_ascii=False, indent=1))

    def import_from(self, path) -> list[Preset]:
        d = safeio.as_dict(safeio.read_json(path, "Файл пресетов"), "файл пресетов")
        ver = d.get("rvmat_studio_presets")
        if not isinstance(ver, int) or isinstance(ver, bool):
            raise safeio.SchemaError("Это не файл пресетов RVMAT Studio")
        items = safeio.as_list(d.get("items"), "items", 500)
        if len(self.items) + len(items) > limits.MAX_PRESETS + len(BUILTIN):
            raise safeio.SchemaError("Слишком много пресетов")
        out = []
        for it in items:
            it = dict(safeio.as_dict(it, "пресет"))
            it["id"] = new_id()                                   # id всегда новый — внешний id не используется
            p = Preset.from_dict(it)
            p.builtin = False
            self.items[p.id] = p
            self._persist(p)
            out.append(p)
        return out
