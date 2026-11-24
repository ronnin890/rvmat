"""GPU-рендер предпросмотра (OpenGL 3.3 через moderngl, офлайн-контекст; кадр читается в numpy и показывается в окне программы).

Модель света — та же, что у процессорного рендера (core/render.py): воссоздание шейдера Super, не движок Enfusion.
Текстуры грузятся в полном разрешении (с мипмапами и анизотропией), тангенты считаются в шейдере по производным UV,
поэтому тяжёлые модели и текстуры 4K/8K не требуют процессорной обработки.
Контекст привязан к потоку: пользуйтесь им из GLThread.
"""
from __future__ import annotations

import math
import queue
import atexit
import threading
import weakref
from pathlib import Path
from typing import Callable, Optional

import numpy as np

try:
    import moderngl
except ImportError:                                           # без moderngl работает процессорный рендер
    moderngl = None

from ..backend.procedural import is_procedural, parse_procedural
from .logs import log_exc
from .render import LIGHTING, POWER_GLOSS_EXP, POWER_MAX, RenderOptions, _rot, _uv_matrix
from .textures import load_rgba

VERT = """#version 330 core
in vec3 aPos; in vec3 aN; in vec2 aUV;
uniform vec3 rot0, rot1, rot2; uniform float dist, focal, aspect; uniform vec2 pan;
out vec3 vPos; out vec3 vN; out vec2 vUV;
vec3 rot(vec3 v){ return vec3(dot(rot0,v), dot(rot1,v), dot(rot2,v)); }
void main(){
    vec3 P = rot(aPos); float w = dist - P.z;
    vPos = P; vN = rot(aN); vUV = aUV;
    const float n = 0.1, f = 40.0;
    float zc = (f + n) / (f - n) * w - 2.0 * f * n / (f - n);
    gl_Position = vec4((P.x - pan.x) * focal / aspect, (P.y - pan.y) * focal, zc, w);
}
"""

FRAG = """#version 330 core
in vec3 vPos; in vec3 vN; in vec2 vUV; out vec4 frag;
uniform sampler2D tNormal, tDetail, tAs, tSmdi, tHdri, tBase;
uniform int hasN, hasD, hasA, hasS, hasH, hasB, detailMode, view;
uniform vec4 mN, mD, mA, mS; uniform vec2 oN, oD, oA, oS;
uniform float tiling, dist, power, ambK, sunI, fresN, fresK, nStr, envGain, hl;
uniform vec3 amb, dif, forced, emi, spc, detailConst, sunDir, sunCol, skyTop, skyHor, skyGnd;
vec2 tx(vec4 m, vec2 o, vec2 uv){ return vec2(m.x*uv.x + m.y*uv.y, m.z*uv.x + m.w*uv.y) + o; }
vec3 envColor(vec3 d){
    if (hasH == 1) { vec2 uv = vec2(atan(d.x, -d.z) / 6.2831853 + 0.5, acos(clamp(d.y, -1.0, 1.0)) / 3.14159265); return pow(texture(tHdri, uv).rgb, vec3(2.2)); }
    float y = d.y; vec3 sky = skyHor + (skyTop - skyHor) * pow(clamp(y, 0.0, 1.0), 0.6);
    vec3 g = skyHor + (skyGnd - skyHor) * clamp(-y * 3.0, 0.0, 1.0); vec3 c = y >= 0.0 ? sky : g;
    return c + sunCol * (pow(clamp(dot(d, sunDir), 0.0, 1.0), 200.0) * 6.0 * sunI);
}
void main(){
    vec3 N = normalize(vN); vec3 V = normalize(vec3(0.0, 0.0, dist) - vPos);
    if (dot(N, V) < 0.0) N = -N;
    vec2 uv = vUV * tiling;
    // касательный базис по производным UV (тангенты в вершинах не нужны)
    vec3 dp1 = dFdx(vPos), dp2 = dFdy(vPos); vec2 du1 = dFdx(uv), du2 = dFdy(uv);
    vec3 dp2p = cross(dp2, N), dp1p = cross(N, dp1);
    vec3 T = dp2p * du1.x + dp1p * du2.x;
    T = T - N * dot(T, N); float tl = length(T); T = tl > 1e-9 ? T / tl : normalize(cross(N, vec3(0.0, 1.0, 0.0)) + vec3(1e-6)); vec3 B = cross(N, T);
    vec3 albedo = vec3(0.45);
    if (hasB == 1) albedo = pow(texture(tBase, uv).rgb, vec3(2.2));
    float ao = 1.0;
    if (hasA == 1) ao = clamp(texture(tAs, tx(mA, oA, uv)).g, 0.0, 1.0);
    if (view == 8) { vec2 c = tx(mN, oN, uv); float ck = mod(floor(c.x * 8.0) + floor(c.y * 8.0), 2.0); float g = 0.25 + 0.55 * ck;
        albedo = pow(clamp(vec3(g + 0.25 * fract(c.x), g, g + 0.25 * fract(c.y)), 0.0, 1.0), vec3(2.2)); }
    vec3 detail = vec3(1.0);
    if (detailMode == 1) detail = detailConst * 2.0; else if (detailMode == 2) detail = texture(tDetail, tx(mD, oD, uv)).rgb * 2.0;
    vec3 Nf = N;
    if (hasN == 1) {
        vec4 nm = texture(tNormal, tx(mN, oN, uv));
        float nx = nm.r * 2.0 - 1.0, ny = nm.g * 2.0 - 1.0; float r2 = nx*nx + ny*ny;
        if (r2 > 1.0) { float k = inversesqrt(r2); nx *= k; ny *= k; }
        float nz = max(sqrt(clamp(1.0 - nx*nx - ny*ny, 0.0, 1.0)), 0.05); nx *= nStr; ny *= nStr;
        Nf = normalize(T * nx + B * ny + N * nz);
    }
    float specI = 1.0, gloss = 1.0, smdiR = 1.0;
    if (hasS == 1) { vec4 s = texture(tSmdi, tx(mS, oS, uv)); smdiR = s.r; specI = s.g; gloss = s.b; }
    vec3 L = normalize(sunDir); float lam = clamp(dot(Nf, L), 0.0, 1.0); vec3 sunC = sunCol * sunI;
    vec3 H = normalize(L + V); float nh = clamp(dot(Nf, H), 0.0, 1.0);
    float spExp = max(power * gloss, 1.0); float ndv = clamp(dot(Nf, V), 0.0, 1.0);
    float f0 = ((fresN - 1.0) * (fresN - 1.0) + fresK * fresK) / ((fresN + 1.0) * (fresN + 1.0) + fresK * fresK);
    float fres = f0 + (1.0 - f0) * pow(1.0 - ndv, 5.0);
    vec3 R = 2.0 * dot(Nf, V) * Nf - V; vec3 envc = envColor(R), ambEnv = envColor(Nf);
    float ks = clamp(fres * specI, 0.0, 1.0); float scale = min(0.10986 * power + 0.0254, 24.05); float terminator = clamp(lam * 5.0, 0.0, 1.0);
    vec3 direct = (lam * dif * (1.0 - ks) * sunC + forced) * ao;
    vec3 ambient = amb * ambEnv * ambK * 2.0 * (0.5 * ao + 0.5);
    vec3 diffuseT = albedo * detail * (direct + ambient);
    vec3 specT = spc * pow(nh, spExp) * (terminator * ks * scale) * sunC;
    float pg = pow(clamp(power / @POWER_MAX@, 0.0, 1.0), @POWER_GLOSS_EXP@);
    float wEnv = 0.3 + 0.7 * gloss * sqrt(pg); vec3 envEff = envc * wEnv + ambEnv * (1.0 - wEnv);
    vec3 reflT = (0.5 * ao + 0.5) * envEff * spc * ks * envGain * pg;
    vec3 lin = diffuseT + specT + reflT + emi;
    if (view == 1 || view == 8) lin = albedo; else if (view == 2) lin = pow(Nf * 0.5 + 0.5, vec3(2.2));
    else if (view == 3) lin = vec3(specI); else if (view == 4) lin = vec3(smdiR); else if (view == 5) lin = vec3(gloss);
    else if (view == 6) lin = emi; else if (view == 7) lin = detail * 0.5; else if (view == 9) lin = vec3(ao);
    vec3 outc = pow(clamp(lin, 0.0, 1.0), vec3(1.0 / 2.2)); outc = mix(outc, vec3(1.0, 0.55, 0.1), 0.4 * hl);
    frag = vec4(outc, 1.0);
}
"""
BG_V = """#version 330 core
out vec2 vy; void main(){ vec2 p = vec2(float((gl_VertexID << 1) & 2), float(gl_VertexID & 2)); vy = p; gl_Position = vec4(p * 2.0 - 1.0, 0.999999, 1.0); }
"""
BG_F = """#version 330 core
in vec2 vy; out vec4 frag; uniform vec3 top, hor; uniform int grid;
void main(){ vec3 c = mix(top * 0.5, hor * 0.4, 1.0 - vy.y); c = pow(c, vec3(1.0 / 1.6));
    if (grid == 1 && (mod(gl_FragCoord.x, 40.0) < 1.0 || mod(gl_FragCoord.y, 40.0) < 1.0)) c += 0.1; frag = vec4(c, 1.0); }
"""
LINE_F = """#version 330 core
out vec4 frag; void main(){ frag = vec4(1.0, 1.0, 1.0, 0.28); }
"""
VIEWS = {"final": 0, "albedo": 1, "normal": 2, "specular": 3, "metal": 4, "gloss": 5, "emissive": 6, "detail": 7, "checker": 8, "ao": 9}
MAX_TEX = 8192                                                # больше — уменьшаем при загрузке
KEEP_TEXTURES = 28


class GLUnavailable(RuntimeError):
    pass


def available() -> bool:
    return moderngl is not None


class GLRenderer:
    """Должен создаваться и использоваться в одном потоке (см. GLThread)."""

    def __init__(self, samples: int = 4):
        if moderngl is None:
            raise GLUnavailable("moderngl не установлен")
        self.ctx, err = None, ""
        import sys
        backends = (["egl", None] if sys.platform.startswith("linux") else [None])      # EGL не трогает дисплей окна программы
        for be in backends:
            try:
                self.ctx = (moderngl.create_standalone_context(require=330, backend=be) if be else moderngl.create_standalone_context(require=330))
                break
            except Exception as e:
                log_exc("glrender.__init__")
                err = str(e)
        if self.ctx is None:
            raise GLUnavailable(f"не удалось создать контекст OpenGL 3.3: {err}")
        ctx = self.ctx
        self.info = f"{ctx.info.get('GL_RENDERER', '?')} · OpenGL {ctx.info.get('GL_VERSION', '?').split()[0]}"
        self.samples = max(0, min(samples, int(ctx.info.get("GL_MAX_SAMPLES", 4) or 0)))
        self.max_tex = min(int(ctx.info.get("GL_MAX_TEXTURE_SIZE", 4096)), MAX_TEX)
        self.prog = ctx.program(vertex_shader=VERT, fragment_shader=FRAG.replace("@POWER_MAX@", f"{POWER_MAX:.1f}").replace("@POWER_GLOSS_EXP@", f"{POWER_GLOSS_EXP:.3f}"))
        self.bg = ctx.program(vertex_shader=BG_V, fragment_shader=BG_F)
        self.line = ctx.program(vertex_shader=VERT, fragment_shader=LINE_F)
        self.vao_bg = ctx.vertex_array(self.bg, [])
        self.dummy = ctx.texture((1, 1), 4, b"\x00\x00\x00\x00")
        self.tex: dict = {}                    # ключ -> [texture, keepalive, номер кадра последнего использования]
        self.frame = 0
        self.mesh_key = None
        self.fbos: Optional[tuple] = None

    # ---- ресурсы
    def _upload(self, rgba: np.ndarray):
        h, w = rgba.shape[:2]
        if max(w, h) > self.max_tex:                                         # больше лимита GPU — уменьшаем
            from PIL import Image
            s = self.max_tex / max(w, h)
            rgba = np.asarray(Image.fromarray(rgba).resize((max(1, int(w * s)), max(1, int(h * s))), Image.LANCZOS))
            h, w = rgba.shape[:2]
        t = self.ctx.texture((w, h), 4, np.ascontiguousarray(rgba).tobytes())
        t.build_mipmaps()
        t.filter = (moderngl.LINEAR_MIPMAP_LINEAR, moderngl.LINEAR)
        try:
            t.anisotropy = 8.0
        except Exception:
            log_exc("glrender._upload")
            pass
        return t

    def texture_for(self, src):
        """src: путь к файлу (кэш по пути и времени изменения) или ndarray (HDRI/готовые данные)."""
        if src is None:
            return None
        if isinstance(src, np.ndarray):
            key = ("arr", id(src))
            if key not in self.tex:
                a = src if src.dtype == np.uint8 else (np.clip(src, 0, 1) ** (1 / 2.2) * 255 + 0.5).astype(np.uint8)
                if a.shape[2] == 3:
                    a = np.dstack([a, np.full(a.shape[:2], 255, np.uint8)])
                self._put(key, self._upload(a), src)
        else:
            p = Path(src)
            try:
                key = ("file", str(p), p.stat().st_mtime_ns)
            except OSError:
                return None
            if key not in self.tex:
                try:
                    rgba, _ = load_rgba(p)
                except Exception:
                    log_exc("glrender.texture_for")
                    self._put(key, None)
                    return None
                self._put(key, self._upload(rgba))
        ent = self.tex[key]
        ent[2] = self.frame                                      # использована в этом кадре — вытеснять нельзя
        return ent[0]

    def _put(self, key, tex, keepalive=None):
        self.tex[key] = [tex, keepalive, self.frame]
        if len(self.tex) > KEEP_TEXTURES:                        # вытесняем самые давние из НЕ использованных в текущем кадре
            for old in sorted((k for k in self.tex if k != key and self.tex[k][2] != self.frame), key=lambda k: self.tex[k][2]):
                t = self.tex.pop(old)[0]
                if t is not None:
                    t.release()
                if len(self.tex) <= KEEP_TEXTURES:
                    break

    def set_mesh(self, mesh):
        if self.mesh_key == id(mesh):
            return
        for a in ("vao", "vao_line", "vbo", "ibo", "ebo"):
            o = getattr(self, a, None)
            if o is not None:
                o.release()
        self.vao_line = self.ebo = None
        data = np.hstack([mesh.v, mesh.n, mesh.uv]).astype("f4")
        self.vbo = self.ctx.buffer(np.ascontiguousarray(data).tobytes())
        self.ibo = self.ctx.buffer(np.ascontiguousarray(mesh.f.astype("u4")).tobytes())
        self.vao = self.ctx.vertex_array(self.prog, [(self.vbo, "3f 3f 2f", "aPos", "aN", "aUV")], index_buffer=self.ibo)
        self.n_tris = len(mesh.f)
        self.ranges = mesh.part_ranges() if mesh.face_part is not None and len(mesh.part_names or []) > 1 else []
        self._mesh_faces = mesh.f
        self.mesh_key = id(mesh)

    def _wire_vao(self):
        if self.vao_line is None:
            f = self._mesh_faces
            e = np.stack([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]], 1).reshape(-1, 2).astype("u4")    # рёбра треугольников
            self.ebo = self.ctx.buffer(np.ascontiguousarray(e).tobytes())
            self.vao_line = self.ctx.vertex_array(self.line, [(self.vbo, "3f 5x4", "aPos")], index_buffer=self.ebo)
            self.n_edges = len(e)
        return self.vao_line

    def _fbo(self, w, h):
        if self.fbos and self.fbos[0] == (w, h):
            return self.fbos[1], self.fbos[2]
        if self.fbos:
            for o in self.fbos[1:]:
                o.release()
        ctx = self.ctx
        ms = ctx.framebuffer(ctx.renderbuffer((w, h), 4, samples=self.samples), ctx.depth_renderbuffer((w, h), samples=self.samples))
        res = ctx.framebuffer(ctx.renderbuffer((w, h), 4))
        self.fbos = ((w, h), ms, res)
        return ms, res

    # ---- рисование
    @staticmethod
    def _set(prog, name, val):
        try:
            prog[name].value = val
        except KeyError:                                                     # uniform убран компилятором
            pass

    def _cam(self, prog, o, aspect):
        R = _rot(o.yaw, o.pitch)
        for i in range(3):
            self._set(prog, f"rot{i}", tuple(float(x) for x in R[i]))
        self._set(prog, "dist", float(4.0 / max(o.zoom, 0.05)))
        self._set(prog, "focal", float(1 / math.tan(math.radians(17.5))))
        self._set(prog, "aspect", float(aspect))
        self._set(prog, "pan", (float(o.pan[0]), float(o.pan[1])))

    def _material(self, mat, base, nstr, o, find, hl):
        p, S = self.prog, self._set
        unit = 0
        def bind(stage, sampler, flag, mn, on):
            nonlocal unit
            s = mat.stage(stage)
            t = None
            if s is not None and o.stages_on.get(stage, True) and s.texture and not is_procedural(s.texture):
                f = find(s.texture)
                t = self.texture_for(f) if f else None
            M, off = _uv_matrix(s)
            S(p, mn, (float(M[0, 0]), float(M[0, 1]), float(M[1, 0]), float(M[1, 1])))
            S(p, on, (float(off[0]), float(off[1])))
            (t or self.dummy).use(location=unit)
            S(p, sampler, unit); unit += 1
            S(p, flag, 1 if t else 0)
            return t
        bind(1, "tNormal", "hasN", "mN", "oN"); bind(2, "tDetail", "hasD", "mD", "oD")
        bind(4, "tAs", "hasA", "mA", "oA"); bind(5, "tSmdi", "hasS", "mS", "oS")
        hd = self.texture_for(getattr(o, "hdri", None))
        (hd or self.dummy).use(location=unit); S(p, "tHdri", unit); unit += 1; S(p, "hasH", 1 if hd else 0)
        bt = self.texture_for(base)
        (bt or self.dummy).use(location=unit); S(p, "tBase", unit); unit += 1; S(p, "hasB", 1 if bt else 0)
        s2 = mat.stage(2)
        dm, dc = 0, (1.0, 1.0, 1.0)
        if s2 and o.stages_on.get(2, True) and s2.texture:
            if is_procedural(s2.texture):
                pr = parse_procedural(s2.texture)
                if pr.rgba:
                    dm, dc = 1, tuple(float(x) for x in pr.rgba[:3])
            elif find(s2.texture) and self.texture_for(find(s2.texture)):
                dm = 2
        S(p, "detailMode", dm); S(p, "detailConst", dc)
        col = lambda n, d: tuple(float(x) for x in (mat.get(n) or d)[:3])
        for name, key, d in (("ambient", "amb", [1] * 3), ("diffuse", "dif", [1] * 3), ("forcedDiffuse", "forced", [0, 0, 0]),
                             ("emmisive", "emi", [0] * 3), ("specular", "spc", [0] * 3)):
            S(p, key, col(name, d))
        S(p, "power", float(mat.get("specularPower") or 10))
        fn, fk = 1.5, 0.0
        s6 = mat.stage(6)
        if s6 and o.stages_on.get(6, True) and s6.texture and is_procedural(s6.texture):
            pr = parse_procedural(s6.texture)
            if pr.fresnel:
                fn, fk = pr.fresnel
        S(p, "fresN", float(fn)); S(p, "fresK", float(fk))
        S(p, "nStr", float(nstr)); S(p, "hl", float(hl))

    def render(self, mat, o: RenderOptions, find: Callable[[str], Optional[Path]], mesh, part_mats: Optional[dict] = None) -> np.ndarray:
        """-> RGBA uint8 (H,W,4). part_mats: {индекс части: (Material, base_color_путь_или_массив, сила_нормали)}."""
        ctx, p = self.ctx, self.prog
        W, H = int(o.width), int(o.height)
        self.frame += 1
        self.set_mesh(mesh)
        ms, res = self._fbo(W, H)
        ms.use()
        ctx.viewport = (0, 0, W, H)
        L = LIGHTING.get(o.lighting, LIGHTING["Студия"])
        ms.clear(0.0, 0.0, 0.0, 1.0, depth=1.0)
        ctx.disable(moderngl.DEPTH_TEST | moderngl.BLEND)
        self._set(self.bg, "top", tuple(map(float, L["top"]))); self._set(self.bg, "hor", tuple(map(float, L["hor"])))
        self._set(self.bg, "grid", 1 if o.grid else 0)
        self.vao_bg.render(moderngl.TRIANGLES, vertices=3)
        ctx.enable(moderngl.DEPTH_TEST)
        self._cam(p, o, W / max(H, 1))
        ang = math.radians(o.light_rot)
        s = L["sun"]
        sv = np.array([s[0] * math.cos(ang) + s[2] * math.sin(ang), s[1], -s[0] * math.sin(ang) + s[2] * math.cos(ang)])
        sv /= np.linalg.norm(sv)
        S = self._set
        S(p, "sunDir", tuple(float(x) for x in sv)); S(p, "sunCol", tuple(map(float, L["col"]))); S(p, "sunI", float(L["i"])); S(p, "ambK", float(L["amb"]))
        S(p, "skyTop", tuple(map(float, L["top"]))); S(p, "skyHor", tuple(map(float, L["hor"]))); S(p, "skyGnd", tuple(map(float, L["gnd"])))
        S(p, "tiling", float(o.tiling)); S(p, "view", VIEWS.get(o.view, 0)); S(p, "envGain", float(o.env_gain))
        default_base, default_n = o.base_color, o.normal_strength
        if self.ranges and part_mats:
            for pid, (start, cnt) in enumerate(self.ranges):
                if not cnt:
                    continue
                m_, b_, *ex = part_mats.get(pid, (mat, default_base, default_n))
                self._material(m_, b_, ex[0] if ex else default_n, o, find, 1.0 if pid == o.highlight else 0.0)
                self.vao.render(moderngl.TRIANGLES, vertices=cnt * 3, first=start * 3)
        else:
            self._material(mat, default_base, default_n, o, find, 0.0)
            self.vao.render(moderngl.TRIANGLES)
        if o.wire:
            ctx.enable(moderngl.BLEND)
            self._cam(self.line, o, W / max(H, 1))
            self._wire_vao().render(moderngl.LINES)
            ctx.disable(moderngl.BLEND)
        ctx.copy_framebuffer(res, ms)
        raw = res.read(components=4)
        return np.frombuffer(raw, np.uint8).reshape(H, W, 4)[::-1].copy()

    def release(self):
        try:
            self.ctx.release()
        except Exception:
            log_exc("glrender.release")
            pass


class GLThread(threading.Thread):
    """Поток-владелец GL-контекста: задачи выполняются по очереди; контекст создаётся внутри потока."""

    def __init__(self):
        super().__init__(daemon=True)
        self.q: queue.Queue = queue.Queue()
        self.ready = threading.Event()
        self.renderer: Optional[GLRenderer] = None
        self.error = ""
        _live.add(self)
        self.start()

    def run(self):
        try:
            self.renderer = GLRenderer()
        except Exception as e:
            log_exc("glrender.run")
            self.error = str(e)
            self.ready.set()
            return
        self.ready.set()
        while True:
            job = self.q.get()
            if job is None:
                break
            fn, box, cb = job
            try:
                box["result"] = fn(self.renderer)
            except Exception as e:
                log_exc("glrender.run")
                box["error"] = e
            finally:
                box["done"].set()
                if cb:
                    cb(box)
        self.renderer.release()

    def submit(self, fn, cb=None) -> dict:
        box = {"done": threading.Event()}
        self.q.put((fn, box, cb))
        return box

    def call(self, fn, timeout=60):
        box = self.submit(fn)
        box["done"].wait(timeout)
        if "error" in box:
            raise box["error"]
        return box.get("result")

    def stop(self, timeout: float = 10.0):
        """Остановить поток и дождаться освобождения GL-ресурсов (иначе при выходе процесса возможен сбой в драйвере)."""
        self.q.put(None)
        if threading.current_thread() is not self and self.is_alive():
            self.join(timeout)


_live: "weakref.WeakSet[GLThread]" = weakref.WeakSet()


def _stop_all():
    for t in list(_live):
        try:
            t.stop(5.0)
        except Exception:
            pass


atexit.register(_stop_all)
