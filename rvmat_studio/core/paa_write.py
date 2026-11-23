"""Кодирование PNG/TGA/… → PAA (DXT1/DXT5, несжатые mip-уровни) на numpy.

Рекомендуется для боевых текстур использовать ImageToPAA из DayZ Tools: этот кодер проверен только
чтением собственным декодером (core/paa.py), не самой игрой.
"""
from __future__ import annotations

import math
import struct

import numpy as np
from PIL import Image


def _pow2(n: int) -> int:
    return max(4, 2 ** round(math.log2(max(n, 1))))


def format_for(role: str, has_alpha: bool) -> str:
    return "DXT5" if role in ("nohq", "smdi", "ca") or has_alpha else "DXT1"


def swizzle_normal(rgba: np.ndarray) -> np.ndarray:
    """Исходная нормаль (R=X, G=Y, A=1) -> формат файла _nohq: A = 255−R, R = 255−A."""
    out = rgba.copy()
    out[:, :, 0] = 255 - rgba[:, :, 3]
    out[:, :, 3] = 255 - rgba[:, :, 0]
    return out


def _to565(c: np.ndarray) -> np.ndarray:
    r = np.rint(c[:, 0] * 31 / 255).astype(np.uint32)
    g = np.rint(c[:, 1] * 63 / 255).astype(np.uint32)
    b = np.rint(c[:, 2] * 31 / 255).astype(np.uint32)
    return (r << 11) | (g << 5) | b


def _from565(c: np.ndarray) -> np.ndarray:
    return np.stack([((c >> 11) & 31) * 255 / 31, ((c >> 5) & 63) * 255 / 63, (c & 31) * 255 / 31], -1)


def encode_dxt(rgba: np.ndarray, kind: str) -> bytes:
    h, w = rgba.shape[:2]
    bw, bh = (w + 3) // 4, (h + 3) // 4
    a = np.pad(rgba, ((0, bh * 4 - h), (0, bw * 4 - w), (0, 0)), mode="edge")
    blocks = a.reshape(bh, 4, bw, 4, 4).transpose(0, 2, 1, 3, 4).reshape(-1, 16, 4)
    out = []
    for s in range(0, len(blocks), 32768):                         # по частям — ограничить память
        b = blocks[s:s + 32768]
        n = len(b)
        res = np.zeros((n, 16 if kind == "DXT5" else 8), np.uint8)
        o = 0
        if kind == "DXT5":
            al = b[:, :, 3].astype(np.int32)
            a0, a1 = al.max(1), al.min(1)
            eq = a0 == a1
            a1 = np.where(eq & (a1 > 0), a1 - 1, a1); a0 = np.where(eq & (a1 == 0) & (a0 == 0), 1, a0)
            ks = np.arange(1, 7)
            pal = np.concatenate([a0[:, None], a1[:, None], ((7 - ks)[None] * a0[:, None] + ks[None] * a1[:, None]) // 7], 1)    # (n,8)
            idx = np.abs(al[:, :, None] - pal[:, None, :]).argmin(2).astype(np.uint64)
            bits = (idx << (3 * np.arange(16, dtype=np.uint64))[None]).sum(1)
            res[:, 0], res[:, 1] = a0, a1
            for k in range(6):
                res[:, 2 + k] = ((bits >> np.uint64(8 * k)) & np.uint64(255)).astype(np.uint8)
            o = 8
        rgb = b[:, :, :3].astype(np.float32)
        mn, mx = rgb.min(1), rgb.max(1)
        inset = np.floor((mx - mn) / 16)
        c0, c1 = _to565(np.clip(mx - inset, 0, 255)), _to565(np.clip(mn + inset, 0, 255))
        sw = c0 < c1
        c0, c1 = np.where(sw, c1, c0), np.where(sw, c0, c1)
        c0 = np.where(c0 == c1, np.minimum(c0 + 1, 65535), c0)
        A, B = _from565(c0), _from565(c1)
        pal = np.stack([A, B, (2 * A + B) / 3, (A + 2 * B) / 3], 1)             # (n,4,3)
        d = ((rgb[:, :, None, :] - pal[:, None, :, :]) ** 2).sum(-1)             # (n,16,4)
        ix = d.argmin(2).astype(np.uint32)
        bits = (ix << (2 * np.arange(16, dtype=np.uint32))[None]).sum(1).astype(np.uint32)
        res[:, o] = c0 & 255; res[:, o + 1] = c0 >> 8; res[:, o + 2] = c1 & 255; res[:, o + 3] = c1 >> 8
        for k in range(4):
            res[:, o + 4 + k] = (bits >> np.uint32(8 * k)) & np.uint32(255)
        out.append(res.tobytes())
    return b"".join(out)


def _tag(name: str, data: bytes) -> bytes:
    return b"GGAT" + name[::-1].encode() + struct.pack("<I", len(data)) + data


def encode_paa(rgba: np.ndarray, role: str = "co") -> tuple[bytes, dict]:
    """rgba uint8 (H,W,4) -> (байты PAA, сведения). Размеры, не равные степени двойки, округляются до ближайшей."""
    rgba = np.ascontiguousarray(rgba, dtype=np.uint8)
    h, w = rgba.shape[:2]
    resized = (w & (w - 1)) != 0 or (h & (h - 1)) != 0
    if resized:
        rgba = np.asarray(Image.fromarray(rgba).resize((_pow2(w), _pow2(h)), Image.LANCZOS))
        h, w = rgba.shape[:2]
    has_alpha = bool((rgba[:, :, 3] < 255).any())
    kind = format_for(role, has_alpha)
    src = swizzle_normal(rgba) if role == "nohq" else rgba
    mips, cur = [], src
    while True:
        mips.append((cur.shape[1], cur.shape[0], encode_dxt(cur, kind)))
        if cur.shape[0] == 1 and cur.shape[1] == 1:
            break
        cur = np.asarray(Image.fromarray(cur).resize((max(1, cur.shape[1] // 2), max(1, cur.shape[0] // 2)), Image.BOX))
    avg = rgba.reshape(-1, 4).mean(0).astype(np.uint8).tobytes()
    mx = rgba.reshape(-1, 4).max(0).astype(np.uint8).tobytes()
    tags = _tag("CGVA", avg) + _tag("CXAM", mx) + _tag("GALF", bytes([1 if has_alpha else 0, 0, 0, 0]))
    head_len = 2 + len(tags) + 12 + 64 + 2
    offs, pos = [0] * 16, head_len
    for i, (_, _, d) in enumerate(mips):
        if i < 16:
            offs[i] = pos
        pos += 7 + len(d)
    tags += _tag("SFFO", struct.pack("<16I", *offs))
    out = bytearray(struct.pack("<H", 0xFF01 if kind == "DXT1" else 0xFF05)) + tags + struct.pack("<H", 0)
    for mw, mh, d in mips:
        out += struct.pack("<HH", mw, mh) + len(d).to_bytes(3, "little") + d
    out += struct.pack("<H", 0)
    return bytes(out), {"kind": kind, "width": w, "height": h, "resized": resized, "mips": len(mips), "alpha": has_alpha}
