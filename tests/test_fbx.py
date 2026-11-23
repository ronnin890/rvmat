import struct
import time
import zlib

import numpy as np
import pytest

from rvmat_studio.core.fbx import FbxError, MAGIC, load_fbx
from rvmat_studio.core.meshes import get_shape, load_obj
from rvmat_studio.core.presets import super_template
from rvmat_studio.core.render import RenderCache, RenderOptions, make_resolver, render


# ---- минимальный писатель бинарного FBX для тестов
def _prop(v, compress=False):
    if isinstance(v, str):
        b = v.encode(); return b"S" + struct.pack("<I", len(b)) + b
    if isinstance(v, int):
        return b"I" + struct.pack("<i", v)
    if isinstance(v, np.ndarray):
        code = {"d": "d", "i": "i"}["d" if v.dtype.kind == "f" else "i"]
        raw = v.astype("<" + code).tobytes()
        enc = 0
        if compress:
            raw, enc = zlib.compress(raw), 1
        return code.encode() + struct.pack("<III", len(v), enc, len(raw)) + raw
    raise TypeError(v)


def _node(name, props, children, start, wide, compress=False):
    hdr = 24 if wide else 12
    nm = name.encode()
    pb = b"".join(_prop(p, compress) for p in props)
    body_start = start + hdr + 1 + len(nm) + len(pb)
    kids = b""
    p = body_start
    for c in children:
        cb = _node(*c, start=p, wide=wide, compress=compress)
        kids += cb; p += len(cb)
    if children:
        kids += b"\x00" * (25 if wide else 13)
    end = body_start + len(kids)
    h = struct.pack("<QQQ", end, len(props), len(pb)) if wide else struct.pack("<III", end, len(props), len(pb))
    return h + bytes([len(nm)]) + nm + pb + kids


def build_fbx(version=7400, compress=False, up=1, normals=True):
    wide = version >= 7500
    verts = np.array([0, 0, 0, 1, 0, 0, 1, 1, 0, 0, 1, 0], float)
    pvi = np.array([0, 1, 2, -4], np.int32)
    geom_children = [
        ("Vertices", [verts], []), ("PolygonVertexIndex", [pvi], []),
    ]
    if normals:
        geom_children.append(("LayerElementNormal", [0], [
            ("MappingInformationType", ["ByPolygonVertex"], []), ("ReferenceInformationType", ["Direct"], []),
            ("Normals", [np.tile([0.0, 0.0, 1.0], 4)], [])]))
    geom_children.append(("LayerElementUV", [0], [
        ("MappingInformationType", ["ByPolygonVertex"], []), ("ReferenceInformationType", ["IndexToDirect"], []),
        ("UV", [np.array([0, 0, 1, 0, 1, 1, 0, 1], float)], []), ("UVIndex", [np.array([0, 1, 2, 3], np.int32)], [])]))
    top = [
        ("GlobalSettings", [], [("Properties70", [], [("P", ["UpAxis", "int", "Integer", "", up], [])])]),
        ("Objects", [], [("Geometry", [1, "quad\x00\x01Geometry", "Mesh"], geom_children)]),
    ]
    data = MAGIC + struct.pack("<I", version)
    for t in top:
        data += _node(*t, start=len(data), wide=wide, compress=compress)
    data += b"\x00" * (25 if wide else 13)
    return data


@pytest.mark.parametrize("version,compress", [(7400, False), (7400, True), (7500, False), (7500, True)])
def test_binary_fbx_versions_and_compression(tmp_path, version, compress):
    f = tmp_path / "q.fbx"
    f.write_bytes(build_fbx(version, compress))
    m = load_fbx(f)
    assert len(m.f) == 2 and len(m.v) == 4 and m.uv.shape == (4, 2) and m.get_tangents().shape == (4, 3)
    assert np.allclose(m.n, [0, 0, 1], atol=1e-5)
    assert set(np.round(m.uv[:, 1], 3)) == {0.0, 1.0}
    assert np.abs(m.v).max() == pytest.approx(1.0, abs=1e-5) or np.linalg.norm(m.v, axis=1).max() == pytest.approx(1.0, abs=1e-5)


def test_fbx_without_normals_welds_and_computes(tmp_path):
    f = tmp_path / "n.fbx"
    f.write_bytes(build_fbx(normals=False))
    m = load_fbx(f)
    assert len(m.v) == 4 and np.allclose(np.abs(m.n[:, 2]), 1, atol=1e-5)


def test_fbx_zup_converted(tmp_path):
    f1, f2 = tmp_path / "y.fbx", tmp_path / "z.fbx"
    f1.write_bytes(build_fbx(up=1)); f2.write_bytes(build_fbx(up=2))
    y, z = load_fbx(f1), load_fbx(f2)
    assert np.ptp(y.v[:, 2]) < 1e-5 and np.ptp(y.v[:, 1]) > 0.5          # плоскость XY
    assert np.ptp(z.v[:, 1]) < 1e-5 and np.ptp(z.v[:, 2]) > 0.5          # после поворота лежит в XZ


def test_ascii_fbx(tmp_path):
    txt = '''; FBX 7.4.0 project file
FBXHeaderExtension:  {
    FBXVersion: 7400
}
GlobalSettings:  {
    Properties70:  {
        P: "UpAxis", "int", "Integer", "",1
    }
}
Objects:  {
    Geometry: 1, "Geometry::tri", "Mesh" {
        Vertices: *9 {
            a: 0,0,0,1,0,0,0,1,0
        }
        PolygonVertexIndex: *3 {
            a: 0,1,-3
        }
        LayerElementUV: 0 {
            MappingInformationType: "ByVertice"
            ReferenceInformationType: "Direct"
            UV: *6 {
                a: 0,0,1,0,0,1
            }
        }
    }
}
'''
    f = tmp_path / "a.fbx"; f.write_text(txt)
    m = load_fbx(f)
    assert len(m.f) == 1 and len(m.v) == 3 and np.allclose(sorted(set(np.round(m.uv[:, 1], 3))), [0, 1])


def test_fbx_errors(tmp_path):
    f = tmp_path / "bad.fbx"
    f.write_bytes(MAGIC + struct.pack("<I", 7400) + b"\x00" * 13)
    with pytest.raises(FbxError):
        load_fbx(f)
    f.write_text("garbage { } ")
    with pytest.raises(FbxError):
        load_fbx(f)


def test_fbx_renders(tmp_path):
    f = tmp_path / "q.fbx"; f.write_bytes(build_fbx())
    img = render(super_template(), RenderOptions(width=96, height=96), make_resolver(None), load_fbx(f))
    assert img[..., :3].std() > 2


# ---- скорость/кэш предпросмотра
def test_cache_gives_identical_result_and_is_faster():
    m = super_template()
    res = make_resolver(None)
    o = RenderOptions(width=320, height=320)
    plain = render(m, o, res, None, None)
    c = RenderCache()
    first = render(m, o, res, None, c)
    t = time.time(); second = render(m, o, res, None, c); t_cached = time.time() - t
    assert (plain == first).all() and (first == second).all()
    t = time.time(); render(m, o, res, None, None); t_plain = time.time() - t
    assert t_cached < t_plain
    m.set_scalar("specularPower", 100)                           # правка материала при кэше геометрии
    assert not (render(m, o, res, None, c) == second).all()
    o.yaw += 10                                                  # смена камеры инвалидирует кэш
    assert (render(m, o, res, None, c) == render(m, o, res, None, None)).all()


def test_mesh_cache_identity():
    assert get_shape("Сфера") is get_shape("Сфера")


def test_vectorized_raster_matches_reference():
    """Векторизованные пачки == построчный эталон по маске покрытия."""
    from rvmat_studio.core import render as R
    mesh = get_shape("Тор")
    o = RenderOptions(width=200, height=200)
    P, N, T, w, sx, sy, dist = R.project_mesh(mesh, o, 200, 200)
    tri, b0, b1 = R.rasterize(mesh, sx, sy, w, 200, 200)
    ref_tri = np.full((200, 200), -1, np.int32); rb0 = np.zeros((200, 200), np.float32); rb1 = rb0.copy(); zb = rb0.copy()
    F = mesh.f
    x, y = sx[F].astype(np.float32), sy[F].astype(np.float32)
    area = (x[:, 1] - x[:, 0]) * (y[:, 2] - y[:, 0]) - (x[:, 2] - x[:, 0]) * (y[:, 1] - y[:, 0])
    R._raster_big(F, x, y, (1 / w).astype(np.float32), area, np.nonzero(np.abs(area) > 1e-9)[0], 200, 200, ref_tri, rb0, rb1, zb)
    assert ((tri >= 0) == (ref_tri >= 0)).all()
    both = (tri >= 0)
    assert (tri[both] == ref_tri[both]).mean() > 0.99
