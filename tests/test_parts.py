"""Селекшены: части модели, материалы-слоты проекта, рендер по частям, выбор кликом, экспорт."""
import json
import struct
import time
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from rvmat_studio.backend import Severity, State
from rvmat_studio.core import export as ex
from rvmat_studio.core.fbx import MAGIC, load_fbx
from rvmat_studio.core.meshes import SHAPES, get_shape, load_obj
from rvmat_studio.core.presets import super_template
from rvmat_studio.core.project import Project
from rvmat_studio.core.render import RenderCache, RenderOptions, make_resolver, pick_part, render
from tests.test_fbx import _node


# ------------------------------------------------------------------ части модели
def test_primitives_have_named_parts_covering_all_faces():
    for name, expect in (("Сфера", 2), ("Куб", 6), ("Цилиндр", 3), ("Тестовая модель", 2)):
        m = get_shape(name)
        assert m.n_parts == expect, name
        assert set(np.unique(m.face_part)) == set(range(expect))
        assert sum(c for _, c in m.part_ranges()) == len(m.f)
        # грани отсортированы по частям: диапазоны непрерывны
        for i, (s, c) in enumerate(m.part_ranges()):
            assert (m.face_part[s:s + c] == i).all()
    assert get_shape("Плоскость").part_names is None and get_shape("Тор").part_names is None


def test_obj_groups_become_parts(tmp_path):
    f = tmp_path / "m.obj"
    f.write_text("v 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\nv 2 0 0\nv 2 1 0\n"
                 "g body\nf 1 2 3 4\ng glass\nf 2 5 6 3\n")
    m = load_obj(f)
    assert m.part_names == ["body", "glass"] and len(m.f) == 4
    assert (m.face_part == [0, 0, 1, 1]).all()


def test_obj_usemtl_fallback_and_single_group_means_no_parts(tmp_path):
    f = tmp_path / "m.obj"
    f.write_text("v 0 0 0\nv 1 0 0\nv 0 1 0\nv 1 1 0\nusemtl a\nf 1 2 3\nusemtl b\nf 2 4 3\n")
    assert load_obj(f).part_names == ["a", "b"]
    g = tmp_path / "one.obj"
    g.write_text("v 0 0 0\nv 1 0 0\nv 0 1 0\ng only\nf 1 2 3\n")
    assert load_obj(g).part_names is None


def _two_geom_fbx(path):
    def geom(gid, name, ox):
        verts = np.array([ox, 0, 0, ox + 1, 0, 0, ox + 1, 1, 0, ox, 1, 0], float)
        return ("Geometry", [gid, name + "\x00\x01Geometry", "Mesh"],
                [("Vertices", [verts], []), ("PolygonVertexIndex", [np.array([0, 1, 2, -4], np.int32)], [])])
    top = [("Objects", [], [geom(1, "hull", 0), geom(2, "window", 2)])]
    data = MAGIC + struct.pack("<I", 7400)
    for t in top:
        data += _node(*t, start=len(data), wide=False)
    path.write_bytes(data + b"\x00" * 13)


def test_fbx_geometries_become_parts(tmp_path):
    f = tmp_path / "two.fbx"; _two_geom_fbx(f)
    m = load_fbx(f)
    assert m.part_names == ["hull", "window"] and len(m.f) == 4 and set(m.face_part) == {0, 1}


# ------------------------------------------------------------------ слоты проекта
def test_slots_add_rename_remove_and_part_assignment():
    p = Project("helmet")
    assert [s.name for s in p.slots] == ["helmet"] and p.name == "helmet" and p.title == "helmet"
    i = p.add_slot("helmet_glass")
    assert i == 1 and p.active == 1 and p.name == "helmet_glass"
    with pytest.raises(ValueError):
        p.add_slot("helmet_glass")
    with pytest.raises(ValueError):
        p.add_slot("   ")
    p.assign_part("visor", "helmet_glass")
    assert p.slot_for_part("visor").name == "helmet_glass" and p.slot_for_part("other").name == "helmet"
    p.rename("glass2")                                           # переименование обновляет назначения
    assert p.part_map == {"visor": "glass2"} and p.title == "helmet"
    p.assign_part("visor", None)
    assert p.part_map == {}
    p.assign_part("visor", "glass2")
    p.remove_slot(1)
    assert [s.name for s in p.slots] == ["helmet"] and p.part_map == {} and p.active == 0
    with pytest.raises(ValueError):
        p.remove_slot(0)
    with pytest.raises(KeyError):
        p.assign_part("x", "nope")


def test_slots_are_independent_materials_with_own_edits_and_base_color():
    p = Project("m")
    p.set_value("shared", "specularPower", 33)
    p.add_slot("m_b", copy_from=0)                                # копия берёт текущие значения
    assert p.spec.shared.get("specularPower") == 33
    p.set_value("shared", "specularPower", 99)
    p.set_base_color("/x/b_co.png")
    p.set_active(0)
    assert p.spec.shared.get("specularPower") == 33 and p.base_color == ""
    p.set_active(1)
    assert p.spec.shared.get("specularPower") == 99 and p.base_color == "/x/b_co.png"
    assert set(p.kit_all()) == {f"{n}{s}.rvmat" for n in ("m", "m_b") for s in ("", "_damage", "_destruct", "_decorative", "_decorative_damage", "_decorative_destruct")}
    assert len(p.kit()) == 6                                      # kit() — только активный


def test_slots_undo_redo_and_persistence(tmp_path):
    p = Project("m")
    p.add_slot("m_b")
    p.assign_part("p1", "m_b")
    p.model_path = "/models/x.obj"
    f = tmp_path / "p.rvproj"; p.save(f)
    q = Project.load(f)
    assert [s.name for s in q.slots] == ["m", "m_b"] and q.active == 1 and q.part_map == {"p1": "m_b"} and q.model_path == "/models/x.obj"
    assert json.loads(f.read_text("utf-8"))["version"] == 2
    p.undo()                                                      # отмена назначения части
    assert p.part_map == {}
    p.undo()                                                      # отмена нового материала
    assert [s.name for s in p.slots] == ["m"]
    p.redo(); p.redo()
    assert [s.name for s in p.slots] == ["m", "m_b"] and p.part_map == {"p1": "m_b"}


def test_old_v1_project_files_still_open(tmp_path):
    old = Project("legacy")
    old.set_value("shared", "specularPower", 21)
    d = {"version": 1, "name": "legacy", "rvmat": old.spec.shared.to_text(), "decorative": {}, "overrides": {},
         "enabled": ["base", "damage"], "project_root": "", "export_dir": "", "textures": [], "tex_map": {}, "base_color": "/a_co.png"}
    f = tmp_path / "old.rvproj"; f.write_text(json.dumps(d), "utf-8")
    p = Project.load(f)
    assert p.title == "legacy" and len(p.slots) == 1 and p.spec.shared.get("specularPower") == 21
    assert p.base_color == "/a_co.png" and len(p.kit()) == 2


def test_import_files_goes_into_active_slot():
    S = Path(__file__).parent / "samples"
    p = Project("m")
    p.add_slot("m_b")
    p.import_files([S / "eyemask_damage.rvmat"])
    assert p.slots[0].spec.shared.stage(1).texture == ""            # первый материал не затронут
    assert p.slots[1].spec.shared.stage(1).texture.endswith("eyemask_black_nohq.paa")


# ------------------------------------------------------------------ рендер по частям
def solid(rgb):
    return np.tile(np.array([*rgb, 255], np.float32) / 255, (8, 8, 1))


def test_cpu_renders_each_part_with_its_own_material_and_base_color():
    mesh = get_shape("Сфера")                                       # части: нижняя(0) / верхняя(1)
    res = make_resolver(None)
    red, blue = solid((230, 20, 20)), solid((20, 20, 230))
    m = super_template()
    o = RenderOptions(width=200, height=200, view="albedo", pitch=0, yaw=0)
    img = render(m, o, res, mesh, RenderCache(), {0: (m, blue), 1: (m, red)})
    top, bottom = img[50, 100], img[150, 100]
    assert top[0] > 150 and top[2] < 80 and bottom[2] > 150 and bottom[0] < 80    # верх красный, низ синий
    # другой материал на части: разная specular → разный итоговый кадр, но albedo одинаков
    hard, soft = super_template(power=120, specular=1.0), super_template(power=2, specular=0.0)
    a = render(m, RenderOptions(width=200, height=200), res, mesh, None, {0: (hard, None), 1: (hard, None)})
    b = render(m, RenderOptions(width=200, height=200), res, mesh, None, {0: (hard, None), 1: (soft, None)})
    assert np.abs(a[:100].astype(int) - b[:100].astype(int)).mean() > 0.3 and (a[110:] == b[110:]).all() is not None


def test_cpu_parts_cache_and_unassigned_parts_use_default():
    mesh, res = get_shape("Куб"), make_resolver(None)
    m = super_template(); c = RenderCache()
    o = RenderOptions(width=120, height=120, view="albedo", yaw=30, pitch=-20)
    only_px = {5: (m, solid((250, 10, 10)))}                       # только +Z (смотрит на камеру) красная, остальное — по умолчанию (серый)
    a = render(m, o, res, mesh, c, only_px)
    b = render(m, o, res, mesh, c, only_px)                          # кэш
    assert (a == b).all()
    red = (a[..., 0].astype(int) - a[..., 2].astype(int)) > 100
    r_, g_, b_ = (a[..., i].astype(int) for i in range(3))
    grey = (abs(r_ - b_) < 6) & (abs(r_ - g_) < 6) & (r_ > 160) & (r_ < 200)       # нейтральный albedo 0.45 → ≈177 после гаммы
    assert red.sum() > 200 and grey.sum() > 200


def test_highlight_tints_selected_part_only():
    mesh, res = get_shape("Сфера"), make_resolver(None)
    m = super_template()
    base = render(m, RenderOptions(width=160, height=160, pitch=0, yaw=0), res, mesh, None, None)
    hl = render(m, RenderOptions(width=160, height=160, pitch=0, yaw=0, highlight=1), res, mesh, None, None)
    diff = np.abs(base[..., :3].astype(int) - hl[..., :3].astype(int)).sum(-1)
    assert diff[30:75, 80].mean() > 20 and diff[100:140, 80].max() == 0     # подсвечена только верхняя половина


def test_pick_part_returns_part_under_pixel():
    mesh = get_shape("Сфера")
    o = RenderOptions(width=200, height=200, pitch=0, yaw=0)
    assert pick_part(mesh, o, 200, 200, 100, 50) == 1 and pick_part(mesh, o, 200, 200, 100, 150) == 0
    assert pick_part(mesh, o, 200, 200, 3, 3) == -1                  # фон
    assert pick_part(get_shape("Плоскость"), o, 200, 200, 100, 100) == -1   # нет частей


# ------------------------------------------------------------------ GL


# ------------------------------------------------------------------ GUI
from rvmat_studio.core.presets import PresetManager
from rvmat_studio.core.settings import Settings


