"""Загрузчик FBX (бинарный 7.x и ASCII) без внешних зависимостей.

Берёт геометрию (Geometry/Mesh): вершины, полигоны (триангуляция веером), нормали и UV
(по ByPolygonVertex / ByVertice / ByPolygon, Direct / IndexToDirect). Все меши сцены объединяются;
трансформации узлов и материалы не применяются. UpAxis=Z приводится к Y-up.
"""
from __future__ import annotations

import re
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .meshes import Mesh

MAGIC = b"Kaydara FBX Binary  \x00\x1a\x00"


class FbxError(ValueError):
    pass


@dataclass
class Node:
    name: str
    props: list = field(default_factory=list)
    children: list = field(default_factory=list)

    def find(self, name):
        return next((c for c in self.children if c.name == name), None)

    def all(self, name):
        return [c for c in self.children if c.name == name]


# ------------------------------------------------------------------ binary
_ARR = {"f": ("f", 4), "d": ("d", 8), "l": ("q", 8), "i": ("i", 4), "b": ("?", 1)}


def _parse_binary(data: bytes) -> list[Node]:
    ver = struct.unpack_from("<I", data, 23)[0]
    wide = ver >= 7500
    pos = 27

    def rd(fmt, p):
        return struct.unpack_from(fmt, data, p)

    def node(p):
        if wide:
            end, nprops, plen = rd("<QQQ", p); p += 24
        else:
            end, nprops, plen = rd("<III", p); p += 12
        nl = data[p]; p += 1
        if end == 0:
            return None, p - (25 if wide else 13)
        name = data[p:p + nl].decode("latin1"); p += nl
        props = []
        for _ in range(nprops):
            t = chr(data[p]); p += 1
            if t == "Y": props.append(rd("<h", p)[0]); p += 2
            elif t == "C": props.append(bool(data[p])); p += 1
            elif t == "I": props.append(rd("<i", p)[0]); p += 4
            elif t == "F": props.append(rd("<f", p)[0]); p += 4
            elif t == "D": props.append(rd("<d", p)[0]); p += 8
            elif t == "L": props.append(rd("<q", p)[0]); p += 8
            elif t in _ARR:
                n, enc, clen = rd("<III", p); p += 12
                code, sz = _ARR[t]
                raw = data[p:p + clen]; p += clen
                if enc == 1:
                    raw = zlib.decompress(raw)
                props.append(np.frombuffer(raw[:n * sz], dtype="<" + code).copy())
            elif t in "SR":
                n = rd("<I", p)[0]; p += 4
                b = data[p:p + n]; p += n
                props.append(b.decode("utf-8", "replace") if t == "S" else bytes(b))
            else:
                raise FbxError(f"Неизвестный тип свойства {t!r}")
        n = Node(name, props)
        while p < end:
            ch, p2 = node(p)
            if ch is None:
                p = p2 + (25 if wide else 13)
                break
            n.children.append(ch)
            p = p2
        return n, end

    out = []
    while pos < len(data) - 13:
        n, p2 = node(pos)
        if n is None:
            break
        out.append(n)
        pos = p2
    return out


# ------------------------------------------------------------------ ASCII
_TOK = re.compile(r'\s*(?:;[^\n]*\n|(?P<s>"(?:[^"\\]|\\.)*")|(?P<k>[A-Za-z_][\w.]*)\s*:|(?P<n>[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)|(?P<p>[{},*]))')


def _parse_ascii(text: str) -> list[Node]:
    toks, pos = [], 0
    while pos < len(text):
        m = _TOK.match(text, pos)
        if not m or m.end() == pos:
            if text[pos:].strip() == "":
                break
            # пропуск неизвестных символов (например, идентификаторы без двоеточия — значения-ключи)
            m2 = re.compile(r"\s*([A-Za-z_]\w*)").match(text, pos)
            if m2:
                toks.append(("s", m2.group(1))); pos = m2.end(); continue
            raise FbxError(f"ASCII FBX: неожиданный символ в позиции {pos}")
        pos = m.end()
        if m.group("s") is not None: toks.append(("s", m.group("s")[1:-1]))
        elif m.group("k") is not None: toks.append(("k", m.group("k")))
        elif m.group("n") is not None: toks.append(("n", m.group("n")))
        elif m.group("p") is not None: toks.append(("p", m.group("p")))
    i = 0

    def parse_nodes(end_on_brace):
        nonlocal i
        res = []
        while i < len(toks):
            k, v = toks[i]
            if k == "p" and v == "}":
                i += 1
                if end_on_brace:
                    return res
                continue
            if k != "k":
                i += 1
                continue
            i += 1
            n = Node(v)
            props = []
            while i < len(toks):
                k, v2 = toks[i]
                if k == "k" or (k == "p" and v2 == "}"):
                    break
                if k == "p" and v2 == "{":
                    i += 1
                    n.children = parse_nodes(True)
                    break
                if k == "p" and v2 == "*":
                    i += 2        # *N — размер массива; дальше идёт { a: ... }
                    continue
                if k == "p" and v2 == ",":
                    i += 1
                    continue
                props.append(float(v2) if k == "n" and ("." in v2 or "e" in v2.lower()) else (int(v2) if k == "n" else v2))
                i += 1
            n.props = props
            # блок массива "{ a: 1,2,3 }" -> узел 'a' со свойствами-числами: свернуть в numpy
            a = n.find("a")
            if a is not None and len(n.children) == 1:
                n.props = [np.array(a.props, dtype=np.float64 if any(isinstance(x, float) for x in a.props) else np.int64)]
                n.children = []
            res.append(n)
        return res
    return parse_nodes(False)


# ------------------------------------------------------------------ mesh
def parse_fbx(data: bytes) -> list[Node]:
    if data.startswith(MAGIC[:20]):
        return _parse_binary(data)
    return _parse_ascii(data.decode("utf-8", "replace"))


def _arr(node, name):
    ch = node.find(name) if node else None
    if ch is None or not ch.props:
        return None
    a = ch.props[0]
    return a if isinstance(a, np.ndarray) else None


def _geometry_to_arrays(g: Node):
    verts = _arr(g, "Vertices")
    pvi = _arr(g, "PolygonVertexIndex")
    if verts is None or pvi is None:
        return None
    verts = np.asarray(verts, np.float64).reshape(-1, 3)
    pvi = np.asarray(pvi, np.int64)
    K = len(pvi)
    vidx = np.where(pvi < 0, ~pvi, pvi)
    ends = np.nonzero(pvi < 0)[0]
    starts = np.concatenate([[0], ends[:-1] + 1])
    poly_of = np.repeat(np.arange(len(ends)), ends - starts + 1)

    def layer(kind, dim, valname, idxname):
        le = g.find(kind)
        if le is None:
            return None
        vals = _arr(le, valname)
        if vals is None:
            return None
        vals = np.asarray(vals, np.float64).reshape(-1, dim)
        mp = (le.find("MappingInformationType").props or [""])[0] if le.find("MappingInformationType") else "ByPolygonVertex"
        ref = (le.find("ReferenceInformationType").props or [""])[0] if le.find("ReferenceInformationType") else "Direct"
        ix = _arr(le, idxname)
        if mp == "ByPolygonVertex":
            sel = np.arange(K)
        elif mp in ("ByVertice", "ByVertex"):
            sel = vidx
        elif mp == "ByPolygon":
            sel = poly_of
        elif mp == "AllSame":
            sel = np.zeros(K, np.int64)
        else:
            return None
        if ref.startswith("Index") and ix is not None:
            sel = np.asarray(ix, np.int64)[sel]
        return vals[np.clip(sel, 0, len(vals) - 1)]

    nrm = layer("LayerElementNormal", 3, "Normals", "NormalsIndex")
    uv = layer("LayerElementUV", 2, "UV", "UVIndex")
    # триангуляция веером в пространстве индексов «полигон-вершина»
    tris = []
    for s, e in zip(starts, ends):
        for k in range(s + 1, e):
            tris.append((s, k, k + 1))
    return verts[vidx], nrm, uv, np.array(tris, np.int64).reshape(-1, 3), vidx


def load_fbx(path) -> Mesh:
    data = Path(path).read_bytes()
    nodes = parse_fbx(data)
    objs = next((n for n in nodes if n.name == "Objects"), None)
    if objs is None:
        raise FbxError("В файле нет раздела Objects")
    up = 1
    gs = next((n for n in nodes if n.name == "GlobalSettings"), None)
    if gs is not None:
        p70 = gs.find("Properties70")
        for p in (p70.all("P") if p70 else []):
            if p.props and p.props[0] == "UpAxis" and len(p.props) > 4:
                up = int(p.props[4])
    V, N, U, F, off, have_n = [], [], [], [], 0, True
    for g in objs.all("Geometry"):
        r = _geometry_to_arrays(g)
        if r is None:
            continue
        v, n, uv, tris, vidx = r
        V.append(v)
        U.append(uv if uv is not None else np.zeros((len(v), 2)))
        if n is None:
            have_n = False
            N.append(np.zeros((len(v), 3)))
        else:
            N.append(n)
        F.append(tris + off)
        off += len(v)
    if not V or not any(len(f) for f in F):
        raise FbxError("В FBX не найдено полигональной геометрии")
    v = np.vstack(V).astype(np.float32)
    n = np.vstack(N).astype(np.float32)
    uv = np.vstack(U).astype(np.float32)
    f = np.vstack(F)
    if up == 2:                                   # Z-up -> Y-up
        v = v[:, [0, 2, 1]] * np.array([1, 1, -1], np.float32)
        n = n[:, [0, 2, 1]] * np.array([1, 1, -1], np.float32)
    uv[:, 1] = 1 - uv[:, 1]                       # как в DCC-вьюпорте: изображение «стоит» прямо
    if not have_n:
        # сварить вершины по позиции+UV, чтобы получить сглаженные нормали
        key = np.round(np.hstack([v, uv]), 5)
        _, first, inv = np.unique(key, axis=0, return_index=True, return_inverse=True)
        inv = inv.reshape(-1)
        v, uv, f = v[first], uv[first], inv[f]
        n = None
    return Mesh(v, n, uv, f).finalize()
