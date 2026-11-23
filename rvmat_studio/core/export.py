"""Экспорт «как в Blender»: одна операция, в диалоге — путь и что именно экспортировать.

Структура результата::

    <корень проекта>/<имя меша>/<имя меша>.fbx
    <корень проекта>/<имя меша>/data/<имя меша>[_<селекшен>]_nohq.paa, _as.paa, _smdi.paa, _co.paa …
    <корень проекта>/<имя меша>/data/<имя меша>[_<селекшен>][_<пресет>][_damage|_destruct|…].rvmat

План экспорта (plan_export) — чистая функция без записи на диск: список операций, конфликты и диагностика.
Выполнение (run_export) записывает всё: RVMAT, копии PAA, PNG/TGA→PAA (встроенный кодер или ImageToPAA из
DayZ Tools) и FBX.
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .logs import log_exc
from ..backend import ALL_STATES, Severity, State, kit_filename, validate
from . import fbx, fbxwrite, limits, meshes, paa_write, safeio, textures
from .project import Project, Slot
from .texnames import SOURCE_EXT, STAGE_SUFFIX, convert_with_tool, safe_name

DEFAULT_STATES = ["base", "damage", "destruct"]


@dataclass
class Op:
    kind: str                      # rvmat | texture | fbx
    dst: str
    text: str = ""                 # rvmat
    src: str = ""                  # texture / fbx
    mode: str = ""                 # texture: copy | convert | convert-tool ; fbx: copy | write
    note: str = ""
    role: str = ""                 # texture: nohq/as/smdi/co/...
    slot: str = ""

    def to_dict(self):
        return self.__dict__.copy()


@dataclass
class ExportPlan:
    out_dir: str
    data_dir: str
    mesh_name: str
    ops: list[Op] = field(default_factory=list)
    issues: list = field(default_factory=list)
    existing: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def has_errors(self):
        return any(i.severity == Severity.ERROR for i in self.issues)

    def to_dict(self):
        return {"out_dir": self.out_dir, "data_dir": self.data_dir, "mesh_name": self.mesh_name,
                "ops": [o.to_dict() for o in self.ops], "existing": self.existing, "notes": self.notes,
                "issues": [{"severity": i.severity.name, "code": i.code, "message": i.message, "location": i.location} for i in self.issues]}


def default_mesh_name(proj: Project) -> str:
    if proj.mesh_name:
        return safe_name(proj.mesh_name)
    if proj.model_path:
        return safe_name(Path(proj.model_path).stem)
    return safe_name(proj.title)


def default_prefix(out_root: str, p_drive: str = "P:\\") -> str:
    """Игровой префикс: путь корня относительно диска P: (P:\\MyMod -> 'MyMod'; сам P:\\ -> '').
    Если корень вне P: — для путей Windows всё после диска, иначе имя последней папки."""
    from pathlib import PurePath, PureWindowsPath
    s = str(out_root)
    P = PureWindowsPath if (len(s) > 1 and s[1] == ":") or "\\" in s else PurePath
    root = P(s)
    try:
        return "\\".join(root.relative_to(P(p_drive)).parts)
    except ValueError:
        pass
    if P is PureWindowsPath:
        return "\\".join(root.parts[1:])
    return root.name


def slot_names(proj: Project, mesh: str) -> list[tuple[str, str]]:
    """[(имя для текстур, имя для RVMAT)] по материалам проекта."""
    out, used = [], set()
    multi = len(proj.slots) > 1
    for sl in proj.slots:
        tex = mesh
        if multi and (sl is not proj.slots[0] or sl.label):          # первый материал покрывает «остальное» и называется по мешу
            parts = [p for p, sn in proj.part_map.items() if sn == sl.name]
            label = sl.label or (parts[0] if parts else sl.name)
            tex = f"{mesh}_{safe_name(label)}"
            base, n = tex, 2
            while tex.lower() in used:                       # одинаковые метки — нумеруем
                tex, n = f"{base}_{n}", n + 1
        used.add(tex.lower())
        rv = tex + (f"_{safe_name(sl.preset_tag)}" if sl.preset_tag else "")
        out.append((tex, rv))
    return out


def plan_export(proj: Project, opts: dict) -> ExportPlan:
    """opts: out_root, mesh_name, textures(bool), fbx(bool), states([...]), prefix, converter('builtin'|'tool'), image_to_paa."""
    out_root = opts.get("out_root") or proj.project_root
    if not out_root:
        raise ValueError("Не указана папка проекта")
    mesh = safe_name(opts.get("mesh_name") or default_mesh_name(proj))
    out_dir = Path(out_root) / mesh
    data = out_dir / "data"
    prefix = opts["prefix"] if opts.get("prefix") is not None else default_prefix(out_root, opts.get("p_drive") or "P:\\")
    prefix = prefix.strip("\\/ ").replace("/", "\\")
    base_game = (prefix + "\\" if prefix else "") + mesh + "\\data\\"
    states = [State(s) for s in opts.get("states", DEFAULT_STATES) if s in {x.value for x in State}]
    plan = ExportPlan(str(out_dir), str(data), mesh)
    names = slot_names(proj, mesh)
    want_tex = bool(opts.get("textures", True))
    converter = opts.get("converter", "builtin")
    tex_ops: dict[str, Op] = {}                                  # dst -> Op (дедупликация одинаковых файлов)
    src_to_dst: dict[tuple[str, str], str] = {}                  # (slot, source path) -> game path

    def export_texture(slot_i: int, role: str, src: Path) -> str:
        """Имя в data/ для исходного файла; один и тот же файл у материала экспортируется один раз."""
        tex_base = names[slot_i][0]
        key = (tex_base, str(src))
        if key in src_to_dst:
            return src_to_dst[key]
        stem, n = f"{tex_base}_{role}", 2
        dst_name = f"{stem}.paa"
        taken = {Path(o.dst).name.lower() for o in tex_ops.values()}
        while dst_name.lower() in taken:
            dst_name, n = f"{stem}{n}.paa", n + 1
        dst = str(data / dst_name)
        ext = src.suffix.lower()
        if ext in (".paa", ".pac"):
            mode = "copy"
        elif converter == "tool" and opts.get("image_to_paa"):
            mode = "convert-tool"
        else:
            mode = "convert"
        tex_ops[dst] = Op("texture", dst, src=str(src), mode=mode, role=role, slot=tex_base,
                          note="PNG/TGA → PAA" if mode != "copy" else "копия PAA")
        src_to_dst[key] = base_game + dst_name
        return src_to_dst[key]

    rvmats: list[Op] = []
    for si, slot in enumerate(proj.slots):
        # базовый цвет (_co/_ca) — не в RVMAT, но кладётся в data/ рядом
        if want_tex and slot.base_color and Path(slot.base_color).is_file():
            suf = "ca" if Path(slot.base_color).stem.lower().endswith("_ca") else "co"
            export_texture(si, suf, Path(slot.base_color))
        for state in states:
            m = proj.slot_effective(slot, state)
            for st in m.stages():
                tex = st.texture or ""
                if not want_tex or not tex or tex.startswith("#("):
                    continue
                src = proj.find_texture(tex)
                if src is None:
                    continue
                role = STAGE_SUFFIX.get(st.index, f"stage{st.index}")
                st.texture = export_texture(si, role, src)
            for i in validate(m, None, state):
                if i.severity >= Severity.WARNING:
                    i.location = f"{names[si][1]}{state.suffix}.rvmat:{i.location}"
                    plan.issues.append(i)
            fn = kit_filename(names[si][1], state)
            rvmats.append(Op("rvmat", str(data / fn), text=m.to_text(), slot=names[si][0], note=f"{slot.name} · {state.value}"))
    plan.ops = list(tex_ops.values()) + rvmats
    if opts.get("fbx", True):
        if proj.model_path and Path(proj.model_path).is_file():
            mode = "copy" if proj.model_path.lower().endswith(".fbx") else "write"
            plan.ops.insert(0, Op("fbx", str(out_dir / f"{mesh}.fbx"), src=proj.model_path, mode=mode,
                                  note="копия исходного FBX" if mode == "copy" else "OBJ → FBX"))
        else:
            plan.notes.append("Меш не экспортирован: загрузите свою модель (OBJ/FBX) — встроенные тестовые формы не экспортируются.")
    if want_tex:
        for sl in proj.slots:
            for st in sl.spec.shared.stages():
                t = st.texture or ""
                if (t and not t.startswith("#(") and st.index in STAGE_SUFFIX and not t.lower().startswith(("dz\\", "data\\"))
                        and not proj.find_texture(t)):
                    plan.notes.append(f"{sl.name}: Stage{st.index} «{t}» не найден на диске — путь оставлен как есть")
    for o in plan.ops:                                           # каждая запись обязана остаться внутри папки экспорта
        try:
            safeio.ensure_within(o.dst, out_dir)
        except safeio.PathError as e:
            raise ExportError(f"Путь вывода вне папки экспорта: {o.dst}") from e
    plan.existing = [o.dst for o in plan.ops if Path(o.dst).exists()]
    return plan


# ------------------------------------------------------------------ выполнение
class ExportError(RuntimeError):
    pass


@dataclass
class ExportResult:
    written: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _backup_and_prepare(dst: Path, overwrite: bool):
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        if not overwrite:
            raise FileExistsError(str(dst))
        shutil.copy2(dst, dst.with_name(dst.name + ".bak"))


def _load_model(path: str):
    return fbx.load_fbx(path) if path.lower().endswith(".fbx") else meshes.load_obj(path)


def run_op(op: Op, overwrite: bool, image_to_paa: str = "", root: Optional[str] = None) -> str:
    """Выполняет одну операцию; возвращает короткое примечание (или пусто).
    root — папка экспорта: путь записи обязан лежать внутри неё (иначе ExportError). Запись атомарная."""
    dst = Path(op.dst)
    if root is not None:
        try:
            safeio.ensure_within(dst, root)
        except safeio.PathError as e:
            raise ExportError(f"Путь вывода вне папки экспорта: {dst}") from e
    _backup_and_prepare(dst, overwrite)
    if op.kind == "rvmat":
        safeio.atomic_write(dst, op.text.encode("utf-8"))
    elif op.kind == "texture" and op.mode == "copy":
        safeio.atomic_write(dst, limits.read_limited(op.src, limits.MAX_PAA_FILE, "PAA"))
    elif op.kind == "texture" and op.mode == "convert-tool":
        tmp = dst.with_name("." + dst.stem + "__tmp" + Path(op.src).suffix)
        out = dst.with_name("." + dst.stem + "__out.paa")
        shutil.copyfile(op.src, tmp)
        try:
            convert_with_tool(tmp, image_to_paa, out)
            os.replace(out, dst)
        finally:
            tmp.unlink(missing_ok=True)
            out.unlink(missing_ok=True)
    elif op.kind == "texture":
        rgba, _ = textures.load_rgba(op.src)
        data, info = paa_write.encode_paa(rgba, op.role)
        safeio.atomic_write(dst, data)
        return f"{info['kind']} {info['width']}×{info['height']}" + (" (размер округлён до степени двойки)" if info["resized"] else "")
    elif op.kind == "fbx":
        if op.mode == "copy":
            safeio.atomic_write(dst, limits.read_limited(op.src, limits.MAX_FBX_FILE, "FBX"))
        else:
            fbxwrite.write_fbx(_load_model(op.src), dst, dst.stem)
    return ""


def run_export(plan: ExportPlan, overwrite: bool = False, image_to_paa: str = "", progress=None) -> ExportResult:
    """Выполняет план. progress(i, n, op) вызывается перед каждой операцией."""
    res = ExportResult()
    for i, op in enumerate(plan.ops):
        if progress:
            progress(i, len(plan.ops), op)
        try:
            note = run_op(op, overwrite, image_to_paa, plan.out_dir)
            res.written.append(op.dst)
            if note:
                res.notes.append(f"{Path(op.dst).name}: {note}")
        except FileExistsError as e:
            res.errors.append(f"Файл уже существует: {e}")
        except Exception as e:                                   # одна ошибка не должна обрывать весь экспорт
            log_exc("export.run_export")
            res.errors.append(f"{Path(op.dst).name}: {type(e).__name__}: {e}")
    return res
