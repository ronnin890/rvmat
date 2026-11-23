"""Фаззинг парсеров (hypothesis): любой вход → успех или контролируемая ошибка своего типа, без других исключений."""
import os
import tempfile

import pytest

hyp = pytest.importorskip("hypothesis", reason="hypothesis не установлен (pip install hypothesis)")
from hypothesis import HealthCheck, given, settings, strategies as st

from rvmat_studio.backend import Material
from rvmat_studio.backend.parser import RvmatParseError
from rvmat_studio.core import fbxwrite, meshes, safeio
from rvmat_studio.core.fbx import FbxError, load_fbx
from rvmat_studio.core.meshes import MeshError, load_obj
from rvmat_studio.core.paa import PaaError, lzo1x_decompress, read_paa
from rvmat_studio.core.presets import Preset
from rvmat_studio.core.project import Project
from rvmat_studio.core.textures import TextureError, load_rgba

CFG = settings(max_examples=200, deadline=2000, suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large])


def _tmp(data: bytes, suffix: str) -> str:
    fd, p = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return p


def _run(data, suffix, fn, errors):
    p = _tmp(data, suffix)
    try:
        fn(p)
    except errors:
        pass
    finally:
        os.unlink(p)


def _valid_fbx() -> bytes:
    p = _tmp(b"", ".fbx")
    fbxwrite.write_fbx(meshes.cube(), p, "cube")
    d = open(p, "rb").read()
    os.unlink(p)
    return d


VALID_FBX = _valid_fbx()


@CFG
@given(st.binary(max_size=4000))
def test_fuzz_fbx_random_bytes(data):
    _run(data, ".fbx", load_fbx, FbxError)


@CFG
@given(st.integers(0, len(VALID_FBX) - 1), st.integers(0, 255), st.integers(0, len(VALID_FBX)))
def test_fuzz_fbx_mutated(pos, byte, cut):
    d = bytearray(VALID_FBX)
    d[pos] = byte
    _run(bytes(d[:cut or len(d)]), ".fbx", load_fbx, FbxError)


@CFG
@given(st.text(max_size=3000))
def test_fuzz_fbx_ascii(text):
    _run(("; FBX 7.4 project file\n" + text).encode("utf-8", "replace"), ".fbx", load_fbx, FbxError)


@CFG
@given(st.binary(max_size=3000))
def test_fuzz_paa(data):
    try:
        read_paa(data, "x_nohq.paa")
    except PaaError:
        pass


@CFG
@given(st.binary(min_size=2, max_size=2000), st.integers(0, 100000))
def test_fuzz_lzo(data, n):
    try:
        lzo1x_decompress(data, n)
    except PaaError:
        pass


@CFG
@given(st.text(max_size=2000))
def test_fuzz_obj(text):
    _run(text.encode("utf-8", "replace"), ".obj", load_obj, MeshError)


@CFG
@given(st.text(alphabet="vtnf/ -0123456789.\n", max_size=1500))
def test_fuzz_obj_structured(text):
    _run(text.encode(), ".obj", load_obj, MeshError)


@CFG
@given(st.text(max_size=2000))
def test_fuzz_rvmat(text):
    try:
        Material.from_text(text)
    except RvmatParseError:
        pass


@CFG
@given(st.text(alphabet='class{}[]=;,:" \n/*_0123456789.abcPixelShaderID', max_size=1500))
def test_fuzz_rvmat_structured(text):
    try:
        m = Material.from_text(text)
        Material.from_text(m.to_text())                       # то, что разобралось, обязано сериализоваться и читаться обратно
    except RvmatParseError:
        pass


@CFG
@given(st.binary(max_size=2000))
def test_fuzz_images(data):
    _run(data, ".png", load_rgba, (TextureError, PaaError, OSError))


JSONISH = st.recursive(st.none() | st.booleans() | st.integers() | st.floats(allow_nan=True) | st.text(max_size=20),
                       lambda c: st.lists(c, max_size=4) | st.dictionaries(st.text(max_size=12), c, max_size=5), max_leaves=30)


@CFG
@given(JSONISH)
def test_fuzz_project_schema(d):
    try:
        Project.from_dict(d)
    except safeio.DataError:
        pass


@CFG
@given(st.dictionaries(st.sampled_from(["slots", "title", "active", "part_map", "model_path", "export_opts", "enabled", "tex_map", "textures", "version", "name", "rvmat"]), JSONISH, max_size=8))
def test_fuzz_project_known_keys(d):
    try:
        Project.from_dict(d)
    except safeio.DataError:
        pass


@CFG
@given(JSONISH)
def test_fuzz_preset_schema(d):
    try:
        Preset.from_dict(d)
    except safeio.DataError:
        pass
