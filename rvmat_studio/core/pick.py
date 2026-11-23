"""Выбор части (селекшена) по лучу: векторизованный Möller–Trumbore, быстро и для моделей на сотни тысяч треугольников."""
import math

import numpy as np

from .render import RenderOptions, _rot


def pick_part_ray(mesh, o: RenderOptions, W: int, H: int, x: float, y: float) -> int:
    """Индекс части под точкой (x,y) кадра W×H или -1. Использует ту же камеру, что и оба рендерера."""
    if mesh.face_part is None or not (0 <= x < W and 0 <= y < H):
        return -1
    R = _rot(o.yaw, o.pitch).astype(np.float64)
    dist, f, asp = 4.0 / max(o.zoom, 0.05), 1 / math.tan(math.radians(17.5)), W / max(H, 1)
    nx, ny = (x / W) * 2 - 1, 1 - (y / H) * 2
    d = np.array([nx * asp / f, ny / f, -1.0])
    O = np.array([o.pan[0], o.pan[1], dist])
    Om, dm = R.T @ O, R.T @ d                                  # луч в системе координат модели
    v = mesh.v.astype(np.float64)
    best, part = np.inf, -1
    for s in range(0, len(mesh.f), 250_000):
        tri = mesh.f[s:s + 250_000]
        a = v[tri[:, 0]]
        e1, e2 = v[tri[:, 1]] - a, v[tri[:, 2]] - a
        p = np.cross(dm, e2)
        det = (e1 * p).sum(1)
        ok = np.abs(det) > 1e-14
        inv = 1.0 / np.where(ok, det, 1.0)
        tv = Om - a
        u = (tv * p).sum(1) * inv
        q = np.cross(tv, e1)
        w = (dm * q).sum(1) * inv
        t = (e2 * q).sum(1) * inv
        hit = ok & (u >= 0) & (w >= 0) & (u + w <= 1) & (t > 0)
        if hit.any():
            idx = np.nonzero(hit)[0]
            j = idx[np.argmin(t[idx])]
            if t[j] < best:
                best, part = t[j], int(mesh.face_part[s + j])
    return part
