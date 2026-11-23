"""Тёмная тема для tkinter/ttk."""
import tkinter as tk
from tkinter import ttk

BG, PANEL, PANEL2, LINE = "#1e1f24", "#23252b", "#272930", "#34363f"
FG, DIM, ACC, ACC2, INPUT = "#d9dbe0", "#8b90a0", "#3b6fd8", "#6d9bff", "#15161a"
WARN, ERR, INFO, OVER = "#e3b341", "#ff7b72", "#79c0ff", "#ffb454"
FONT = ("Segoe UI", 9)


def apply(root: tk.Tk):
    root.configure(bg=BG)
    root.option_add("*Font", FONT)
    st = ttk.Style(root)
    st.theme_use("clam")
    st.configure(".", background=BG, foreground=FG, fieldbackground=INPUT, bordercolor=LINE, lightcolor=LINE, darkcolor=LINE,
                 troughcolor=INPUT, focuscolor=ACC, selectbackground=ACC, selectforeground="#fff", font=FONT)
    st.configure("TFrame", background=BG)
    st.configure("Panel.TFrame", background=PANEL)
    st.configure("TLabel", background=BG, foreground=FG)
    st.configure("Hint.TLabel", foreground=DIM)
    st.configure("Over.TLabel", foreground=OVER, font=(FONT[0], FONT[1], "bold"))
    st.configure("Head.TLabel", foreground=ACC2, font=(FONT[0], FONT[1], "bold"))
    st.configure("TButton", background="#323540", foreground=FG, bordercolor="#424652", padding=(8, 3))
    st.map("TButton", background=[("active", "#3d4150"), ("disabled", "#2a2c34")], foreground=[("disabled", "#777")])
    st.configure("Accent.TButton", background=ACC, foreground="#fff", bordercolor="#5486ee", padding=(12, 4), font=(FONT[0], FONT[1], "bold"))
    st.map("Accent.TButton", background=[("active", "#4a7be6"), ("disabled", "#2a3b66")])
    st.configure("Tool.TButton", padding=(6, 2))
    st.configure("TCheckbutton", background=BG, foreground=FG, indicatorbackground=INPUT, indicatorforeground=ACC2)
    st.map("TCheckbutton", background=[("active", BG)], indicatorbackground=[("selected", ACC)])
    st.configure("TNotebook", background=PANEL, bordercolor=LINE, tabmargins=(0, 0, 0, 0))
    st.configure("TNotebook.Tab", background=PANEL2, foreground=FG, padding=(9, 4), bordercolor=LINE)
    st.map("TNotebook.Tab", background=[("selected", BG)], foreground=[("selected", "#fff")])
    st.configure("TEntry", fieldbackground=INPUT, foreground=FG, insertcolor=FG, bordercolor="#373a45")
    st.configure("TSpinbox", fieldbackground=INPUT, foreground=FG, arrowcolor=FG, bordercolor="#373a45")
    st.configure("TCombobox", fieldbackground=INPUT, background="#323540", foreground=FG, arrowcolor=FG, bordercolor="#373a45")
    st.map("TCombobox", fieldbackground=[("readonly", INPUT)], foreground=[("readonly", FG)], selectbackground=[("readonly", INPUT)])
    root.option_add("*TCombobox*Listbox.background", INPUT)
    root.option_add("*TCombobox*Listbox.foreground", FG)
    root.option_add("*TCombobox*Listbox.selectBackground", ACC)
    st.configure("Treeview", background=INPUT, fieldbackground=INPUT, foreground=FG, bordercolor="#373a45", rowheight=22)
    st.map("Treeview", background=[("selected", ACC)], foreground=[("selected", "#fff")])
    st.configure("Treeview.Heading", background=PANEL2, foreground=FG, bordercolor=LINE)
    st.configure("Horizontal.TScale", background=ACC2, troughcolor="#3a3d49", bordercolor="#3a3d49", lightcolor=ACC2, darkcolor=ACC)
    st.map("Horizontal.TScale", background=[("active", "#8fb2ff")])
    for o in ("Vertical", "Horizontal"):
        st.configure(f"{o}.TScrollbar", background="#3d404b", troughcolor="#1a1b1f", bordercolor="#1a1b1f", arrowcolor=FG,
                     lightcolor="#3d404b", darkcolor="#3d404b", gripcount=0, arrowsize=13)
        st.map(f"{o}.TScrollbar", background=[("active", "#50546a")], lightcolor=[("active", "#50546a")], darkcolor=[("active", "#50546a")])
    st.configure("TPanedwindow", background="#2a2c34")
    st.configure("Sash", sashthickness=5, background="#2a2c34")
    st.configure("TLabelframe", background=BG, bordercolor=LINE)
    st.configure("TLabelframe.Label", background=BG, foreground=ACC2)
    st.configure("Horizontal.TProgressbar", background=ACC, troughcolor=INPUT)


def style_text(w, **kw):
    w.configure(bg=INPUT, fg=FG, insertbackground=FG, selectbackground=ACC, highlightthickness=1, highlightbackground="#373a45",
                relief="flat", **kw)
    return w
