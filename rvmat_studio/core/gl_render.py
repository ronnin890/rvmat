"""OpenGL-рендер предпросмотра (GLSL 3.30 core), повторяющий модель освещения программного рендерера.

Те же оговорки, что и у render.py: это приближение шейдера DayZ "Super", не движок Enfusion.
Только Qt (PySide6.QtOpenGL) — без PyOpenGL. Все функции требуют текущего GL-контекста.
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np
import shiboken6
from PySide6.QtCore import QSize
from PySide6.QtGui import (QOffscreenSurface, QOpenGLContext, QSurfaceFormat, QVector2D, QVector3D, QVector4D)
from PySide6.QtOpenGL import (QOpenGLBuffer, QOpenGLFramebufferObject, QOpenGLFramebufferObjectFormat,
                              QOpenGLShader, QOpenGLShaderProgram, QOpenGLTexture, QOpenGLVertexArrayObject)

from ..backend import Material
from ..backend.procedural import is_procedural, parse_procedural
from .meshes import Mesh, get_shape
from .render import LIGHTING, RenderOptions, _rot, _uv_matrix

_NULL = shiboken6.VoidPtr(0)
GL_FLOAT, GL_UNSIGNED_INT, GL_TRIANGLES, GL_LINES = 0x1406, 0x1405, 0x0004, 0x0001
GL_DEPTH_TEST, GL_BLEND, GL_COLOR_BUFFER_BIT, GL_DEPTH_BUFFER_BIT = 0x0B71, 0x0BE2, 0x4000, 0x0100
GL_LEQUAL, GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA = 0x0203, 0x0302, 0x0303

VERT = """#version 330 core
layout(location=0) in vec3 aPos; layout(location=1) in vec3 aN; layout(location=2) in vec3 aT; layout(location=3) in vec2 aUV;
uniform vec3 rot0, rot1, rot2;       // строки матрицы поворота модели
uniform float dist, focal, aspect; uniform vec2 pan;
out vec3 vPos; out vec3 vN; out vec3 vT; out vec2 vUV;
vec3 rot(vec3 v){ return vec3(dot(rot0,v), dot(rot1,v), dot(rot2,v)); }
void main(){
    vec3 P = rot(aPos);
    float w = dist - P.z;
    vPos = P; vN = rot(aN); vT = rot(aT); vUV = aUV;
    const float n = 0.1, f = 40.0;
    float zc = (f + n) / (f - n) * w - 2.0 * f * n / (f - n);
    gl_Position = vec4((P.x - pan.x) * focal / aspect, (P.y - pan.y) * focal, zc, w);
}
"""

FRAG = """#version 330 core
in vec3 vPos; in vec3 vN; in vec3 vT; in vec2 vUV;
out vec4 frag;
uniform sampler2D tNormal, tDetail, tAs, tSmdi, tHdri, tBase;
uniform int hasN, hasD, hasA, hasS, hasH, hasB, detailMode;
uniform vec4 mN, mD, mA, mS; uniform vec2 oN, oD, oA, oS;     // uv' = [m.x m.y; m.z m.w]*uv + off
uniform float tiling, dist, power, ambK, sunI, fresN, fresK, lightOn, nStr, envGain;
uniform vec3 amb, dif, forced, emi, spc, detailConst, sunDir, sunCol, skyTop, skyHor, skyGnd;
uniform int view; uniform float hl;
vec2 tx(vec4 m, vec2 o, vec2 uv){ return vec2(m.x*uv.x + m.y*uv.y, m.z*uv.x + m.w*uv.y) + o; }
vec3 envColor(vec3 d){
    vec3 c;
    if (hasH == 1) {
        vec2 uv = vec2(atan(d.x, -d.z) / 6.2831853 + 0.5, acos(clamp(d.y, -1.0, 1.0)) / 3.14159265);
        return pow(texture(tHdri, uv).rgb, vec3(2.2));
    }
    float y = d.y;
    vec3 sky = skyHor + (skyTop - skyHor) * pow(clamp(y, 0.0, 1.0), 0.6);
    vec3 g = skyHor + (skyGnd - skyHor) * clamp(-y * 3.0, 0.0, 1.0);
    c = y >= 0.0 ? sky : g;
    return c + sunCol * (pow(clamp(dot(d, sunDir), 0.0, 1.0), 200.0) * 6.0 * sunI);
}
void main(){
    vec3 N = normalize(vN);
    vec3 V = normalize(vec3(0.0, 0.0, dist) - vPos);
    if (dot(N, V) < 0.0) N = -N;
    vec3 T = normalize(vT - N * dot(vT, N));
    vec3 B = cross(N, T);
    vec2 uv = vUV * tiling;
    vec3 albedo = vec3(0.45);
    if (hasB == 1) albedo = pow(texture(tBase, uv).rgb, vec3(2.2));      // _co с текстуры грани модели
    float ao = 1.0;
    if (hasA == 1) ao = clamp(texture(tAs, tx(mA, oA, uv)).g, 0.0, 1.0);   // _as: R/B = 1, затенение в G
    if (view == 8) {
        vec2 c = tx(mN, oN, uv);
        float ck = mod(floor(c.x * 8.0) + floor(c.y * 8.0), 2.0);
        float g = 0.25 + 0.55 * ck;
        albedo = pow(clamp(vec3(g + 0.25 * fract(c.x), g, g + 0.25 * fract(c.y)), 0.0, 1.0), vec3(2.2));
    }
    vec3 detail = vec3(1.0);
    if (detailMode == 1) detail = detailConst * 2.0;
    else if (detailMode == 2) detail = texture(tDetail, tx(mD, oD, uv)).rgb * 2.0;
    vec3 Nf = N;
    if (hasN == 1) {
        vec4 nm = texture(tNormal, tx(mN, oN, uv));
        float nx = nm.r * 2.0 - 1.0, ny = nm.g * 2.0 - 1.0;
        float r2 = nx*nx + ny*ny;
        if (r2 > 1.0) { float k = inversesqrt(r2); nx *= k; ny *= k; }
        float nz = max(sqrt(clamp(1.0 - nx*nx - ny*ny, 0.0, 1.0)), 0.05);
        nx *= nStr; ny *= nStr;
        Nf = normalize(T * nx + B * ny + N * nz);
    }
    float specI = 1.0, metal = 0.0, gloss = 1.0, glow = 0.0, smdiR = 1.0;   // _smdi: R=1, G=specular, B=specular power
    if (hasS == 1) { vec4 s = texture(tSmdi, tx(mS, oS, uv)); smdiR = s.r; specI = s.g; gloss = s.b; }
    vec3 L = normalize(sunDir);
    float lam = clamp(dot(Nf, L), 0.0, 1.0);
    vec3 sunC = sunCol * sunI;
    vec3 H = normalize(L + V);
    float nh = clamp(dot(Nf, H), 0.0, 1.0);
    float spExp = max(power * gloss, 1.0);
    float ndv = clamp(dot(Nf, V), 0.0, 1.0);
    float f0 = ((fresN - 1.0) * (fresN - 1.0) + fresK * fresK) / ((fresN + 1.0) * (fresN + 1.0) + fresK * fresK);
    float fres = f0 + (1.0 - f0) * pow(1.0 - ndv, 5.0);
    vec3 R = 2.0 * dot(Nf, V) * Nf - V;
    vec3 envc = envColor(R), ambEnv = envColor(Nf);
    float spcMean = (spc.r + spc.g + spc.b) / 3.0;
    float reflAmt = clamp(fres * specI * spcMean, 0.0, 1.0);
    vec3 kd = albedo * detail * (1.0 - metal) * (1.0 - 0.7 * reflAmt);
    vec3 diffuseT = kd * (amb * ambEnv * ambK * 2.0 * ao + dif * lam * sunC + forced);
    vec3 specCol = spc * (1.0 - metal) + albedo * metal;
    vec3 specT = specCol * pow(nh, spExp) * lam * sunC * specI * (spExp + 8.0) / 25.0;
    float wEnv = 0.3 + 0.7 * gloss;
    vec3 envEff = envc * wEnv + ambEnv * (1.0 - wEnv);
    vec3 reflT = ao * envEff * specCol * fres * (specI * (0.25 + 0.75 * gloss)) * envGain;
    vec3 emis = emi + albedo * glow;
    vec3 lin = diffuseT + specT + reflT + emis;
    if (view == 1 || view == 8) lin = albedo;
    else if (view == 2) lin = pow(Nf * 0.5 + 0.5, vec3(2.2));
    else if (view == 3) lin = vec3(specI);
    else if (view == 4) lin = vec3(smdiR);
    else if (view == 5) lin = vec3(gloss);
    else if (view == 6) lin = emis;
    else if (view == 7) lin = detail * 0.5;
    else if (view == 9) lin = vec3(ao);
    vec3 outc = pow(clamp(lin, 0.0, 1.0), vec3(1.0 / 2.2));
    outc = mix(outc, vec3(1.0, 0.55, 0.1), 0.4 * hl);
    frag = vec4(outc, 1.0);
}
"""

BG_VERT = """#version 330 core
out vec2 vy;
void main(){ vec2 p = vec2((gl_VertexID << 1) & 2, gl_VertexID & 2); vy = p; gl_Position = vec4(p * 2.0 - 1.0, 0.999999, 1.0); }
"""
BG_FRAG = """#version 330 core
in vec2 vy; out vec4 frag;
uniform vec3 top, hor; uniform int grid;
void main(){
    vec3 c = mix(top * 0.5, hor * 0.4, 1.0 - vy.y);
    c = pow(c, vec3(1.0 / 1.6));
    if (grid == 1 && (mod(gl_FragCoord.x, 40.0) < 1.0 || mod(gl_FragCoord.y, 40.0) < 1.0)) c += 0.1;
    frag = vec4(c, 1.0);
}
"""
LINE_FRAG = """#version 330 core
out vec4 frag;
void main(){ frag = vec4(1.0, 1.0, 1.0, 0.28); }
"""
LINE_VERT = VERT.replace("gl_Position = vec4(", "gl_Position = vec4(").replace("const float n = 0.1", "const float n = 0.1")


def _set(prog: QOpenGLShaderProgram, name: str, value):
    loc = prog.uniformLocation(name)
    if loc < 0:
        return                      # uniform убран компилятором как неиспользуемый
    if isinstance(value, bool):
        value = int(value)
    if isinstance(value, float):
        # setUniformValue(loc, float) в PySide6 уходит в int-перегрузку (INVALID_OPERATION) — берём прямой вызов
        QOpenGLContext.currentContext().functions().glUniform1f(loc, value)
        return
    prog.setUniformValue(loc, value)


import os
_DEBUG = bool(os.environ.get("RVMAT_GL_DEBUG"))


def _chk(f, tag):
    if _DEBUG:
        e = f.glGetError()
        if e:
            print(f"[GL] {tag}: error 0x{e:x}")


def _u8(arr: np.ndarray) -> bytes:
    return np.ascontiguousarray((np.clip(arr, 0, 1) * 255 + 0.5).astype(np.uint8)).tobytes()


class GLScene:
    """Ресурсы GL (шейдеры, буферы, текстуры). Создавать и использовать при текущем контексте."""

    def __init__(self, ctx: QOpenGLContext):
        self.ctx = ctx
        self.f = ctx.functions()
        self.prog = self._program(VERT, FRAG)
        self.bg = self._program(BG_VERT, BG_FRAG)
        self.line = self._program(VERT, LINE_FRAG)
        self.vao_bg = QOpenGLVertexArrayObject(); self.vao_bg.create()
        self.mesh_key = None
        self.vao = self.vbo = self.ibo = self.ebo = self.vao_line = None
        self.n_idx = self.n_edge = 0
        self.ranges = []
        self.tex: dict = {}          # id(array) -> (QOpenGLTexture, array)
        self.dummy = self._texture(np.zeros((1, 1, 4), np.float32))

    def _program(self, v, f) -> QOpenGLShaderProgram:
        p = QOpenGLShaderProgram()
        if not p.addShaderFromSourceCode(QOpenGLShader.Vertex, v):
            raise RuntimeError("Vertex shader: " + p.log())
        if not p.addShaderFromSourceCode(QOpenGLShader.Fragment, f):
            raise RuntimeError("Fragment shader: " + p.log())
        if not p.link():
            raise RuntimeError("Link: " + p.log())
        return p

    def _texture(self, arr: np.ndarray, srgb_store=False) -> QOpenGLTexture:
        h, w = arr.shape[:2]
        t = QOpenGLTexture(QOpenGLTexture.Target2D)
        t.setFormat(QOpenGLTexture.RGBA8_UNorm)
        t.setSize(w, h)
        t.setMipLevels(max(1, int(math.log2(max(w, h))) + 1))
        t.allocateStorage()
        t.setData(0, 0, QOpenGLTexture.RGBA, QOpenGLTexture.UInt8, _u8(arr))   # строка 0 = t=0, как в CPU-рендере
        t.generateMipMaps()
        t.setMinMagFilters(QOpenGLTexture.LinearMipMapLinear, QOpenGLTexture.Linear)
        t.setWrapMode(QOpenGLTexture.Repeat)
        try:
            t.setMaximumAnisotropy(8.0)
        except Exception:
            pass
        return t

    def texture(self, arr: np.ndarray) -> QOpenGLTexture:
        k = id(arr)
        if k not in self.tex:
            self.tex[k] = (self._texture(arr), arr)
        return self.tex[k][0]

    def trim_textures(self, limit: int = 64):
        """Сбросить кэш текстур. Вызывается ТОЛЬКО в начале кадра: сброс посреди отрисовки
        удалил бы уже привязанные текстуры."""
        if len(self.tex) > limit:
            for t, _ in self.tex.values():
                t.destroy()
            self.tex.clear()

    def set_mesh(self, mesh: Mesh):
        if self.mesh_key == id(mesh):
            return
        self.mesh_key = id(mesh)
        for b in (self.vbo, self.ibo, self.ebo):
            if b is not None:
                b.destroy()
        for a in (self.vao, self.vao_line):
            if a is not None:
                a.destroy()
        data = np.ascontiguousarray(np.hstack([mesh.v, mesh.n, mesh.t, mesh.uv]).astype(np.float32))
        idx = np.ascontiguousarray(mesh.f.astype(np.uint32))
        e = np.sort(np.vstack([mesh.f[:, [0, 1]], mesh.f[:, [1, 2]], mesh.f[:, [2, 0]]]), axis=1)
        edges = np.ascontiguousarray(np.unique(e, axis=0).astype(np.uint32))
        self.vbo = QOpenGLBuffer(QOpenGLBuffer.VertexBuffer); self.vbo.create(); self.vbo.bind()
        self.vbo.allocate(data.tobytes(), data.nbytes)
        self.ibo = QOpenGLBuffer(QOpenGLBuffer.IndexBuffer); self.ibo.create()
        self.ebo = QOpenGLBuffer(QOpenGLBuffer.IndexBuffer); self.ebo.create()
        stride = 11 * 4
        self.vao = QOpenGLVertexArrayObject(); self.vao.create(); self.vao.bind()
        self.vbo.bind(); self.ibo.bind(); self.ibo.allocate(idx.tobytes(), idx.nbytes)
        for loc, off, n in ((0, 0, 3), (1, 12, 3), (2, 24, 3), (3, 36, 2)):
            self.prog.enableAttributeArray(loc)
            self.prog.setAttributeBuffer(loc, GL_FLOAT, off, n, stride)
        self.vao.release()
        self.vao_line = QOpenGLVertexArrayObject(); self.vao_line.create(); self.vao_line.bind()
        self.vbo.bind(); self.ebo.bind(); self.ebo.allocate(edges.tobytes(), edges.nbytes)
        self.line.enableAttributeArray(0)
        self.line.setAttributeBuffer(0, GL_FLOAT, 0, 3, stride)
        self.vao_line.release()
        self.n_idx, self.n_edge = idx.size, edges.size
        self.ranges = mesh.part_ranges() if mesh.face_part is not None else []
        _chk(self.f, 'set_mesh')

    # ------------------------------------------------------------------ рисование
    def draw(self, mat: Material, o: RenderOptions, resolve, mesh: Mesh, vp, hdri=None, part_mats=None):
        hdri = hdri if hdri is not None else getattr(o, "hdri", None)
        """Рисует в текущий framebuffer, во viewport vp=(x,y,w,h)."""
        f = self.f
        x, y, w, h = vp
        f.glViewport(x, y, w, h)
        self.trim_textures()
        self.set_mesh(mesh)
        L = LIGHTING.get(o.lighting, LIGHTING["Студия"])
        f.glDisable(GL_DEPTH_TEST)
        self.bg.bind()
        _set(self.bg, "top", QVector3D(*L["top"])); _set(self.bg, "hor", QVector3D(*L["hor"]))
        _set(self.bg, "grid", 1 if o.grid else 0)
        self.vao_bg.bind(); f.glDrawArrays(GL_TRIANGLES, 0, 3); self.vao_bg.release()
        self.bg.release()
        f.glEnable(GL_DEPTH_TEST); f.glDepthFunc(GL_LEQUAL)
        f.glClear(GL_DEPTH_BUFFER_BIT)

        R = _rot(o.yaw, o.pitch)
        cam = dict(dist=4.0 / max(o.zoom, 0.05), focal=1 / math.tan(math.radians(17.5)), aspect=w / max(h, 1))

        def cam_uniforms(p):
            for i in range(3):
                _set(p, f"rot{i}", QVector3D(*[float(v) for v in R[i]]))
            _set(p, "dist", float(cam["dist"])); _set(p, "focal", float(cam["focal"]))
            _set(p, "aspect", float(cam["aspect"])); _set(p, "pan", QVector2D(float(o.pan[0]), float(o.pan[1])))

        p = self.prog
        p.bind()
        cam_uniforms(p)
        def apply(mat, base, hl, nstr):
            unit = 0

            def bind_stage(idx, sampler, flag, mname, oname):
                nonlocal unit
                s = mat.stage(idx)
                arr = None
                if s is not None and o.stages_on.get(idx, True) and s.texture:
                    arr = resolve(s.texture)
                M, off = _uv_matrix(s)
                _set(p, mname, QVector4D(float(M[0, 0]), float(M[0, 1]), float(M[1, 0]), float(M[1, 1])))
                _set(p, oname, QVector2D(float(off[0]), float(off[1])))
                t = self.texture(arr) if arr is not None else self.dummy
                t.bind(unit)
                _set(p, sampler, unit)
                unit += 1
                _set(p, flag, 1 if arr is not None else 0)
                return arr is not None

            bind_stage(1, "tNormal", "hasN", "mN", "oN")
            bind_stage(2, "tDetail", "hasD", "mD", "oD")
            bind_stage(4, "tAs", "hasA", "mA", "oA")
            bind_stage(5, "tSmdi", "hasS", "mS", "oS")
            # HDRI
            if hdri is not None:
                ht = self.texture(np.clip(hdri, 0, 1) ** (1 / 2.2))
                ht.bind(unit); _set(p, "tHdri", unit); _set(p, "hasH", 1)
            else:
                self.dummy.bind(unit); _set(p, "tHdri", unit); _set(p, "hasH", 0)
            bt = self.texture(base) if base is not None else self.dummy
            bt.bind(unit + 1); _set(p, "tBase", unit + 1); _set(p, "hasB", 1 if base is not None else 0)
            # detail: процедурный цвет или текстура
            s2 = mat.stage(2)
            dmode, dconst = 0, (1.0, 1.0, 1.0)
            if s2 and o.stages_on.get(2, True) and s2.texture:
                if is_procedural(s2.texture):
                    pr = parse_procedural(s2.texture)
                    if pr.rgba:
                        dmode, dconst = 1, pr.rgba[:3]
                elif p is not None and s2.texture and resolve(s2.texture) is not None:
                    dmode = 2
            _set(p, "detailMode", dmode)
            _set(p, "detailConst", QVector3D(*[float(c) for c in dconst]))
            col = lambda name, d: [float(v) for v in (mat.get(name) or d)[:4]]
            for name, d in (("ambient", [1] * 4), ("diffuse", [1] * 4), ("forcedDiffuse", [0, 0, 0, 1]), ("emmisive", [0] * 4), ("specular", [0] * 4)):
                key = {"ambient": "amb", "diffuse": "dif", "forcedDiffuse": "forced", "emmisive": "emi", "specular": "spc"}[name]
                _set(p, key, QVector3D(*col(name, d)[:3]))
            _set(p, "power", float(mat.get("specularPower") or 10))
            fn, fk = 1.5, 0.0
            s6 = mat.stage(6)
            if s6 and o.stages_on.get(6, True) and s6.texture and is_procedural(s6.texture):
                pr = parse_procedural(s6.texture)
                if pr.fresnel:
                    fn, fk = pr.fresnel
            _set(p, "fresN", float(fn)); _set(p, "fresK", float(fk))
            ang = math.radians(o.light_rot)
            sv = np.array(L["sun"], np.float32)
            sv = np.array([sv[0] * math.cos(ang) + sv[2] * math.sin(ang), sv[1], -sv[0] * math.sin(ang) + sv[2] * math.cos(ang)])
            sv /= np.linalg.norm(sv)
            _set(p, "sunDir", QVector3D(*[float(v) for v in sv]))
            _set(p, "sunCol", QVector3D(*[float(v) for v in L["col"]]))
            _set(p, "sunI", float(L["i"])); _set(p, "ambK", float(L["amb"]))
            for k in ("top", "hor", "gnd"):
                _set(p, {"top": "skyTop", "hor": "skyHor", "gnd": "skyGnd"}[k], QVector3D(*[float(v) for v in L[k]]))
            _set(p, "tiling", float(o.tiling))
            views = {"final": 0, "albedo": 1, "normal": 2, "specular": 3, "metal": 4, "gloss": 5, "emissive": 6, "detail": 7, "checker": 8, "ao": 9}
            _set(p, "view", views.get(o.view, 0))
            _set(p, "hl", float(hl))
            _set(p, "nStr", float(nstr))
            _set(p, "envGain", float(o.env_gain))

        default_base = getattr(o, "base_color", None)
        self.vao.bind()
        _chk(f, 'before draw')
        if part_mats and self.ranges:
            for pid, (cnt_start, cnt) in enumerate(self.ranges):
                if not cnt:
                    continue
                m_, b_, *ex = part_mats.get(pid, (mat, default_base))
                apply(m_, b_, 1.0 if pid == o.highlight else 0.0, ex[0] if ex else o.normal_strength)
                f.glDrawElements(GL_TRIANGLES, cnt * 3, GL_UNSIGNED_INT, shiboken6.VoidPtr(cnt_start * 12))
        else:
            apply(mat, default_base, 0.0, o.normal_strength)
            f.glDrawElements(GL_TRIANGLES, self.n_idx, GL_UNSIGNED_INT, _NULL)
        _chk(f, 'draw mesh')
        self.vao.release()
        p.release()
        if o.wire:
            f.glEnable(GL_BLEND); f.glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
            self.line.bind(); cam_uniforms(self.line)
            self.vao_line.bind(); f.glDrawElements(GL_LINES, self.n_edge, GL_UNSIGNED_INT, _NULL); self.vao_line.release()
            self.line.release(); f.glDisable(GL_BLEND)

    def destroy(self):
        for t, _ in self.tex.values():
            t.destroy()
        self.dummy.destroy()
        for b in (self.vbo, self.ibo, self.ebo):
            if b is not None:
                b.destroy()


def surface_format() -> QSurfaceFormat:
    f = QSurfaceFormat()
    f.setVersion(3, 3)
    f.setProfile(QSurfaceFormat.CoreProfile)
    f.setDepthBufferSize(24)
    f.setSamples(4)
    return f


class GLOffscreen:
    """Офлайн-рендер в FBO (тесты, снимки, проверка доступности GL)."""

    def __init__(self):
        self.ctx = QOpenGLContext()
        fmt = QSurfaceFormat(); fmt.setVersion(3, 3); fmt.setProfile(QSurfaceFormat.CoreProfile)
        self.ctx.setFormat(fmt)
        if not self.ctx.create():
            raise RuntimeError("OpenGL-контекст недоступен")
        self.surface = QOffscreenSurface()
        self.surface.setFormat(self.ctx.format())
        self.surface.create()
        if not self.ctx.makeCurrent(self.surface):
            raise RuntimeError("Не удалось активировать OpenGL-контекст")
        self.scene = GLScene(self.ctx)
        self.info = (self.ctx.functions().glGetString(0x1F01) or b"", self.ctx.functions().glGetString(0x1F02) or b"")

    def render(self, mat, o: RenderOptions, resolve, mesh=None, hdri=None, samples=4, parts=None) -> np.ndarray:
        self.ctx.makeCurrent(self.surface)
        mesh = mesh or get_shape(o.shape)
        fmt = QOpenGLFramebufferObjectFormat()
        fmt.setAttachment(QOpenGLFramebufferObject.CombinedDepthStencil)
        fmt.setSamples(samples)
        fbo = QOpenGLFramebufferObject(QSize(o.width, o.height), fmt)
        fbo.bind()
        self.scene.draw(mat, o, resolve, mesh, (0, 0, o.width, o.height), hdri, parts)
        fbo.release()
        img = fbo.toImage().convertToFormat(fbo.toImage().Format.Format_RGBA8888)
        ptr = img.constBits()
        arr = np.frombuffer(ptr, np.uint8).reshape(img.height(), img.bytesPerLine())[:, :img.width() * 4]
        return arr.reshape(img.height(), img.width(), 4).copy()

    def close(self):
        """Освободить GL-ресурсы сразу (не оставлять контекст сборщику мусора до выхода из QApplication)."""
        if self.ctx is None:
            return
        self.ctx.makeCurrent(self.surface)
        self.scene.destroy()
        self.ctx.doneCurrent()
        self.surface.destroy()
        self.scene = self.surface = self.ctx = None


_avail = None


def gl_available() -> bool:
    """Проверка: создаётся ли контекст 3.3 core и компилируются ли шейдеры (один раз за процесс)."""
    global _avail
    if _avail is None:
        try:
            g = GLOffscreen()
            g.close()
            _avail = True
        except Exception:
            _avail = False
    return _avail
