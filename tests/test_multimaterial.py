"""Два и более материала: правки и текстуры не должны влиять друг на друга."""
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from rvmat_studio.core.meshes import get_shape
from rvmat_studio.core.presets import PresetManager, super_template
from rvmat_studio.core.project import Project
from rvmat_studio.core.render import RenderOptions, make_resolver, render
from rvmat_studio.core.settings import Settings
from rvmat_studio.core.textures import unique_game_path


# ------------------------------------------------------------------ уникальные игровые пути
def test_unique_game_path_disambiguates_same_names(tmp_path):
    a, b, c = tmp_path / "jacket" / "x_as.png", tmp_path / "plate" / "x_as.png", tmp_path / "other" / "plate" / "x_as.png"
    m: dict = {}
    pa = unique_game_path(a, m); m[pa.lower()] = str(a)
    assert pa == "x_as.paa"
    assert unique_game_path(a, m) == "x_as.paa"                       # тот же файл — то же имя (повторное назначение)
    pb = unique_game_path(b, m); m[pb.lower()] = str(b)
    assert pb == "plate\\x_as.paa"
    pc = unique_game_path(c, m); m[pc.lower()] = str(c)
    assert pc == "other\\plate\\x_as.paa" and len({pa.lower(), pb.lower(), pc.lower()}) == 3
    # регистр имени не делает файлы «разными»
    assert unique_game_path(tmp_path / "z" / "X_AS.png", {"x_as.paa": str(a)}) != "X_AS.paa"


# ------------------------------------------------------------------ GUI


def _png(path, rgba):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.tile(np.array(rgba, np.uint8), (8, 8, 1))).save(path)


# ------------------------------------------------------------------ GL: кэш текстур
