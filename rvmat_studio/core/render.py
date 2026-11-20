"""Программный (numpy) предпросмотр материала.

ВАЖНО: это приближение шейдера DayZ "Super", а не сам движок Enfusion.
Интерпретация каналов (приближение): AS.rgb — цвет; nohq.rg — XY нормали;
SMDI: R — specular, G — metal, B — gloss, A — свечение; DT — множитель детали (×2);
Fresnel — по параметрам fresnel(n,k); окружение — процедурное небо или пользовательский HDRI/панорама.
См. DIFFERENCES — список известных отличий от игры.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import numpy as np
from PIL import Image, ImageDraw

from ..backend import Material
from ..backend.procedural import is_procedural, parse_procedural
from .meshes import Mesh, SHAPES, get_shape
from .textures import load_rgba

DIFFERENCES = [
    "Рендер — приближение шейдера Super, а не движок Enfusion.",
    "Карты MC (damage/destruct) игры не применяются: показывается только основной материал.",
    "Окружение (Stage7) заменено процедурным небом/панорамой; кубическая env-карта игры не используется.",
    "Интерпретация SMDI (R=specular, G=metal, B=gloss, A=glow) — оценочная.",
    "Нет теней, SSAO, пост-обработки и HDR-экспозиции игры.",
]

LIGHTING = {   # sun_dir (в пространстве камеры), sun_color, sun_int, sky_top, sky_horizon, ground, ambient
    "Студия": dict(sun=(-0.5, 0.6, 0.65), col=(1.0, 0.98, 0.95), i=1.0, top=(0.55, 0.6, 0.7), hor=(0.35, 0.37, 0.4), gnd=(0.12, 0.12, 0.13), amb=0.35),
    "Дневной свет": dict(sun=(0.4, 0.8, 0.45), col=(1.0, 0.95, 0.85), i=1.3, top=(0.35, 0.55, 0.9), hor=(0.75, 0.82, 0.9), gnd=(0.25, 0.22, 0.18), amb=0.45),
    "Закат": dict(sun=(-0.8, 0.15, 0.55), col=(1.0, 0.55, 0.3), i=1.2, top=(0.25, 0.3, 0.55), hor=(0.95, 0.5, 0.3), gnd=(0.12, 0.08, 0.08), amb=0.3),
    "Пасмурно": dict(sun=(0.0, 1.0, 0.3), col=(0.85, 0.88, 0.92), i=0.5, top=(0.6, 0.62, 0.66), hor=(0.55, 0.57, 0.6), gnd=(0.3, 0.3, 0.3), amb=0.6),
    "Ночь": dict(sun=(0.3, 0.7, 0.6), col=(0.55, 0.65, 1.0), i=0.35, top=(0.02, 0.03, 0.08), hor=(0.05, 0.06, 0.1), gnd=(0.01, 0.01, 0.015), amb=0.1),
}


@dataclass
class RenderOptions:
    width: int = 480
    height: int = 480
    shape: str = "Сфера"
    yaw: float = 25.0
    pitch: float = -15.0
    zoom: float = 1.0
    pan: tuple = (0.0, 0.0)
    tiling: float = 1.0
    lighting: str = "Студия"
    light_rot: float = 0.0
    view: str = "final"      # final|albedo|normal|specular|metal|gloss|emissive|detail|checker
    stages_on: dict = field(default_factory=lambda: {i: True for i in range(1, 8)})
    wire: bool = False
    grid: bool = False
    ssaa: int = 1


TexResolver = Callable[[str], Optional[np.ndarray]]   # игровой путь -> RGBA float32 [0..1] или None


def make_resolver(project_root: Optional[Path], extra_dirs=(), max_size=768, mapping: Optional[dict] = None) -> TexResolver:
    cache: dict = {}
    mapping = {k.lower(): v for k, v in (mapping or {}).items()}

    def find(tex: str) -> Optional[Path]:
        mp = mapping.get(tex.replace('/', '\\').strip('\\').lower())
        if mp and Path(mp).is_file():
            return Path(mp)
        parts = tex.replace("/", "\\").strip("\\").split("\\")
        roots = ([Path(project_root)] if project_root else []) + [Path(d) for d in extra_dirs]
        for r in roots:
            cur = r
            for part in parts:
                if not cur.is_dir():
                    cur = None
                    break
                hit = next((c for c in cur.iterdir() if c.name.lower() == part.lower()), None)
                if hit is None and part is parts[-1]:           # допускаем исходник вместо .paa
                    stem = Path(part).stem.lower()
                    hit = next((c for c in cur.iterdir() if c.stem.lower() == stem and c.suffix.lower() in
                                (".png", ".tga", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp")), None)
                if hit is None:
                    cur = None
                    break
                cur = hit
            if cur is not None and cur.is_file():
                return cur
        p = Path(tex)
        return p if p.is_absolute() and p.is_file() else None

    def resolve(tex: str):
        if not tex or is_procedural(tex):
            return None
        try:
            f = find(tex)
            if f is None:
                return None
            key = (str(f), f.stat().st_mtime_ns)
            if key not in cache:
                arr, _ = load_rgba(f)
                im = Image.fromarray(arr)
                if max(im.size) > max_size:
                    im.thumbnail((max_size, max_size))
                cache[key] = np.asarray(im).astype(np.float32) / 255
            return cache[key]
        except Exception:
            return None
    resolve.find = find  # type: ignore
    return resolve


def sample(tex: np.ndarray, uv: np.ndarray) -> np.ndarray:
    """Билинейная выборка с повторением. uv (K,2) -> (K,4)."""
    h, w = tex.shape[:2]
    x = uv[:, 0] * w - 0.5
    y = (1 - uv[:, 1]) * h - 0.5 if False else uv[:, 1] * h - 0.5
    x0, y0 = np.floor(x).astype(np.int64), np.floor(y).astype(np.int64)
    fx, fy = (x - x0)[:, None], (y - y0)[:, None]
    x0m, x1m, y0m, y1m = x0 % w, (x0 + 1) % w, y0 % h, (y0 + 1) % h
    return ((tex[y0m, x0m] * (1 - fx) + tex[y0m, x1m] * fx) * (1 - fy) +
            (tex[y1m, x0m] * (1 - fx) + tex[y1m, x1m] * fx) * fy)


def _uv_matrix(stage):
    if stage is None:
        return np.array([[1, 0], [0, 1]], np.float32), np.zeros(2, np.float32)
    a, u, p = stage.uv("aside") or [1, 0, 0], stage.uv("up") or [0, 1, 0], stage.uv("pos") or [0, 0, 0]
    # u' = a.x*u + up.x*v + p.x ; v' = a.y*u + up.y*v + p.y
    return np.array([[a[0], u[0]], [a[1], u[1]]], np.float32), np.array(p[:2], np.float32)


def _rot(yaw, pitch):
    cy, sy, cp, sp = math.cos(math.radians(yaw)), math.sin(math.radians(yaw)), math.cos(math.radians(pitch)), math.sin(math.radians(pitch))
    Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    Rx = np.array([[1, 0, 0], [0, cp, -sp], [0, sp, cp]])
    return (Rx @ Ry).astype(np.float32)


def _checker(uv):
    c = ((np.floor(uv[:, 0] * 8) + np.floor(uv[:, 1] * 8)) % 2)
    g = 0.25 + 0.55 * c
    col = np.stack([g, g, g, np.ones_like(g)], 1)
    col[:, 0] += 0.25 * (uv[:, 0] % 1)
    col[:, 2] += 0.25 * (uv[:, 1] % 1)
    return np.clip(col, 0, 1)


def env_color(d: np.ndarray, L: dict, hdri: Optional[np.ndarray]) -> np.ndarray:
    if hdri is not None:
        u = (np.arctan2(d[:, 0], -d[:, 2]) / (2 * np.pi) + 0.5) % 1
        v = np.arccos(np.clip(d[:, 1], -1, 1)) / np.pi
        return sample(hdri, np.stack([u, v], 1))[:, :3]
    y = d[:, 1:2]
    top, hor, gnd = (np.array(L[k], np.float32) for k in ("top", "hor", "gnd"))
    sky = hor + (top - hor) * np.clip(y, 0, 1) ** 0.6
    g = hor + (gnd - hor) * np.clip(-y * 3, 0, 1)
    c = np.where(y >= 0, sky, g)
    s = np.array(L["sun"], np.float32)
    s /= np.linalg.norm(s)
    c = c + np.array(L["col"], np.float32) * (np.clip(d @ s, 0, 1) ** 200 * 6 * L["i"])[:, None]
    return c


def _fresnel_curve(n: float, k: float, cos: np.ndarray) -> np.ndarray:
    f0 = ((n - 1) ** 2 + k * k) / ((n + 1) ** 2 + k * k)
    return f0 + (1 - f0) * (1 - np.clip(cos, 0, 1)) ** 5


def project_mesh(mesh: Mesh, o: RenderOptions, W, H):
    R = _rot(o.yaw, o.pitch)
    P = mesh.v @ R.T
    N = mesh.n @ R.T
    T = mesh.t @ R.T
    dist = 4.0 / max(o.zoom, 0.05)
    f = 1 / math.tan(math.radians(17.5))
    w = dist - P[:, 2]
    sx = (P[:, 0] - o.pan[0]) / w * f * H / 2 + W / 2
    sy = H / 2 - (P[:, 1] - o.pan[1]) / w * f * H / 2
    return P, N, T, w, sx, sy, dist


def _raster_big(F, x, y, iw, area, ids, W, H, tri, b0, b1, zb):
    x0 = np.maximum(np.floor(x.min(1)).astype(int), 0)
    x1 = np.minimum(np.ceil(x.max(1)).astype(int), W - 1)
    y0 = np.maximum(np.floor(y.min(1)).astype(int), 0)
    y1 = np.minimum(np.ceil(y.max(1)).astype(int), H - 1)
    for i in ids:
        xs = np.arange(x0[i], x1[i] + 1) + 0.5
        ys = np.arange(y0[i], y1[i] + 1) + 0.5
        px, py = np.meshgrid(xs, ys)
        ax, ay, bx, by, cx, cy = x[i, 0], y[i, 0], x[i, 1], y[i, 1], x[i, 2], y[i, 2]
        l1 = ((px - ax) * (cy - ay) - (cx - ax) * (py - ay)) / area[i]
        l2 = ((bx - ax) * (py - ay) - (px - ax) * (by - ay)) / area[i]
        l0 = 1 - l1 - l2
        inside = (l0 >= -1e-6) & (l1 >= -1e-6) & (l2 >= -1e-6)
        if not inside.any():
            continue
        i0, i1, i2 = iw[F[i]]
        z = l0 * i0 + l1 * i1 + l2 * i2
        sl = (slice(y0[i], y1[i] + 1), slice(x0[i], x1[i] + 1))
        m = inside & (z > zb[sl])
        if m.any():
            zb[sl][m] = z[m]
            tri[sl][m] = i
            b0[sl][m] = (l0 * i0 / z)[m]
            b1[sl][m] = (l1 * i1 / z)[m]


def rasterize(mesh: Mesh, sx, sy, w, W, H):
    """Z-буфер. Мелкие треугольники обрабатываются пачками (векторизованно), крупные — по одному."""
    tri = np.full((H, W), -1, np.int32)
    b0 = np.zeros((H, W), np.float32)
    b1 = np.zeros((H, W), np.float32)
    zb = np.zeros((H, W), np.float32)      # 1/w
    iw = (1 / w).astype(np.float32)
    F = mesh.f
    x, y = sx[F].astype(np.float32), sy[F].astype(np.float32)
    area = (x[:, 1] - x[:, 0]) * (y[:, 2] - y[:, 0]) - (x[:, 2] - x[:, 0]) * (y[:, 1] - y[:, 0])
    x0 = np.maximum(np.floor(x.min(1)).astype(np.int32), 0)
    x1 = np.minimum(np.ceil(x.max(1)).astype(np.int32), W - 1)
    y0 = np.maximum(np.floor(y.min(1)).astype(np.int32), 0)
    y1 = np.minimum(np.ceil(y.max(1)).astype(np.int32), H - 1)
    ok = (np.abs(area) > 1e-9) & (x1 >= x0) & (y1 >= y0)
    size = np.maximum(x1 - x0, y1 - y0) + 1
    frags = []
    lo = 0
    for hi in (4, 8, 16, 32, 64):
        ids = np.nonzero(ok & (size > lo) & (size <= hi))[0]
        lo = hi
        if not len(ids):
            continue
        step = max(1, 3_000_000 // (hi * hi))
        ar = np.arange(hi, dtype=np.int32)
        for k in range(0, len(ids), step):
            t = ids[k:k + step]
            gx = x0[t][:, None, None] + ar[None, None, :]
            gy = y0[t][:, None, None] + ar[None, :, None]
            px, py = gx + np.float32(0.5), gy + np.float32(0.5)
            ax, ay = x[t, 0][:, None, None], y[t, 0][:, None, None]
            bx, by = x[t, 1][:, None, None], y[t, 1][:, None, None]
            cx, cy = x[t, 2][:, None, None], y[t, 2][:, None, None]
            ar_ = area[t][:, None, None]
            l1 = ((px - ax) * (cy - ay) - (cx - ax) * (py - ay)) / ar_
            l2 = ((bx - ax) * (py - ay) - (px - ax) * (by - ay)) / ar_
            l0 = 1 - l1 - l2
            inside = (l0 >= -1e-6) & (l1 >= -1e-6) & (l2 >= -1e-6) & (gx <= x1[t][:, None, None]) & (gy <= y1[t][:, None, None])
            ti, yi, xi = np.nonzero(inside)
            if not len(ti):
                continue
            L0, L1, L2 = l0[ti, yi, xi], l1[ti, yi, xi], l2[ti, yi, xi]
            tt = t[ti]
            i0, i1, i2 = iw[F[tt, 0]], iw[F[tt, 1]], iw[F[tt, 2]]
            z = L0 * i0 + L1 * i1 + L2 * i2
            pix = gy[ti, yi, 0].astype(np.int64) * W + gx[ti, 0, xi]
            frags.append((pix, z, tt, L0 * i0 / z, L1 * i1 / z))
    if frags:
        pix = np.concatenate([f[0] for f in frags]); z = np.concatenate([f[1] for f in frags])
        tt = np.concatenate([f[2] for f in frags]); fb0 = np.concatenate([f[3] for f in frags]); fb1 = np.concatenate([f[4] for f in frags])
        order = np.argsort(z, kind="stable")           # дальние первыми, ближние перезаписывают (последний индекс побеждает)
        pix, z, tt, fb0, fb1 = pix[order], z[order], tt[order], fb0[order], fb1[order]
        tri.reshape(-1)[pix] = tt
        b0.reshape(-1)[pix] = fb0
        b1.reshape(-1)[pix] = fb1
        zb.reshape(-1)[pix] = z
    big = np.nonzero(ok & (size > 64))[0]
    if len(big):
        _raster_big(F, x, y, iw, area, big, W, H, tri, b0, b1, zb)
    return tri, b0, b1


class Geo:
    """Геометрическая часть кадра (не зависит от материала): кэшируется между правками параметров."""
    __slots__ = ("ys", "xs", "pos", "nrm", "tan", "bit", "uv", "view", "samples", "tri")


def prep_geometry(mesh, P, N, T, tri, b0, b1, dist) -> Geo:
    g = Geo()
    sel = tri >= 0
    g.ys, g.xs = np.nonzero(sel)
    t = tri[sel]
    B0, B1 = b0[sel][:, None], b1[sel][:, None]
    B2 = 1 - B0 - B1
    F = mesh.f[t]
    interp = lambda A: (A[F[:, 0]] * B0 + A[F[:, 1]] * B1 + A[F[:, 2]] * B2).astype(np.float32)
    pos, nrm, tan, uv = interp(P), interp(N), interp(T), interp(mesh.uv)
    nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-8)
    view = np.array([0, 0, dist], np.float32) - pos
    view /= np.maximum(np.linalg.norm(view, axis=1, keepdims=True), 1e-8)
    flip = ((nrm * view).sum(1) < 0)[:, None]
    nrm = np.where(flip, -nrm, nrm)
    tan = tan - nrm * (tan * nrm).sum(1, keepdims=True)
    tan /= np.maximum(np.linalg.norm(tan, axis=1, keepdims=True), 1e-8)
    g.pos, g.nrm, g.tan, g.bit, g.uv, g.view = pos, nrm, tan, np.cross(nrm, tan).astype(np.float32), uv, view
    g.samples = {}
    g.tri = t
    return g


def shade(geo: Geo, mat: Material, o: RenderOptions, resolve: TexResolver):
    L = dict(LIGHTING.get(o.lighting, LIGHTING["Студия"]))
    f32 = np.float32
    n = len(geo.uv)
    nrm, tan, bit, view = geo.nrm, geo.tan, geo.bit, geo.view

    def stage_uv(i):
        s = mat.stage(i)
        M, off = _uv_matrix(s)
        return (geo.uv * f32(o.tiling)) @ M.T + off

    def get(i, lin=False):
        s = mat.stage(i)
        if s is None or not o.stages_on.get(i, True) or not s.texture:
            return None
        tex = resolve(s.texture)
        if tex is None:
            return None
        M, off = _uv_matrix(s)
        key = (i, s.texture, M.tobytes(), off.tobytes(), o.tiling, id(tex), lin)
        r = geo.samples.get(key)
        if r is None:
            r = sample(tex, stage_uv(i)).astype(f32)
            if lin:
                r[:, :3] **= f32(2.2)
            geo.samples[key] = (r, tex)     # держим tex, чтобы id() не переиспользовался
            return r
        return r[0]

    col = lambda name, d: np.array((mat.get(name) or d)[:4], f32)
    amb, dif, forced, emi, spc = col("ambient", [1] * 4), col("diffuse", [1] * 4), col("forcedDiffuse", [0, 0, 0, 1]), col("emmisive", [0] * 4), col("specular", [0] * 4)
    power = float(mat.get("specularPower") or 10)

    albedo = get(4, lin=True)
    albedo = albedo[:, :3] if albedo is not None else np.full((n, 3), 0.45, f32)
    if o.view == "checker":
        albedo = _checker(stage_uv(1))[:, :3] ** f32(2.2)
    s2 = mat.stage(2)
    detail = np.ones((n, 3), f32)
    if s2 and o.stages_on.get(2, True) and s2.texture:
        if is_procedural(s2.texture):
            pr = parse_procedural(s2.texture)
            if pr.rgba:
                detail = np.tile(np.array(pr.rgba[:3], f32) * 2, (n, 1))
        else:
            dt = get(2)
            if dt is not None:
                detail = dt[:, :3] * 2
    nm = get(1)
    if nm is not None:
        nx, ny = nm[:, 0] * 2 - 1, nm[:, 1] * 2 - 1
        nz = np.sqrt(np.clip(1 - nx * nx - ny * ny, 0, 1))
        nrm_f = tan * nx[:, None] + bit * ny[:, None] + nrm * nz[:, None]
        nrm_f /= np.maximum(np.linalg.norm(nrm_f, axis=1, keepdims=True), 1e-8)
    else:
        nrm_f = nrm
    sm = get(5)
    spec_i, metal, gloss, glow = (sm[:, 0], sm[:, 1], sm[:, 2], sm[:, 3]) if sm is not None else (np.ones(n, f32), np.zeros(n, f32), np.full(n, 0.5, f32), np.zeros(n, f32))

    ang = math.radians(o.light_rot)
    sv = np.array(L["sun"], f32)
    sv = np.array([sv[0] * math.cos(ang) + sv[2] * math.sin(ang), sv[1], -sv[0] * math.sin(ang) + sv[2] * math.cos(ang)], f32)
    sv /= np.linalg.norm(sv)
    lam = np.clip(nrm_f @ sv, 0, 1)[:, None]
    sun_c = np.array(L["col"], f32) * f32(L["i"])
    h = sv + view
    h /= np.maximum(np.linalg.norm(h, axis=1, keepdims=True), 1e-8)
    nh = np.clip((nrm_f * h).sum(1), 0, 1)
    sp_exp = np.maximum(f32(power) * (f32(0.4) + gloss), f32(1.0))
    fr_stage = mat.stage(6)
    n_, k_ = 1.5, 0.0
    if fr_stage and o.stages_on.get(6, True) and fr_stage.texture and is_procedural(fr_stage.texture):
        pr = parse_procedural(fr_stage.texture)
        if pr.fresnel:
            n_, k_ = pr.fresnel
    ndv = np.clip((nrm_f * view).sum(1), 0, 1)
    fres = _fresnel_curve(n_, k_, ndv).astype(f32)[:, None]
    hdri = getattr(o, "hdri", None)
    refl = 2 * (nrm_f * view).sum(1, keepdims=True) * nrm_f - view
    envc = env_color(refl, L, hdri).astype(f32)
    amb_env = env_color(nrm_f, L, hdri).astype(f32)

    kd = albedo * detail * (1 - metal[:, None])
    diffuse_term = kd * (amb[:3] * amb_env * f32(L["amb"] * 2) + dif[:3] * lam * sun_c + forced[:3])
    spec_col = spc[:3] * (1 - metal[:, None]) + albedo * metal[:, None]
    spec_term = spec_col * (nh ** sp_exp)[:, None] * lam * sun_c * spec_i[:, None] * (sp_exp[:, None] + 8) / 25
    refl_term = envc * spec_col * fres * (spec_i * (f32(0.25) + f32(0.75) * gloss))[:, None] * f32(spc[:3].mean())
    emis = emi[:3] + albedo * glow[:, None]
    lin = diffuse_term + spec_term + refl_term + emis

    v = o.view
    if v in ("albedo", "checker"):
        lin = albedo
    elif v == "normal":
        lin = (nrm_f * 0.5 + 0.5) ** 2.2
    elif v == "specular":
        lin = np.tile(spec_i[:, None], (1, 3))
    elif v == "metal":
        lin = np.tile(metal[:, None], (1, 3))
    elif v == "gloss":
        lin = np.tile(gloss[:, None], (1, 3))
    elif v == "emissive":
        lin = emis
    elif v == "detail":
        lin = detail * 0.5
    return geo.ys, geo.xs, np.clip(lin, 0, 1) ** f32(1 / 2.2)


def background(W, H, L):
    yy = np.linspace(0, 1, H)[:, None, None]
    top, hor = np.array(L["top"], np.float32) * 0.5, np.array(L["hor"], np.float32) * 0.4
    return np.broadcast_to(top * (1 - yy) + hor * yy, (H, W, 3)).copy() ** (1 / 1.6)


class RenderCache:
    """Кэш растеризации/геометрии между кадрами: при правке только материала пересчитывается шейдинг."""
    def __init__(self):
        self.key = None
        self.data = None


def render(mat: Material, o: RenderOptions, resolve: TexResolver, mesh: Optional[Mesh] = None,
           cache: Optional[RenderCache] = None) -> np.ndarray:
    """-> RGBA uint8 (H,W,4)."""
    mesh = mesh or get_shape(o.shape)
    s = max(1, o.ssaa)
    W, H = o.width * s, o.height * s
    key = (id(mesh), round(o.yaw, 3), round(o.pitch, 3), round(o.zoom, 4), tuple(round(x, 4) for x in o.pan), W, H)
    if cache is not None and cache.key == key:
        P, N, T, w, sx, sy, dist, geo = cache.data
    else:
        P, N, T, w, sx, sy, dist = project_mesh(mesh, o, W, H)
        tri, b0, b1 = rasterize(mesh, sx, sy, w, W, H)
        geo = prep_geometry(mesh, P, N, T, tri, b0, b1, dist) if (tri >= 0).any() else None
        if cache is not None:
            cache.key, cache.data = key, (P, N, T, w, sx, sy, dist, geo)
    L = LIGHTING.get(o.lighting, LIGHTING["Студия"])
    img = background(W, H, L).astype(np.float32)
    if geo is not None:
        ys, xs, col = shade(geo, mat, o, resolve)
        img[ys, xs] = col
    if s > 1:
        img = img.reshape(o.height, s, o.width, s, 3).mean((1, 3))
    out = np.dstack([(np.clip(img, 0, 1) * 255).astype(np.uint8), np.full((o.height, o.width), 255, np.uint8)])
    if o.wire or o.grid:
        im = Image.fromarray(out)
        d = ImageDraw.Draw(im)
        if o.wire:
            px, py = sx / s, sy / s
            for a, b, c in mesh.f:
                d.line([(px[a], py[a]), (px[b], py[b]), (px[c], py[c]), (px[a], py[a])], fill=(255, 255, 255, 70))
        if o.grid:
            for i in range(0, o.width, 40):
                d.line([(i, 0), (i, o.height)], fill=(255, 255, 255, 25))
            for j in range(0, o.height, 40):
                d.line([(0, j), (o.width, j)], fill=(255, 255, 255, 25))
        out = np.asarray(im)
    return out


def render_uv_layout(mesh: Mesh, size=512, background_tex: Optional[np.ndarray] = None) -> np.ndarray:
    im = Image.new("RGBA", (size, size), (24, 24, 28, 255))
    if background_tex is not None:
        bt = Image.fromarray((background_tex * 255).astype(np.uint8)).convert("RGBA").resize((size, size))
        bt.putalpha(120)
        im.alpha_composite(bt)
    d = ImageDraw.Draw(im)
    uv = mesh.uv % 1.0 if mesh.uv.max() <= 1.0001 else mesh.uv
    for a, b, c in mesh.f:
        pts = [(float(uv[i][0] * size), float(size - uv[i][1] * size)) for i in (a, b, c, a)]
        d.line(pts, fill=(90, 200, 255, 200))
    d.rectangle([0, 0, size - 1, size - 1], outline=(255, 255, 255, 120))
    return np.asarray(im)
