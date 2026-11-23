"""Базовые виджеты: прокручиваемая панель, ползунок с полем и сбросом, RGBA-строка."""
import tkinter as tk
from tkinter import colorchooser, ttk

from . import theme


class ScrollFrame(ttk.Frame):
    """Вертикально прокручиваемая панель; содержимое — в .inner"""

    def __init__(self, parent, **kw):
        super().__init__(parent, **kw)
        self.canvas = tk.Canvas(self, bg=theme.BG, highlightthickness=0, bd=0)
        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.vsb.set)
        self.vsb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = ttk.Frame(self.canvas)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._win, width=e.width))
        for w in (self.canvas, self.inner):
            w.bind("<Enter>", self._bind_wheel)
            w.bind("<Leave>", self._unbind_wheel)

    def _bind_wheel(self, _):
        self.canvas.bind_all("<MouseWheel>", self._wheel)
        self.canvas.bind_all("<Button-4>", lambda e: self.canvas.yview_scroll(-3, "units"))
        self.canvas.bind_all("<Button-5>", lambda e: self.canvas.yview_scroll(3, "units"))

    def _unbind_wheel(self, _):
        for ev in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.canvas.unbind_all(ev)

    def _wheel(self, e):
        self.canvas.yview_scroll(int(-e.delta / 120) * 3, "units")


def fmt(v, nd=4) -> str:
    s = f"{v:.{nd}f}"
    if "." in s:                       # обрезать «хвостовые» нули только у дробной части (80 остаётся 80)
        s = s.rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


class SliderRow(ttk.Frame):
    """Название — ползунок — точное поле — ↺. on_change(value) вызывается только от пользователя."""

    def __init__(self, parent, label, lo, hi, step=1.0, nd=0, suffix="", on_change=None, on_reset=None, label_width=16):
        super().__init__(parent)
        self.lo, self.hi, self.step, self.nd, self.on_change = lo, hi, step, nd, on_change
        self._lock = False
        self.label = ttk.Label(self, text=label, width=label_width)
        self.label.grid(row=0, column=0, sticky="w")
        self.var = tk.DoubleVar(value=lo)
        self.scale = ttk.Scale(self, from_=lo, to=hi, orient="horizontal", variable=self.var, command=self._scale)
        self.scale.grid(row=0, column=1, sticky="ew", padx=6)
        self.sv = tk.StringVar()
        self.entry = ttk.Spinbox(self, from_=lo, to=hi, increment=step, textvariable=self.sv, width=7, command=self._entry)
        self.entry.grid(row=0, column=2)
        self.entry.bind("<Return>", self._entry)
        self.entry.bind("<FocusOut>", self._entry)
        if suffix:
            ttk.Label(self, text=suffix, style="Hint.TLabel").grid(row=0, column=3, padx=(2, 0))
        self.reset = ttk.Button(self, text="↺", width=3, style="Tool.TButton", command=on_reset or (lambda: None))
        self.reset.grid(row=0, column=4, padx=(6, 0))
        self.columnconfigure(1, weight=1)
        self.set(lo)

    def _snap(self, v):
        v = min(max(v, self.lo), self.hi)
        return round(v / self.step) * self.step if self.step else v

    def _scale(self, _=None):
        if self._lock:
            return
        v = self._snap(self.var.get())
        self.sv.set(fmt(v, self.nd))
        if self.on_change:
            self.on_change(v)

    def _entry(self, _=None):
        if self._lock:
            return
        try:
            v = float(self.sv.get().replace(",", "."))
        except ValueError:
            self.sv.set(fmt(self.var.get(), self.nd))
            return
        v = min(max(v, self.lo), self.hi)
        self._lock = True
        self.var.set(v)
        self._lock = False
        if self.on_change:
            self.on_change(v)

    def set(self, v):
        """Программная установка (без on_change)."""
        self._lock = True
        try:
            self.var.set(min(max(v, self.lo), self.hi))
            if self.focus_get() is not self.entry:
                self.sv.set(fmt(v, self.nd))
        finally:
            self._lock = False

    def value(self) -> float:
        try:
            return float(self.sv.get().replace(",", "."))
        except ValueError:
            return self.var.get()

    def mark(self, overridden: bool):
        self.label.configure(style="Over.TLabel" if overridden else "TLabel")


class ColorRow(ttk.Frame):
    """RGBA: четыре поля + выбор цвета + ↺."""

    def __init__(self, parent, label, on_change=None, on_reset=None, label_width=16, maxv=4.0):
        super().__init__(parent)
        self.on_change, self._lock = on_change, False
        self.label = ttk.Label(self, text=label, width=label_width)
        self.label.grid(row=0, column=0, sticky="w")
        self.vars = [tk.StringVar(value="0") for _ in range(4)]
        self.spins = []
        for i, (v, ch) in enumerate(zip(self.vars, "RGBA")):
            sp = ttk.Spinbox(self, from_=0, to=maxv, increment=0.05, textvariable=v, width=6, command=self._emit)
            sp.grid(row=0, column=1 + i, padx=1)
            sp.bind("<Return>", self._emit)
            sp.bind("<FocusOut>", self._emit)
            self.spins.append(sp)
        self.swatch = tk.Button(self, width=2, relief="flat", bd=1, command=self._pick)
        self.swatch.grid(row=0, column=5, padx=(6, 0))
        self.reset = ttk.Button(self, text="↺", width=3, style="Tool.TButton", command=on_reset or (lambda: None))
        self.reset.grid(row=0, column=6, padx=(6, 0))

    def values(self):
        out = []
        for v in self.vars:
            try:
                out.append(float(v.get().replace(",", ".")))
            except ValueError:
                out.append(0.0)
        return out

    def _paint(self):
        r, g, b = (max(0, min(1, x)) for x in self.values()[:3])
        self.swatch.configure(bg="#%02x%02x%02x" % (int(r * 255), int(g * 255), int(b * 255)), activebackground="#%02x%02x%02x" % (int(r * 255), int(g * 255), int(b * 255)))

    def _emit(self, _=None):
        if self._lock:
            return
        self._paint()
        if self.on_change:
            self.on_change(self.values())

    def _pick(self):
        r, g, b, a = self.values()
        rgb, _hex = colorchooser.askcolor(color="#%02x%02x%02x" % (int(min(r, 1) * 255), int(min(g, 1) * 255), int(min(b, 1) * 255)), parent=self)
        if rgb:
            self._lock = True
            for v, x in zip(self.vars[:3], rgb):
                v.set(f"{x / 255:.4f}".rstrip("0").rstrip("."))
            self._lock = False
            self._emit()

    def set(self, vals):
        self._lock = True
        try:
            for v, x in zip(self.vars, vals):
                v.set(fmt(x))
        finally:
            self._lock = False
        self._paint()

    def mark(self, overridden: bool):
        self.label.configure(style="Over.TLabel" if overridden else "TLabel")


def tree(parent, columns, headings, widths=None, height=8, show="headings", selectmode="browse"):
    f = ttk.Frame(parent)
    t = ttk.Treeview(f, columns=columns, show=show, height=height, selectmode=selectmode)
    for c, h in zip(columns, headings):
        t.heading(c, text=h)
        t.column(c, width=(widths or {}).get(c, 100), stretch=True, anchor="w")
    sb = ttk.Scrollbar(f, orient="vertical", command=t.yview)
    t.configure(yscrollcommand=sb.set)
    t.pack(side="left", fill="both", expand=True)
    sb.pack(side="right", fill="y")
    return f, t


def ask_text(parent, title, prompt, initial=""):
    from tkinter import simpledialog
    return simpledialog.askstring(title, prompt, initialvalue=initial, parent=parent)
