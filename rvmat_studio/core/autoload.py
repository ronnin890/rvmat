"""Загрузка проекта из папки: <меш>/<меш>.fbx + data/ (текстуры .paa/.png и .rvmat).

Обратная операция к экспорту: по именам файлов восстанавливаются материалы (по одному на группу RVMAT),
селекшены, имена пресетов, состояния, базовый цвет (_co/_ca), сопоставление игровых путей текстур с файлами
и параметры экспорта, так что повторный экспорт даёт те же имена и пути.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import safeio
from .logs import log_exc
from ..backend import Material, State
from ..backend.kit import flatten_values
from .project import Project, Slot
from .texnames import ALL_EXT, safe_name

MODEL_EXT = (".fbx", ".obj")
SKIP_DIRS = {"data", ".git", "__pycache__", "node_modules"}
_STATE_SUFFIXES = sorted([(s.suffix, s) for s in State if s.suffix], key=lambda x: -len(x[0]))


@dataclass
class Candidate:
    mesh_dir: Path
    mesh_name: str
    model: Optional[Path] = None
    data_dir: Optional[Path] = None
    rvmats: list[Path] = field(default_factory=list)
    textures: list[Path] = field(default_factory=list)

    def summary(self) -> str:
        return (f"{self.mesh_name}: " + ", ".join(x for x in (
            f"модель {self.model.name}" if self.model else "", f"текстур: {len(self.textures)}" if self.textures else "",
            f"RVMAT: {len(self.rvmats)}" if self.rvmats else "") if x))


def split_state(stem: str) -> tuple[str, State]:
    low = stem.lower()
    for suf, st in _STATE_SUFFIXES:
        if low.endswith(suf):
            return stem[:-len(suf)], st
    return stem, State.BASE


def _inspect(d: Path) -> Optional[Candidate]:
    """Папка меша: рядом с .fbx/.obj и/или с data/, где лежат rvmat/текстуры."""
    try:
        files = [p for p in d.iterdir() if p.is_file()]
        models = [p for p in files if p.suffix.lower() in MODEL_EXT]
        data = next((p for p in d.iterdir() if p.is_dir() and p.name.lower() == "data"), None)
    except OSError:
        return None
    inside = lambda p: safeio.is_within(p, d)                   # ссылки (symlink) наружу из папки меша игнорируются
    models = [p for p in models if inside(p)]
    rv, tx = [], []
    if data is not None and inside(data):
        try:
            rv = sorted(p for p in data.iterdir() if p.is_file() and p.suffix.lower() == ".rvmat" and inside(p))[:500]
            tx = sorted(p for p in data.iterdir() if p.is_file() and p.suffix.lower() in ALL_EXT and inside(p))[:2000]
        except OSError:
            pass
    if not models and not (rv or tx):
        return None
    model = None
    if models:                                                # предпочитаем .fbx и имя, совпадающее с папкой
        models.sort(key=lambda p: (p.suffix.lower() != ".fbx", p.stem.lower() != d.name.lower(), p.name.lower()))
        model = models[0]
    return Candidate(d, model.stem if model else d.name, model, data, rv, tx)


def scan_folder(folder, max_depth: int = 2, max_dirs: int = 400) -> list[Candidate]:
    """Выбранная папка — сама папка меша (приоритет) или содержит такие папки (до max_depth уровней вглубь)."""
    root = Path(folder)
    here = _inspect(root)
    if here is not None:
        return [here]
    out, seen = [], 0
    level = [root]
    for _ in range(max_depth):
        nxt = []
        for d in level:
            try:
                subs = sorted(p for p in d.iterdir() if p.is_dir() and not p.name.startswith(".") and p.name.lower() not in SKIP_DIRS)
            except OSError:
                continue
            for s in subs:
                seen += 1
                if seen > max_dirs:
                    return out
                c = _inspect(s)
                if c is not None:
                    out.append(c)
                else:
                    nxt.append(s)
        level = nxt
    return out


def is_pristine(proj: Project) -> bool:
    return not proj.dirty and not proj.model_path and not proj.textures and len(proj.slots) == 1 and not proj._undo


def _build_slot(paths: list[Path], base: str) -> tuple[Slot, set[State], list[str]]:
    """Один материал из группы RVMAT (разные состояния одного набора)."""
    found: dict[State, Material] = {}
    for p in paths:
        found[split_state(p.stem)[1]] = Material.load(p)
    pick = lambda order: next((found[s] for s in order if s in found), None)
    shared = pick([State.BASE, State.DAMAGE, State.DESTRUCT]) or pick([State.DECORATIVE, State.DECORATIVE_DAMAGE, State.DECORATIVE_DESTRUCT])
    slot = Slot(base, shared.clone())
    msgs = []
    dec = pick([State.DECORATIVE, State.DECORATIVE_DAMAGE, State.DECORATIVE_DESTRUCT])
    if dec is not None and dec is not shared:
        a, b = flatten_values(shared), flatten_values(dec)
        for k, v in b.items():
            if k != "Stage3.texture" and a.get(k) != v:
                slot.spec.decorative[k] = v
    # любые отличия остальных файлов от того, что даёт генератор из общей основы, сохраняются как переопределения состояния
    for st, m in found.items():
        want = flatten_values(slot.spec.render(st))
        for k, v in flatten_values(m).items():
            if k != "Stage3.texture" and want.get(k) != v:
                slot.spec.overrides.setdefault(st, {})[k] = v
    return slot, set(found), msgs


def _game_prefix(mesh: str, tex_strings: list[str], mesh_dir: Path) -> tuple[str, Optional[Path]]:
    """Из путей вида <префикс>\\<меш>\\data\\x.paa: префикс и корень P:/проекта, где лежит <префикс>/<меш>."""
    key = f"\\{mesh.lower()}\\data\\"
    for t in tex_strings:
        t = re.sub(r"^[A-Za-z]:[\\/]", "", t)                      # P:\\MyMod\\mesh\\data\\x.paa → MyMod\\mesh\\data\\x.paa
        low = t.lower().replace("/", "\\")
        i = low.find(key)
        if i >= 0 or low.startswith(key[1:]):
            prefix = t.replace("/", "\\")[:max(i, 0)].strip("\\")
            parts = [p for p in prefix.split("\\") if p]
            tail = [x.lower() for x in mesh_dir.parts[-(len(parts) + 1):-1]] if parts else []
            if parts and tail == [p.lower() for p in parts] and len(mesh_dir.parents) > len(parts):
                return prefix, mesh_dir.parents[len(parts)]
            if not parts:
                return "", mesh_dir.parent
            return prefix, None
    return "", None


def load_candidate(proj: Project, cand: Candidate, part_names: Optional[list[str]] = None) -> list[str]:
    """Заполняет проект из найденной папки (одним шагом отмены). part_names — части загруженной модели."""
    msgs: list[str] = []
    proj._change("Загрузка папки проекта")
    mesh = cand.mesh_name
    groups: dict[str, list[Path]] = {}
    names: dict[str, str] = {}
    for p in cand.rvmats:
        base = split_state(p.stem)[0]
        groups.setdefault(base.lower(), []).append(p)
        names.setdefault(base.lower(), base)
    order = sorted(groups, key=lambda k: (k != mesh.lower(), k))
    slots, enabled = [], set()
    for k in order:
        try:
            sl, sts, _ = _build_slot(groups[k], names[k])
        except Exception as e:
            log_exc("autoload.load_candidate")
            msgs.append(f"{names[k]}: не удалось разобрать RVMAT ({e})")
            continue
        slots.append(sl); enabled |= sts
    if not slots:
        slots = [Slot(mesh)]
    part_map: dict[str, str] = {}
    pk = {safe_name(p).lower(): p for p in (part_names or [])}
    for i, sl in enumerate(slots):
        b = sl.name
        rest = b[len(mesh) + 1:] if b.lower().startswith(mesh.lower() + "_") else ("" if b.lower() == mesh.lower() else b)
        if not rest:
            continue
        low = rest.lower()
        hit = next((k for k in sorted(pk, key=len, reverse=True) if low == k or low.startswith(k + "_")), None)
        if hit:
            sl.label = rest[:len(hit)]
            sl.preset_tag = rest[len(hit) + 1:]
            part_map[pk[hit]] = sl.name
        elif i == 0 and len(slots) == 1:
            sl.preset_tag = rest                        # единственный материал: «меш_пресет»
        else:
            sl.label = rest
    # базовый цвет и список текстур
    by_lower = {t.name.lower(): t for t in cand.textures}
    by_stem = {t.stem.lower(): t for t in cand.textures}
    for sl in slots:
        tex_base = mesh if (sl is slots[0] and not sl.label) else f"{mesh}_{safe_name(sl.label or sl.name)}"
        for suf in ("co", "ca"):
            f = by_stem.get(f"{tex_base}_{suf}".lower())
            if f is not None:
                sl.base_color = str(f)
                break
    # сопоставление игровых путей текстур из RVMAT с файлами из data/
    tex_map: dict[str, str] = {}
    tex_strings: list[str] = []
    for sl in slots:
        for st in State:
            for s in sl.spec.render(st).stages():
                t = s.texture or ""
                if t and not t.startswith("#("):
                    tex_strings.append(t)
                    f = by_lower.get(t.replace("/", "\\").split("\\")[-1].lower()) or by_stem.get(Path(t.replace("\\", "/")).stem.lower())
                    if f is not None:
                        tex_map[t.replace("/", "\\").strip("\\").lower()] = str(f)
    prefix, root = _game_prefix(mesh, tex_strings, cand.mesh_dir)
    if root is None:
        root = cand.mesh_dir.parent
    proj.slots, proj.active, proj.part_map = slots, 0, part_map
    proj.textures = [str(t) for t in cand.textures]
    proj.tex_map = tex_map
    proj.model_path = str(cand.model) if cand.model else ""
    proj.mesh_name = mesh
    proj.project_root = str(root)
    if enabled:
        proj.enabled = set(enabled)
    proj.export_opts = {"out_root": str(cand.mesh_dir.parent), "mesh_name": mesh, "prefix": prefix, "textures": True,
                        "fbx": bool(cand.model), "converter": "builtin",
                        "drive_paths": any(re.match(r"^[A-Za-z]:[\\/]", t) for t in tex_strings),
                        "states": [s.value for s in State if s in (enabled or {State.BASE, State.DAMAGE, State.DESTRUCT})]}
    if proj.title in ("newmaterial", "mymaterial", "") or len(proj.slots) >= 1:
        proj.title = mesh
    proj.dirty = True
    msgs.append(f"Загружено из {cand.mesh_dir}: " + cand.summary() + f"; материалов: {len(slots)}"
                + (f", селекшенов назначено: {len(part_map)}" if part_map else ""))
    if prefix:
        msgs.append(f"Префикс игровых путей: {prefix}")
    proj._notify()
    return msgs
