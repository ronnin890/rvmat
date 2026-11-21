"""Трактовка _as и _smdi по описанию Super-шейдера: AS — затенение в G; SMDI — R=1, G=specular, B=specular power."""
import numpy as np
import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication

from rvmat_studio.core.meshes import get_shape
from rvmat_studio.core.presets import super_template
from rvmat_studio.core.render import RenderOptions, make_resolver, render


def mad(a, b):
    return np.abs(a[..., :3].astype(int) - b[..., :3].astype(int)).mean()


_n = [0]


def scene(tmp_path, smdi=None, as_=None, power=40):
    """У каждой сцены свои файлы (иначе поздняя сцена перезаписывает текстуру ранней)."""
    (tmp_path / "dz").mkdir(exist_ok=True)
    _n[0] += 1
    k = _n[0]
    m = super_template(power=power)
    if smdi is not None:
        Image.fromarray(np.tile(np.array(smdi, np.uint8), (8, 8, 1))).save(tmp_path / "dz" / f"x{k}_smdi.png")
        m.stage(5).texture = f"dz\\x{k}_smdi.paa"
    if as_ is not None:
        Image.fromarray(np.tile(np.array(as_, np.uint8), (8, 8, 1))).save(tmp_path / "dz" / f"x{k}_as.png")
        m.stage(4).texture = f"dz\\x{k}_as.paa"
    return m, make_resolver(tmp_path)


O = dict(width=160, height=160)


def test_smdi_alpha_is_never_glow(tmp_path):
    m1, r1 = scene(tmp_path, smdi=[255, 200, 200, 255])
    a = render(m1, RenderOptions(**O), r1)
    m2, r2 = scene(tmp_path, smdi=[255, 200, 200, 0])
    assert mad(a, render(m2, RenderOptions(**O), r2)) < 0.01            # альфа не влияет
    plain = render(super_template(power=40), RenderOptions(**O), make_resolver(None))
    assert a[..., :3].mean() < plain[..., :3].mean() + 25               # без самосвечения: не светлее «чистого» материала


def test_smdi_r_is_ignored_and_g_is_specular(tmp_path):
    lo, rlo = scene(tmp_path, smdi=[255, 0, 255, 255])
    hi, rhi = scene(tmp_path, smdi=[255, 255, 255, 255])
    r0, rr0 = scene(tmp_path, smdi=[0, 255, 255, 255])
    a_lo, a_hi = render(lo, RenderOptions(**O), rlo), render(hi, RenderOptions(**O), rhi)
    hot = lambda a: int((a[..., :3].mean(-1) > 200).sum())
    assert mad(a_lo, a_hi) > 1.0 and hot(a_hi) > hot(a_lo)                           # G=0 → нет блика; G=1 → блик
    assert mad(a_hi, render(r0, RenderOptions(**O), rr0)) < 0.01                    # R не влияет (по спецификации R = 1)
    # режимы просмотра показывают именно эти каналы
    spec = render(hi, RenderOptions(view="specular", **O), rhi)
    assert spec[80, 80, 0] > 240 and render(lo, RenderOptions(view="specular", **O), rlo)[80, 80, 0] < 15


def test_smdi_b_scales_specular_power(tmp_path):
    glossy, rg = scene(tmp_path, smdi=[255, 255, 255, 255], power=80)
    rough, rr = scene(tmp_path, smdi=[255, 255, 40, 255], power=80)
    a, b = render(glossy, RenderOptions(**O), rg), render(rough, RenderOptions(**O), rr)
    assert mad(a, b) > 0.5                                                          # меньше B → другой (шире/слабее) блик
    assert render(rough, RenderOptions(view="gloss", **O), rr)[80, 80, 0] < 120


def test_as_ambient_shadow_is_in_green_channel(tmp_path):
    m, r = scene(tmp_path, as_=[255, 40, 255, 255])           # затенение в G
    ao = render(m, RenderOptions(view="ao", **O), r)
    assert 100 < ao[80, 80, 0] < 120
    m2, r2 = scene(tmp_path, as_=[40, 255, 40, 255])          # R/B тёмные, G = 1 → затенения нет
    assert render(m2, RenderOptions(view="ao", **O), r2)[80, 80, 0] > 240
    pink = render(*scene(tmp_path, as_=[255, 40, 255, 255])[:1], RenderOptions(**O), r)
    clear = render(*scene(tmp_path, as_=[255, 255, 255, 255])[:1], RenderOptions(**O), r)
    assert pink[..., :3].mean() < clear[..., :3].mean()


def test_no_smdi_means_plain_rvmat_values(tmp_path):
    m = super_template(power=40)
    a = render(m, RenderOptions(**O), make_resolver(None))
    m2, r2 = scene(tmp_path, smdi=[255, 255, 255, 255], power=40)
    assert mad(a, render(m2, RenderOptions(**O), r2)) < 0.5            # SMDI из единиц = «без карты»


def test_gl_matches_cpu_for_new_smdi_and_as(tmp_path):
    from rvmat_studio.core import gl_render as G
    QApplication.instance() or QApplication([])
    if not G.gl_available():
        pytest.skip("OpenGL недоступен")
    m, res = scene(tmp_path, smdi=[255, 180, 90, 255], as_=[255, 90, 255, 255], power=60)
    g = G.GLOffscreen()
    try:
        for view in ("final", "specular", "gloss", "metal", "ao"):
            o = RenderOptions(view=view, **O)
            assert mad(g.render(m, o, res, samples=0), render(m, o, res)) < 6.0, view
    finally:
        g.close()


# ------------------------------------------------------------------ отражения и сохранение энергии
def test_glossy_metal_surface_is_bright_reflection_not_albedo(tmp_path):
    """Блестящая поверхность (G высокий) со «скучным» тёмным альбедо выглядит светлее за счёт отражения окружения;
    матовая (G низкий) остаётся тёмной."""
    base = np.tile(np.array([40, 40, 40, 255], np.float32) / 255, (8, 8, 1))
    shiny, rs = scene(tmp_path, smdi=[255, 240, 140, 255], power=40)
    matte, rm = scene(tmp_path, smdi=[255, 20, 140, 255], power=40)
    o = dict(width=160, height=160, base_color=base)
    a, b = render(shiny, RenderOptions(**o), rs), render(matte, RenderOptions(**o), rm)
    assert a[..., :3].mean() > b[..., :3].mean() + 5


def test_env_gain_scales_reflections_only_on_specular_areas(tmp_path):
    shiny, rs = scene(tmp_path, smdi=[255, 240, 140, 255])
    matte, rm = scene(tmp_path, smdi=[255, 0, 140, 255])
    lo, hi = dict(width=160, height=160, env_gain=0.0), dict(width=160, height=160, env_gain=4.0)
    assert mad(render(shiny, RenderOptions(**lo), rs), render(shiny, RenderOptions(**hi), rs)) > 3.0
    assert mad(render(matte, RenderOptions(**lo), rm), render(matte, RenderOptions(**hi), rm)) < 0.5      # G=0 → отражения нет


def test_rough_gloss_blurs_reflection(tmp_path):
    """Низкий B (шероховатость) даёт менее контрастное отражение, чем высокий."""
    sharp, r1 = scene(tmp_path, smdi=[255, 240, 255, 255])
    rough, r2 = scene(tmp_path, smdi=[255, 240, 30, 255])
    base = np.tile(np.array([30, 30, 30, 255], np.float32) / 255, (8, 8, 1))
    a = render(sharp, RenderOptions(width=160, height=160, base_color=base, env_gain=3.0, pitch=-60), r1)
    b = render(rough, RenderOptions(width=160, height=160, base_color=base, env_gain=3.0, pitch=-60), r2)
    assert mad(a, b) > 1.0


def test_gl_matches_cpu_with_env_gain(tmp_path):
    from rvmat_studio.core import gl_render as G
    QApplication.instance() or QApplication([])
    if not G.gl_available():
        pytest.skip("OpenGL недоступен")
    m, res = scene(tmp_path, smdi=[255, 220, 100, 255], as_=[255, 120, 255, 255], power=50)
    g = G.GLOffscreen()
    try:
        for gain in (0.0, 1.0, 4.0):
            o = RenderOptions(width=160, height=160, env_gain=gain)
            assert mad(g.render(m, o, res, samples=0), render(m, o, res)) < 6.0, gain
    finally:
        g.close()
