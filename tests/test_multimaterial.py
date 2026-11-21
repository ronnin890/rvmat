"""Два и более материала: правки и текстуры не должны влиять друг на друга."""
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication

from rvmat_studio.core.meshes import get_shape
from rvmat_studio.core.presets import PresetManager, super_template
from rvmat_studio.core.project import Project
from rvmat_studio.core.render import RenderOptions, make_resolver, render
from rvmat_studio.core.settings import Settings
from rvmat_studio.core.textures import unique_game_path
from rvmat_studio.gui.main_window import MainWindow
from rvmat_studio.gui.theme import DARK


# ------------------------------------------------------------------ уникальные игровые пути
def test_unique_game_path_disambiguates_same_names(tmp_path):
    a, b, c = tmp_path / "jacket" / "x_as.png", tmp_path / "plate" / "x_as.png", tmp_path / "other" / "plate" / "x_as.png"
    m: dict = {}
    pa = unique_game_path(a, m); m[pa.lower()] = str(a)
    assert pa == "x_as.paa"
    assert unique_game_path(a, m) == "x_as.paa"                       # тот же файл — то же имя (повторное назначение)
    pb = unique_game_path(b, m); m[pb.lower()] = str(b)
    assert pb == "plate\\x_as.paa"
    pc = unique_game_path(c, m); m[pc.lower()] = str(c)
    assert pc == "other\\plate\\x_as.paa" and len({pa.lower(), pb.lower(), pc.lower()}) == 3
    # регистр имени не делает файлы «разными»
    assert unique_game_path(tmp_path / "z" / "X_AS.png", {"x_as.paa": str(a)}) != "X_AS.paa"


# ------------------------------------------------------------------ GUI
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


def _png(path, rgba):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.tile(np.array(rgba, np.uint8), (8, 8, 1))).save(path)


def test_same_named_textures_of_two_materials_do_not_collide(app, win, tmp_path):
    for d, g in (("jacket", 30), ("plate", 255)):
        _png(tmp_path / d / "SCI_FI_as.png", [255, g, 255, 255])         # затенение в G: у жилета 30, у пластины 255
    p = win.proj
    win.c_shape.setCurrentText("Сфера")
    p.add_slot("plate"); p.assign_part("верхняя", "plate")
    p.set_active(0); win.assign_texture(4, tmp_path / "jacket" / "SCI_FI_as.png")
    p.set_active(1); win.assign_texture(4, tmp_path / "plate" / "SCI_FI_as.png")
    t0, t1 = (s.spec.shared.stage(4).texture for s in p.slots)
    assert t0 != t1
    assert Path(win.resolver.find(t0)).parent.name == "jacket" and Path(win.resolver.find(t1)).parent.name == "plate"
    # в превью: нижняя часть (жилет) затенена, верхняя (пластина) — нет
    win.preview.opts.view = "ao"; win.preview.opts.pitch = 0; win.preview.opts.yaw = 0
    img = win.preview.render_sync()
    h, wd = img.shape[:2]
    top, bottom = img[int(h * .3), wd // 2, 0], img[int(h * .7), wd // 2, 0]
    assert top > 240 and bottom < 120


def _regions(win):
    img = win.preview.render_sync().astype(int)
    h, wd = img.shape[:2]
    return img[int(h * .15):int(h * .45), int(wd * .35):int(wd * .65), :3], img[int(h * .55):int(h * .85), int(wd * .35):int(wd * .65), :3]


def test_editing_each_material_changes_only_its_part(app, win):
    p, pn = win.proj, win.panel
    win.c_shape.setCurrentText("Сфера")
    win.preview.opts.pitch = 0; win.preview.opts.yaw = 0
    p.add_slot("second"); p.assign_part("верхняя", "second")
    win.on_project_changed()
    d = lambda a, b: float(np.abs(a - b).mean())
    t0, b0 = _regions(win)
    win.tab_parts.slots.setCurrentRow(1)
    assert p.name == "second"
    pn.quick["rough"].spin.setValue(0)                                    # правим ВТОРОЙ материал
    t1, b1 = _regions(win)
    assert d(t0, t1) > 1.0 and d(b0, b1) == 0.0
    win.tab_parts.slots.setCurrentRow(0)
    pn.quick["rough"].spin.setValue(100)                                  # правим ПЕРВЫЙ
    t2, b2 = _regions(win)
    assert d(b1, b2) > 1.0 and d(t1, t2) == 0.0
    assert p.slots[0].spec.shared.get("specularPower") == 1.0 and p.slots[1].spec.shared.get("specularPower") == 200.0


def test_stage_texture_edit_goes_to_the_active_material_only(app, win):
    p = win.proj
    p.add_slot("second")
    win.panel.stage_eds[1].tex.setText("dz\\second_nohq.paa"); win.panel.stage_eds[1].tex.editingFinished.emit()
    assert p.slots[1].spec.shared.stage(1).texture == "dz\\second_nohq.paa" and p.slots[0].spec.shared.stage(1).texture == ""
    win.tab_parts.slots.setCurrentRow(0)
    assert win.panel.stage_eds[1].tex.text() == ""                        # панель показывает материал 0
    win.tab_parts.slots.setCurrentRow(1)
    assert win.panel.stage_eds[1].tex.text() == "dz\\second_nohq.paa"


def test_undo_keeps_both_materials_consistent(app, win):
    p = win.proj
    p.add_slot("second")
    win.panel.quick["rough"].spin.setValue(0)
    p.set_active(0)
    win.panel.quick["rough"].spin.setValue(100)
    win.undo()                                                           # откатывает правку первого
    assert p.slots[0].spec.shared.get("specularPower") == 15.0 and p.slots[1].spec.shared.get("specularPower") == 200.0
    assert len(p.slots) == 2


def test_base_color_per_material_survives_texture_assignment(app, win, tmp_path):
    _png(tmp_path / "a" / "co.png", [220, 20, 20, 255]); _png(tmp_path / "b" / "co.png", [20, 20, 220, 255])
    win.c_shape.setCurrentText("Сфера")
    p = win.proj
    p.add_slot("second"); p.assign_part("верхняя", "second")
    p.set_active(0); win.assign_texture(0, tmp_path / "a" / "co.png")
    p.set_active(1); win.assign_texture(0, tmp_path / "b" / "co.png")
    win.preview.opts.view = "albedo"; win.preview.opts.pitch = 0; win.preview.opts.yaw = 0
    img = win.preview.render_sync()
    h, wd = img.shape[:2]
    assert img[int(h * .3), wd // 2, 2] > 150 and img[int(h * .7), wd // 2, 0] > 150     # верх синий (второй), низ красный (первый)


# ------------------------------------------------------------------ GL: кэш текстур
def test_gl_texture_cache_trim_does_not_break_drawing(tmp_path):
    from rvmat_studio.core import gl_render as G
    QApplication.instance() or QApplication([])
    if not G.gl_available():
        pytest.skip("OpenGL недоступен")
    g = G.GLOffscreen()
    try:
        sc = g.scene
        keep = []
        for i in range(70):                                              # больше лимита кэша
            arr = np.full((4, 4, 4), (i % 255) / 255, np.float32)
            keep.append(arr)
            sc.texture(arr)
        assert len(sc.tex) == 71 or len(sc.tex) > 64                    # texture() сам ничего не сбрасывает
        mesh, res = get_shape("Сфера"), make_resolver(None)
        m = super_template()
        base = np.tile(np.array([0.9, 0.1, 0.1, 1], np.float32), (8, 8, 1))
        o = RenderOptions(width=120, height=120, view="albedo", base_color=base)
        img = g.render(m, o, res, mesh, samples=0)                       # trim_textures() в начале кадра
        assert len(sc.tex) <= 3 and img[60, 60, 0] > 150 and img[60, 60, 1] < 90
    finally:
        g.close()
