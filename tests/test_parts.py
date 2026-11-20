"""Селекшены: части модели, материалы-слоты проекта, рендер по частям, выбор кликом, экспорт."""
import json
import struct
import time
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

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


def test_export_writes_every_slot_and_selection_map(tmp_path):
    p = Project("helmet")
    p.add_slot("helmet_glass")
    p.assign_part("visor", "helmet_glass")
    plan = ex.plan_kit(p, tmp_path)
    names = {x.name for x in plan.files}
    assert {"helmet.rvmat", "helmet_damage.rvmat", "helmet_glass.rvmat", "helmet_glass_destruct.rvmat", "helmet_selections.txt"} <= names
    assert len(names) == 13
    ex.execute(plan)
    assert (tmp_path / "helmet_selections.txt").read_text("utf-8").splitlines()[1] == "visor;helmet_glass.rvmat"
    # диагностика покрывает все материалы
    files_in_issues = {i.location.split(":")[0] for i in plan.issues}
    assert any(f.startswith("helmet_glass") for f in files_in_issues) and any(f.startswith("helmet_d") or f == "helmet.rvmat" for f in files_in_issues)


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
def test_gl_renders_parts_with_different_materials():
    from rvmat_studio.core import gl_render as G
    QApplication.instance() or QApplication([])
    if not G.gl_available():
        pytest.skip("OpenGL недоступен")
    g = G.GLOffscreen()
    try:
        mesh, res = get_shape("Сфера"), make_resolver(None)
        m = super_template()
        o = RenderOptions(width=200, height=200, view="albedo", pitch=0, yaw=0)
        img = g.render(m, o, res, mesh, samples=0, parts={0: (m, solid((20, 20, 230))), 1: (m, solid((230, 20, 20)))})
        assert img[50, 100, 0] > 150 and img[150, 100, 2] > 150
        cpu = render(m, o, res, mesh, None, {0: (m, solid((20, 20, 230))), 1: (m, solid((230, 20, 20)))})
        assert np.abs(img[..., :3].astype(int) - cpu[..., :3].astype(int)).mean() < 6
        o.highlight = 1
        hl = g.render(m, o, res, mesh, samples=0, parts={0: (m, None), 1: (m, None)})
        o.highlight = -1
        pl = g.render(m, o, res, mesh, samples=0, parts={0: (m, None), 1: (m, None)})
        assert np.abs(hl[50, 100, :3].astype(int) - pl[50, 100, :3].astype(int)).sum() > 20
        assert (hl[150, 100] == pl[150, 100]).all()
    finally:
        g.close()


# ------------------------------------------------------------------ GUI
from rvmat_studio.core.presets import PresetManager
from rvmat_studio.core.settings import Settings
from rvmat_studio.gui.main_window import MainWindow
from rvmat_studio.gui.theme import DARK


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    a.setStyle("Fusion"); a.setStyleSheet(DARK)
    return a


@pytest.fixture
def win(app, tmp_path):
    w = MainWindow(Settings(), PresetManager(tmp_path / "presets"))
    w.show()
    yield w
    w.proj.dirty = False
    w.close()


def test_parts_tab_assign_and_per_part_preview(app, win):
    win.c_shape.setCurrentText("Сфера")
    tp = win.tab_parts
    assert tp.table.rowCount() == 2 and tp.table.item(0, 0).text() == "нижняя"
    win.proj.add_slot("sphere_top")
    assert tp.slots.count() == 2 and win.proj.active == 1
    cb = tp.table.cellWidget(1, 1)                                  # часть «верхняя»
    cb.setCurrentText("sphere_top")
    assert win.proj.part_map == {"верхняя": "sphere_top"}
    # слот с красным base color на верхней части, у основного — синий
    for sl, col in ((win.proj.slots[0], (20, 20, 230)), (win.proj.slots[1], (230, 20, 20))):
        f = Path(win.proj.project_root or ".") / f"{sl.name}_co.png"
        Image.fromarray(np.tile(np.array([*col, 255], np.uint8), (8, 8, 1))).save(f)
        sl.base_color = str(f.resolve())
    win.on_project_changed()
    win.preview.opts.view = "albedo"; win.preview.opts.pitch = 0; win.preview.opts.yaw = 0
    img = win.preview.render_sync()
    h = img.shape[0]; cx = img.shape[1] // 2
    assert img[int(h * 0.3), cx, 0] > 150 and img[int(h * 0.7), cx, 2] > 150
    for sl in win.proj.slots:
        Path(sl.base_color).unlink(missing_ok=True)


def test_selecting_part_highlights_and_activates_its_material(app, win):
    win.c_shape.setCurrentText("Сфера")
    win.proj.add_slot("top_mat")
    win.proj.assign_part("верхняя", "top_mat")
    win.proj.set_active(0)
    win.tab_parts.table.selectRow(1)                                 # верхняя
    assert win.preview.opts.highlight == 1 and win.proj.name == "top_mat"
    win.tab_parts.table.selectRow(0)
    assert win.preview.opts.highlight == 0 and win.proj.active == 0
    win.tab_parts.select_part(-1)
    assert win.preview.opts.highlight == -1


def test_click_in_preview_picks_part(app, win):
    win.c_shape.setCurrentText("Сфера")
    p = win.preview
    p.opts.pitch = 0; p.opts.yaw = 0
    p.resize(400, 400)
    got = []
    p.part_picked.connect(got.append)
    assert p.pick_at(p.width() / 2, p.height() * 0.25) == 1
    assert p.pick_at(p.width() / 2, p.height() * 0.75) == 0
    assert got[-2:] == [1, 0]
    assert win.tab_parts.table.currentRow() == 0


def test_edits_go_to_the_active_slot_only(app, win):
    win.proj.add_slot("other")
    win.panel.power.spin.setValue(77.0)
    assert win.proj.slots[1].spec.shared.get("specularPower") == 77.0
    assert win.proj.slots[0].spec.shared.get("specularPower") == 15.0
    win.tab_parts.slots.setCurrentRow(0)                              # выбор материала в списке
    assert win.proj.active == 0 and win.panel.power.value() == 15.0
    assert "other" in win.windowTitle() or win.proj.name == win.proj.slots[0].name


def test_issue_goto_switches_to_the_materials_slot(app, win):
    win.proj.add_slot("second")
    win.proj.set_active(0)
    win.run_validation()
    item = next(win.issues.topLevelItem(i) for i in range(win.issues.topLevelItemCount())
                if win.issues.topLevelItem(i).text(3).startswith("second_damage.rvmat:Stage4"))
    win.goto_issue(item)
    assert win.proj.name == "second" and win.panel.target_id == State.DAMAGE.value


def test_import_model_with_parts_via_window(app, win, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    f = tmp_path / "car.obj"
    f.write_text("v 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\nv 2 0 0\nv 2 1 0\ng body\nf 1 2 3 4\ng glass\nf 2 5 6 3\n")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(f), ""))
    win.import_obj()
    assert win.proj.model_path == str(f) and win.tab_parts.table.rowCount() == 2
    assert [win.tab_parts.table.item(i, 0).text() for i in range(2)] == ["body", "glass"]
    win.proj.add_slot("car_glass"); win.proj.assign_part("glass", "car_glass")
    pr = tmp_path / "car.rvproj"; win.proj.save(pr)
    win.preview.mesh = None; win.c_shape.setCurrentText("Сфера")
    win.open_project(str(pr))                                          # модель восстанавливается из проекта
    assert win.preview.opts.shape == "Своя модель" and win.tab_parts.table.rowCount() == 2
    assert win.proj.part_map == {"glass": "car_glass"}


def test_parts_table_does_not_leak_cell_widgets(app, win):
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QComboBox
    win.c_shape.setCurrentText("Цилиндр")
    win.proj.add_slot("cap")
    for _ in range(6):
        win.tab_parts.refresh()
        win.proj.assign_part("верх", "cap")
        win.proj.assign_part("верх", None)
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()
    t = win.tab_parts.table
    assert t.rowCount() == 3 and len(t.viewport().findChildren(QComboBox)) == 3
