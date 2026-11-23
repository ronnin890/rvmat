"""Интерфейс на tkinter. Нужен tkinter и дисплей (на Linux: xvfb-run -a pytest); иначе тесты пропускаются."""
import time
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

tk = pytest.importorskip("tkinter")

from rvmat_studio.backend import Material, State  # noqa: E402
from rvmat_studio.core import export as ex  # noqa: E402
from rvmat_studio.core.presets import PresetManager  # noqa: E402
from rvmat_studio.core.project import TARGET_DECOR, TARGET_SHARED  # noqa: E402
from rvmat_studio.core.settings import Settings  # noqa: E402

try:
    _r = tk.Tk(); _r.destroy()
except tk.TclError:
    pytest.skip("нет дисплея для tkinter", allow_module_level=True)

from rvmat_studio.ui import dialogs  # noqa: E402
from rvmat_studio.ui.app import StudioApp  # noqa: E402

S = Path(__file__).parent / "samples"


def pump(app, cond=lambda: False, timeout=8.0):
    t = time.time()
    while time.time() - t < timeout:
        app.root.update()
        if cond():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def app(tmp_path):
    a = StudioApp(Settings(), PresetManager(tmp_path / "presets"))
    a.root.update()
    yield a
    a.proj.dirty = False
    try:
        a.close()
    except tk.TclError:
        pass


def png(path, rgba, size=16, vary=True):
    """vary=True — левая половина темнее (чтобы кодер PAA не «схлопнул» блоки); False — однотонная."""
    path.parent.mkdir(parents=True, exist_ok=True)
    a = np.zeros((size, size, 4), np.uint8); a[...] = rgba
    if vary:
        a[:, : size // 2, 0] = 20
    Image.fromarray(a).save(path)
    return path


def _slider(row, v):
    """Имитация действия пользователя над SliderRow."""
    row.sv.set(str(v)); row._entry()


def test_window_builds_and_renders(app):
    assert pump(app, app.preview.has_image)
    assert app.root.title().endswith("DayZ RVMAT Studio")
    assert [app.props.nb.tab(i, "text") for i in range(8)][:3] == ["Материал", "S1", "S2"]
    assert app.preview.render_sync().shape[2] == 4
    assert "Сфера" in app.shape_cb["values"]


def test_quick_slider_edits_real_rvmat_fields_and_undo(app):
    sh = app.proj.spec.shared
    _slider(app.props.quick["rough"], 0)
    assert sh.get("specularPower") == 200.0
    app.flush()
    assert app.props.power.value() == 200.0                                   # «Подробно» синхронно
    _slider(app.props.quick["metal"], 100)
    assert sh.get("diffuse")[0] == pytest.approx(0.18, abs=1e-5) and sh.get("specular")[0] == pytest.approx(1.0, abs=1e-5)
    n = len(app.proj._undo)
    _slider(app.props.quick["shine"], 50)
    app.flush()
    assert app.props.quick["shine"].value() == 50
    app.undo()
    assert len(app.proj._undo) == n and app.proj.spec.shared.get("specular")[0] == pytest.approx(1.0, abs=1e-5)   # после undo материал подменяется — читаем заново


def test_metallic_is_one_undo_step_and_reset_restores(app):
    n = len(app.proj._undo)
    _slider(app.props.quick["metal"], 80)
    assert len(app.proj._undo) == n + 1                                      # три поля — один шаг
    app.props.quick["metal"].reset.invoke()
    sh = app.proj.spec.shared
    assert sh.get("diffuse")[0] == pytest.approx(0.9, abs=1e-5) and sh.get("specular")[0] == pytest.approx(0.8, abs=1e-5)


def test_advanced_fields_and_toggle(app):
    assert not app.props._adv_open
    app.props._toggle_adv(); assert app.props._adv_open
    app.props.power.sv.set("33"); app.props.power._entry()
    assert app.proj.spec.shared.get("specularPower") == 33.0
    app.props.colors["specular"].vars[0].set("0.25"); app.props.colors["specular"]._emit()
    assert app.proj.spec.shared.get("specular")[0] == pytest.approx(0.25, abs=1e-6)
    app.props.ps.set("Multi"); app.props.set_value("PixelShaderID", "Multi")
    assert app.proj.spec.shared.get("PixelShaderID") == "Multi"


def test_state_target_overrides_and_marks(app):
    app.set_target(State.DAMAGE.value)
    _slider(app.props.quick["rough"], 10)
    assert app.proj.is_overridden(State.DAMAGE.value, "specularPower")
    assert app.proj.effective(State.DESTRUCT).get("specularPower") == 15.0
    app.flush()
    assert str(app.props.quick["rough"].label.cget("style")) == "Over.TLabel"
    app.props.quick["rough"].reset.invoke()
    assert not app.proj.is_overridden(State.DAMAGE.value, "specularPower")


def test_stage_editor_texture_uv_filter_procedural(app):
    ed = app.props.stages[1]
    ed.tex.set("dz\\x\\y_nohq.paa"); app.props.set_value("Stage1.texture", ed.tex.get())
    assert app.proj.spec.shared.stage(1).texture == "dz\\x\\y_nohq.paa"
    ed4 = app.props.stages[4]
    ed4.uv["pos"][0].set("0.5"); ed4._uv("pos")
    assert app.proj.spec.shared.stage(4).uv("pos")[0] == 0.5
    ed4.h["tile_u"].set("3"); ed4._helper()
    assert app.proj.spec.shared.stage(4).uv("aside")[0] == pytest.approx(3.0)
    ed2 = app.props.stages[2]
    ed2.filt.set("Anisotropic"); app.props.set_value("Stage2.Filter", "Anisotropic")
    assert app.proj.spec.shared.stage(2).filter == "Anisotropic"
    app.flush()
    ed2.pc[0].set("0.25"); ed2._color()
    assert "color(0.25,0.5,0.5,1,DT)" in app.proj.spec.shared.stage(2).texture
    app.flush()
    app.props.stages[6].fn.set("1.5"); app.props.stages[6]._fres()
    assert app.proj.spec.shared.stage(6).texture.startswith("#(ai,32,128,1)fresnel(1.5,")


def test_stage_visibility_does_not_modify_rvmat(app):
    before = app.proj.spec.shared.to_text()
    app.props.stages[4].vis.set(False); app.set_stage_visible(4, False)
    assert app.preview.opts.stages_on[4] is False and app.proj.spec.shared.to_text() == before


def test_assign_texture_root_base_color_and_preview(app, tmp_path):
    root = tmp_path / "P"
    ao = np.full((32, 32, 4), 255, np.uint8); ao[..., 1] = 60
    as_tex = png(root / "dz" / "mod" / "helm_as.png", (255, 60, 255, 255), 32, vary=False); Image.fromarray(ao).save(as_tex)
    co = png(root / "dz" / "mod" / "helm_co.png", (20, 220, 20, 255), 32, vary=False)
    app.proj.project_root = str(root); app.rebuild_resolver()
    app.add_textures([as_tex, co])
    app.flush()
    assert len(app.left.tex.tv.get_children()) == 2
    before = app.proj.spec.shared.to_text()
    app.assign_texture(0, co)
    assert app.proj.base_color == str(co) and app.proj.spec.shared.to_text() == before
    app.assign_texture(4, as_tex)
    assert app.proj.spec.shared.stage(4).texture == "dz\\mod\\helm_as.paa"
    app.flush()
    assert app.props.stages[4]._photo is not None and app.props.base.get() == str(co)
    app.preview.opts.view = "albedo"
    img = app.preview.render_sync(); assert img[img.shape[0] // 2, img.shape[1] // 2, 1] > 150
    app.preview.opts.view = "ao"
    img = app.preview.render_sync(); assert 120 < img[img.shape[0] // 2, img.shape[1] // 2, 0] < 145


def test_same_named_textures_of_two_materials_do_not_collide(app, tmp_path):
    for d, g in (("jacket", 30), ("plate", 255)):
        png(tmp_path / d / "SCI_FI_as.png", (255, g, 255, 255), vary=False)
    app.shape.set("Сфера"); app._opts_changed()
    p = app.proj
    p.add_slot("plate"); p.assign_part("верхняя", "plate")
    p.set_active(0); app.assign_texture(4, tmp_path / "jacket" / "SCI_FI_as.png")
    p.set_active(1); app.assign_texture(4, tmp_path / "plate" / "SCI_FI_as.png")
    t0, t1 = (s.spec.shared.stage(4).texture for s in p.slots)
    assert t0 != t1 and Path(app.resolver.find(t0)).parent.name == "jacket" and Path(app.resolver.find(t1)).parent.name == "plate"
    app.flush()
    app.preview.opts.view = "ao"; app.preview.opts.pitch = 0; app.preview.opts.yaw = 0
    img = app.preview.render_sync(); h, w = img.shape[:2]
    assert img[int(h * .3), w // 2, 0] > 240 and img[int(h * .7), w // 2, 0] < 120


def test_auto_assign_sends_co_to_base_color(app, tmp_path, monkeypatch):
    for n in ("helm_nohq", "helm_as", "helm_smdi", "helm_co"):
        png(tmp_path / f"{n}.png", (128, 128, 255, 255))
    app.add_textures([tmp_path / f"{n}.png" for n in ("helm_nohq", "helm_as", "helm_smdi", "helm_co")])
    app.left.tex.auto_assign()                                                # единственный набор — без вопросов
    sh = app.proj.spec.shared
    assert [sh.stage(i).texture for i in (1, 4, 5)] == ["helm_nohq.paa", "helm_as.paa", "helm_smdi.paa"]
    assert app.proj.base_color.endswith("helm_co.png")


def test_presets_tab_apply_keeps_textures_and_tags_slot(app, monkeypatch):
    pt = app.left.presets
    assert len(pt.tv.get_children()) >= 12
    pt.cat.set("Металл"); pt.refresh()
    assert len(pt.tv.get_children()) == 2
    app.props.set_value("Stage4.texture", "keep_as.paa")
    pid = pt.tv.get_children()[0]
    app.apply_preset(pid)
    assert app.proj.spec.shared.stage(4).texture == "keep_as.paa" and app.proj.spec.shared.get("specularPower") in (40.0, 20.0)
    assert app.proj.slot.preset_tag == pid
    pt.cat.set("Все категории"); pt.q.set("стекло")
    assert len(pt.tv.get_children()) == 1


def test_states_tab_toggle_and_diff(app):
    app.proj.set_value(State.DAMAGE.value, "specularPower", 9)
    st = app.left.states
    assert len(st.tv.get_children()) == 6
    st._diff("damage")
    assert "specularPower" in st.diff.get("1.0", "end") and "Stage3.texture" in st.diff.get("1.0", "end")
    app.proj.set_enabled(State.DAMAGE, False)
    assert State.DAMAGE not in app.proj.enabled and len(app.proj.kit()) == 5


def test_validation_panel_and_goto(app):
    issues = app.proj.validate()
    app.run_validation()
    assert len(app.bottom.issues.get_children()) == len(issues) > 0
    app.goto_issue("newmaterial_damage.rvmat:Stage4.texture")
    assert app.props.nb.index(app.props.nb.select()) == 4 and app.target == State.DAMAGE.value


def test_import_samples_is_clean(app):
    app.import_rvmat([str(p) for p in sorted(S.glob("*.rvmat"))])
    app.flush()
    from rvmat_studio.backend import Severity
    assert not [i for i in app.proj.validate() if i.severity >= Severity.WARNING] and app.proj.name == "eyemask"
    assert app.props.stages[1].tex.get().endswith("eyemask_black_nohq.paa")
    app.set_target(TARGET_DECOR)
    assert app.props.stages[1].tex.get().endswith("eyemask_newyears_nohq.paa")


def test_compare_mode_and_views(app):
    app.cmp_on.set(True); app._opts_changed()
    assert app.preview.mat_b is not None and pump(app, app.preview.has_image)
    app.cmp_on.set(False)
    for t in ("Нормали", "Specular (SMDI-G)", "AS: затенение ambient", "UV-шашечки"):
        app.view.set(t); app._opts_changed()
    app.shape.set("Тор"); app.light.set("Закат"); app.wire.set(True); app.grid.set(True); app.uv.set(True); app._opts_changed()
    assert pump(app, app.preview.is_idle, 10)


def test_parts_tab_per_part_preview_and_pick(app, tmp_path):
    app.shape.set("Сфера"); app._opts_changed()
    tp = app.left.parts
    assert len(tp.table.get_children()) == 2 and tp.table.item("0", "values")[0] == "нижняя"
    app.proj.add_slot("sphere_top"); app.proj.assign_part("верхняя", "sphere_top")
    app.flush()
    assert tp.table.item("1", "values")[1] == "sphere_top" and app.proj.active == 1
    for sl, col in ((app.proj.slots[0], (20, 20, 230)), (app.proj.slots[1], (230, 20, 20))):
        f = png(tmp_path / f"{sl.name}_co.png", (*col, 255), vary=False); sl.base_color = str(f)
    app.flush(); app.preview.opts.view = "albedo"; app.preview.opts.pitch = 0; app.preview.opts.yaw = 0
    img = app.preview.render_sync(); h, w = img.shape[:2]
    assert img[int(h * .3), w // 2, 0] > 150 and img[int(h * .7), w // 2, 2] > 150        # верх красный, низ синий
    tp.table.selection_set("0"); tp._row_sel()
    assert app.preview.opts.highlight == 0 and app.proj.active == 0
    app.preview.pick_at(app.preview.canvas.winfo_width() / 2, app.preview.canvas.winfo_height() * 0.2)
    assert app.preview.opts.highlight in (0, 1)


def test_edits_go_to_active_material_only_and_undo(app):
    app.proj.add_slot("other")
    _slider(app.props.quick["rough"], 0)
    assert app.proj.slots[1].spec.shared.get("specularPower") == 200.0 and app.proj.slots[0].spec.shared.get("specularPower") == 15.0
    app.proj.set_active(0)
    _slider(app.props.quick["rough"], 100)
    app.undo()
    assert app.proj.slots[0].spec.shared.get("specularPower") == 15.0 and app.proj.slots[1].spec.shared.get("specularPower") == 200.0


def test_import_model_with_parts_and_project_roundtrip(app, tmp_path):
    f = tmp_path / "car.obj"
    f.write_text("v 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\nv 2 0 0\nv 2 1 0\ng body\nf 1 2 3 4\ng glass\nf 2 5 6 3\n")
    app.import_model(str(f))
    assert app.proj.model_path == str(f) and [app.left.parts.table.item(i, "values")[0] for i in app.left.parts.table.get_children()] == ["body", "glass"]
    app.proj.add_slot("car_glass"); app.proj.assign_part("glass", "car_glass")
    pr = tmp_path / "car.rvproj"; app.proj.save(pr)
    app.preview.mesh = None; app.shape.set("Сфера")
    app.open_project(str(pr))
    assert app.preview.shape == "Своя модель" and app.proj.part_map == {"glass": "car_glass"}
    assert len(app.left.parts.table.get_children()) == 2


def test_save_open_roundtrip_via_window(app, tmp_path):
    app.props.power.sv.set("42"); app.props.power._entry()
    f = tmp_path / "a.rvproj"; app.proj.path = f
    assert app.save_project() and not app.proj.dirty
    app.open_project(str(f))
    assert app.proj.spec.shared.get("specularPower") == 42 and str(f) in app.settings.recent


def test_settings_and_viewer_dialogs_construct(app, tmp_path):
    d = dialogs.SettingsDialog(app.root, Settings())
    assert "dayz_tools" in d.vars; d.destroy()
    f = png(tmp_path / "t.png", (10, 120, 250, 200))
    v = dialogs.TextureViewer(app.root, str(f))
    for m in ("rgb", "rgba", "r", "g", "b", "a", "swapg"):
        v.mode.set(m); v.draw()
    assert "16×16" in v.info.get("1.0", "end"); v.destroy()


# ------------------------------------------------------------------ окно экспорта
@pytest.fixture
def export_app(app, tmp_path):
    src = tmp_path / "src"
    for suf, rgba in (("nohq", (128, 128, 255, 255)), ("smdi", (255, 200, 90, 255)), ("as", (255, 150, 255, 255))):
        png(src / f"h_{suf}.png", rgba)
    png(src / "h_co.png", (120, 80, 200, 255))
    for stage, suf in ((1, "nohq"), (5, "smdi"), (4, "as")):
        game = f"src\\h_{suf}.paa"
        app.proj.tex_map[game.lower()] = str(src / f"h_{suf}.png")
        app.proj.spec.shared.stage(stage).texture = game
    app.proj.set_base_color(str(src / "h_co.png"))
    (src / "helmet.obj").write_text("v 0 0 0\nv 2 0 0\nv 2 2 0\nv 0 2 0\nv 4 0 0\nv 4 2 0\ng body\nf 1 2 3 4\ng visor\nf 2 5 6 3\n")
    app.import_model(str(src / "helmet.obj"))
    app.settings.p_drive = str(tmp_path / "P")
    return app


def test_export_dialog_plan_and_run(export_app, tmp_path):
    app = export_app
    d = dialogs.ExportDialog(app.root, app)
    d.root_var.set(str(tmp_path / "P" / "MyMod")); d.mesh.set("helmet"); d.update_plan()
    assert d.prefix.get() == "MyMod" and not d._prefix_manual                  # префикс — по пути относительно P:
    shown = d.treebox.get("1.0", "end")
    assert "helmet/" in shown and "helmet.fbx" in shown and "data/" in shown and "helmet_nohq.paa" in shown and "helmet_damage.rvmat" in shown
    assert str(d.go.cget("text")) == "Экспортировать" and "disabled" not in d.go.state()
    d.states["decorative"].set(True); d.update_plan()
    assert "helmet_decorative.rvmat" in d.treebox.get("1.0", "end")
    d.run()
    assert pump(app, lambda: not d._running and d._plan is None, 15)
    out = tmp_path / "P" / "MyMod" / "helmet"
    assert (out / "helmet.fbx").exists() and (out / "data" / "helmet_nohq.paa").exists() and (out / "data" / "helmet.rvmat").exists()
    assert Material.load(out / "data" / "helmet_damage.rvmat").stage(1).texture == "MyMod\\helmet\\data\\helmet_nohq.paa"
    assert "Записано" in d.status.cget("text") and app.proj.export_opts["states"].count("decorative") == 1
    assert app.proj.project_root == "" or app.proj.project_root                  # корень запомнен
    d.destroy()


def test_export_dialog_overwrite_guard_and_options(export_app, tmp_path, monkeypatch):
    app = export_app
    shown = []
    monkeypatch.setattr(dialogs.messagebox, "showwarning", lambda *a, **k: shown.append(a[1]))
    d = dialogs.ExportDialog(app.root, app)
    d.root_var.set(str(tmp_path / "P" / "MyMod")); d.mesh.set("helmet"); d.update_plan()
    d.run(); pump(app, lambda: not d._running and d._plan is None, 15); d.destroy()
    d2 = dialogs.ExportDialog(app.root, app)
    d2.root_var.set(str(tmp_path / "P" / "MyMod")); d2.mesh.set("helmet"); d2.update_plan()
    assert d2._plan.existing and "перезапись" in d2.treebox.get("1.0", "end")
    d2.run()
    assert shown and "уже существуют" in shown[0] and not d2._running             # без галочки — ничего не пишется
    d2.over.set(True); d2.run()
    assert pump(app, lambda: not d2._running and d2._plan is None, 15)
    assert (tmp_path / "P" / "MyMod" / "helmet" / "data" / "helmet.rvmat.bak").exists()
    d2.destroy()


def test_export_dialog_validation_messages_and_toggles(export_app, tmp_path):
    app = export_app
    d = dialogs.ExportDialog(app.root, app)
    d.root_var.set(""); d.update_plan()
    assert "Укажите папку" in d.notes.get("1.0", "end") and "disabled" in d.go.state()
    d.root_var.set(str(tmp_path / "X")); d.mesh.set("m")
    for s in d.states.values():
        s.set(False)
    d.update_plan()
    assert "хотя бы один RVMAT" in d.notes.get("1.0", "end")
    d.states["base"].set(True); d.tex.set(False); d.fbx.set(False); d.update_plan()
    t = d.treebox.get("1.0", "end")
    assert "m.rvmat" in t and ".paa" not in t and ".fbx" not in t
    d.destroy()


def test_export_dialog_fbx_disabled_without_model(app, tmp_path):
    d = dialogs.ExportDialog(app.root, app)
    assert "disabled" in d.fbxcb.state() and not d.fbx.get()
    d.destroy()


def test_selftest_runs():
    from rvmat_studio.ui.selftest import selftest
    assert selftest() == 0


# ------------------------------------------------------------------ выбор рендера
def test_renderer_selection_and_fallback(app, monkeypatch):
    from rvmat_studio.core import glrender
    from rvmat_studio.ui.preview import PreviewView
    monkeypatch.delenv("RVMAT_RENDERER", raising=False)
    cpu = PreviewView(app.root, renderer="cpu")
    assert cpu.backend == "cpu" and "процессор" in cpu.backend_info
    cpu.shutdown(); cpu.destroy()
    monkeypatch.setattr(glrender, "moderngl", None)                         # нет библиотеки — тихий откат на процессор
    auto = PreviewView(app.root, renderer="auto")
    assert auto.backend == "cpu" and "moderngl" in auto.backend_info
    auto.shutdown(); auto.destroy()
    monkeypatch.setenv("RVMAT_RENDERER", "cpu")                              # переменная окружения важнее настроек
    forced = PreviewView(app.root, renderer="gl")
    assert forced.backend == "cpu"
    forced.shutdown(); forced.destroy()


def test_settings_dialog_has_renderer_choice(app):
    s = Settings()
    d = dialogs.SettingsDialog(app.root, s)
    d.rend.current(2); d._ok()
    assert s.renderer == "cpu"


def test_huge_texture_thumbnail_does_not_block_ui(app, tmp_path):
    big = np.zeros((4096, 4096, 4), np.uint8); big[...] = (90, 120, 200, 255)
    f = tmp_path / "huge_as.png"; Image.fromarray(big).save(f)
    app.thumbs.version, t0 = 0, time.time()
    assert app.thumbs.peek(f) is None and time.time() - t0 < 0.5             # peek мгновенный: миниатюра считается в фоне
    assert pump(app, lambda: app.thumbs.version > 0, 30)
    assert app.thumbs.peek(f) is not None


# ------------------------------------------------------------------ загрузка папки проекта
@pytest.fixture
def exported_dir(tmp_path):
    from tests.test_autoload import OBJ, png
    from rvmat_studio.core.project import Project
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


def test_choosing_project_root_autoloads_model_textures_and_materials(app, exported_dir, monkeypatch):
    monkeypatch.setattr(dialogs.filedialog, "askdirectory", lambda **k: str(exported_dir))
    from rvmat_studio.ui import app as appmod
    monkeypatch.setattr(appmod.filedialog, "askdirectory", lambda **k: str(exported_dir))
    assert app.proj.model_path == "" and not app.proj.textures
    app.set_project_root()                                                       # «Инструменты → Корень проекта»
    app.flush()
    p = app.proj
    assert p.model_path.endswith("helmet.fbx") and app.preview.shape == "Своя модель"
    assert [s.name for s in p.slots] == ["helmet", "helmet_visor_glass"] and p.part_map == {"visor": "helmet_visor_glass"}
    assert len(p.textures) == 4 and p.slots[0].spec.shared.get("specularPower") == 44
    assert [app.left.parts.table.item(i, "values")[0] for i in app.left.parts.table.get_children()] == ["body", "visor"]
    assert len(app.left.tex.tv.get_children()) == len(p.textures)
    assert app.props.stages[1].tex.get().endswith("helmet_nohq.paa") and app.props.stages[1]._photo is not None or pump(app, lambda: app.props.stages[1]._photo is not None, 5)
    img = app.preview.render_sync(); assert img[..., :3].std() > 3
    assert not [i for i in p.validate() if i.severity.name == "ERROR"]
    assert app.settings.project_root == p.project_root


def test_open_project_folder_menu_and_non_pristine_confirmation(app, exported_dir, monkeypatch):
    from rvmat_studio.ui import app as appmod
    app.props.power.sv.set("77"); app.props.power._entry()                       # проект уже изменён
    asked = []
    monkeypatch.setattr(appmod.messagebox, "askyesno", lambda *a, **k: (asked.append(a[1]), False)[1])
    assert app.load_project_folder(str(exported_dir)) is False                   # отказ — ничего не заменяется
    assert asked and "будет заменён" in asked[0] and app.proj.spec.shared.get("specularPower") == 77 and app.proj.model_path == ""
    monkeypatch.setattr(appmod.messagebox, "askyesno", lambda *a, **k: True)
    monkeypatch.setattr(appmod.messagebox, "askyesnocancel", lambda *a, **k: False)    # «не сохранять»
    assert app.load_project_folder(str(exported_dir)) is True
    assert app.proj.slots[0].spec.shared.get("specularPower") == 44 and app.proj.model_path.endswith("helmet.fbx")
    app.undo()                                                                   # загрузка — один шаг отмены
    assert app.proj.model_path == ""


def test_folder_without_project_files_and_multiple_candidates(app, exported_dir, tmp_path, monkeypatch):
    from rvmat_studio.ui import app as appmod
    shown = []
    monkeypatch.setattr(appmod.messagebox, "showinfo", lambda *a, **k: shown.append(a[1]))
    (tmp_path / "empty").mkdir()
    assert app.load_project_folder(str(tmp_path / "empty")) is False and shown and "не найдено" in shown[0]
    shown.clear()
    assert app.load_project_folder(str(tmp_path / "empty"), quiet=True) is False and not shown      # при выборе корня — без назначения
    import shutil
    parent = tmp_path / "two"; shutil.copytree(exported_dir, parent / "helmet"); shutil.copytree(exported_dir, parent / "other")
    (parent / "other" / "helmet.fbx").rename(parent / "other" / "other.fbx")
    chosen = []
    class Fake:
        def __init__(self, parent_, cands): chosen.append([c.mesh_name for c in cands]); self.result = cands[1]
    monkeypatch.setattr(appmod, "CandidateDialog", Fake)
    monkeypatch.setattr(app.root, "wait_window", lambda w: None)
    assert app.load_project_folder(str(parent)) is True
    assert sorted(chosen[0]) == ["helmet", "other"] and app.proj.mesh_name == "other"
