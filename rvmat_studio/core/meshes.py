"""Тестовые модели и импорт OBJ."""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass
class Mesh:
    v: np.ndarray      # (N,3)
    n: np.ndarray      # (N,3)
    uv: np.ndarray     # (N,2)
    f: np.ndarray      # (M,3)
    t: np.ndarray = None   # tangents (N,3)

    def finalize(self) -> "Mesh":
        self.v = self.v.astype(np.float32)
        c = (self.v.max(0) + self.v.min(0)) / 2
        self.v -= c
        r = np.linalg.norm(self.v, axis=1).max()
        self.v /= (r if r > 0 else 1)
        if self.n is None or len(self.n) != len(self.v):
            self.n = vertex_normals(self.v, self.f)
        self.n = (self.n / np.maximum(np.linalg.norm(self.n, axis=1, keepdims=True), 1e-8)).astype(np.float32)
        self.t = tangents(self)
        return self


def vertex_normals(v, f):
    fn = np.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]])
    n = np.zeros_like(v)
    for k in range(3):
        np.add.at(n, f[:, k], fn)
    return n


def tangents(m: Mesh) -> np.ndarray:
    v, uv, f = m.v, m.uv, m.f
    e1, e2 = v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]]
    d1, d2 = uv[f[:, 1]] - uv[f[:, 0]], uv[f[:, 2]] - uv[f[:, 0]]
    det = d1[:, 0] * d2[:, 1] - d2[:, 0] * d1[:, 1]
    det = np.where(np.abs(det) < 1e-12, 1e-12, det)
    tri_t = (e1 * d2[:, 1:2] - e2 * d1[:, 1:2]) / det[:, None]
    t = np.zeros_like(v)
    for k in range(3):
        np.add.at(t, f[:, k], tri_t)
    t -= m.n * (t * m.n).sum(1, keepdims=True)
    ln = np.linalg.norm(t, axis=1, keepdims=True)
    fallback = np.cross(m.n, np.array([0, 1, 0.0]))
    t = np.where(ln > 1e-8, t / np.maximum(ln, 1e-8), fallback)
    return t.astype(np.float32)


def _grid(nu, nv, fn):
    """Параметрическая поверхность: fn(u,v)->(pos, uv). u,v в [0,1]."""
    us, vs = np.meshgrid(np.linspace(0, 1, nu + 1), np.linspace(0, 1, nv + 1))
    P, UV = fn(us, vs)
    v = P.reshape(-1, 3)
    uv = UV.reshape(-1, 2)
    idx = np.arange((nu + 1) * (nv + 1)).reshape(nv + 1, nu + 1)
    a, b, c, d = idx[:-1, :-1], idx[:-1, 1:], idx[1:, 1:], idx[1:, :-1]
    f = np.concatenate([np.stack([a, b, c], -1).reshape(-1, 3), np.stack([a, c, d], -1).reshape(-1, 3)])
    return v, uv, f


def sphere(seg=36) -> Mesh:
    def fn(u, v):
        th, ph = u * 2 * math.pi, v * math.pi
        P = np.stack([np.sin(ph) * np.cos(th), np.cos(ph), np.sin(ph) * np.sin(th)], -1)
        return P, np.stack([u, 1 - v], -1)
    v, uv, f = _grid(seg, seg // 2 + 2, fn)
    return Mesh(v, v.copy(), uv, f).finalize()


def torus(nu=48, nv=24, R=1.0, r=0.4) -> Mesh:
    def fn(u, v):
        a, b = u * 2 * math.pi, v * 2 * math.pi
        x = (R + r * np.cos(b)) * np.cos(a)
        z = (R + r * np.cos(b)) * np.sin(a)
        P = np.stack([x, r * np.sin(b), z], -1)
        return P, np.stack([u, v], -1)
    v, uv, f = _grid(nu, nv, fn)
    ctr = np.stack([R * np.cos(uv[:, 0] * 2 * math.pi), np.zeros(len(v)), R * np.sin(uv[:, 0] * 2 * math.pi)], 1)
    return Mesh(v, v - ctr, uv, f).finalize()


def plane(div=2) -> Mesh:
    def fn(u, v):
        return np.stack([u * 2 - 1, (1 - v) * 2 - 1, np.zeros_like(u)], -1), np.stack([u, v], -1)
    v, uv, f = _grid(div, div, fn)
    return Mesh(v, np.tile([0, 0, 1.0], (len(v), 1)), uv, f).finalize()


def _quad_mesh(quads):
    vs, ns, uvs, fs = [], [], [], []
    for (p0, p1, p2, p3), n in quads:
        base = len(vs)
        vs += [p0, p1, p2, p3]
        ns += [n] * 4
        uvs += [(0, 0), (1, 0), (1, 1), (0, 1)]
        fs += [(base, base + 1, base + 2), (base, base + 2, base + 3)]
    return np.array(vs, np.float32), np.array(ns, np.float32), np.array(uvs, np.float32), np.array(fs)


def cube() -> Mesh:
    q = []
    for ax in range(3):
        for s in (-1, 1):
            n = np.zeros(3); n[ax] = s
            a, b = (ax + 1) % 3, (ax + 2) % 3
            def pt(i, j):
                p = np.zeros(3); p[ax] = s; p[a] = i; p[b] = j
                return p
            c = [pt(-1, -1), pt(1, -1), pt(1, 1), pt(-1, 1)]
            if s < 0:
                c = c[::-1]
                q.append((tuple(c), n))
            else:
                q.append((tuple(c), n))
    # исправить ориентацию UV для перевёрнутых граней — для теста достаточно
    v, n, uv, f = _quad_mesh(q)
    return Mesh(v, n, uv, f).finalize()


def cylinder(seg=40) -> Mesh:
    def side(u, v):
        a = u * 2 * math.pi
        return np.stack([np.cos(a), 1 - 2 * v, np.sin(a)], -1), np.stack([u, v], -1)
    v, uv, f = _grid(seg, 2, side)
    n = np.stack([v[:, 0], np.zeros(len(v)), v[:, 2]], 1)
    parts = [(v, n, uv, f)]
    for y in (1.0, -1.0):
        ang = np.linspace(0, 2 * math.pi, seg, endpoint=False)
        ring = np.stack([np.cos(ang), np.full(seg, y), np.sin(ang)], 1)
        cv = np.vstack([[0, y, 0], ring])
        cuv = np.vstack([[0.5, 0.5], 0.5 + 0.5 * np.stack([np.cos(ang), np.sin(ang)], 1)])
        cf = np.array([(0, 1 + i, 1 + (i + 1) % seg) if y > 0 else (0, 1 + (i + 1) % seg, 1 + i) for i in range(seg)])
        parts.append((cv, np.tile([0, y, 0.0], (len(cv), 1)), cuv, cf))
    off, V, N, U, F = 0, [], [], [], []
    for pv, pn, pu, pf in parts:
        V.append(pv); N.append(pn); U.append(pu); F.append(pf + off); off += len(pv)
    return Mesh(np.vstack(V), np.vstack(N), np.vstack(U), np.vstack(F)).finalize()


def capsule_test(seg=36) -> Mesh:
    """Параметрическая тестовая модель: капсула с кольцевым рельефом (проверка нормалей/UV)."""
    def fn(u, v):
        th, ph = u * 2 * math.pi, v * math.pi
        y = np.cos(ph) * 0.6 + np.where(v < 0.5, 0.7, -0.7)
        rad = np.sin(ph) * (0.6 + 0.04 * np.sin(v * 40))
        return np.stack([rad * np.cos(th), y, rad * np.sin(th)], -1), np.stack([u, 1 - v], -1)
    v, uv, f = _grid(seg, seg, fn)
    return Mesh(v, None, uv, f).finalize()


def load_obj(path) -> Mesh:
    V, VT, VN, faces = [], [], [], []
    for line in Path(path).read_text("utf-8", "ignore").splitlines():
        p = line.split()
        if not p:
            continue
        if p[0] == "v":
            V.append([float(x) for x in p[1:4]])
        elif p[0] == "vt":
            VT.append([float(x) for x in p[1:3]])
        elif p[0] == "vn":
            VN.append([float(x) for x in p[1:4]])
        elif p[0] == "f":
            faces.append([tuple((int(x) if x else 0) for x in (tok.split("/") + ["", ""])[:3]) for tok in p[1:]])
    if not V or not faces:
        raise ValueError("OBJ не содержит геометрии")
    key, verts, norms, uvs, tri = {}, [], [], [], []
    def idx(i, n):
        return i - 1 if i > 0 else n + i
    for fc in faces:
        ids = []
        for (vi, ti, ni) in fc:
            k = (vi, ti, ni)
            if k not in key:
                key[k] = len(verts)
                verts.append(V[idx(vi, len(V))])
                uvs.append([VT[idx(ti, len(VT))][0], 1 - VT[idx(ti, len(VT))][1]] if ti and VT else [0, 0])
                norms.append(VN[idx(ni, len(VN))] if ni and VN else [0, 0, 0])
            ids.append(key[k])
        for i in range(1, len(ids) - 1):
            tri.append((ids[0], ids[i], ids[i + 1]))
    v = np.array(verts, np.float32)
    n = np.array(norms, np.float32)
    f = np.array(tri)
    if not np.abs(n).sum():
        n = None
    return Mesh(v, n, np.array(uvs, np.float32), f).finalize()


SHAPES = {"Сфера": sphere, "Куб": cube, "Цилиндр": cylinder, "Плоскость": plane, "Тор": torus,
          "Тестовая модель": capsule_test}


_shape_cache: dict = {}


def get_shape(name: str) -> Mesh:
    """Модели строятся один раз (id меши служит ключом кэша рендера)."""
    if name not in _shape_cache:
        _shape_cache[name] = SHAPES.get(name, sphere)()
    return _shape_cache[name]
