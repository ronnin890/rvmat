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
    center: tuple = (0.0, 0.0, 0.0)   # исходный центр и масштаб (до нормировки) — для записи FBX
    scale: float = 1.0

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

    def get_tangents(self) -> np.ndarray:
        if self.t is None:
            self.t = tangents(self)
        return self.t

    def finalize(self) -> "Mesh":
        self.v = self.v.astype(np.float32)
        c = (self.v.max(0) + self.v.min(0)) / 2
        self.v -= c
        r = np.linalg.norm(self.v, axis=1).max()
        self.center, self.scale = tuple(float(x) for x in c), float(r if r > 0 else 1.0)
        self.v /= self.scale
        if self.n is None or len(self.n) != len(self.v):
            self.n = vertex_normals(self.v, self.f)
        self.n = (self.n / np.maximum(np.linalg.norm(self.n, axis=1, keepdims=True), 1e-8)).astype(np.float32)
        self.t = None                     # тангенты нужны только CPU-рендеру и считаются по запросу (get_tangents)
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
    """OBJ (в т.ч. на сотни тысяч треугольников): векторизованная сборка вершин и триангуляция веером.
    Части (селекшены) берутся из групп `g`/`o`, если их нет — из `usemtl`."""
    V, VT, VN, toks, counts, face_group, face_mtl = [], [], [], [], [], [], []
    group = mtl = None
    for line in Path(path).read_text("utf-8", "ignore").splitlines():
        if not line or line[0] == "#":
            continue
        if line[0] == "v":
            c = line[1:2]
            if c == " ":
                V.append(line[2:].split()[:3])
            elif c == "t":
                VT.append(line[3:].split()[:2])
            elif c == "n":
                VN.append(line[3:].split()[:3])
        elif line[0] == "f" and line[1:2] in (" ", "\t"):
            t = line[2:].split()
            toks.extend(t); counts.append(len(t)); face_group.append(group); face_mtl.append(mtl)
        elif line.startswith(("g ", "o ")):
            group = line[2:].strip() or None
        elif line.startswith("usemtl"):
            mtl = line[6:].strip() or None
    if not V or not counts:
        raise ValueError("OBJ не содержит геометрии")
    Va = np.array(V, np.float64)
    VTa = np.array(VT, np.float64) if VT else np.zeros((0, 2))
    VNa = np.array(VN, np.float64) if VN else np.zeros((0, 3))
    parts = [t.split("/") for t in toks]
    vi = np.fromiter((int(p[0]) for p in parts), np.int64, len(parts))
    ti = np.fromiter((int(p[1]) if len(p) > 1 and p[1] else 0 for p in parts), np.int64, len(parts))
    ni = np.fromiter((int(p[2]) if len(p) > 2 and p[2] else 0 for p in parts), np.int64, len(parts))
    fix = lambda a, n: np.where(a > 0, a - 1, np.where(a < 0, n + a, -1))     # 1-based/отрицательные индексы → 0-based, 0 → «нет»
    vi, ti, ni = fix(vi, len(Va)), fix(ti, len(VTa)), fix(ni, len(VNa))
    key = (vi * (len(VTa) + 1) + (ti + 1)) * (len(VNa) + 1) + (ni + 1)
    _, first, inv = np.unique(key, return_index=True, return_inverse=True)
    inv = inv.reshape(-1)
    v = Va[vi[first]].astype(np.float32)
    uv = np.zeros((len(first), 2), np.float32)
    has_t = ti[first] >= 0
    if VTa.size and has_t.any():
        uv[has_t] = VTa[ti[first][has_t]]
        uv[:, 1] = np.where(has_t, 1 - uv[:, 1], 0)
    n = np.zeros((len(first), 3), np.float32)
    has_n = ni[first] >= 0
    if VNa.size and has_n.any():
        n[has_n] = VNa[ni[first][has_n]]
    # триангуляция веером: грань из c вершин -> c-2 треугольника
    c = np.asarray(counts, np.int64)
    start = np.concatenate([[0], np.cumsum(c)[:-1]])
    ntri = np.maximum(c - 2, 0)
    face_of = np.repeat(np.arange(len(c)), ntri)
    k = np.arange(ntri.sum()) - np.repeat(np.cumsum(ntri) - ntri, ntri)
    f = np.stack([inv[start[face_of]], inv[start[face_of] + k + 1], inv[start[face_of] + k + 2]], 1)
    m = Mesh(v, n if np.abs(n).sum() else None, uv, f.astype(np.int64))
    labels = face_group if any(face_group) else face_mtl
    m.finalize()
    if any(labels):
        names, index = [], {}
        per_face = np.empty(len(c), np.int32)
        for i, lab in enumerate(labels):
            lab = lab or "(без группы)"
            if lab not in index:
                index[lab] = len(names); names.append(lab)
            per_face[i] = index[lab]
        if len(names) > 1:
            m.set_parts(per_face[face_of], names)
    return m


SHAPES = {"Сфера": sphere, "Куб": cube, "Цилиндр": cylinder, "Плоскость": plane, "Тор": torus,
          "Тестовая модель": capsule_test}


_shape_cache: dict = {}


def get_shape(name: str) -> Mesh:
    """Модели строятся один раз (id меши служит ключом кэша рендера)."""
    if name not in _shape_cache:
        _shape_cache[name] = SHAPES.get(name, sphere)()
    return _shape_cache[name]
