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
from rvmat_studio.gui.dialogs import BatchDialog, ExportDialog, SettingsDialog, TextureViewer
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


def test_export_dialog_overwrite_logic(app, win, tmp_path):
    from rvmat_studio.core import export as ex
    plan = ex.plan_kit(win.proj, tmp_path)
    ex.execute(plan)
    d = ExportDialog(ex.plan_kit(win.proj, tmp_path))
    assert d.over.isVisibleTo(d) and len(d.plan.existing) == 6


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


def test_preview_two_phase_quality(app, win):
    p = win.preview
    if p.backend != "cpu":
        pytest.skip("двухфазное качество — только у CPU-рендера")
    assert spin(app, p.has_image)
    p.request()
    assert p._quality is False                    # сначала быстрый кадр
    assert spin(app, lambda: p._quality is True, 3)   # затем качественный
    assert spin(app, lambda: not p._busy, 10)
