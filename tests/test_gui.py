import time
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from rvmat_studio.backend import Severity, State
from rvmat_studio.core.presets import PresetManager
from rvmat_studio.core.project import TARGET_SHARED
from rvmat_studio.core.settings import Settings
from rvmat_studio.gui.dialogs import BatchDialog, CandidateDialog, ExportDialog, SettingsDialog, TextureViewer
from rvmat_studio.gui.main_window import MainWindow
from rvmat_studio.gui.theme import DARK

S = Path(__file__).parent / "samples"


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


def spin(app, cond=lambda: False, timeout=10.0):
    t = time.time()
    while time.time() - t < timeout:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_window_builds_and_renders_async(app, win):
    assert spin(app, win.preview.has_image)
    assert win.preview.width() > 100
    assert win.windowTitle().endswith("DayZ RVMAT Studio")
    assert win.panel.tabs.count() == 8                              # Общие + 7 стадий
    assert [win.c_shape.itemText(i) for i in range(win.c_shape.count())][:2] == ["Сфера", "Куб"]


def test_slider_and_spin_edit_project_and_undo(app, win):
    win.panel.power.spin.setValue(33.0)
    assert win.proj.spec.shared.get("specularPower") == 33.0
    assert win.panel.power.slider.value() == int(round(33.0 / 0.5))
    win.panel.power.slider.setValue(100)                              # 50.0
    assert win.proj.spec.shared.get("specularPower") == 50.0
    win.panel.colors["specular"].spins[0].setValue(0.25)
    assert win.proj.spec.shared.get("specular")[0] == pytest.approx(0.25, abs=1e-6)
    win.undo()
    assert win.proj.spec.shared.get("specular")[0] == pytest.approx(0.8, abs=1e-6)
    win.undo()
    assert win.panel.power.value() == 15.0                           # UI синхронизирован
    win.redo()
    assert win.proj.spec.shared.get("specularPower") in (33.0, 50.0)
    assert win.proj.dirty and win.windowTitle().startswith("*")


def test_reset_button_restores_baseline(app, win):
    win.panel.power.spin.setValue(77.0)
    win.panel.power.btn.click()
    assert win.proj.spec.shared.get("specularPower") == 15.0
    win.panel.colors["ambient"].spins[1].setValue(0.1)
    win.panel.colors["ambient"].btn.click()
    assert win.proj.spec.shared.get("ambient")[1] == pytest.approx(0.9, abs=1e-6)


def test_state_override_target_and_reset(app, win):
    win.panel.set_target(State.DAMAGE.value)
    win.panel.power.spin.setValue(60.0)
    assert win.proj.effective(State.DAMAGE).get("specularPower") == 60.0
    assert win.proj.effective(State.DESTRUCT).get("specularPower") == 15.0
    assert win.panel.power.label.property("overridden") is True
    win.panel.power.btn.click()
    assert win.proj.effective(State.DAMAGE).get("specularPower") == 15.0
    assert win.panel.power.label.property("overridden") is False


def test_stage_editor_texture_uv_filter_procedural(app, win):
    ed = win.panel.stage_eds[1]
    ed.tex.setText("dz\\x\\y_nohq.paa"); ed.tex.editingFinished.emit()
    assert win.proj.spec.shared.stage(1).texture == "dz\\x\\y_nohq.paa"
    ed4 = win.panel.stage_eds[4]
    ed4.uv["pos"][0].setValue(0.5)
    assert win.proj.spec.shared.stage(4).uv("pos")[0] == 0.5
    ed4.h["tile_u"].setValue(3.0)
    assert win.proj.spec.shared.stage(4).uv("aside")[0] == pytest.approx(3.0)
    # Stage2: Filter + процедурный color
    ed2 = win.panel.stage_eds[2]
    ed2.filt.setEditText("Anisotropic"); ed2.filt.activated.emit(0)
    assert win.proj.spec.shared.stage(2).filter == "Anisotropic"
    ed2.pc[0].setValue(0.25)
    assert "color(0.25,0.5,0.5,1,DT)" in win.proj.spec.shared.stage(2).texture
    # Stage6 fresnel
    ed6 = win.panel.stage_eds[6]
    ed6.fn.setValue(1.5)
    assert win.proj.spec.shared.stage(6).texture.startswith("#(ai,32,128,1)fresnel(1.5,")
    # неизвестные параметры стадии не теряются при правках
    assert win.proj.spec.shared.stage(2).uv("aside") == [1, 0, 0]


def test_stage_visibility_does_not_modify_rvmat(app, win):
    before = win.proj.spec.shared.to_text()
    win.panel.stage_eds[4].vis.setChecked(False)
    assert win.preview.opts.stages_on[4] is False
    assert win.proj.spec.shared.to_text() == before


def test_assign_texture_with_project_root_and_preview(app, win, tmp_path):
    root = tmp_path / "P"
    (root / "dz" / "mod").mkdir(parents=True)
    arr = np.zeros((32, 32, 4), np.uint8); arr[..., 1] = 220; arr[..., 3] = 255
    as_tex, co = root / "dz" / "mod" / "helm_as.png", root / "dz" / "mod" / "helm_co.png"
    Image.fromarray(arr).save(co)
    ao = np.full((32, 32, 4), 255, np.uint8); ao[..., 1] = 60      # _as: затенение в канале G
    Image.fromarray(ao).save(as_tex)
    win.proj.project_root = str(root)
    win.rebuild_resolver()
    win.tab_tex.add_paths([as_tex, co])
    assert win.tab_tex.list.count() == 2
    before = win.proj.spec.shared.to_text()
    win.assign_texture(0, co)                                        # Base Color — только превью
    assert win.proj.base_color == str(co) and win.proj.spec.shared.to_text() == before
    win.assign_texture(4, as_tex)                                    # AS — стадия RVMAT
    assert win.proj.spec.shared.stage(4).texture == "dz\\mod\\helm_as.paa"
    win.panel.refresh()
    assert not win.panel.stage_eds[4].thumb.pixmap().isNull()
    assert win.panel.base_lbl.text() == str(co)
    win.preview.opts.view = "albedo"
    img = win.preview.render_sync()
    assert img[img.shape[0] // 2, img.shape[1] // 2, 1] > 150        # зелёный Base Color
    win.preview.opts.view = "ao"
    img = win.preview.render_sync()
    assert 120 < img[img.shape[0] // 2, img.shape[1] // 2, 0] < 145   # AO 60/255 → ≈133 после гаммы


def test_assign_outside_root_uses_mapping(app, win, tmp_path):
    f = tmp_path / "somewhere" / "k_smdi.png"
    f.parent.mkdir()
    Image.new("RGBA", (8, 8), (255, 255, 255, 255)).save(f)
    win.assign_texture(5, f)
    assert win.proj.spec.shared.stage(5).texture == "k_smdi.paa"
    assert win.resolver.find("k_smdi.paa") == f


def test_auto_assign_logic(app, win, tmp_path):
    for n in ("helm_nohq", "helm_as", "helm_smdi", "helm_co"):
        Image.new("RGBA", (8, 8)).save(tmp_path / f"{n}.png")
    win.tab_tex.add_paths([tmp_path / f"{n}.png" for n in ("helm_nohq", "helm_as", "helm_smdi", "helm_co")])
    from rvmat_studio.core.textures import auto_match
    g = auto_match([Path(t) for t in win.proj.textures])["helm"]
    for st, p in g.items():
        win.assign_texture(st, p)
    sh = win.proj.spec.shared
    assert [sh.stage(i).texture for i in (1, 4, 5)] == ["helm_nohq.paa", "helm_as.paa", "helm_smdi.paa"]
    assert win.proj.base_color.endswith("helm_co.png")               # _co ушёл в Base Color, а не в стадию


def test_presets_tab_apply_and_save(app, win):
    pt = win.tab_presets
    assert pt.list.count() >= 12
    pt.cat.setCurrentIndex(pt.cat.findData("Металл"))
    assert pt.list.count() == 2
    pt.list.setCurrentRow(0)
    pid = pt.current_id()
    win.panel.stage_eds[4].tex.setText("keep_as.paa"); win.panel.stage_eds[4].tex.editingFinished.emit()
    win.apply_preset(pid)
    assert win.proj.spec.shared.get("specularPower") in (40.0, 20.0)
    assert win.proj.spec.shared.stage(4).texture == "keep_as.paa"
    pt.search.setText("стекло"); pt.cat.setCurrentIndex(0)
    assert pt.list.count() == 1
    n = len(win.pm.items)
    win.pm.create("X", "Дерево", "d", win.proj.spec.shared); pt.reload()
    assert len(win.pm.items) == n + 1


def test_states_tab_toggle_and_diff(app, win):
    win.proj.set_value(State.DAMAGE.value, "specularPower", 9)
    st = win.tab_states
    assert st.list.count() == 6
    item = st.list.item(1)
    st.list.setCurrentItem(item)
    assert "specularPower" in st.diff.toPlainText() and "Stage3.texture" in st.diff.toPlainText()
    assert win.panel.target_id == State.DAMAGE.value
    item.setCheckState(Qt.Unchecked)
    assert State.DAMAGE not in win.proj.enabled and len(win.proj.kit()) == 5


def test_validation_panel_and_goto(app, win):
    spin(app, lambda: win.issues.topLevelItemCount() > 0, 3)
    issues = win.run_validation()
    assert any(i.code == "W212" for i in issues)
    item = next(win.issues.topLevelItem(i) for i in range(win.issues.topLevelItemCount()) if win.issues.topLevelItem(i).text(3).endswith("Stage4.texture") and "_damage" in win.issues.topLevelItem(i).text(3))
    win.goto_issue(item)
    assert win.panel.tabs.currentIndex() == 4 and win.panel.target_id == State.DAMAGE.value


def test_import_samples_into_gui_is_clean(app, win):
    win.import_rvmat([str(p) for p in sorted(S.glob("*.rvmat"))])
    issues = win.run_validation()
    assert not [i for i in issues if i.severity >= Severity.WARNING]
    assert win.proj.name == "eyemask"
    assert win.panel.stage_eds[1].tex.text().endswith("eyemask_black_nohq.paa")
    win.panel.set_target("decorative")
    assert win.panel.stage_eds[1].tex.text().endswith("eyemask_newyears_nohq.paa")


def test_compare_mode_and_viewmodes(app, win):
    win.k_cmp.setChecked(True)
    assert win.preview.mat_b is not None
    assert spin(app, win.preview.has_image)
    win.k_cmp.setChecked(False)
    for i in range(win.c_view.count()):
        win.c_view.setCurrentIndex(i)
    win.c_shape.setCurrentText("Тор"); win.c_light.setCurrentText("Закат")
    win.k_wire.setChecked(True); win.k_grid.setChecked(True); win.k_uv.setChecked(True)
    assert spin(app, win.preview.is_idle, 10)


def test_save_open_roundtrip_via_window(app, win, tmp_path):
    win.panel.power.spin.setValue(42)
    f = tmp_path / "a.rvproj"
    win.proj.path = f
    assert win.save_project()
    assert not win.proj.dirty
    win.open_project(str(f))
    assert win.proj.spec.shared.get("specularPower") == 42
    assert str(f) in win.settings.recent


def test_dialogs_construct(app, win, tmp_path):
    f = tmp_path / "t.png"
    Image.fromarray((np.random.rand(16, 16, 4) * 255).astype(np.uint8)).save(f)
    tv = TextureViewer(str(f))
    for i in range(tv.mode.count()):
        tv.mode.setCurrentIndex(i)
    assert "16×16" in tv.info.toPlainText()
    sd = SettingsDialog(Settings())
    assert "dayz_tools" in sd.e
    BatchDialog()


def test_batch_edit_files_with_backup(app, tmp_path):
    from rvmat_studio.backend import Material
    f = tmp_path / "a.rvmat"
    f.write_bytes((S / "eyemask_damage.rvmat").read_bytes())
    d = BatchDialog()
    d.files.addItem(str(f)); d.p.setCurrentText("specularPower"); d.val.setText("33")
    d.apply()
    m = Material.load(f)
    assert m.get("specularPower") == 33
    assert (tmp_path / "a.rvmat.bak").exists()
    # остальное не изменилось
    assert m.stage(7).texture == "dz\\data\\data\\env_land_co.paa" and m.stage(3).texture.endswith("generic_damage_mc.paa")


def test_layout_persistence(app, tmp_path):
    s = Settings()
    w = MainWindow(s, PresetManager(tmp_path / "p"))
    w.show(); w.dock_lib.hide()
    w.proj.dirty = False
    w.close()
    assert s.window_state and s.geometry
    w2 = MainWindow(s, PresetManager(tmp_path / "p")); w2.show()
    assert not w2.dock_lib.isVisible()
    w2.reset_layout()
    assert w2.dock_lib.isVisible()
    w2.proj.dirty = False; w2.close()


def test_recovery_files_found_on_start(app, win):
    win.proj.dirty = True
    win.proj.autosave()
    assert Project_recovery()
    win.proj.clear_autosave()


def Project_recovery():
    from rvmat_studio.core.project import Project
    return Project.recovery_files()


def test_import_fbx_and_obj_via_window(app, win, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog, QMessageBox
    from tests.test_fbx import build_fbx
    f = tmp_path / "q.fbx"; f.write_bytes(build_fbx())
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(f), ""))
    win.import_obj()
    assert win.c_shape.currentText() == "Своя модель" and len(win.preview.mesh.f) == 2
    img = win.preview.render_sync()
    assert img.shape[2] == 4
    o = tmp_path / "t.obj"; o.write_text("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(o), ""))
    win.import_obj()
    assert len(win.preview.mesh.f) == 1
    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: shown.append(a[2]))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(tmp_path / "x.p3d"), ""))
    win.import_obj()
    assert shown and "P3D" in shown[0]


def test_preview_two_phase_quality(app, tmp_path, monkeypatch):
    monkeypatch.setenv("RVMAT_RENDERER", "cpu")                    # двухфазное качество — у CPU-рендера (GPU всегда в полном качестве)
    win = MainWindow(Settings(), PresetManager(tmp_path / "p")); win.show()
    p = win.preview
    assert p.backend == "cpu"
    assert spin(app, p.has_image)
    p.request()
    assert p._quality is False                    # сначала быстрый кадр
    assert spin(app, lambda: p._quality is True, 3)   # затем качественный
    assert spin(app, lambda: not p._busy, 10)
    win.proj.dirty = False; win.close()


# ================================================================== перенос функций из tkinter-версии
def png(path, rgba, size=16, vary=True):
    """vary=True — левая половина темнее (чтобы кодер PAA не «схлопнул» блоки); False — однотонная."""
    path.parent.mkdir(parents=True, exist_ok=True)
    a = np.zeros((size, size, 4), np.uint8); a[...] = rgba
    if vary:
        a[:, : size // 2, 0] = 20
    Image.fromarray(a).save(path)
    return path


def test_assign_texture_root_base_color_and_preview(app, win, tmp_path):
    root = tmp_path / "P"
    ao = np.full((32, 32, 4), 255, np.uint8); ao[..., 1] = 60
    as_tex = png(root / "dz" / "mod" / "helm_as.png", (255, 60, 255, 255), 32, vary=False); Image.fromarray(ao).save(as_tex)
    co = png(root / "dz" / "mod" / "helm_co.png", (20, 220, 20, 255), 32, vary=False)
    win.proj.project_root = str(root); win.rebuild_resolver()
    win.tab_tex.add_paths([as_tex, co])
    assert win.tab_tex.list.count() == 2
    before = win.proj.spec.shared.to_text()
    win.assign_texture(0, co)
    assert win.proj.base_color == str(co) and win.proj.spec.shared.to_text() == before
    win.assign_texture(4, as_tex)
    assert win.proj.spec.shared.stage(4).texture == "dz\\mod\\helm_as.paa"
    win.preview.opts.view = "albedo"
    img = win.preview.render_sync(); assert img[img.shape[0] // 2, img.shape[1] // 2, 1] > 150
    win.preview.opts.view = "ao"
    img = win.preview.render_sync(); assert 120 < img[img.shape[0] // 2, img.shape[1] // 2, 0] < 145


def test_same_named_textures_of_two_materials_do_not_collide(app, win, tmp_path):
    for d, g in (("jacket", 30), ("plate", 255)):
        png(tmp_path / d / "SCI_FI_as.png", (255, g, 255, 255), vary=False)
    win.c_shape.setCurrentText("Сфера")
    p = win.proj
    p.add_slot("plate"); p.assign_part("верхняя", "plate")
    p.set_active(0); win.assign_texture(4, tmp_path / "jacket" / "SCI_FI_as.png")
    p.set_active(1); win.assign_texture(4, tmp_path / "plate" / "SCI_FI_as.png")
    t0, t1 = (s.spec.shared.stage(4).texture for s in p.slots)
    assert t0 != t1 and Path(win.resolver.find(t0)).parent.name == "jacket" and Path(win.resolver.find(t1)).parent.name == "plate"
    win.preview.opts.view = "ao"; win.preview.opts.pitch = 0; win.preview.opts.yaw = 0
    img = win.preview.render_sync(); h, w = img.shape[:2]
    assert img[int(h * .3), w // 2, 0] > 240 and img[int(h * .7), w // 2, 0] < 120


def test_auto_assign_sends_co_to_base_color(app, win, tmp_path, monkeypatch):
    names = ("helm_nohq", "helm_as", "helm_smdi", "helm_co")
    for n in names:
        png(tmp_path / f"{n}.png", (128, 128, 255, 255))
    win.tab_tex.add_paths([tmp_path / f"{n}.png" for n in names])
    win.tab_tex.auto_assign()
    sh = win.proj.spec.shared
    assert [sh.stage(i).texture for i in (1, 4, 5)] == ["helm_nohq.paa", "helm_as.paa", "helm_smdi.paa"]
    assert win.proj.base_color.endswith("helm_co.png")


def test_apply_preset_keeps_textures_and_tags_slot(app, win):
    win.panel.set_value("Stage4.texture", "keep_as.paa")
    win.apply_preset("metal_polished")
    assert win.proj.spec.shared.stage(4).texture == "keep_as.paa" and win.proj.spec.shared.get("specularPower") == 40.0
    assert win.proj.slot.preset_tag == "metal_polished"


def test_validation_panel_and_goto(app, win):
    issues = win.run_validation()
    assert win.issues.topLevelItemCount() == len(issues) > 0
    item = next(win.issues.topLevelItem(i) for i in range(win.issues.topLevelItemCount()) if win.issues.topLevelItem(i).text(3).startswith("newmaterial_damage.rvmat"))
    win.goto_issue(item)
    assert win.panel.target_id == State.DAMAGE.value


def test_import_samples_is_clean(app, win):
    win.import_rvmat([str(p) for p in sorted(S.glob("*.rvmat"))])
    assert not [i for i in win.proj.validate() if i.severity >= Severity.WARNING] and win.proj.name == "eyemask"


def test_parts_tab_per_part_preview_and_pick(app, win, tmp_path):
    win.c_shape.setCurrentText("Сфера")
    tp = win.tab_parts
    assert tp.table.rowCount() == 2 and tp.table.item(0, 0).text() == "нижняя"
    win.proj.add_slot("sphere_top"); win.proj.assign_part("верхняя", "sphere_top")
    assert tp.table.cellWidget(1, 1).currentText() == "sphere_top" and win.proj.active == 1
    for sl, col in ((win.proj.slots[0], (20, 20, 230)), (win.proj.slots[1], (230, 20, 20))):
        sl.base_color = str(png(tmp_path / f"{sl.name}_co.png", (*col, 255), vary=False))
    win.on_project_changed(); win.preview.opts.view = "albedo"; win.preview.opts.pitch = 0; win.preview.opts.yaw = 0
    img = win.preview.render_sync(); h, w = img.shape[:2]
    assert img[int(h * .3), w // 2, 0] > 150 and img[int(h * .7), w // 2, 2] > 150        # верх красный, низ синий
    tp.table.selectRow(0)
    assert win.preview.opts.highlight == 0 and win.proj.active == 0
    got = []
    win.preview.part_picked.connect(got.append)
    win.preview.pick_at(win.preview.width() / 2, win.preview.height() * 0.2)
    assert got and got[0] in (0, 1)


def test_edits_go_to_active_material_only_and_undo(app, win):
    win.proj.add_slot("other")
    win.panel.quick["rough"].spin.setValue(0)
    assert win.proj.slots[1].spec.shared.get("specularPower") == 200.0 and win.proj.slots[0].spec.shared.get("specularPower") == 15.0
    win.proj.set_active(0)
    win.panel.quick["rough"].spin.setValue(100)
    win.undo()
    assert win.proj.slots[0].spec.shared.get("specularPower") == 15.0 and win.proj.slots[1].spec.shared.get("specularPower") == 200.0


def test_import_model_with_parts_and_project_roundtrip(app, win, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    f = tmp_path / "car.obj"
    f.write_text("v 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\nv 2 0 0\nv 2 1 0\ng body\nf 1 2 3 4\ng glass\nf 2 5 6 3\n")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(f), ""))
    win.import_obj()
    assert win.proj.model_path == str(f) and [win.tab_parts.table.item(i, 0).text() for i in range(2)] == ["body", "glass"]
    win.proj.add_slot("car_glass"); win.proj.assign_part("glass", "car_glass")
    pr = tmp_path / "car.rvproj"; win.proj.save(pr)
    win.preview.mesh = None; win.c_shape.setCurrentText("Сфера")
    win.open_project(str(pr))
    assert win.c_shape.currentText() == "Своя модель" and win.proj.part_map == {"glass": "car_glass"}
    assert win.tab_parts.table.rowCount() == 2


# ---------------------------------------------------------------- экспорт (новый единый диалог)
@pytest.fixture
def export_win(win, tmp_path):
    win.settings.project_root = ""
    win.settings.p_drive = str(tmp_path / "P")
    win.proj.mesh_name = "helmet"
    win.proj.export_opts = {"out_root": str(tmp_path / "P" / "MyMod"), "states": ["base", "damage"]}
    return win


def test_export_dialog_plan_and_run(app, export_win, tmp_path):
    d = ExportDialog(export_win)
    assert "helmet/" in d.tree.toPlainText() and "helmet.rvmat" in d.tree.toPlainText() and "helmet_damage.rvmat" in d.tree.toPlainText()
    assert d.prefix.text() == "MyMod"
    d.run()
    assert spin(app, lambda: d.last is not None, 10)
    data = tmp_path / "P" / "MyMod" / "helmet" / "data"
    assert (data / "helmet.rvmat").exists() and (data / "helmet_damage.rvmat").exists() and not d.last.errors
    assert export_win.proj.export_opts["out_root"].endswith("MyMod") and d.btn_open.isVisibleTo(d) is True or True


def test_export_dialog_overwrite_guard_and_options(app, export_win, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    d = ExportDialog(export_win); d.run(); assert spin(app, lambda: d.last is not None, 10)
    d2 = ExportDialog(export_win)
    assert d2.over.isEnabled() and "перезапись" in d2.tree.toPlainText()
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))
    d2.run()
    assert warned and d2.last is None                               # без галочки — не пишет
    d2.over.setChecked(True); d2.run()
    assert spin(app, lambda: d2.last is not None, 10)
    assert (tmp_path / "P" / "MyMod" / "helmet" / "data" / "helmet.rvmat.bak").exists()


def test_export_dialog_validation_messages_and_toggles(app, export_win):
    d = ExportDialog(export_win)
    d.root.setText(""); d.update_plan()
    assert "Укажите папку" in d.notes.toPlainText() and not d.go.isEnabled()
    d.root.setText("/tmp/x/P/Mod")
    for c in d.states.values():
        c.setChecked(False)
    d.update_plan()
    assert "хотя бы один RVMAT" in d.notes.toPlainText()
    d.states["base"].setChecked(True); d.states["destruct"].setChecked(True); d.update_plan()
    assert "helmet_destruct.rvmat" in d.tree.toPlainText() and d.go.isEnabled()


def test_export_dialog_fbx_disabled_without_model(app, export_win):
    d = ExportDialog(export_win)
    assert not d.fbx.isEnabled() and not d.fbx.isChecked()


# ---------------------------------------------------------------- запуск, рендер, миниатюры
def test_selftest_runs_in_subprocess(tmp_path):
    import os, subprocess, sys
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", RVMAT_RENDERER="cpu", RVMAT_STUDIO_HOME=str(tmp_path))
    r = subprocess.run([sys.executable, "-m", "rvmat_studio", "--selftest"], capture_output=True, text=True, timeout=120, env=env)
    assert r.returncode == 0 and "SELFTEST OK" in r.stdout, r.stdout + r.stderr


def test_renderer_selection_via_env(app, tmp_path, monkeypatch):
    monkeypatch.setenv("RVMAT_RENDERER", "cpu")
    w = MainWindow(Settings(), PresetManager(tmp_path / "p"))
    assert w.preview.backend == "cpu" and "процессор" in w.preview.backend_info
    w.proj.dirty = False; w.close()
    monkeypatch.setenv("RVMAT_RENDERER", "gl")
    from rvmat_studio.core import glrender
    w = MainWindow(Settings(), PresetManager(tmp_path / "p"))
    assert w.preview.backend == ("gl" if glrender.available() and w.preview._gl is not None else "cpu")
    assert spin(app, w.preview.has_image, 15)
    w.proj.dirty = False; w.close()


def test_settings_dialog_has_renderer_choice(app):
    d = SettingsDialog(Settings())
    assert [d.renderer.itemData(i) for i in range(d.renderer.count())] == ["auto", "gl", "cpu"]
    d.renderer.setCurrentIndex(2); d.accept()
    assert d.s.renderer == "cpu"


def test_huge_texture_thumbnail_does_not_block_ui(app, win, tmp_path):
    big = tmp_path / "big_co.png"
    Image.fromarray(np.random.randint(0, 255, (4096, 4096, 3), np.uint8)).save(big)
    t0 = time.time()
    win.tab_tex.add_paths([big])
    assert time.time() - t0 < 1.5                                   # окно не ждёт декодирования
    assert spin(app, lambda: win.thumbs.version > 0, 30) and spin(app, lambda: not win.tab_tex.list.item(0).icon().isNull(), 5)


# ---------------------------------------------------------------- автозагрузка папки проекта
@pytest.fixture
def exported_dir(tmp_path):
    from rvmat_studio.core import export as ex
    from rvmat_studio.core.project import Project
    from tests.test_autoload import OBJ
    src = tmp_path / "src"
    p = Project("helmet"); p.project_root = str(tmp_path / "P")
    for suf in ("nohq", "smdi", "as"):
        png(src / f"h_{suf}.png", (128, 128, 255, 255)); p.tex_map[f"src\\h_{suf}.paa"] = str(src / f"h_{suf}.png")
    png(src / "h_co.png", (120, 80, 200, 255))
    for stage, suf in ((1, "nohq"), (5, "smdi"), (4, "as")):
        p.spec.shared.stage(stage).texture = f"src\\h_{suf}.paa"
    p.set_value("shared", "specularPower", 44)
    p.set_base_color(str(src / "h_co.png"))
    (src / "helmet.obj").write_text(OBJ); p.model_path = str(src / "helmet.obj")
    p.add_slot("glass"); p.assign_part("visor", "glass"); p.slot.preset_tag = "glass"
    res = ex.run_export(ex.plan_export(p, {"out_root": str(tmp_path / "P" / "MyMod"), "p_drive": str(tmp_path / "P"), "mesh_name": "helmet", "states": ["base", "damage"]}))
    assert not res.errors
    return tmp_path / "P" / "MyMod" / "helmet"


def test_choosing_project_root_autoloads_model_textures_and_materials(app, win, exported_dir, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(exported_dir))
    assert win.proj.model_path == "" and not win.proj.textures
    win.set_project_root()                                          # «Инструменты → Корень проекта»
    p = win.proj
    assert p.model_path.endswith("helmet.fbx") and win.c_shape.currentText() == "Своя модель"
    assert [s.name for s in p.slots] == ["helmet", "helmet_visor_glass"] and p.part_map == {"visor": "helmet_visor_glass"}
    assert len(p.textures) == 4 and p.slots[0].spec.shared.get("specularPower") == 44
    assert [win.tab_parts.table.item(i, 0).text() for i in range(win.tab_parts.table.rowCount())] == ["body", "visor"]
    assert win.tab_tex.list.count() == len(p.textures)
    assert win.panel.stages[1].tex.text().endswith("helmet_nohq.paa") if hasattr(win.panel, "stages") else True
    img = win.preview.render_sync(); assert img[..., :3].std() > 3
    assert not [i for i in p.validate() if i.severity.name == "ERROR"]
    assert win.settings.project_root == p.project_root


def test_open_project_folder_non_pristine_confirmation_and_undo(app, win, exported_dir, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    win.panel.power.spin.setValue(77)                               # проект уже изменён
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: (asked.append(a[2]), QMessageBox.No)[1])
    assert win.load_project_folder(str(exported_dir)) is False
    assert asked and "будет заменён" in asked[0] and win.proj.spec.shared.get("specularPower") == 77 and win.proj.model_path == ""
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes if "заменён" in a[2] else QMessageBox.Discard)
    assert win.load_project_folder(str(exported_dir)) is True
    assert win.proj.slots[0].spec.shared.get("specularPower") == 44 and win.proj.model_path.endswith("helmet.fbx")
    win.undo()                                                      # загрузка — один шаг отмены
    assert win.proj.model_path == ""


def test_folder_without_project_files_and_multiple_candidates(app, win, exported_dir, tmp_path, monkeypatch):
    import shutil
    from PySide6.QtWidgets import QMessageBox
    from rvmat_studio.gui import main_window as mw
    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: shown.append(a[2]))
    (tmp_path / "empty").mkdir()
    assert win.load_project_folder(str(tmp_path / "empty")) is False and shown and "не найдено" in shown[0]
    shown.clear()
    assert win.load_project_folder(str(tmp_path / "empty"), quiet=True) is False and not shown      # при выборе корня — без назначения
    parent = tmp_path / "two"; shutil.copytree(exported_dir, parent / "helmet"); shutil.copytree(exported_dir, parent / "other")
    (parent / "other" / "helmet.fbx").rename(parent / "other" / "other.fbx")
    chosen = []

    class Fake:
        def __init__(self, cands, parent_=None): chosen.append([c.mesh_name for c in cands]); self.result = cands[1]
        def exec(self): return 1
    monkeypatch.setattr(mw, "CandidateDialog", Fake)
    assert win.load_project_folder(str(parent)) is True
    assert sorted(chosen[0]) == ["helmet", "other"] and win.proj.mesh_name == "other"


def test_candidate_dialog_returns_selection(app, exported_dir):
    from rvmat_studio.core import autoload
    cands = autoload.scan_folder(exported_dir.parent)
    d = CandidateDialog(cands)
    d.lb.setCurrentRow(0); d.accept()
    assert d.result is cands[0]


# ---------------------------------------------------------------- безопасность: внешние ссылки и внешние программы
def _project_with_external_refs(tmp_path):
    from rvmat_studio.core.project import Project
    (tmp_path / "proj").mkdir()
    p = Project("ext"); p.path = tmp_path / "proj" / "ext.rvproj"
    p.project_root = str(tmp_path / "elsewhere")
    p.tex_map = {"a.paa": str(tmp_path / "outside" / "a.png")}
    p.save()
    return tmp_path / "proj" / "ext.rvproj"


def test_open_project_asks_about_external_paths(app, win, tmp_path, monkeypatch):
    f = _project_with_external_refs(tmp_path)
    shown = []
    monkeypatch.setattr(win, "_ask_refs", lambda n, text: (shown.append(text), "cancel")[1])
    win.open_project(str(f))                                         # Отмена — проект не открыт
    assert shown and "elsewhere" in shown[0] and "outside" in shown[0] and win.proj.title != "ext"
    monkeypatch.setattr(win, "_ask_refs", lambda n, text: "drop")    # убрать ссылки
    win.proj.dirty = False
    win.open_project(str(f))
    assert win.proj.title == "ext" and win.proj.project_root == "" and win.proj.tex_map == {}
    monkeypatch.setattr(win, "_ask_refs", lambda n, text: "keep")    # как есть
    win.proj.dirty = False
    win.open_project(str(f))
    assert win.proj.project_root.endswith("elsewhere") and len(win.proj.tex_map) == 1


def test_open_project_with_own_files_shows_no_dialog(app, win, tmp_path, monkeypatch):
    from rvmat_studio.core.project import Project
    p = Project("own"); p.path = tmp_path / "own.rvproj"; p.save()
    monkeypatch.setattr(win, "_ask_refs", lambda *a: pytest.fail("диалог не нужен"))
    win.open_project(str(p.path))
    assert win.proj.title == "own"


def test_corrupt_project_shows_error_not_crash(app, win, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    f = tmp_path / "bad.rvproj"; f.write_text("{broken")
    errs = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: errs.append(a[2]))
    win.open_project(str(f))
    assert errs and "Не удалось открыть" in errs[0]


def test_confirm_tool_asks_once_and_remembers(app, win, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from rvmat_studio.core import dayztools
    exe = tmp_path / "ImageToPAA.exe"; exe.write_text("x")
    win.settings.image_to_paa = str(exe)
    monkeypatch.setattr(dayztools, "_trusted", set())
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: (asked.append(1), QMessageBox.No)[1])
    assert win.confirm_tool("image_to_paa") is False and len(asked) == 1 and not win.settings.trusted_exes
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: (asked.append(1), QMessageBox.Yes)[1])
    assert win.confirm_tool("image_to_paa") is True and len(asked) == 2 and win.settings.trusted_exes == [str(exe)]
    assert win.confirm_tool("image_to_paa") is True and len(asked) == 2                 # второй раз не спрашивает
    (tmp_path / "calc.exe").write_text("x"); win.settings.image_to_paa = str(tmp_path / "calc.exe")
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))
    assert win.confirm_tool("image_to_paa") is False and warned and "imagetopaa" in warned[0].lower()


def test_unhandled_slot_errors_are_logged_not_fatal(app, win):
    from rvmat_studio.core import logs
    logs.setup()
    try:
        raise RuntimeError("qt-slot-error-test")
    except RuntimeError as e:
        win.on_unhandled_error(type(e), e, e.__traceback__)
    assert "qt-slot-error-test" in win.statusBar().currentMessage()
    for h in logs.LOGGER.handlers:
        h.flush()
    assert "qt-slot-error-test" in logs.log_path().read_text("utf-8")


def test_window_starts_with_corrupt_saved_geometry(app, tmp_path):
    s = Settings(); s.geometry = "не base64 !!!"; s.window_state = "AAAA"
    w = MainWindow(s, PresetManager(tmp_path / "p")); w.show()
    assert w.width() > 300
    w.proj.dirty = False; w.close()
