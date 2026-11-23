"""Запись бинарного FBX 7.4 из Mesh: по Model+Geometry на часть (селекшен), UpAxis=Y, исходный масштаб восстановлен."""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

import numpy as np

MAGIC = b"Kaydara FBX Binary  \x00\x1a\x00"


def _prop(v) -> bytes:
    if isinstance(v, str):
        b = v.encode("utf-8"); return b"S" + struct.pack("<I", len(b)) + b
    if isinstance(v, bool):
        return b"C" + bytes([v])
    if isinstance(v, int):
        return (b"L" + struct.pack("<q", v)) if abs(v) > 2 ** 31 - 1 else (b"I" + struct.pack("<i", v))
    if isinstance(v, float):
        return b"D" + struct.pack("<d", v)
    if isinstance(v, np.ndarray):
        code, dt = ("d", "<f8") if v.dtype.kind == "f" else ("i", "<i4")
        raw = zlib.compress(np.ascontiguousarray(v.astype(dt)).tobytes())
        return code.encode() + struct.pack("<III", v.size, 1, len(raw)) + raw
    raise TypeError(type(v))


def _node(name, props, children, start) -> bytes:
    nm = name.encode()
    pb = b"".join(_prop(p) for p in props)
    body = start + 13 + len(nm) + len(pb)
    kids, p = b"", body
    for c in children:
        cb = _node(*c, start=p)
        kids += cb; p += len(cb)
    if children:
        kids += b"\x00" * 13
    return struct.pack("<III", body + len(kids), len(props), len(pb)) + bytes([len(nm)]) + nm + pb + kids


def write_fbx(mesh, path, name: str = "mesh"):
    names = list(mesh.part_names) if mesh.part_names and len(mesh.part_names) > 1 else [name]
    ranges = mesh.part_ranges() if len(names) > 1 else [(0, len(mesh.f))]
    cx, cy, cz = mesh.center
    objects, conns = [], []
    for pi, (pname, (t0, tn)) in enumerate(zip(names, ranges)):
        tri = mesh.f[t0:t0 + tn]
        uniq, inv = np.unique(tri.ravel(), return_inverse=True)
        verts = mesh.v[uniq].astype(np.float64) * mesh.scale + np.array([cx, cy, cz])
        uv = mesh.uv[uniq].astype(np.float64).copy(); uv[:, 1] = 1 - uv[:, 1]
        idx = inv.reshape(-1, 3).astype(np.int32).copy()
        idx[:, 2] = ~idx[:, 2]                                           # конец полигона
        norms = mesh.n[tri.ravel()].astype(np.float64)
        gid, mid = 1000 + pi * 2, 1001 + pi * 2
        objects.append(("Geometry", [gid, f"{pname}\x00\x01Geometry", "Mesh"], [
            ("Properties70", [], []), ("GeometryVersion", [124], []),
            ("Vertices", [verts.ravel()], []), ("PolygonVertexIndex", [idx.ravel()], []),
            ("LayerElementNormal", [0], [("Version", [101], []), ("Name", [""], []), ("MappingInformationType", ["ByPolygonVertex"], []),
                                         ("ReferenceInformationType", ["Direct"], []), ("Normals", [norms.ravel()], [])]),
            ("LayerElementUV", [0], [("Version", [101], []), ("Name", ["UVMap"], []), ("MappingInformationType", ["ByPolygonVertex"], []),
                                     ("ReferenceInformationType", ["IndexToDirect"], []), ("UV", [uv.ravel()], []),
                                     ("UVIndex", [inv.astype(np.int32)], [])]),
            ("Layer", [0], [("Version", [100], []),
                            ("LayerElement", [], [("Type", ["LayerElementNormal"], []), ("TypedIndex", [0], [])]),
                            ("LayerElement", [], [("Type", ["LayerElementUV"], []), ("TypedIndex", [0], [])])])]))
        objects.append(("Model", [mid, f"{pname}\x00\x01Model", "Mesh"], [
            ("Version", [232], []), ("Properties70", [], []), ("Shading", [True], []), ("Culling", ["CullingOff"], [])]))
        conns += [("C", ["OO", gid, mid], []), ("C", ["OO", mid, 0], [])]
    p70 = [("P", ["UpAxis", "int", "Integer", "", 1], []), ("P", ["UpAxisSign", "int", "Integer", "", 1], []),
           ("P", ["FrontAxis", "int", "Integer", "", 2], []), ("P", ["FrontAxisSign", "int", "Integer", "", 1], []),
           ("P", ["CoordAxis", "int", "Integer", "", 0], []), ("P", ["CoordAxisSign", "int", "Integer", "", 1], []),
           ("P", ["UnitScaleFactor", "double", "Number", "", 1.0], [])]
    top = [("FBXHeaderExtension", [], [("FBXHeaderVersion", [1003], []), ("FBXVersion", [7400], []), ("Creator", ["DayZ RVMAT Studio"], [])]),
           ("GlobalSettings", [], [("Version", [1000], []), ("Properties70", [], p70)]),
           ("Definitions", [], [("Version", [100], []), ("Count", [len(objects) + 1], []),
                                ("ObjectType", ["Model"], [("Count", [len(names)], [])]),
                                ("ObjectType", ["Geometry"], [("Count", [len(names)], [])])]),
           ("Objects", [], objects), ("Connections", [], conns)]
    data = bytearray(MAGIC + struct.pack("<I", 7400))
    for t in top:
        data += _node(*t, start=len(data))
    data += b"\x00" * 13
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(bytes(data))
