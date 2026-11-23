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

from . import limits
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
_SCALAR = {"Y": ("<h", 2), "I": ("<i", 4), "F": ("<f", 4), "D": ("<d", 8), "L": ("<q", 8)}


def _parse_binary(data: bytes) -> list[Node]:
    """Итеративный разбор (без рекурсии) с проверкой границ, глубины и размера распакованных массивов."""
    size = len(data)
    if size < 27 + 13:
        raise FbxError("FBX обрезан: слишком короткий файл")
    ver = struct.unpack_from("<I", data, 23)[0]
    wide = ver >= 7500
    hdr = 24 if wide else 12
    nul = hdr + 1                                    # размер «нулевой» записи-терминатора
    fmt = "<QQQ" if wide else "<III"
    pos = 27
    out: list[Node] = []
    stack: list[tuple[Node, int]] = []               # (узел, конец)
    nodes = 0
    total_arr = 0

    def need(p, n):
        if n < 0 or p + n > size:
            raise FbxError("FBX обрезан или повреждён: выход за границы файла")

    while True:
        if stack and pos >= stack[-1][1]:            # узел закончился без явного терминатора
            pos = stack.pop()[1]
            continue
        parent_end = stack[-1][1] if stack else size
        if pos + nul > parent_end:
            if stack:
                raise FbxError("FBX повреждён: узел выходит за границы родителя")
            break
        end, nprops, plen = struct.unpack_from(fmt, data, pos)
        nl = data[pos + hdr]
        if end == 0:                                 # терминатор: закрывает текущий узел
            pos += nul
            if stack:
                node, nend = stack.pop()
                if nend < pos or nend > parent_end:
                    raise FbxError("FBX повреждён: неверный конец узла")
                pos = nend
                continue
            break
        if end <= pos or end > parent_end:
            raise FbxError("FBX повреждён: неверное смещение конца узла")
        nodes += 1
        if nodes > limits.MAX_FBX_NODES:
            raise FbxError(f"FBX: слишком много узлов (> {limits.MAX_FBX_NODES})")
        if len(stack) >= limits.MAX_FBX_DEPTH:
            raise FbxError(f"FBX: слишком глубокая вложенность (> {limits.MAX_FBX_DEPTH})")
        if nprops > limits.MAX_FBX_PROPS or nprops > plen:
            raise FbxError("FBX повреждён: неверное число свойств")
        p = pos + hdr + 1
        need(p, nl)
        name = data[p:p + nl].decode("latin1"); p += nl
        pend = p + plen
        if pend > end:
            raise FbxError("FBX повреждён: свойства выходят за границы узла")
        props = []
        for _ in range(nprops):
            need(p, 1)
            t = chr(data[p]); p += 1
            if t in _SCALAR:
                f, n = _SCALAR[t]
                need(p, n); props.append(struct.unpack_from(f, data, p)[0]); p += n
            elif t == "C":
                need(p, 1); props.append(bool(data[p])); p += 1
            elif t in _ARR:
                need(p, 12)
                n, enc, clen = struct.unpack_from("<III", data, p); p += 12
                code, sz = _ARR[t]
                nbytes = n * sz
                if nbytes > limits.MAX_FBX_ARRAY_BYTES:
                    raise FbxError(f"FBX: массив слишком велик ({nbytes // limits.MiB} МБ)")
                total_arr += nbytes
                if total_arr > limits.MAX_FBX_TOTAL_ARRAY_BYTES:
                    raise FbxError("FBX: суммарный размер массивов превышает допустимый")
                need(p, clen)
                raw = data[p:p + clen]; p += clen
                if enc == 1:
                    d = zlib.decompressobj()
                    try:
                        raw = d.decompress(raw, nbytes + 1)          # не больше заявленного размера (защита от zip-бомбы)
                    except zlib.error as e:
                        raise FbxError(f"FBX: повреждённый сжатый массив ({e})") from e
                    if len(raw) > nbytes:
                        raise FbxError("FBX: массив распаковывается в больший размер, чем заявлено")
                elif enc != 0:
                    raise FbxError(f"FBX: неизвестное кодирование массива {enc}")
                if len(raw) < nbytes:
                    raise FbxError("FBX: массив короче заявленного")
                props.append(np.frombuffer(raw, dtype="<" + code, count=n))
            elif t in "SR":
                need(p, 4)
                n = struct.unpack_from("<I", data, p)[0]; p += 4
                if n > limits.MAX_FBX_STRING:
                    raise FbxError("FBX: слишком длинная строка")
                need(p, n)
                b = data[p:p + n]; p += n
                props.append(b.decode("utf-8", "replace") if t == "S" else bytes(b))
            else:
                raise FbxError(f"Неизвестный тип свойства {t!r}")
            if p > pend:
                raise FbxError("FBX повреждён: свойство выходит за заявленный размер")
        node = Node(name, props)
        (stack[-1][0].children if stack else out).append(node)
        pos = pend
        if pos < end:
            stack.append((node, end))               # есть дочерние узлы
        else:
            pos = end
    return out


# ------------------------------------------------------------------ ASCII
_TOK = re.compile(r'\s*(?:;[^\n]*\n|(?P<s>"(?:[^"\\]|\\.)*")|(?P<k>[A-Za-z_][\w.]*)\s*:|(?P<n>[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)|(?P<p>[{},*]))')
_IDENT = re.compile(r"\s*([A-Za-z_]\w*)")


def _parse_ascii(text: str) -> list[Node]:
    toks, pos, n_text = [], 0, len(text)
    while pos < n_text:
        m = _TOK.match(text, pos)
        if not m or m.end() == pos:
            if text[pos:pos + 1024].strip() == "" and text[pos:].strip() == "":
                break
            # пропуск неизвестных символов (например, идентификаторы без двоеточия — значения-ключи)
            m2 = _IDENT.match(text, pos)
            if m2:
                toks.append(("s", m2.group(1))); pos = m2.end(); continue
            raise FbxError(f"ASCII FBX: неожиданный символ в позиции {pos}")
        pos = m.end()
        if m.group("s") is not None: toks.append(("s", m.group("s")[1:-1]))
        elif m.group("k") is not None: toks.append(("k", m.group("k")))
        elif m.group("n") is not None: toks.append(("n", m.group("n")))
        elif m.group("p") is not None: toks.append(("p", m.group("p")))
        if len(toks) > limits.MAX_ASCII_TOKENS:
            raise FbxError(f"ASCII FBX: слишком много токенов (> {limits.MAX_ASCII_TOKENS})")
    i = 0
    count = 0

    def parse_nodes(end_on_brace, depth):
        nonlocal i, count
        if depth > limits.MAX_FBX_DEPTH:
            raise FbxError(f"FBX: слишком глубокая вложенность (> {limits.MAX_FBX_DEPTH})")
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
            count += 1
            if count > limits.MAX_FBX_NODES:
                raise FbxError(f"FBX: слишком много узлов (> {limits.MAX_FBX_NODES})")
            n = Node(v)
            props = []
            while i < len(toks):
                k, v2 = toks[i]
                if k == "k" or (k == "p" and v2 == "}"):
                    break
                if k == "p" and v2 == "{":
                    i += 1
                    n.children = parse_nodes(True, depth + 1)
                    break
                if k == "p" and v2 == "*":
                    i += 2        # *N — размер массива; дальше идёт { a: ... }
                    continue
                if k == "p" and v2 == ",":
                    i += 1
                    continue
                try:
                    props.append(float(v2) if k == "n" and ("." in v2 or "e" in v2.lower()) else (int(v2) if k == "n" else v2))
                except ValueError as e:
                    raise FbxError(f"ASCII FBX: неверное число {v2!r}") from e
                i += 1
            n.props = props
            # блок массива "{ a: 1,2,3 }" -> узел 'a' со свойствами-числами: свернуть в numpy
            a = n.find("a")
            if a is not None and len(n.children) == 1:
                try:
                    n.props = [np.array(a.props, dtype=np.float64 if any(isinstance(x, float) for x in a.props) else np.int64)]
                except (ValueError, TypeError, OverflowError) as e:
                    raise FbxError("ASCII FBX: некорректный числовой массив") from e
                n.children = []
            res.append(n)
        return res
    return parse_nodes(False, 0)


# ------------------------------------------------------------------ mesh
def parse_fbx(data: bytes) -> list[Node]:
    if data.startswith(MAGIC[:20]):
        return _parse_binary(data)
    if len(data) > limits.MAX_FBX_ASCII_FILE:
        raise FbxError(f"ASCII FBX слишком велик (максимум {limits.MAX_FBX_ASCII_FILE // limits.MiB} МБ)")
    head = data[:64].lstrip()
    if not (head.startswith(b";") or head[:1].isalpha()):
        raise FbxError("Это не FBX-файл (неизвестный заголовок)")
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
    if len(verts) % 3 or len(verts) == 0 or len(pvi) == 0:
        return None
    verts = np.asarray(verts, np.float64).reshape(-1, 3)
    if len(verts) > limits.MAX_VERTICES or len(pvi) > limits.MAX_TRIANGLES * 3:
        raise FbxError("FBX: слишком много вершин/полигонов")
    pvi = np.asarray(pvi, np.int64)
    K = len(pvi)
    vidx = np.where(pvi < 0, ~pvi, pvi)
    if vidx.min() < 0 or vidx.max() >= len(verts):
        raise FbxError("FBX повреждён: индекс вершины вне диапазона")
    ends = np.nonzero(pvi < 0)[0]
    if len(ends) == 0:
        return None
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
    # триангуляция веером в пространстве индексов «полигон-вершина» (векторно)
    ntri = np.maximum(ends - starts + 1 - 2, 0)
    total = int(ntri.sum())
    if total > limits.MAX_TRIANGLES:
        raise FbxError(f"FBX: слишком много треугольников ({total})")
    poly = np.repeat(np.arange(len(ends)), ntri)
    k = np.arange(total) - np.repeat(np.cumsum(ntri) - ntri, ntri) + 1
    tris = np.stack([starts[poly], starts[poly] + k, starts[poly] + k + 1], 1) if total else np.zeros((0, 3), np.int64)
    return verts[vidx], nrm, uv, tris.astype(np.int64), vidx


def load_fbx(path) -> Mesh:
    """Загрузка FBX. Любые ошибки данных (обрезанный/повреждённый/вредоносный файл) → FbxError."""
    try:
        data = limits.read_limited(path, limits.MAX_FBX_FILE, "FBX")
        return _load_fbx_data(data)
    except FbxError:
        raise
    except (limits.LimitError, struct.error, IndexError, KeyError, zlib.error, ValueError, TypeError,
            OverflowError, MemoryError, RecursionError, UnicodeError, AttributeError) as e:
        raise FbxError(f"Не удалось прочитать FBX: {e or type(e).__name__}") from e


def _load_fbx_data(data: bytes) -> Mesh:
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
    names, fparts = [], []
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
        raw = str(g.props[1]) if len(g.props) > 1 else ""
        nm = raw.split("\x00")[0].replace("Geometry::", "").strip() or f"часть {len(names) + 1}"
        names.append(nm if nm not in names else f"{nm}.{len(names)}")
        fparts.append(np.full(len(tris), len(names) - 1))
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
    m = Mesh(v, n, uv, f).finalize()
    if len(names) > 1:                              # каждый Geometry-объект — отдельная часть (селекшен)
        m.set_parts(np.concatenate(fparts), names)
    return m
