"""GPU-рендер (moderngl): совпадение с процессорным рендером, части, кэш текстур, тяжёлые модели и текстуры.
Пропускается, если нельзя создать контекст OpenGL 3.3 (на Linux: xvfb-run + Mesa)."""
import time

import numpy as np
import pytest
from PIL import Image

from rvmat_studio.core import glrender as G
from rvmat_studio.core.meshes import Mesh, SHAPES, get_shape
from rvmat_studio.core.pick import pick_part_ray
from rvmat_studio.core.presets import super_template
from rvmat_studio.core.render import RenderCache, RenderOptions, make_resolver, render


@pytest.fixture(scope="module")
def gl():
    if not G.available():
        pytest.skip("moderngl не установлен")
    t = G.GLThread()
    t.ready.wait(20)
    if t.renderer is None:
        pytest.skip(f"нет OpenGL 3.3: {t.error}")
    yield t
    t.stop()


def mad(a, b):
    return np.abs(a[..., :3].astype(int) - b[..., :3].astype(int)).mean()


def draw(gl, mat, o, find, mesh=None, parts=None):
    mesh = mesh or get_shape(o.shape)
    return gl.call(lambda r: r.render(mat, o, find, mesh, parts))


@pytest.fixture
def scene(tmp_path):
    d = tmp_path / "dz" / "t"; d.mkdir(parents=True)
    N = 128; y, x = np.mgrid[0:N, 0:N] / N
    st = np.sin(x * 40) > 0
    a = np.zeros((N, N, 4), np.uint8); a[..., 0] = 255; a[..., 1] = np.where(st, 255, 90); a[..., 2] = 255; a[..., 3] = 255
    Image.fromarray(a).save(d / "m_as.png")
    h = np.sin(x * 2 * np.pi * 8) * np.sin(y * 2 * np.pi * 8)
    gx, gy = np.gradient(h, axis=1) * N * 0.02, np.gradient(h, axis=0) * N * 0.02
    nm = np.zeros((N, N, 4), np.uint8); nm[..., 0] = np.clip(128 + gx * 127, 0, 255); nm[..., 1] = np.clip(128 + gy * 127, 0, 255); nm[..., 2] = 255; nm[..., 3] = 255
    Image.fromarray(nm).save(d / "m_nohq.png")
    sm = np.zeros((N, N, 4), np.uint8); sm[..., 0] = 255; sm[..., 1] = np.where(st, 230, 40); sm[..., 2] = np.where(st, 160, 60); sm[..., 3] = 255
    Image.fromarray(sm).save(d / "m_smdi.png")
    base = np.zeros((N, N, 4), np.float32); base[..., 0] = np.where(st, 0.8, 0.2); base[..., 1] = 0.3; base[..., 2] = 0.2; base[..., 3] = 1
    Image.fromarray((base * 255).astype(np.uint8)).save(d / "m_co.png")
    m = super_template(power=40)
    for i, s in ((1, "nohq"), (4, "as"), (5, "smdi")):
        m.stage(i).texture = f"dz\\t\\m_{s}.paa"
    res = make_resolver(tmp_path)
    return m, res, base, str(d / "m_co.png")


@pytest.mark.parametrize("view", ["final", "albedo", "ao", "normal", "specular", "metal", "gloss", "emissive", "detail", "checker"])
def test_gl_matches_cpu(gl, scene, view):
    m, res, base, base_path = scene
    o = RenderOptions(width=192, height=192, view=view, tiling=2, base_color=base)
    og = RenderOptions(width=192, height=192, view=view, tiling=2, base_color=base_path)
    assert mad(draw(gl, m, og, res.find), render(m, o, res)) < 7.0, view


def test_gl_shapes_lighting_and_camera(gl, scene):
    m, res, base, bp = scene
    for shape, light in (("Тор", "Закат"), ("Куб", "Ночь"), ("Цилиндр", "Пасмурно"), ("Плоскость", "Дневной свет")):
        og = RenderOptions(shape=shape, width=160, height=160, lighting=light, yaw=40, pitch=-25, base_color=bp)
        o = RenderOptions(shape=shape, width=160, height=160, lighting=light, yaw=40, pitch=-25, base_color=base)
        assert mad(draw(gl, m, og, res.find), render(m, o, res)) < 9.0, (shape, light)
    a = draw(gl, m, RenderOptions(width=128, height=128, base_color=bp), res.find)
    assert mad(a, draw(gl, m, RenderOptions(width=128, height=128, base_color=bp, yaw=120), res.find)) > 0.3
    assert mad(a, draw(gl, m, RenderOptions(width=128, height=128, base_color=bp, zoom=2.0), res.find)) > 1
    assert mad(a, draw(gl, m, RenderOptions(width=128, height=128, base_color=bp, wire=True), res.find)) > 0.3
    assert mad(a, draw(gl, m, RenderOptions(width=128, height=128, base_color=bp, grid=True), res.find)) > 0.2
    assert mad(a, draw(gl, m, RenderOptions(width=128, height=128, base_color=bp, pan=(0.3, 0.1)), res.find)) > 0.5


def test_gl_material_params_normal_strength_stage_toggle_and_hdri(gl, scene):
    m, res, base, bp = scene
    o = lambda **kw: RenderOptions(width=128, height=128, base_color=bp, **kw)
    a = draw(gl, super_template(power=2), o(), res.find)
    assert mad(a, draw(gl, super_template(power=120), o(), res.find)) > 0.5
    n0, n3 = draw(gl, m, o(normal_strength=0.0), res.find), draw(gl, m, o(normal_strength=3.0), res.find)
    assert mad(n0, n3) > 1.0
    on = draw(gl, m, o(view="ao"), res.find)
    off_o = o(view="ao"); off_o.stages_on[4] = False
    off = draw(gl, m, off_o, res.find)
    assert off[64, 64, 0] > 240 and mad(on, off) > 1.0                      # стадию AS в превью можно отключить
    hd = np.zeros((16, 32, 3), np.float32); hd[..., 0] = 0.9
    oh = o(); oh.hdri = hd
    assert mad(draw(gl, m, o(), res.find), draw(gl, m, oh, res.find)) > 2


def test_gl_parts_materials_highlight_and_cpu_parity(gl):
    mesh, res = get_shape("Сфера"), make_resolver(None)
    m = super_template()
    solid = lambda rgb: np.tile(np.array([*rgb, 255], np.float32) / 255, (8, 8, 1))
    import tempfile, pathlib
    d = pathlib.Path(tempfile.mkdtemp())
    for n, c in (("blue", (20, 20, 230)), ("red", (230, 20, 20))):
        Image.fromarray(np.tile(np.array([*c, 255], np.uint8), (8, 8, 1))).save(d / f"{n}.png")
    parts_gl = {0: (m, str(d / "blue.png"), 1.0), 1: (m, str(d / "red.png"), 1.0)}
    o = RenderOptions(width=200, height=200, view="albedo", pitch=0, yaw=0)
    img = draw(gl, m, o, res.find, mesh, parts_gl)
    assert img[50, 100, 0] > 150 and img[150, 100, 2] > 150                   # верх красный, низ синий
    cpu = render(m, o, res, mesh, RenderCache(), {0: (m, solid((20, 20, 230))), 1: (m, solid((230, 20, 20)))})
    assert mad(img, cpu) < 7
    h = RenderOptions(width=200, height=200, pitch=0, yaw=0, highlight=1)
    p = RenderOptions(width=200, height=200, pitch=0, yaw=0)
    assert np.abs(draw(gl, m, h, res.find, mesh, {0: (m, None, 1.0), 1: (m, None, 1.0)})[50, 100, :3].astype(int) - draw(gl, m, p, res.find, mesh, {0: (m, None, 1.0), 1: (m, None, 1.0)})[50, 100, :3].astype(int)).sum() > 20


def test_gl_texture_cache_is_bounded_and_never_evicts_current_frame(gl, tmp_path):
    r = gl.renderer
    mesh = get_shape("Сфера")
    m = super_template()
    for i in range(G.KEEP_TEXTURES + 12):
        p = tmp_path / f"t{i}.png"
        Image.fromarray(np.full((8, 8, 4), (i * 5) % 255, np.uint8)).save(p)
        o = RenderOptions(width=48, height=48, view="albedo", base_color=str(p))
        img = draw(gl, m, o, lambda s: None, mesh)
        assert img.shape == (48, 48, 4)
    assert len(r.tex) <= G.KEEP_TEXTURES + 2
    # изменённый файл перечитывается (ключ — путь + время изменения)
    p = tmp_path / "chg.png"
    Image.fromarray(np.tile(np.array([250, 10, 10, 255], np.uint8), (8, 8, 1))).save(p)
    o = RenderOptions(width=64, height=64, view="albedo", base_color=str(p))
    a = draw(gl, m, o, lambda s: None, mesh)
    time.sleep(0.02)
    Image.fromarray(np.tile(np.array([10, 10, 250, 255], np.uint8), (8, 8, 1))).save(p)
    import os; os.utime(p, (time.time() + 5, time.time() + 5))
    b = draw(gl, m, o, lambda s: None, mesh)
    assert a[32, 32, 0] > 150 and b[32, 32, 2] > 150


def _big_sphere(nu, nv):
    th = np.linspace(0, 2 * np.pi, nu + 1); ph = np.linspace(0, np.pi, nv + 1)
    T, P = np.meshgrid(th, ph)
    v = np.stack([(np.sin(P) * np.cos(T)).ravel(), np.cos(P).ravel(), (np.sin(P) * np.sin(T)).ravel()], 1).astype(np.float32)
    uv = np.stack([(T / (2 * np.pi)).ravel(), (P / np.pi).ravel()], 1).astype(np.float32)
    idx = np.arange((nu + 1) * (nv + 1)).reshape(nv + 1, nu + 1)
    a, b, c, d = idx[:-1, :-1].ravel(), idx[:-1, 1:].ravel(), idx[1:, 1:].ravel(), idx[1:, :-1].ravel()
    f = np.concatenate([np.stack([a, b, c], 1), np.stack([a, c, d], 1)])
    return Mesh(v, v.copy(), uv, f.astype(np.int64)).finalize()


def test_gl_renders_million_triangle_model_and_big_texture(gl, tmp_path):
    mesh = _big_sphere(1000, 500)                                          # 1 000 000 треугольников
    assert len(mesh.f) == 1_000_000
    n = 4096
    big = np.zeros((n, n, 4), np.uint8); big[..., 0] = (np.arange(n)[None, :] // 64 % 2) * 200 + 30; big[..., 1] = 90; big[..., 2] = 60; big[..., 3] = 255
    p = tmp_path / "huge_co.png"; Image.fromarray(big).save(p)           # текстура 4096×4096
    m = super_template(power=30)
    o = RenderOptions(width=640, height=480, view="albedo", base_color=str(p), tiling=2)
    t0 = time.time(); first = draw(gl, m, o, lambda s: None, mesh); t_first = time.time() - t0
    t0 = time.time()
    for i in range(3):
        o.yaw = 10 * i
        img = draw(gl, m, o, lambda s: None, mesh)
    t_next = (time.time() - t0) / 3
    print(f"\n1M tris + 4096² texture: first frame {t_first:.2f}s, next {t_next * 1000:.0f} ms (программный GL llvmpipe)")
    assert first[..., :3].std() > 5 and (first[240, 320, 0] != first[0, 0, 0])
    assert t_next < 5.0


def test_gl_pick_and_ray_parity():
    mesh = get_shape("Сфера")
    o = RenderOptions(width=200, height=200, pitch=0, yaw=0)
    assert pick_part_ray(mesh, o, 200, 200, 100, 50) == 1 and pick_part_ray(mesh, o, 200, 200, 100, 150) == 0
    assert pick_part_ray(mesh, o, 200, 200, 3, 3) == -1 and pick_part_ray(get_shape("Плоскость"), o, 200, 200, 100, 100) == -1
    big = _big_sphere(700, 350)
    big.set_parts((big.v[big.f].mean(1)[:, 1] >= 0).astype(int), ["низ", "верх"])
    t0 = time.time()
    assert pick_part_ray(big, o, 200, 200, 100, 50) == 1 and pick_part_ray(big, o, 200, 200, 100, 150) == 0
    assert time.time() - t0 < 5


def test_glthread_reports_errors_and_stops(gl):
    with pytest.raises(ZeroDivisionError):
        gl.call(lambda r: 1 / 0)
    assert gl.call(lambda r: r.max_tex) >= 2048


def test_no_moderngl_means_cpu_fallback(monkeypatch):
    monkeypatch.setattr(G, "moderngl", None)
    assert not G.available()
    with pytest.raises(G.GLUnavailable):
        G.GLRenderer()
