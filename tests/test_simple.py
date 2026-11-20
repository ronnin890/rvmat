"""Быстрая настройка: ползунки «по смыслу» поверх реальных полей RVMAT."""
import numpy as np
import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication

from rvmat_studio.backend import State
from rvmat_studio.core import simple as S
from rvmat_studio.core.meshes import get_shape
from rvmat_studio.core.presets import PresetManager, super_template
from rvmat_studio.core.project import Project
from rvmat_studio.core.render import RenderOptions, make_resolver, render
from rvmat_studio.core.settings import Settings
from rvmat_studio.gui.main_window import MainWindow
from rvmat_studio.gui.theme import DARK


# ------------------------------------------------------------------ математика
def test_roughness_maps_to_specular_power_and_back():
    assert S.roughness_to_power(0) == 200.0 and S.roughness_to_power(100) == 1.0
    for r in (0, 10, 33, 50, 77, 100):
        assert S.power_to_roughness(S.roughness_to_power(r)) == pytest.approx(r, abs=0.2)
    assert S.roughness_to_power(30) > S.roughness_to_power(60)        # шероховатее → меньше specularPower
    assert S.power_to_roughness(0) == pytest.approx(100)              # защита от 0


def test_shine_keeps_tint_and_alpha():
    m = super_template()
    m.set_color("specular", [0.8, 0.4, 0.2, 0.5])
    new = S.set_shine(m, 50)["specular"]
    assert np.mean(new[:3]) == pytest.approx(0.5, abs=1e-4) and new[3] == 0.5
    assert new[0] / new[1] == pytest.approx(2.0, rel=1e-3) and new[1] / new[2] == pytest.approx(2.0, rel=1e-3)   # оттенок сохранён
    m.set_color("specular", [0, 0, 0, 1])
    assert S.set_shine(m, 40)["specular"][:3] == [0.4, 0.4, 0.4]       # из нуля — нейтральный серый


def test_metallic_is_a_macro_over_real_fields_and_reads_back():
    m = super_template()
    up = S.set_metallic(m, 100)
    assert up["specular"][0] == pytest.approx(1.0) and up["diffuse"][0] == pytest.approx(0.18) and up["ambient"][0] == pytest.approx(0.18)
    for k, v in up.items():
        m.set_color(k, v)
    assert S.metallic(m) == pytest.approx(100, abs=0.5) and S.shine(m) == pytest.approx(100, abs=0.5)
    zero = S.set_metallic(m, 0)
    assert zero["diffuse"][0] == pytest.approx(0.9) and zero["specular"][0] == pytest.approx(0.1)
    assert all(0 <= x <= 4 for v in up.values() for x in v[:3])


def test_glow_and_values_stay_in_range():
    m = super_template()
    assert S.set_glow(m, 100)["emmisive"] == [1.0, 1.0, 1.0, 0]       # альфа emmisive не тронута
    assert S.glow(m) == 0 and 0 <= S.shine(m) <= 100 and 0 <= S.metallic(m) <= 100 and 0 <= S.roughness(m) <= 100
    for r in (-50, 500):
        assert 1.0 <= S.roughness_to_power(r) <= 200.0


# ------------------------------------------------------------------ эффект в картинке
def _mad(a, b):
    return np.abs(a[..., :3].astype(int) - b[..., :3].astype(int)).mean()


def test_sliders_visibly_change_the_render():
    res = make_resolver(None)
    o = RenderOptions(width=160, height=160)
    def img(**kw):
        m = super_template()
        for path, v in kw.items():
            for k, val in v.items():
                m.set_color(k, val) if isinstance(val, list) else m.set_scalar(k, val)
        return render(m, o, res)
    base = render(super_template(), o, res)
    glossy = render(_apply(super_template(), S.set_roughness, 0), o, res)
    rough = render(_apply(super_template(), S.set_roughness, 100), o, res)
    metal = render(_apply(super_template(), S.set_metallic, 100), o, res)
    assert _mad(glossy, rough) > 1.0 and _mad(base, metal) > 2.0
    # блик у гладкого материала резче/ярче: максимум яркости выше
    assert glossy[..., :3].max() >= rough[..., :3].max()


def _apply(m, fn, v):
    for k, val in fn(m, v).items():
        m.set_scalar(k, val) if isinstance(val, (int, float)) else m.set_color(k, val)
    return m


@pytest.fixture
def normal_scene(tmp_path):
    N = 128
    y, x = np.mgrid[0:N, 0:N] / N
    h = np.sin(x * 2 * np.pi * 8) * np.sin(y * 2 * np.pi * 8)
    gx, gy = np.gradient(h, axis=1) * N * 0.025, np.gradient(h, axis=0) * N * 0.025
    nm = np.zeros((N, N, 4), np.uint8)
    nm[..., 0] = np.clip(128 + gx * 127, 0, 255); nm[..., 1] = np.clip(128 + gy * 127, 0, 255); nm[..., 2] = 255; nm[..., 3] = 255
    (tmp_path / "dz").mkdir()
    Image.fromarray(nm).save(tmp_path / "dz" / "n_nohq.png")
    m = super_template(power=30)
    m.stage(1).texture = "dz\\n_nohq.paa"
    return m, make_resolver(tmp_path)


def test_normal_strength_changes_relief_only_in_preview(normal_scene):
    m, res = normal_scene
    before = m.to_text()
    mk = lambda s: render(m, RenderOptions(width=200, height=200, normal_strength=s, tiling=2), res)
    flat, normal, strong = mk(0.0), mk(1.0), mk(3.0)
    plain = render(super_template(power=30), RenderOptions(width=200, height=200, tiling=2), res)   # без нормали
    assert _mad(flat, plain) < 1.0                                    # 0 % = плоско, как без карты нормалей
    assert _mad(flat, normal) > 1.0 and _mad(normal, strong) > 0.5
    nv = lambda st: render(m, RenderOptions(width=200, height=200, normal_strength=st, tiling=2, view="normal"), res)
    n0, n1, n2, n3 = nv(0.0), nv(1.0), nv(2.0), nv(3.0)
    assert _mad(n0, n1) < _mad(n0, n2) < _mad(n0, n3)                 # отклонение нормали растёт монотонно с силой
    assert _mad(nv(0.0), render(super_template(power=30), RenderOptions(width=200, height=200, tiling=2, view="normal"), res)) < 1.0
    assert m.to_text() == before                                      # в RVMAT ничего не записано


def test_normal_strength_in_gl_matches_cpu(normal_scene):
    from rvmat_studio.core import gl_render as G
    QApplication.instance() or QApplication([])
    if not G.gl_available():
        pytest.skip("OpenGL недоступен")
    m, res = normal_scene
    g = G.GLOffscreen()
    try:
        for s in (0.0, 1.0, 2.5):
            o = RenderOptions(width=160, height=160, normal_strength=s, tiling=2)
            assert _mad(g.render(m, o, res, samples=0), render(m, o, res)) < 6.0, s
        a = g.render(m, RenderOptions(width=160, height=160, normal_strength=0.0, tiling=2), res, samples=0)
        b = g.render(m, RenderOptions(width=160, height=160, normal_strength=3.0, tiling=2), res, samples=0)
        assert _mad(a, b) > 1.0
    finally:
        g.close()


def test_project_normal_strength_is_per_material_and_persists(tmp_path):
    p = Project("m")
    p.set_normal_strength(2.0)
    p.add_slot("m_b")
    assert p.slot.normal_strength == 1.0
    f = tmp_path / "p.rvproj"; p.save(f)
    q = Project.load(f)
    assert [s.normal_strength for s in q.slots] == [2.0, 1.0]
    p.set_active(0); p.undo() if False else None
    before = {k: m.to_text() for k, m in p.kit_all().items()}
    p.set_normal_strength(0.5)
    assert {k: m.to_text() for k, m in p.kit_all().items()} == before   # RVMAT не затронуты
    p.undo()
    assert p.slots[0].normal_strength == 2.0


# ------------------------------------------------------------------ интерфейс
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


def test_quick_panel_is_the_first_thing_and_advanced_is_collapsed(app, win):
    pn = win.panel
    assert pn.tabs.tabText(0) == "Материал" and set(pn.quick) == {"rough", "shine", "metal", "glow", "normal"}
    assert not pn.adv.isChecked()
    pn.adv.setChecked(True)
    assert pn.power.isVisibleTo(pn.tabs.widget(0))
    pn.adv.setChecked(False)
    assert pn.quick["rough"].spin.suffix() == " %"
    assert "specularPower" in pn.quick_note["rough"].text()           # показывает, какое поле RVMAT меняется


def test_moving_roughness_writes_specular_power_and_updates_advanced(app, win):
    pn, sh = win.panel, win.proj.spec.shared
    pn.quick["rough"].spin.setValue(0)
    assert sh.get("specularPower") == 200.0 and pn.power.value() == 200.0   # расширенное поле синхронно
    pn.quick["rough"].spin.setValue(100)
    assert sh.get("specularPower") == 1.0
    pn.power.spin.setValue(50.0)                                           # правка «подробно» двигает ползунок
    assert pn.quick["rough"].spin.value() == pytest.approx(S.power_to_roughness(50.0), abs=0.6)


def test_metallic_slider_is_one_undo_step_and_moves_shine(app, win):
    pn = win.panel
    n_before = len(win.proj._undo)
    pn.quick["metal"].spin.setValue(100)
    sh = win.proj.spec.shared
    assert sh.get("diffuse")[0] == pytest.approx(0.18, abs=1e-5) and sh.get("specular")[0] == pytest.approx(1.0, abs=1e-5)
    assert pn.quick["shine"].spin.value() == 100                           # ползунок блика подтянулся
    assert len(win.proj._undo) == n_before + 1                             # три поля — один шаг отмены
    win.undo()
    assert sh is not None and win.proj.spec.shared.get("diffuse")[0] == pytest.approx(0.9, abs=1e-5)
    assert pn.quick["metal"].spin.value() == 0


def test_quick_reset_restores_original_values(app, win):
    pn = win.panel
    pn.quick["metal"].spin.setValue(80)
    pn.quick["metal"].btn.click()
    sh = win.proj.spec.shared
    assert sh.get("diffuse")[0] == pytest.approx(0.9, abs=1e-5) and sh.get("specular")[0] == pytest.approx(0.8, abs=1e-5)
    pn.quick["glow"].spin.setValue(60)
    assert sh.get("emmisive")[0] == pytest.approx(0.6, abs=1e-5)
    pn.quick["glow"].btn.click()
    assert win.proj.spec.shared.get("emmisive")[0] == 0


def test_quick_sliders_edit_only_the_selected_state(app, win):
    pn = win.panel
    pn.set_target(State.DAMAGE.value)
    pn.quick["rough"].spin.setValue(10)
    assert win.proj.is_overridden(State.DAMAGE.value, "specularPower")
    assert win.proj.effective(State.DAMAGE).get("specularPower") == S.roughness_to_power(10)
    assert win.proj.effective(State.DESTRUCT).get("specularPower") == 15.0
    assert pn.quick["rough"].label.property("overridden") is True
    pn.quick["rough"].btn.click()
    assert not win.proj.is_overridden(State.DAMAGE.value, "specularPower")


def test_normal_strength_slider_is_preview_only(app, win):
    pn = win.panel
    before = {k: m.to_text() for k, m in win.proj.kit_all().items()}
    pn.quick["normal"].spin.setValue(250)
    assert win.proj.slot.normal_strength == pytest.approx(2.5) and win.preview.opts.normal_strength == pytest.approx(2.5)
    assert {k: m.to_text() for k, m in win.proj.kit_all().items()} == before
    pn.quick["normal"].btn.click()
    assert win.proj.slot.normal_strength == 1.0


def test_quick_sliders_move_the_live_preview(app, win):
    win.c_shape.setCurrentText("Сфера")
    win.preview.opts.pitch = 0; win.preview.opts.yaw = 0
    win.panel.quick["rough"].spin.setValue(100)
    a = win.preview.render_sync()
    win.panel.quick["rough"].spin.setValue(0)
    b = win.preview.render_sync()
    assert _mad(a, b) > 0.5
