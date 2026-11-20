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
    face_part: np.ndarray = None      # (M,) индекс части (селекшена) для каждой грани
    part_names: list = None           # имена частей; None/[] — модель без селекшенов

    @property
    def n_parts(self) -> int:
        return len(self.part_names or [])

    def set_parts(self, face_part, names) -> "Mesh":
        """Задать селекшены. Грани сортируются по частям (диапазоны для отрисовки одним вызовом)."""
        fp = np.asarray(face_part, np.int32)
        order = np.argsort(fp, kind="stable")
        self.f, self.face_part = self.f[order], fp[order]
        self.part_names = list(names)
        return self

    def part_ranges(self) -> list[tuple[int, int]]:
        """[(первая грань, число граней)] для каждой части (в порядке part_names)."""
        out = []
        for i in range(self.n_parts):
            idx = np.nonzero(self.face_part == i)[0]
            out.append((int(idx[0]), len(idx)) if len(idx) else (0, 0))
        return out

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


def parts_by_axis(m: Mesh, axis: int, neg: str, pos: str) -> Mesh:
    c = m.v[m.f][:, :, axis].mean(1)
    return m.set_parts((c >= 0).astype(int), [neg, pos])


def parts_by_normal(m: Mesh, names: dict) -> Mesh:
    """names: {(ось, знак): имя}; часть — по доминирующей оси нормали грани."""
    fn = np.cross(m.v[m.f[:, 1]] - m.v[m.f[:, 0]], m.v[m.f[:, 2]] - m.v[m.f[:, 0]])
    ax = np.abs(fn).argmax(1)
    sg = np.sign(fn[np.arange(len(fn)), ax]).astype(int)
    # ориентация граней в наших генераторах непостоянна — определяем знак по положению центра грани
    ctr = m.v[m.f].mean(1)
    sg = np.sign(ctr[np.arange(len(ctr)), ax]).astype(int)
    keys = list(names)
    idx = np.array([keys.index((int(a), int(g))) if (int(a), int(g)) in names else 0 for a, g in zip(ax, sg)])
    return m.set_parts(idx, [names[k] for k in keys])


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
    return parts_by_axis(Mesh(v, v.copy(), uv, f).finalize(), 1, "нижняя", "верхняя")


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
    m = Mesh(v, n, uv, f).finalize()
    return parts_by_normal(m, {(0, -1): "-X", (0, 1): "+X", (1, -1): "-Y", (1, 1): "+Y", (2, -1): "-Z", (2, 1): "+Z"})


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
    m = Mesh(np.vstack(V), np.vstack(N), np.vstack(U), np.vstack(F)).finalize()
    side_n = (parts_a := len(parts[0][3]))
    fp = np.zeros(len(m.f), int)
    fp[:side_n] = 0                                   # боковая поверхность
    cap_top = len(parts[1][3])
    fp[side_n:side_n + cap_top] = 1                   # верхняя крышка (y=+1 до нормализации)
    fp[side_n + cap_top:] = 2
    return m.set_parts(fp, ["бок", "верх", "низ"])


def capsule_test(seg=36) -> Mesh:
    """Параметрическая тестовая модель: капсула с кольцевым рельефом (проверка нормалей/UV)."""
    def fn(u, v):
        th, ph = u * 2 * math.pi, v * math.pi
        y = np.cos(ph) * 0.6 + np.where(v < 0.5, 0.7, -0.7)
        rad = np.sin(ph) * (0.6 + 0.04 * np.sin(v * 40))
        return np.stack([rad * np.cos(th), y, rad * np.sin(th)], -1), np.stack([u, 1 - v], -1)
    v, uv, f = _grid(seg, seg, fn)
    return parts_by_axis(Mesh(v, None, uv, f).finalize(), 1, "низ", "верх")


def load_obj(path) -> Mesh:
    """OBJ: части берутся из групп `g`/`o` (если их нет — из `usemtl`)."""
    V, VT, VN, faces, face_group, face_mtl = [], [], [], [], [], []
    group = mtl = None
    for line in Path(path).read_text("utf-8", "ignore").splitlines():
        p = line.split(None, 1)
        if not p:
            continue
        k = p[0]
        rest = p[1].strip() if len(p) > 1 else ""
        if k == "v":
            V.append([float(x) for x in rest.split()[:3]])
        elif k == "vt":
            VT.append([float(x) for x in rest.split()[:2]])
        elif k == "vn":
            VN.append([float(x) for x in rest.split()[:3]])
        elif k in ("g", "o"):
            group = rest or None
        elif k == "usemtl":
            mtl = rest or None
        elif k == "f":
            faces.append([tuple((int(x) if x else 0) for x in (tok.split("/") + ["", ""])[:3]) for tok in rest.split()])
            face_group.append(group)
            face_mtl.append(mtl)
    if not V or not faces:
        raise ValueError("OBJ не содержит геометрии")
    key, verts, norms, uvs, tri, tri_src = {}, [], [], [], [], []
    def idx(i, n):
        return i - 1 if i > 0 else n + i
    for fi, fc in enumerate(faces):
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
            tri_src.append(fi)
    v = np.array(verts, np.float32)
    n = np.array(norms, np.float32)
    f = np.array(tri)
    if not np.abs(n).sum():
        n = None
    m = Mesh(v, n, np.array(uvs, np.float32), f)
    labels = face_group if any(face_group) else face_mtl
    if any(labels):
        names, fp = [], []
        for fi in tri_src:
            nm = labels[fi] or "(без группы)"
            if nm not in names:
                names.append(nm)
            fp.append(names.index(nm))
        m.finalize()
        return m.set_parts(fp, names) if len(names) > 1 else m
    return m.finalize()


SHAPES = {"Сфера": sphere, "Куб": cube, "Цилиндр": cylinder, "Плоскость": plane, "Тор": torus,
          "Тестовая модель": capsule_test}


_shape_cache: dict = {}


def get_shape(name: str) -> Mesh:
    """Модели строятся один раз (id меши служит ключом кэша рендера)."""
    if name not in _shape_cache:
        _shape_cache[name] = SHAPES.get(name, sphere)()
    return _shape_cache[name]
