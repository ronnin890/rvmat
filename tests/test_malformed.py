"""Испорченные и вредоносные файлы: каждый открывается < 2 с, < 512 МБ и заканчивается контролируемой ошибкой."""
import json
import os
import random
import struct
import time
import tracemalloc
import zlib

import numpy as np
import pytest

from rvmat_studio.backend import Material
from rvmat_studio.backend.parser import RvmatParseError
from rvmat_studio.core import fbx, fbxwrite, limits, meshes, paa, safeio, textures
from rvmat_studio.core.fbx import MAGIC, FbxError, load_fbx
from rvmat_studio.core.meshes import MeshError, load_obj
from rvmat_studio.core.paa import PaaError, read_paa
from rvmat_studio.core.project import Project
from rvmat_studio.core.textures import TextureError, load_rgba

MAX_SECONDS = 2.0
MAX_BYTES = 512 * 1024 * 1024


def bounded(fn, exc):
    """Вызывает fn, ожидая именно исключение exc (и никакого другого), измеряя время и пик памяти."""
    tracemalloc.start()
    t0 = time.perf_counter()
    try:
        with pytest.raises(exc):
            fn()
    finally:
        dt = time.perf_counter() - t0
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
    assert dt < MAX_SECONDS, f"слишком долго: {dt:.2f} с"
    assert peak < MAX_BYTES, f"слишком много памяти: {peak / 2**20:.0f} МБ"


# ---------------------------------------------------------------- сборка бинарного FBX
HDR = MAGIC + struct.pack("<I", 7400)


def prop_arr(code, n, enc, payload):
    return code.encode() + struct.pack("<III", n, enc, len(payload)) + payload


def node(name, props=b"", nprops=0, children=b"", start=27):
    nm = name.encode()
    body = start + 13 + len(nm) + len(props)
    kids = children + (b"\x00" * 13 if children else b"")
    return struct.pack("<III", body + len(kids), nprops, len(props)) + bytes([len(nm)]) + nm + props + kids


def write(tmp_path, name, data):
    p = tmp_path / name
    p.write_bytes(data)
    return p


@pytest.fixture
def valid_fbx(tmp_path):
    p = tmp_path / "cube.fbx"
    fbxwrite.write_fbx(meshes.cube(), p, "cube")
    assert len(load_fbx(p).f) > 0
    return p


# ---------------------------------------------------------------- FBX
def test_fbx_zip_bomb_is_stopped(tmp_path):
    bomb = zlib.compress(b"\0" * (64 * 1024 * 1024), 9)             # 64 МБ нулей сжимаются в ~64 КБ
    data = HDR + node("Objects", prop_arr("d", 10, 1, bomb), 1) + b"\x00" * 13
    bounded(lambda: load_fbx(write(tmp_path, "bomb.fbx", data)), FbxError)


def test_fbx_huge_declared_array(tmp_path):
    data = HDR + node("Objects", prop_arr("d", 2 ** 31, 0, b"\0" * 16), 1) + b"\x00" * 13
    bounded(lambda: load_fbx(write(tmp_path, "huge.fbx", data)), FbxError)


def test_fbx_total_array_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(limits, "MAX_FBX_TOTAL_ARRAY_BYTES", 1000)
    props = prop_arr("d", 100, 0, b"\0" * 800) + prop_arr("d", 100, 0, b"\0" * 800)
    data = HDR + node("Objects", props, 2) + b"\x00" * 13
    bounded(lambda: load_fbx(write(tmp_path, "budget.fbx", data)), FbxError)


def test_fbx_deep_nesting_binary(tmp_path):
    inner = b""
    for _ in range(3000):                                              # глубже лимита, без рекурсии Python
        inner = node("N", children=inner)
    data = HDR + node("Objects", children=inner) + b"\x00" * 13
    bounded(lambda: load_fbx(write(tmp_path, "deep.fbx", data)), FbxError)


def test_fbx_deep_nesting_ascii(tmp_path):
    text = "; FBX 7.4 project file\n" + "".join(f"N{i} {{\n" for i in range(5000)) + "}\n" * 5000
    bounded(lambda: load_fbx(write(tmp_path, "deep_ascii.fbx", text.encode())), FbxError)


def test_fbx_node_end_points_backwards(tmp_path):
    data = HDR + struct.pack("<III", 5, 0, 0) + bytes([1]) + b"X" + b"\x00" * 40
    bounded(lambda: load_fbx(write(tmp_path, "back.fbx", data)), FbxError)


def test_fbx_node_end_outside_file(tmp_path):
    data = HDR + struct.pack("<III", 10 ** 9, 0, 0) + bytes([1]) + b"X" + b"\x00" * 40
    bounded(lambda: load_fbx(write(tmp_path, "far.fbx", data)), FbxError)


def test_fbx_property_longer_than_node(tmp_path):
    prop = b"S" + struct.pack("<I", 10 ** 8) + b"abc"
    data = HDR + node("Objects", prop, 1) + b"\x00" * 13
    bounded(lambda: load_fbx(write(tmp_path, "str.fbx", data)), FbxError)


def test_fbx_garbage_and_empty(tmp_path):
    rnd = random.Random(7)
    for i, blob in enumerate((b"", b"\x00", b"hello", bytes(rnd.randrange(256) for _ in range(5000)), MAGIC, MAGIC + b"\xff" * 4)):
        bounded(lambda b=blob, i=i: load_fbx(write(tmp_path, f"g{i}.fbx", b)), FbxError)


def test_fbx_every_truncation_is_controlled(valid_fbx, tmp_path):
    data = valid_fbx.read_bytes()
    t0 = time.perf_counter()
    for cut in sorted(set(list(range(0, min(len(data), 700), 7)) + list(range(700, len(data) - 1, max(1, len(data) // 60))))):
        p = write(tmp_path, "cut.fbx", data[:cut])
        try:
            load_fbx(p)
        except FbxError:
            pass                                                        # любое другое исключение — провал теста
    assert time.perf_counter() - t0 < 10


def test_fbx_random_bit_flips_never_crash(valid_fbx, tmp_path):
    data = bytearray(valid_fbx.read_bytes())
    rnd = random.Random(1)
    for _ in range(150):
        d = bytearray(data)
        for _ in range(rnd.randrange(1, 6)):
            d[rnd.randrange(len(d))] = rnd.randrange(256)
        try:
            load_fbx(write(tmp_path, "flip.fbx", bytes(d)))
        except FbxError:
            pass


def test_fbx_polygon_index_out_of_range(tmp_path):
    verts = np.zeros(9)                                                 # 3 вершины
    idx = np.array([0, 1, ~7], np.int32)                                # индекс 7 > числа вершин
    geo = node("Geometry", b"", 0, node("Vertices", prop_arr("d", 9, 0, verts.astype("<f8").tobytes()), 1)
               + node("PolygonVertexIndex", prop_arr("i", 3, 0, idx.astype("<i4").tobytes()), 1))
    data = HDR + node("Objects", children=geo) + b"\x00" * 13
    bounded(lambda: load_fbx(write(tmp_path, "idx.fbx", data)), FbxError)


def test_fbx_file_size_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(limits, "MAX_FBX_FILE", 100)
    bounded(lambda: load_fbx(write(tmp_path, "big.fbx", b"x" * 200)), FbxError)


def test_fbx_ascii_regex_is_linear(tmp_path):
    """Патологические строки не вызывают катастрофического отката."""
    nasty = ["a" * 200_000, '"' + "\\" * 100_000, "1" * 200_000, " " * 300_000 + "@", "k" + " " * 100_000 + "x", "; " + "a" * 200_000]
    for i, s in enumerate(nasty):
        text = "; FBX 7.4 project file\n" + s
        t0 = time.perf_counter()
        try:
            load_fbx(write(tmp_path, f"n{i}.fbx", text.encode()))
        except FbxError:
            pass
        assert time.perf_counter() - t0 < MAX_SECONDS


# ---------------------------------------------------------------- PAA
def _paa_header(w, h, size, tag=0xFF01, lzo=False):
    return struct.pack("<H", tag) + b"GGATGALF" + struct.pack("<II", 4, 0) + struct.pack("<H", 0) \
        + struct.pack("<HH", w | (0x8000 if lzo else 0), h) + size.to_bytes(3, "little")


def test_paa_rejects_huge_dimensions():
    for w, h in ((0x7FFF, 0x7FFF), (16384, 16384), (0, 64), (64, 0), (20000, 4)):
        bounded(lambda w=w, h=h: read_paa(_paa_header(w, h, 8) + b"\0" * 8), PaaError)


def test_paa_truncated_and_garbage():
    rnd = random.Random(3)
    for blob in (b"", b"\xff", b"\x01\xff", bytes(rnd.randrange(256) for _ in range(2000)), _paa_header(64, 64, 10 ** 6) + b"\0" * 10):
        bounded(lambda b=blob: read_paa(b), PaaError)


def test_paa_lzo_bomb_and_bad_streams():
    # поток из длинных совпадений, который «раздувает» вывод; ограничение out_len обрывает его
    stream = bytes([17 + 1]) + b"A" + (b"\x20" + b"\x00" * 100 + b"\xff" + b"\x04\x00") * 50
    for w, h in ((64, 64),):
        bounded(lambda: read_paa(_paa_header(w, h, len(stream), lzo=True) + stream), PaaError)
    rnd = random.Random(5)
    for _ in range(100):
        blob = bytes(rnd.randrange(256) for _ in range(rnd.randrange(1, 300)))
        try:
            paa.lzo1x_decompress(blob, 4096)
        except PaaError:
            pass


def test_paa_valid_still_reads(tmp_path):
    from rvmat_studio.core.paa_write import encode_paa
    data, _ = encode_paa(np.full((16, 16, 4), 200, np.uint8), "co")
    img, kind = read_paa(data, "x_co.paa")
    assert img.shape[:2] == (16, 16)


def test_paa_file_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(limits, "MAX_PAA_FILE", 50)
    p = write(tmp_path, "a.paa", b"\0" * 100)
    bounded(lambda: load_rgba(p), PaaError)


# ---------------------------------------------------------------- OBJ
def test_obj_garbage_and_bad_indices(tmp_path):
    cases = {"empty": "", "nogeo": "# nothing\n", "badidx": "v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 99\n", "zero": "v 0 0 0\nv 1 0 0\nv 0 1 0\nf 0 1 2\n",
             "text": "v a b c\nf x y z\n", "neg": "v 0 0 0\nv 1 0 0\nv 0 1 0\nf -9 1 2\n", "vt": "v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1/9/1 2/9/1 3/9/1\n"}
    for name, text in cases.items():
        bounded(lambda t=text, n=name: load_obj(write(tmp_path, n + ".obj", t.encode())), MeshError)


def test_obj_binary_noise_and_size_limit(tmp_path, monkeypatch):
    rnd = random.Random(9)
    bounded(lambda: load_obj(write(tmp_path, "n.obj", bytes(rnd.randrange(256) for _ in range(20000)))), MeshError)
    monkeypatch.setattr(limits, "MAX_OBJ_FILE", 10)
    bounded(lambda: load_obj(write(tmp_path, "big.obj", b"v 0 0 0\n" * 10)), MeshError)


# ---------------------------------------------------------------- RVMAT
def test_rvmat_deep_nesting_and_garbage():
    bounded(lambda: Material.from_text("class A {" * 5000), RvmatParseError)
    bounded(lambda: Material.from_text("a[] = " + "{" * 5000 + "1" + "}" * 5000 + ";"), RvmatParseError)
    bounded(lambda: Material.from_text(";" * 500_000), RvmatParseError)
    bounded(lambda: Material.from_text("/* " + "x" * 100000), RvmatParseError)
    bounded(lambda: Material.from_text('a = "' + "x" * 100000), RvmatParseError)


def test_rvmat_file_limit(tmp_path, monkeypatch):
    p = write(tmp_path, "big.rvmat", b"a = 1;\n" * (400 * 1024))
    bounded(lambda: Material.load(p), ValueError)


def test_rvmat_regex_linear():
    t0 = time.perf_counter()
    for s in ('"' + '""' * 50000, "1" * 100000, "a" * 100000, "/" * 50000):
        try:
            Material.from_text(s)
        except RvmatParseError:
            pass
    assert time.perf_counter() - t0 < MAX_SECONDS


# ---------------------------------------------------------------- изображения
def _png_header(w, h):
    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(b"\0" * 16)) + chunk(b"IEND", b"")


def test_image_huge_dimensions_rejected_before_decoding(tmp_path):
    bounded(lambda: load_rgba(write(tmp_path, "bomb.png", _png_header(60000, 60000))), TextureError)


def test_image_truncated_and_garbage(tmp_path):
    good = _png_header(4, 4)
    for i, blob in enumerate((good[:20], good[:40], b"\x89PNG\r\n\x1a\n" + b"\0" * 30, b"not an image")):
        bounded(lambda b=blob, i=i: load_rgba(write(tmp_path, f"t{i}.png", b)), (TextureError, OSError))


def test_pillow_limit_is_set():
    assert textures.Image.MAX_IMAGE_PIXELS == limits.MAX_IMAGE_PIXELS


# ---------------------------------------------------------------- JSON проекта
def test_project_json_deep_and_huge(tmp_path, monkeypatch):
    p = write(tmp_path, "deep.rvproj", b"[" * 200000)
    bounded(lambda: Project.load(p), safeio.DataError)
    monkeypatch.setattr(limits, "MAX_JSON_FILE", 100)
    p2 = write(tmp_path, "big.rvproj", json.dumps({"slots": [], "x": "a" * 500}).encode())
    bounded(lambda: Project.load(p2), safeio.DataError)


def test_project_json_garbage(tmp_path):
    for i, blob in enumerate((b"", b"\xff\xfe\x00", b"{", b"null", b"123", b'"str"', b"[]")):
        bounded(lambda b=blob, i=i: Project.load(write(tmp_path, f"j{i}.rvproj", b)), safeio.DataError)
