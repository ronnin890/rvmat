"""Чтение PAA (DXT1/DXT3/DXT5, ARGB4444/1555/8888, AI88) + LZO1X.

Читается только mip 0. Индексные (палитровые) PAA не поддерживаются.
"""
from __future__ import annotations

import struct

import numpy as np

TAGS = {0xFF01: "DXT1", 0xFF02: "DXT2", 0xFF03: "DXT3", 0xFF04: "DXT4", 0xFF05: "DXT5",
        0x4444: "ARGB4444", 0x1555: "ARGB1555", 0x8888: "ARGB8888", 0x8080: "AI88"}


class PaaError(ValueError):
    pass


def lzo1x_decompress(src: bytes, out_len: int) -> bytes:
    """Декомпрессор LZO1X (по референсной реализации)."""
    out = bytearray()
    ip = 0
    n = len(src)

    def copy_match(dist, cnt):
        if dist <= 0 or dist > len(out):
            raise PaaError("LZO: неверное смещение")
        for _ in range(cnt):
            out.append(out[-dist])

    t = src[0]
    state = "loop"
    if t > 17:
        ip = 1
        t -= 17
        if t < 4:
            state = "match_next"
        else:
            out += src[ip:ip + t]; ip += t
            state = "first_literal_run"
    while True:
        if state == "loop":
            t = src[ip]; ip += 1
            if t >= 16:
                state = "match"
            else:
                if t == 0:
                    while src[ip] == 0:
                        t += 255; ip += 1
                    t += 15 + src[ip]; ip += 1
                out += src[ip:ip + t + 3]; ip += t + 3
                state = "first_literal_run"
        if state == "first_literal_run":
            t = src[ip]; ip += 1
            if t >= 16:
                state = "match"
            else:
                dist = 1 + 0x0800 + (t >> 2) + (src[ip] << 2); ip += 1
                copy_match(dist, 3)
                state = "match_done"
        if state == "match":
            if t >= 64:
                dist = 1 + ((t >> 2) & 7) + (src[ip] << 3); ip += 1
                copy_match(dist, (t >> 5) - 1 + 2)
            elif t >= 32:
                t &= 31
                if t == 0:
                    while src[ip] == 0:
                        t += 255; ip += 1
                    t += 31 + src[ip]; ip += 1
                v = src[ip] | (src[ip + 1] << 8); ip += 2
                copy_match(1 + (v >> 2), t + 2)
            elif t >= 16:
                base = (t & 8) << 11
                t &= 7
                if t == 0:
                    while src[ip] == 0:
                        t += 255; ip += 1
                    t += 7 + src[ip]; ip += 1
                v = src[ip] | (src[ip + 1] << 8); ip += 2
                dist = base + (v >> 2)
                if dist == 0:
                    break                      # маркер конца потока
                copy_match(dist + 0x4000, t + 2)
            else:
                dist = 1 + (t >> 2) + (src[ip] << 2); ip += 1
                copy_match(dist, 2)
            state = "match_done"
        if state == "match_done":
            t = src[ip - 2] & 3
            if t == 0:
                state = "loop"
                continue
            state = "match_next"
        if state == "match_next":
            out += src[ip:ip + t]; ip += t
            t = src[ip]; ip += 1
            state = "match"
    if len(out) != out_len:
        raise PaaError(f"LZO: размер {len(out)} != ожидаемого {out_len}")
    return bytes(out)


def lzo_store(data: bytes) -> bytes:
    """Минимальный «упаковщик» только литералами — для тестов декодера."""
    n = len(data)
    if n <= 238:
        return bytes([17 + n]) + data + b"\x11\x00\x00"
    t = n - 3 - 15
    head = bytearray([0])
    t2 = t
    while t2 > 255:
        head.append(0); t2 -= 255
    head.append(t2)
    return bytes(head) + data + b"\x11\x00\x00"


def _rgb565(c):
    r = ((c >> 11) & 31) * 255 // 31
    g = ((c >> 5) & 63) * 255 // 63
    b = (c & 31) * 255 // 31
    return r, g, b


def _dxt_color_blocks(blocks: np.ndarray, dxt1: bool) -> np.ndarray:
    """blocks: (N,8) uint8 -> (N,16,4) RGBA."""
    c0 = blocks[:, 0].astype(np.uint16) | (blocks[:, 1].astype(np.uint16) << 8)
    c1 = blocks[:, 2].astype(np.uint16) | (blocks[:, 3].astype(np.uint16) << 8)
    def unpack(c):
        return np.stack([((c >> 11) & 31) * 255 // 31, ((c >> 5) & 63) * 255 // 63, (c & 31) * 255 // 31], -1).astype(np.int32)
    p0, p1 = unpack(c0), unpack(c1)
    pal = np.zeros((len(blocks), 4, 4), np.int32)
    pal[:, 0, :3], pal[:, 1, :3] = p0, p1
    pal[:, :, 3] = 255
    four = (c0 > c1) | (not dxt1)
    f = four[:, None]
    pal[:, 2, :3] = np.where(f, (2 * p0 + p1) // 3, (p0 + p1) // 2)
    pal[:, 3, :3] = np.where(f, (p0 + 2 * p1) // 3, 0)
    pal[:, 3, 3] = np.where(four, 255, 0)
    bits = blocks[:, 4:8].astype(np.uint32)
    word = bits[:, 0] | (bits[:, 1] << 8) | (bits[:, 2] << 16) | (bits[:, 3] << 24)
    idx = (word[:, None] >> (2 * np.arange(16, dtype=np.uint32))[None]) & 3
    return np.take_along_axis(pal, idx[:, :, None].astype(np.int64), axis=1).astype(np.uint8)


def decode_dxt(data: bytes, w: int, h: int, kind: str) -> np.ndarray:
    bw, bh = (w + 3) // 4, (h + 3) // 4
    bsz = 8 if kind == "DXT1" else 16
    arr = np.frombuffer(data[:bw * bh * bsz], np.uint8)
    if arr.size < bw * bh * bsz:
        raise PaaError("Данные DXT обрезаны")
    arr = arr.reshape(-1, bsz)
    if kind == "DXT1":
        px = _dxt_color_blocks(arr, True)
    else:
        px = _dxt_color_blocks(arr[:, 8:], False)
        if kind in ("DXT3", "DXT2"):
            a = arr[:, :8]
            nib = np.stack([a & 15, a >> 4], -1).reshape(len(arr), 16)
            px[:, :, 3] = nib * 17
        else:  # DXT5 / DXT4
            a0, a1 = arr[:, 0].astype(np.int32), arr[:, 1].astype(np.int32)
            bits = np.zeros(len(arr), np.uint64)
            for i in range(6):
                bits |= arr[:, 2 + i].astype(np.uint64) << np.uint64(8 * i)
            idx = ((bits[:, None] >> (3 * np.arange(16, dtype=np.uint64))[None]) & np.uint64(7)).astype(np.int32)
            pal = np.zeros((len(arr), 8), np.int32)
            pal[:, 0], pal[:, 1] = a0, a1
            for k in range(1, 7):
                pal[:, k + 1] = np.where(a0 > a1, ((7 - k) * a0 + k * a1) // 7,
                                         np.where(k <= 4, ((5 - k) * a0 + k * a1) // 5, 0))
            pal[:, 7] = np.where(a0 > a1, pal[:, 7], 255)
            pal[:, 6] = np.where(a0 > a1, pal[:, 6], 0)
            px[:, :, 3] = np.take_along_axis(pal, idx, 1).astype(np.uint8)
    img = px.reshape(bh, bw, 4, 4, 4).transpose(0, 2, 1, 3, 4).reshape(bh * 4, bw * 4, 4)
    return img[:h, :w]


def read_paa(data: bytes) -> tuple[np.ndarray, str]:
    """-> (RGBA uint8 HxWx4, формат)."""
    if len(data) < 12:
        raise PaaError("Файл слишком мал")
    tag = struct.unpack_from("<H", data, 0)[0]
    if tag not in TAGS:
        raise PaaError("Неподдерживаемый (палитровый) PAA")
    kind = TAGS[tag]
    pos = 2
    while data[pos:pos + 4] == b"GGAT":
        ln = struct.unpack_from("<I", data, pos + 8)[0]
        pos += 12 + ln
    pos += 2                                  # размер палитры
    w, h = struct.unpack_from("<HH", data, pos)
    size = int.from_bytes(data[pos + 4:pos + 7], "little")
    pos += 7
    lzo = bool(w & 0x8000)
    w &= 0x7FFF
    raw = data[pos:pos + size]
    if kind.startswith("DXT"):
        exp = ((w + 3) // 4) * ((h + 3) // 4) * (8 if kind == "DXT1" else 16)
        if lzo:
            raw = lzo1x_decompress(raw, exp)
        return decode_dxt(raw, w, h, kind), kind
    bpp = {"ARGB4444": 2, "ARGB1555": 2, "ARGB8888": 4, "AI88": 2}[kind]
    if len(raw) < w * h * bpp:
        raise PaaError("Сжатие LZSS для этого формата не поддерживается")
    a = np.frombuffer(raw[:w * h * bpp], np.uint8)
    if kind == "ARGB8888":
        bgra = a.reshape(h, w, 4)
        return bgra[:, :, [2, 1, 0, 3]].copy(), kind
    v = np.frombuffer(raw[:w * h * 2], "<u2").reshape(h, w).astype(np.int32)
    if kind == "AI88":
        g = (v & 255).astype(np.uint8)
        al = (v >> 8).astype(np.uint8)
        return np.stack([g, g, g, al], -1), kind
    if kind == "ARGB4444":
        c = [((v >> s) & 15) * 17 for s in (8, 4, 0, 12)]
    else:
        c = [((v >> s) & 31) * 255 // 31 for s in (10, 5, 0)] + [((v >> 15) & 1) * 255]
    return np.stack(c, -1).astype(np.uint8), kind


def write_paa_dxt1_raw(rgba: np.ndarray) -> bytes:
    """Простейший энкодер (DXT1, без mip, без сжатия) для тестов/превью.
    Для реальных игровых текстур используйте ImageToPAA из DayZ Tools."""
    h, w = rgba.shape[:2]
    bw, bh = (w + 3) // 4, (h + 3) // 4
    out = bytearray()
    for by in range(bh):
        for bx in range(bw):
            blk = rgba[by * 4:by * 4 + 4, bx * 4:bx * 4 + 4, :3].reshape(-1, 3).astype(int)
            mean = blk.mean(0).astype(int)
            def p565(c): return ((c[0] * 31 // 255) << 11) | ((c[1] * 63 // 255) << 5) | (c[2] * 31 // 255)
            c0 = p565(mean); c1 = max(0, c0 - 1) if c0 > 0 else 0
            c0, c1 = max(c0, c1 + 1) if c0 == c1 else c0, c1
            out += struct.pack("<HHI", c0, c1, 0)
    hdr = struct.pack("<H", 0xFF01) + b"GGATGALF" + struct.pack("<II", 4, 0) + struct.pack("<H", 0)
    hdr += struct.pack("<HH", w, h) + len(out).to_bytes(3, "little")
    return hdr + bytes(out)
