"""Тесты OpenGL-рендера. Пропускаются, если GL недоступен (платформа offscreen).
Запуск с GL:  LIBGL_ALWAYS_SOFTWARE=1 QT_QPA_PLATFORM=xcb xvfb-run -a pytest tests/test_gl.py"""
import time
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication

from rvmat_studio.core import gl_render as G
from rvmat_studio.core.meshes import SHAPES, get_shape, load_obj
from rvmat_studio.core.presets import super_template
from rvmat_studio.core.render import RenderOptions, make_resolver, render


@pytest.fixture(scope="module")
def gl():
    app = QApplication.instance() or QApplication([])
    if not G.gl_available():
        pytest.skip("OpenGL недоступен")
    g = G.GLOffscreen()
    yield g
    g.close()


def make_base(N=128):
    """Base Color (_co) — приходит с модели, в RVMAT его нет."""
    y, x = np.mgrid[0:N, 0:N] / N
    st = np.sin(x * 40) > 0
    a = np.zeros((N, N, 4), np.float32); a[..., 0] = np.where(st, 0.86, 0.35); a[..., 1] = np.where(st, 0.47, 0.24); a[..., 2] = 0.16; a[..., 3] = 1
    return a


BASE = make_base()


@pytest.fixture
def textured(tmp_path):
    d = tmp_path / "dz" / "t"; d.mkdir(parents=True)
    N = 128; y, x = np.mgrid[0:N, 0:N] / N
    st = np.sin(x * 40) > 0
    a = np.zeros((N, N, 4), np.uint8); a[..., 0] = np.where(st, 220, 90); a[..., 1] = np.where(st, 120, 60); a[..., 2] = 40; a[..., 3] = 255
    Image.fromarray(a).save(d / "m_as.png")
    h = np.sin(x * 2 * np.pi * 6) * np.sin(y * 2 * np.pi * 6)
    gx, gy = np.gradient(h, axis=1) * N * 0.02, np.gradient(h, axis=0) * N * 0.02
    nm = np.zeros((N, N, 4), np.uint8); nm[..., 0] = np.clip(128 + gx * 127, 0, 255); nm[..., 1] = np.clip(128 + gy * 127, 0, 255); nm[..., 2] = 255; nm[..., 3] = 255
    Image.fromarray(nm).save(d / "m_nohq.png")
    sm = np.zeros((N, N, 4), np.uint8); sm[..., 0] = 255; sm[..., 1] = np.where(st, 255, 0); sm[..., 2] = 180
    Image.fromarray(sm).save(d / "m_smdi.png")
    m = super_template()
    for i, s in ((1, "nohq"), (4, "as"), (5, "smdi")):
        m.stage(i).texture = f"dz\\t\\m_{s}.paa"
    return m, make_resolver(tmp_path)


def mad(a, b):
    return np.abs(a[..., :3].astype(int) - b[..., :3].astype(int)).mean()


@pytest.mark.parametrize("shape", list(SHAPES))
def test_gl_draws_every_shape(gl, shape):
    img = gl.render(super_template(), RenderOptions(shape=shape, width=160, height=160), make_resolver(None))
    assert img.shape == (160, 160, 4) and img[..., :3].std() > 3
    assert (img[80, 80, :3] != img[2, 2, :3]).any()          # объект перекрывает фон в центре


@pytest.mark.parametrize("view", ["final", "albedo", "ao", "normal", "specular", "metal", "gloss", "emissive", "detail", "checker"])
def test_gl_matches_cpu_renderer(gl, textured, view):
    m, res = textured
    o = RenderOptions(shape="Сфера", width=192, height=192, view=view, tiling=2, base_color=BASE)
    a, b = gl.render(m, o, res, samples=0), render(m, o, res)
    assert mad(a, b) < 6.0, f"{view}: среднее расхождение {mad(a, b):.2f}"


def test_gl_matches_cpu_on_other_shapes_and_lighting(gl, textured):
    m, res = textured
    for shape, light in (("Тор", "Закат"), ("Куб", "Ночь"), ("Цилиндр", "Пасмурно"), ("Плоскость", "Дневной свет")):
        o = RenderOptions(shape=shape, width=160, height=160, lighting=light, yaw=40, pitch=-25, base_color=BASE)
        assert mad(gl.render(m, o, res, samples=0), render(m, o, res)) < 8.0, (shape, light)


def test_gl_respects_material_params(gl):
    r = make_resolver(None)
    o = RenderOptions(width=128, height=128)
    a = gl.render(super_template(power=2), o, r)
    b = gl.render(super_template(power=120), o, r)
    c = gl.render(super_template(specular=0.0), o, r)
    assert mad(a, b) > 0.5 and mad(a, c) > 0.5


def test_gl_as_is_ambient_shadow_and_base_color_separate(gl, textured):
    m, res = textured
    o = RenderOptions(width=128, height=128, view="ao")
    on = gl.render(m, o, res, samples=0)
    o.stages_on[4] = False
    off = gl.render(m, o, res, samples=0)
    assert off[64, 64, 0] > 240 and on[64, 64, 0] < 240                  # AS → затенение; выключена → ao = 1
    # базовый цвет не зависит от Stage4
    alb = RenderOptions(width=128, height=128, view="albedo", base_color=BASE)
    a1 = gl.render(m, alb, res, samples=0)
    alb.stages_on[4] = False
    assert mad(a1, gl.render(m, alb, res, samples=0)) < 0.01
    assert a1[64, 64, 0] > 80                                             # цвет из BASE, а не серый
    m.stage(4).texture = "dz\\nope\\missing.paa"                          # нет файла — без падения
    assert gl.render(m, RenderOptions(width=64, height=64, view="ao"), res).shape == (64, 64, 4)


def test_gl_camera_wire_hdri_obj(gl, tmp_path, textured):
    m, r = textured                                   # у однотонной сферы поворот по yaw незаметен
    kw = dict(width=128, height=128, base_color=BASE)
    base = gl.render(m, RenderOptions(**kw), r)
    assert mad(base, gl.render(m, RenderOptions(yaw=120, **kw), r)) > 0.3
    assert mad(base, gl.render(m, RenderOptions(zoom=2.0, **kw), r)) > 1
    assert mad(base, gl.render(m, RenderOptions(wire=True, **kw), r)) > 0.3
    assert mad(base, gl.render(m, RenderOptions(grid=True, **kw), r)) > 0.2
    hd = np.zeros((16, 32, 3), np.float32); hd[..., 0] = 0.9
    assert mad(base, gl.render(m, RenderOptions(**kw), r, hdri=hd)) > 2
    f = tmp_path / "t.obj"; f.write_text("v 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\nvt 0 0\nvt 1 0\nvt 1 1\nvt 0 1\nf 1/1 2/2 3/3 4/4\n")
    assert gl.render(m, RenderOptions(width=96, height=96), r, load_obj(f)).std() > 2


def test_gl_is_fast_enough(gl, textured):
    m, res = textured
    o = RenderOptions(width=480, height=480)
    gl.render(m, o, res)
    t = time.time()
    for i in range(10):
        o.yaw = i * 5
        gl.render(m, o, res)
    per = (time.time() - t) / 10
    t = time.time(); render(m, RenderOptions(width=480, height=480), res); cpu = time.time() - t
    print(f"GL {per*1000:.1f} ms/frame vs CPU {cpu*1000:.0f} ms")
    assert per < cpu


def test_gl_widget_paints_and_swaps(tmp_path, gl):
    from rvmat_studio.gui.preview import GLPreviewWidget
    w = GLPreviewWidget()
    w.resize(400, 300); w.show()
    w.set_materials(super_template(), None, make_resolver(None))
    t = time.time()
    while time.time() - t < 5 and not w.has_image():
        QApplication.processEvents(); time.sleep(0.02)
    assert w.has_image() and w.scene is not None
    img = w.grab().toImage()
    assert img.width() >= 300
    assert w.render_sync().shape[2] == 4
    w.mat_b = super_template(power=100)                              # сравнение: две половины
    w.update(); QApplication.processEvents()
    w.show_uv = True; w.update(); QApplication.processEvents()
    w.shutdown(); w.close()
