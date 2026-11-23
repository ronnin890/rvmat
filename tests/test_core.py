import struct
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from rvmat_studio.backend import KitSpec, Material, State, Severity
from rvmat_studio.core import export as ex
from rvmat_studio.core.meshes import SHAPES, load_obj
from rvmat_studio.core.paa import PaaError, decode_dxt, lzo1x_decompress, lzo_store, read_paa, write_paa_dxt1_raw
from rvmat_studio.core.presets import CATEGORIES, BUILTIN, PresetManager, super_template
from rvmat_studio.core.project import Project, TARGET_DECOR, TARGET_SHARED
from rvmat_studio.core.render import RenderOptions, make_resolver, render, render_uv_layout
from rvmat_studio.core.textures import (auto_match, channel_view, guess_stage, probe, scan_folder, split_suffix,
                                        to_game_path)

S = Path(__file__).parent / "samples"


# ------------------------------------------------------------------ PAA / LZO
def test_lzo_literals_roundtrip():
    for n in (1, 3, 17, 238, 239, 1000, 70000):
        data = bytes(np.random.default_rng(n).integers(0, 256, n, dtype=np.uint8))
        assert lzo1x_decompress(lzo_store(data), n) == data


def test_lzo_match_handcrafted():
    # 'abcabcabc': литералы 'abc' (первый байт 17+3=20), затем M2-match: t=0x40|(len-1)<<5.. dist=3 len=6
    lit = bytes([20]) + b"abc"
    # M2: t>=64: dist = 1 + ((t>>2)&7) + (next<<3); len=(t>>5)-1+2. dist=3 -> ((t>>2)&7)=2, next=0; len 6 -> (t>>5)=5
    t = (5 << 5) | (2 << 2)
    stream = lit + bytes([t, 0]) + b"\x11\x00\x00"
    assert lzo1x_decompress(stream, 9) == b"abcabcabc"


def test_dxt1_decode_solid_color():
    # c0=pure red 565 (0xF800), c1=0, все индексы 0 -> красный
    blk = struct.pack("<HHI", 0xF800, 0x0000, 0)
    img = decode_dxt(blk * 4, 8, 8, "DXT1")
    assert img.shape == (8, 8, 4) and (img[..., 0] == 255).all() and (img[..., 1:3] == 0).all() and (img[..., 3] == 255).all()


def test_dxt5_alpha_and_color():
    alpha = bytes([200, 100]) + b"\x00" * 6               # индексы 0 -> a0=200
    color = struct.pack("<HHI", 0x07E0, 0, 0)            # зелёный
    img = decode_dxt(alpha + color, 4, 4, "DXT5")
    assert (img[..., 3] == 200).all() and (img[..., 1] == 255).all()


def test_read_paa_dxt1_and_lzo():
    rgba = np.zeros((8, 8, 4), np.uint8); rgba[..., :3] = (200, 50, 50); rgba[..., 3] = 255
    paa = write_paa_dxt1_raw(rgba)
    arr, fmt = read_paa(paa)
    assert fmt == "DXT1" and arr.shape == (8, 8, 4) and abs(int(arr[0, 0, 0]) - 200) < 12
    # то же с LZO-флагом
    raw = decode_hdr = paa
    pos = paa.index(struct.pack("<HH", 8, 8))
    body = paa[pos + 7:]
    packed = lzo_store(body)
    paa2 = paa[:pos] + struct.pack("<HH", 8 | 0x8000, 8) + len(packed).to_bytes(3, "little") + packed
    arr2, _ = read_paa(paa2)
    assert (arr2 == arr).all()


def test_read_paa_errors():
    with pytest.raises(PaaError):
        read_paa(b"\x00" * 20)
    with pytest.raises(PaaError):
        read_paa(b"\x01")


# ------------------------------------------------------------------ текстуры
def test_suffixes_and_stage_guess():
    assert split_suffix("eyemask_black_nohq") == ("eyemask_black", "nohq")
    assert guess_stage("x_as.paa") == 4 and guess_stage("x_smdi.png") == 5 and guess_stage("x_nohq.tga") == 1
    assert guess_stage("x_co.paa") == 0 and guess_stage("x_ca.paa") == 0   # базовый цвет — не стадия RVMAT
    assert guess_stage("plain.png") is None


def test_auto_match_and_scan(tmp_path):
    for n in ("helm_nohq", "helm_as", "helm_smdi", "helm_co", "helm_ca", "other_as", "readme"):
        Image.new("RGBA", (8, 8)).save(tmp_path / f"{n}.png")
    files = scan_folder(tmp_path)
    g = auto_match(files)
    assert set(g["helm"]) == {0, 1, 4, 5}                       # 0 = Base Color (_co): не стадия RVMAT
    assert g["helm"][4].name == "helm_as.png" and g["helm"][0].name == "helm_co.png"
    assert set(g["other"]) == {4}


def test_probe_and_problems(tmp_path):
    p = tmp_path / "a.png"
    Image.new("RGBA", (30, 16), (1, 2, 3, 128)).save(p)
    ti = probe(p)
    assert (ti.width, ti.height, ti.has_alpha) == (30, 16, True)
    assert any("степень" in x for x in ti.problems()) and any(".paa" in x for x in ti.problems())
    assert probe(tmp_path / "none.png").problems() == ["Файл не найден"]
    (tmp_path / "bad.png").write_bytes(b"not an image")
    assert "Не удалось" in probe(tmp_path / "bad.png").problems()[0]


def test_channel_view():
    a = np.zeros((2, 2, 4), np.uint8); a[..., 0] = 10; a[..., 1] = 20; a[..., 3] = 99
    assert (channel_view(a, "g")[..., 0] == 20).all() and (channel_view(a, "a")[..., 1] == 99).all()
    assert (channel_view(a, "swapg")[..., 1] == 235).all()


def test_to_game_path(tmp_path):
    f = tmp_path / "dz" / "chars" / "x_as.png"
    f.parent.mkdir(parents=True); f.write_bytes(b"")
    assert to_game_path(f, tmp_path) == "dz\\chars\\x_as.paa"
    with pytest.raises(ValueError):
        to_game_path(Path("/elsewhere/x.png"), tmp_path)


# ------------------------------------------------------------------ пресеты
def test_builtin_presets_cover_categories_and_are_valid():
    cats = {p.category for p in BUILTIN}
    assert cats == set(CATEGORIES)
    for p in BUILTIN:
        m = p.material()
        assert m.get("PixelShaderID") == "Super" and len(m.stages()) == 7
        assert m.stage(2).filter == "Trilinear" and m.stage(7).texture.endswith("env_land_co.paa")


def test_preset_manager_crud(tmp_path):
    pm = PresetManager(tmp_path)
    n0 = len(pm.items)
    m = super_template(specular=0.33)
    p = pm.create("Мой", "Металл", "тест", m)
    pm.set_favorite(p.id, True)
    assert [x.id for x in pm.search(favorites_only=True)] == [p.id]
    assert pm.search("мой")[0].id == p.id and not pm.search("несуществует")
    pm2 = PresetManager(tmp_path)                       # персистентность
    assert len(pm2.items) == n0 + 1 and pm2.items[p.id].favorite
    d = pm2.duplicate(p.id)
    pm2.export_to([p.id, d.id], tmp_path / "x.json")
    got = pm2.import_from(tmp_path / "x.json")
    assert len(got) == 2 and len(pm2.items) == n0 + 4
    pm2.update(p.id, name="Новое имя")
    assert pm2.items[p.id].name == "Новое имя"
    pm2.delete(p.id)
    assert p.id not in pm2.items
    with pytest.raises(PermissionError):
        pm2.delete(BUILTIN[0].id)
    with pytest.raises(PermissionError):
        pm2.update(BUILTIN[0].id, name="x")


# ------------------------------------------------------------------ проект
def test_project_undo_redo_and_merge():
    p = Project("t")
    p.set_value(TARGET_SHARED, "specularPower", 20, "sp")
    p.set_value(TARGET_SHARED, "specularPower", 21, "sp")       # слияние серии
    p.set_value(TARGET_SHARED, "ambient", [0.1, 0.1, 0.1, 1])
    assert p.spec.shared.get("specularPower") == 21
    assert p.undo() and p.spec.shared.get("ambient")[0] == pytest.approx(0.9, abs=1e-6)
    assert p.undo() and p.spec.shared.get("specularPower") == 15
    assert not p.undo()
    assert p.redo() and p.spec.shared.get("specularPower") == 21
    assert any("Отмена" in h for h in p.history)


def test_project_targets_and_reset():
    p = Project("t")
    p.set_value(State.DAMAGE.value, "specularPower", 40)
    p.set_value(TARGET_DECOR, "Stage1.texture", "dz\\a_nohq.paa")
    assert p.effective(State.DAMAGE).get("specularPower") == 40
    assert p.effective(State.DESTRUCT).get("specularPower") == 15
    assert p.effective(State.DECORATIVE_DESTRUCT).stage(1).texture == "dz\\a_nohq.paa"
    assert p.effective(State.DESTRUCT).stage(1).texture == ""
    assert p.is_overridden(State.DAMAGE.value, "specularPower")
    p.reset_value(State.DAMAGE.value, "specularPower")
    assert p.effective(State.DAMAGE).get("specularPower") == 15
    p.set_value(TARGET_SHARED, "specularPower", 99)           # общее распространяется
    assert p.effective(State.DECORATIVE).get("specularPower") == 99


def test_project_save_load_and_autosave(tmp_path):
    p = Project("kit")
    p.set_value(State.DESTRUCT.value, "Stage2.Filter", "Anisotropic")
    p.set_value(TARGET_DECOR, "ambient", [0.5, 0.5, 0.5, 1])
    p.textures = ["/x/y.png"]; p.tex_map = {"dz\\y.paa": "/x/y.png"}
    p.enabled.discard(State.BASE)
    f = tmp_path / "k.rvproj"
    p.save(f)
    q = Project.load(f)
    assert q.kit().keys() == p.kit().keys() and "kit.rvmat" not in q.kit()
    assert q.kit()["kit_destruct.rvmat"].to_text() == p.kit()["kit_destruct.rvmat"].to_text()
    assert q.tex_map == p.tex_map and q.textures == p.textures
    p.save(f)                                                  # повторное сохранение создаёт .bak
    assert (tmp_path / "k.rvproj.bak").exists()
    p.dirty = True; p.autosave()
    assert p.autosave_path() in Project.recovery_files()
    assert Project.load(p.autosave_path()).name == "kit"
    p.clear_autosave()


def test_import_samples_regenerates_byte_exact():
    p = Project()
    msgs = p.import_files([S / "eyemask_decorative.rvmat", S / "eyemask_damage.rvmat"])
    assert p.name == "eyemask" and msgs
    kit = p.kit()
    for state_file in ("eyemask_damage.rvmat", "eyemask_decorative.rvmat", "eyemask_decorative_damage.rvmat",
                       "eyemask_decorative_destruct.rvmat"):
        assert kit[state_file].to_text().encode() == (S / state_file).read_bytes(), state_file


def test_import_all_four_samples_and_validate_clean():
    p = Project()
    p.import_files(sorted(S.glob("*.rvmat")))
    assert not [i for i in p.validate() if i.severity >= Severity.WARNING]


def test_base_color_is_project_preview_state_not_rvmat(tmp_path):
    p = Project("t")
    before = {k: m.to_text() for k, m in p.kit().items()}
    p.set_base_color("/x/helm_co.png")
    assert p.base_color == "/x/helm_co.png" and "/x/helm_co.png" in p.textures
    assert {k: m.to_text() for k, m in p.kit().items()} == before         # RVMAT не затронуты
    f = tmp_path / "b.rvproj"; p.save(f)
    assert Project.load(f).base_color == "/x/helm_co.png"
    p.undo()
    assert p.base_color == ""


def test_copy_stage():
    p = Project("t")
    p.set_value(State.DAMAGE.value, "Stage1.texture", "dz\\q.paa")
    p.copy_stage(State.DAMAGE, State.DESTRUCT, 1)
    assert p.effective(State.DESTRUCT).stage(1).texture == "dz\\q.paa"


def test_apply_material_keeps_textures():
    p = Project("t")
    p.set_value(TARGET_SHARED, "Stage4.texture", "dz\\mine_as.paa")
    p.apply_material(super_template(specular=0.1))
    assert p.spec.shared.stage(4).texture == "dz\\mine_as.paa" and p.spec.shared.get("specular")[0] == pytest.approx(0.1, abs=1e-6)


# ------------------------------------------------------------------ экспорт


# ------------------------------------------------------------------ рендер
@pytest.mark.parametrize("shape", list(SHAPES))
def test_render_all_shapes(shape):
    m = super_template()
    img = render(m, RenderOptions(shape=shape, width=160, height=160), make_resolver(None))
    assert img.shape == (160, 160, 4) and img.dtype == np.uint8
    assert img[..., :3].std() > 3                      # не пусто, есть форма и шейдинг


def _solid(rgba):
    return np.tile(np.array(rgba, np.float32) / 255, (16, 16, 1))


def test_base_color_comes_from_model_not_rvmat():
    m = super_template()
    res = make_resolver(None)
    before = m.to_text()
    o = RenderOptions(width=160, height=160, view="albedo", base_color=_solid([230, 20, 20, 255]))
    img = render(m, o, res)
    assert img[80, 80, 0] > 150 and img[80, 80, 1] < 60
    grey = render(m, RenderOptions(width=160, height=160, view="albedo"), res)
    assert abs(int(grey[80, 80, 0]) - int(grey[80, 80, 2])) < 5          # без Base Color — нейтральный серый
    assert m.to_text() == before                                          # RVMAT не изменился


def test_as_is_ambient_shadow_not_color(tmp_path):      # _as: R/B = 1, затенение в G
    (tmp_path / "dz").mkdir()
    Image.fromarray(np.tile(np.array([230, 40, 230, 255], np.uint8), (16, 16, 1))).save(tmp_path / "dz" / "x_as.png")
    m = super_template()
    res = make_resolver(tmp_path)
    base = _solid([200, 120, 40, 255])
    o = RenderOptions(width=160, height=160, view="albedo", base_color=base)
    without = render(m, o, res)
    m.stage(4).texture = "dz\\x_as.paa"
    with_as = render(m, o, res)
    assert (without == with_as).all()                                     # _as не меняет базовый цвет
    ao = render(m, RenderOptions(width=160, height=160, view="ao"), res)
    assert 100 < ao[80, 80, 0] < 120 and abs(int(ao[80, 80, 0]) - int(ao[80, 80, 1])) < 3     # AO = канал G: 40/255 → гамма-вывод ≈109
    fin_as = render(m, RenderOptions(width=160, height=160, base_color=base), res).astype(int)
    m.stage(4).texture = ""
    fin_no = render(m, RenderOptions(width=160, height=160, base_color=base), res).astype(int)
    assert fin_as[..., :3].mean() < fin_no[..., :3].mean()                # затенение темнее
    o_off = RenderOptions(width=160, height=160, view="ao"); m.stage(4).texture = "dz\\x_as.paa"; o_off.stages_on[4] = False
    assert render(m, o_off, res)[80, 80, 0] > 240                         # стадия отключена в превью → ao = 1
    assert m.stage(4).texture == "dz\\x_as.paa"                           # RVMAT не изменён


def test_render_views_and_uv():
    m = super_template()
    for v in ("final", "albedo", "ao", "normal", "specular", "metal", "gloss", "emissive", "detail", "checker"):
        assert render(m, RenderOptions(width=96, height=96, view=v), make_resolver(None)).shape == (96, 96, 4)
    uv = render_uv_layout(SHAPES["Сфера"](), 128)
    assert uv.shape == (128, 128, 4)


def test_specular_power_changes_highlight():
    r = make_resolver(None)
    a, b = super_template(power=2), super_template(power=120)
    ia = render(a, RenderOptions(width=128, height=128), r).astype(int)
    ib = render(b, RenderOptions(width=128, height=128), r).astype(int)
    assert np.abs(ia - ib).mean() > 0.5


def test_obj_import(tmp_path):
    f = tmp_path / "t.obj"
    f.write_text("v 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\nvt 0 0\nvt 1 0\nvt 1 1\nvt 0 1\nf 1/1 2/2 3/3 4/4\n")
    m = load_obj(f)
    assert len(m.f) == 2 and m.t.shape == (4, 3)
    assert render(super_template(), RenderOptions(width=64, height=64), make_resolver(None), m).shape == (64, 64, 4)
    (tmp_path / "e.obj").write_text("# empty")
    with pytest.raises(ValueError):
        load_obj(tmp_path / "e.obj")


def test_uv_params_roundtrip():
    m = super_template()
    st = m.stage(4)
    st.set_uv_params(tile_u=4, tile_v=2, offset_u=0.25, offset_v=0.5, rotation=0)
    assert st.uv("aside")[0] == 4 and st.uv("up")[1] == 2 and st.uv("pos")[:2] == [0.25, 0.5]
    p = st.uv_params()
    assert p["tile_u"] == pytest.approx(4) and p["tile_v"] == pytest.approx(2)
    st.set_uv_params(1, 1, 0, 0, 90)
    assert st.uv_params()["rotation"] == pytest.approx(90)


# ------------------------------------------------------------------ Workbench
def test_find_gproj_priority_and_search(tmp_path):
    from rvmat_studio.core.dayztools import find_gproj
    p = tmp_path / "P"; (p / "MyMod" / "Workbench").mkdir(parents=True)
    g1 = p / "MyMod" / "Workbench" / "dayz.gproj"; g1.write_text("x")
    tools = tmp_path / "Tools"; (tools / "Bin" / "Workbench").mkdir(parents=True)
    g2 = tools / "Bin" / "Workbench" / "dayz.gproj"; g2.write_text("x")
    explicit = tmp_path / "my.gproj"; explicit.write_text("x")
    assert find_gproj(str(explicit), str(p), "", str(tools)) == [explicit, g1, g2]
    assert find_gproj("", str(p), "", "") == [g1]
    (p / "dayz.gproj").write_text("x")
    assert find_gproj("", str(p), "", "")[0] == p / "dayz.gproj"          # корень P: важнее вложенных
    assert find_gproj("", str(tmp_path / "none"), "", "") == []


def test_workbench_launch_uses_project_and_cwd(tmp_path, monkeypatch):
    import subprocess
    from rvmat_studio.core import dayztools as ex
    wb = tmp_path / "Workbench" / "workbenchApp.exe"; wb.parent.mkdir(); wb.write_text("")
    g = tmp_path / "P" / "dayz.gproj"; g.parent.mkdir(); g.write_text("")
    calls = {}
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: calls.update(cmd=cmd, kw=kw) or object())
    _, used = ex.check_in_workbench(str(wb), "", str(tmp_path / "P"), "", "")
    assert used == g and calls["cmd"] == [str(wb), f"-project={g}"]
    assert calls["kw"]["cwd"] == str(wb.parent)                           # рабочая папка = папка Workbench


def test_workbench_errors_are_explained(tmp_path):
    from rvmat_studio.core import dayztools as ex
    wb = tmp_path / "workbenchApp.exe"; wb.write_text("")
    with pytest.raises(ex.WorkbenchError, match="workbenchApp"):
        ex.check_in_workbench("", "", "", "", "")
    with pytest.raises(ex.WorkbenchError, match="Смонтируйте"):
        ex.check_in_workbench(str(wb), "", str(tmp_path / "nodrive"), "", "")
    with pytest.raises(ex.WorkbenchError, match="dayz.gproj"):
        ex.check_in_workbench(str(wb), "", str(tmp_path), "", "")


# ------------------------------------------------------------------ _nohq: перестановка каналов
def _dxt5_paa(r, g, b, a, w=8, h=8, tags=(b"SWIZ",)):
    c565 = ((r * 31 // 255) << 11) | ((g * 63 // 255) << 5) | (b * 31 // 255)
    blk = bytes([a, a, 0, 0, 0, 0, 0, 0]) + struct.pack("<HHI", c565, c565, 0)
    body = blk * (((w + 3) // 4) * ((h + 3) // 4))
    hdr = struct.pack("<H", 0xFF05)
    for t in tags:
        hdr += b"GGAT" + t[::-1] + struct.pack("<I", 4) + b"\x00\x00\x00\x00"
    return hdr + struct.pack("<H", 0) + struct.pack("<HH", w, h) + len(body).to_bytes(3, "little") + body


def test_nohq_paa_is_unswizzled_to_purple_not_cyan():
    # плоская нормаль в файле: R=0, G=128, B=255, A=128 (сырые каналы = «голубой»)
    paa = _dxt5_paa(0, 128, 255, 128)
    raw, _ = read_paa(paa, "x_as.paa")                      # другое имя → без перестановки
    assert raw[0, 0, 0] < 10 and raw[0, 0, 2] > 240          # голубой (0,128,255)
    fixed, _ = read_paa(paa, "helm_nohq.paa")
    r, g, b, a = (int(v) for v in fixed[0, 0])
    assert abs(r - 127) < 6 and abs(g - 128) < 6 and b > 240 and a > 240    # (127,128,255) — фиолетово-синий
    auto, _ = read_paa(paa, "")                              # тег SWIZ без имени → тоже применяется
    assert (auto == fixed).all()
    assert (read_paa(paa, "helm_smdi.paa")[0] == raw).all()   # SMDI с тегом не трогаем
    assert (read_paa(paa, "helm_nohq.paa", swizzle=False)[0] == raw).all()
    nm = fixed[..., :3].astype(float) / 127.5 - 1
    assert abs(nm[0, 0, 0]) < 0.06 and abs(nm[0, 0, 1]) < 0.06      # X≈0, Y≈0 → вектор вдоль Z


def test_dxt1_and_png_are_never_swizzled(tmp_path):
    rgba = np.zeros((8, 8, 4), np.uint8); rgba[..., :3] = (128, 128, 255); rgba[..., 3] = 255
    a, _ = read_paa(write_paa_dxt1_raw(rgba), "x_nohq.paa")
    assert abs(int(a[0, 0, 0]) - 128) < 12 and a[0, 0, 3] == 255
    Image.fromarray(rgba).save(tmp_path / "n_nohq.png")
    from rvmat_studio.core.textures import load_rgba
    assert (load_rgba(tmp_path / "n_nohq.png")[0] == rgba).all()
