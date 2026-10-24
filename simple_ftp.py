"""SimpleFTP - Windows 11 向けのシンプルなマテリアルデザイン風 FTP クライアント

依存: Python 3.10+ の標準ライブラリのみ (tkinter / ftplib)
起動: python simple_ftp.py   (コンソールなしなら pythonw simple_ftp.py)
"""
from __future__ import annotations

import ftplib
import json
import os
import posixpath
import queue
import shutil
import subprocess
import ssl
import string
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

from ftp_core import Cancelled, Entry, FtpClient, human_size, list_local, sort_entries

APP_NAME = "SimpleFTP"

# ---------------------------------------------------------------- カラー (Material 3 風)
BG = "#F3F6FC"            # surface container
SURFACE = "#FFFFFF"
PRIMARY = "#1A73E8"
PRIMARY_HOVER = "#1765CC"
PRIMARY_PRESSED = "#185ABC"
ON_PRIMARY = "#FFFFFF"
PRIMARY_CONTAINER = "#D3E3FD"
PRIMARY_CONTAINER_HOVER = "#C2D7FB"
ON_PRIMARY_CONTAINER = "#0B3B8C"
ON_SURFACE = "#1F1F1F"
ON_SURFACE_VAR = "#5F6368"
OUTLINE = "#DADCE0"
HOVER = "#EEF2F8"
ROW_HOVER = "#F1F5FD"
DISABLED_BG = "#E8EAED"
DISABLED_FG = "#A0A4A8"
ERROR = "#D93025"
SUCCESS = "#188038"
SNACK_BG = "#303134"
SNACK_FG = "#F1F3F4"

S = 1.0          # DPI スケール（起動時に計算）
FONT = "Meiryo"
ICON_FONT: str | None = None

GLYPH = {  # Segoe Fluent Icons / Segoe MDL2 Assets のコードポイントと代替文字
    "up": ("", "↑"), "refresh": ("", "⟳"), "newfolder": ("", "+"),
    "rename": ("", "✎"), "delete": ("", "✕"), "home": ("", "⌂"),
    "right": ("", "→"), "left": ("", "←"), "close": ("", "✕"),
    "lock": ("", "🔒"),
}


def px(v: float) -> int:
    return max(1, int(round(v * S)))


def glyph(name: str) -> str:
    g, alt = GLYPH[name]
    return g if ICON_FONT else alt


def settings_path() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, APP_NAME, "settings.json")


def load_settings() -> dict:
    try:
        with open(settings_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_settings(data: dict):
    try:
        os.makedirs(os.path.dirname(settings_path()), exist_ok=True)
        with open(settings_path(), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def _dpapi(data: bytes, encrypt: bool) -> bytes:
    """Windows DPAPI: 今ログインしている Windows ユーザーだけが復号できる暗号化"""
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    src = BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    dst = BLOB()
    crypt = ctypes.windll.crypt32
    fn = crypt.CryptProtectData if encrypt else crypt.CryptUnprotectData
    if encrypt:
        ok = fn(ctypes.byref(src), "SimpleFTP", None, None, None, 0x1, ctypes.byref(dst))
    else:
        ok = fn(ctypes.byref(src), None, None, None, None, 0x1, ctypes.byref(dst))
    if not ok:
        raise OSError("DPAPI error")
    try:
        return ctypes.string_at(dst.pbData, dst.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(dst.pbData)


def protect_password(pw: str) -> str:
    import base64
    raw = pw.encode("utf-8")
    if sys.platform == "win32":
        return "dpapi:" + base64.b64encode(_dpapi(raw, True)).decode()
    return "b64:" + base64.b64encode(raw).decode()   # Windows 以外（開発用）


def unprotect_password(token: str) -> str:
    import base64
    try:
        kind, _, val = token.partition(":")
        raw = base64.b64decode(val)
        if kind == "dpapi" and sys.platform == "win32":
            return _dpapi(raw, False).decode("utf-8")
        if kind == "b64":
            return raw.decode("utf-8")
    except Exception:
        pass
    return ""


def list_drives() -> list[Entry]:
    if hasattr(os, "listdrives"):
        names = os.listdrives()
    else:
        names = [f"{c}:\\" for c in string.ascii_uppercase if os.path.exists(f"{c}:\\")]
    return [Entry(n, True) for n in names]


def recycle(paths: list[str]) -> None:
    """Windows ならごみ箱へ、それ以外は削除"""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class SHFILEOPSTRUCTW(ctypes.Structure):
            _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT),
                        ("pFrom", ctypes.c_void_p), ("pTo", ctypes.c_void_p),
                        ("fFlags", ctypes.c_uint16), ("fAnyOperationsAborted", wintypes.BOOL),
                        ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", ctypes.c_void_p)]

        buf = ctypes.create_unicode_buffer("\0".join(os.path.abspath(p) for p in paths) + "\0\0")
        op = SHFILEOPSTRUCTW()
        op.wFunc = 3                                   # FO_DELETE
        op.pFrom = ctypes.addressof(buf)
        op.fFlags = 0x40 | 0x10 | 0x04 | 0x400         # ALLOWUNDO|NOCONFIRMATION|SILENT|NOERRORUI
        rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
        if rc != 0:
            raise OSError(f"ごみ箱へ移動できませんでした (code {rc})")
        return
    for p in paths:
        shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)


# ================================================================ 部品
def round_rect(c: tk.Canvas, x1, y1, x2, y2, r, **kw):
    r = min(r, (x2 - x1) / 2, (y2 - y1) / 2)
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return c.create_polygon(pts, smooth=True, **kw)


class Tooltip:
    def __init__(self, widget: tk.Widget, text: str):
        self.w, self.text, self.tip, self.job = widget, text, None, None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _=None):
        self.job = self.w.after(550, self._show)

    def _show(self):
        if self.tip:
            return
        x = self.w.winfo_rootx() + self.w.winfo_width() // 2
        y = self.w.winfo_rooty() + self.w.winfo_height() + px(4)
        self.tip = tk.Toplevel(self.w)
        self.tip.wm_overrideredirect(True)
        lb = tk.Label(self.tip, text=self.text, bg="#3C4043", fg="white",
                      font=(FONT, 9), padx=px(8), pady=px(3))
        lb.pack()
        self.tip.update_idletasks()
        self.tip.geometry(f"+{x - self.tip.winfo_width() // 2}+{y}")

    def _hide(self, _=None):
        if self.job:
            self.w.after_cancel(self.job)
            self.job = None
        if self.tip:
            self.tip.destroy()
            self.tip = None


class MButton(tk.Canvas):
    """角丸ボタン。kind: filled / tonal / text / icon"""

    def __init__(self, master, text="", command=None, kind="filled", icon=None,
                 tooltip=None, bg=None, min_width=0):
        self.bg = bg or master.cget("bg")
        self.kind, self.text, self.icon, self.command = kind, text, icon, command
        self.enabled, self.hover, self.pressed = True, False, False
        self.font = tkfont.Font(family=FONT, size=10, weight="bold" if kind != "icon" else "normal")
        self.ifont = (ICON_FONT, 11) if ICON_FONT else (FONT, 12)
        h = px(36)
        if kind == "icon":
            w = h
        else:
            w = self.font.measure(text) + px(40) + (px(24) if icon else 0)
            w = max(w, px(min_width))
        super().__init__(master, width=w, height=h, bg=self.bg, highlightthickness=0,
                         bd=0, cursor="hand2")
        self.bind("<Enter>", lambda e: self._set(hover=True))
        self.bind("<Leave>", lambda e: self._set(hover=False, pressed=False))
        self.bind("<ButtonPress-1>", lambda e: self._set(pressed=True))
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Configure>", lambda e: self.draw())
        if tooltip:
            Tooltip(self, tooltip)
        self.draw()

    def _set(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)
        self.draw()

    def _release(self, e):
        fire = self.pressed and self.hover and self.enabled
        self._set(pressed=False)
        if fire and self.command:
            self.command()

    def colors(self):
        if not self.enabled:
            if self.kind in ("filled", "tonal"):
                return DISABLED_BG, DISABLED_FG
            return self.bg, DISABLED_FG
        if self.kind == "filled":
            return (PRIMARY_PRESSED if self.pressed else PRIMARY_HOVER if self.hover else PRIMARY), ON_PRIMARY
        if self.kind == "tonal":
            return (PRIMARY_CONTAINER_HOVER if self.hover else PRIMARY_CONTAINER), ON_PRIMARY_CONTAINER
        if self.kind == "text":
            return (HOVER if self.hover else self.bg), PRIMARY
        return (OUTLINE if self.pressed else HOVER if self.hover else self.bg), ON_SURFACE_VAR

    def draw(self):
        self.delete("all")
        w, h = int(self.cget("width")), int(self.cget("height"))
        fill, fg = self.colors()
        if fill != self.bg:
            if self.kind == "icon":
                self.create_oval(1, 1, w - 1, h - 1, fill=fill, outline=fill)
            else:
                round_rect(self, 0, 0, w, h, h / 2, fill=fill, outline=fill)
        if self.kind == "icon":
            self.create_text(w / 2, h / 2, text=self.icon or self.text, fill=fg, font=self.ifont)
            return
        if self.icon:
            tw = self.font.measure(self.text)
            x0 = (w - tw - px(24)) / 2
            self.create_text(x0 + px(8), h / 2, text=self.icon, fill=fg, font=self.ifont)
            self.create_text(x0 + px(24), h / 2, text=self.text, fill=fg, font=self.font, anchor="w")
        else:
            self.create_text(w / 2, h / 2, text=self.text, fill=fg, font=self.font)
        self.configure(cursor="hand2" if self.enabled else "arrow")

    def set_enabled(self, on: bool):
        self.enabled = on
        self.draw()

    def set_text(self, text: str, kind: str | None = None):
        self.text = text
        if kind:
            self.kind = kind
        if self.kind != "icon":
            w = self.font.measure(text) + px(40) + (px(24) if self.icon else 0)
            self.configure(width=max(w, int(self.cget("width"))))
        self.draw()


class MSwitch(tk.Canvas):
    def __init__(self, master, value=False, command=None):
        self.bg = master.cget("bg")
        super().__init__(master, width=px(46), height=px(28), bg=self.bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.value, self.command = value, command
        self.bind("<Button-1>", self.toggle)
        self.draw()

    def toggle(self, _=None):
        self.value = not self.value
        self.draw()
        if self.command:
            self.command(self.value)

    def draw(self):
        self.delete("all")
        w, h = px(46), px(28)
        if self.value:
            round_rect(self, 1, 1, w - 1, h - 1, h / 2, fill=PRIMARY, outline=PRIMARY)
            r = h / 2 - px(5)
            cx = w - h / 2
            self.create_oval(cx - r, h / 2 - r, cx + r, h / 2 + r, fill=ON_PRIMARY, outline=ON_PRIMARY)
        else:
            round_rect(self, 1, 1, w - 1, h - 1, h / 2, fill="#E1E3E8", outline="#80868B", width=max(1, px(1.5)))
            r = h / 2 - px(8)
            cx = h / 2
            self.create_oval(cx - r, h / 2 - r, cx + r, h / 2 + r, fill="#80868B", outline="#80868B")


class LinearProgress(tk.Canvas):
    def __init__(self, master):
        self.bg = master.cget("bg")
        super().__init__(master, height=px(4), bg=self.bg, highlightthickness=0, bd=0)
        self.mode, self.value, self.phase, self.job = "hidden", 0.0, 0.0, None
        self.bind("<Configure>", lambda e: self.draw())

    def set(self, value: float | None):
        """None=不確定アニメーション, 0..1=確定, <0=非表示"""
        if value is None:
            self.mode = "indeterminate"
            if self.job is None:
                self._tick()
        elif value < 0:
            self.mode = "hidden"
        else:
            self.mode, self.value = "determinate", max(0.0, min(1.0, value))
        self.draw()

    def _tick(self):
        if self.mode != "indeterminate":
            self.job = None
            return
        self.phase = (self.phase + 0.018) % 1.4
        self.draw()
        self.job = self.after(16, self._tick)

    def draw(self):
        self.delete("all")
        w, h = self.winfo_width(), int(self.cget("height"))
        if self.mode == "hidden" or w <= 1:
            return
        self.create_rectangle(0, 0, w, h, fill=PRIMARY_CONTAINER, outline="")
        if self.mode == "determinate":
            self.create_rectangle(0, 0, w * self.value, h, fill=PRIMARY, outline="")
        else:
            a = (self.phase - 0.4) * w
            self.create_rectangle(max(0, a), 0, min(w, a + 0.4 * w), h, fill=PRIMARY, outline="")


class RoundCard(tk.Canvas):
    """角丸の白いカード。中身は self.body (Frame) に置く"""

    def __init__(self, master, radius=16, padding=14):
        self.outer = master.cget("bg")
        super().__init__(master, bg=self.outer, highlightthickness=0, bd=0)
        self.r, self.pad = px(radius), px(padding)
        self.body = tk.Frame(self, bg=SURFACE)
        self.win = self.create_window(self.pad, self.pad, window=self.body, anchor="nw")
        self.bind("<Configure>", self._layout)
        self.body.bind("<Configure>", self._fit_height)
        self.fixed_height = False

    def _layout(self, e):
        self.delete("bgshape")
        round_rect(self, 1, 1, e.width - 1, e.height - 1, self.r, fill=SURFACE,
                   outline=OUTLINE, tags="bgshape")
        self.tag_lower("bgshape")
        self.itemconfigure(self.win, width=max(1, e.width - 2 * self.pad))
        if not self.fixed_height:
            self.itemconfigure(self.win, height=max(1, e.height - 2 * self.pad))

    def _fit_height(self, e):
        if self.fixed_height:
            want = e.height + 2 * self.pad
            if int(self.cget("height")) != want:
                self.configure(height=want)

    def autosize(self):
        """中身の高さに合わせる（接続カード用）"""
        self.fixed_height = True
        self.body.update_idletasks()
        self.configure(height=self.body.winfo_reqheight() + 2 * self.pad)


class MField(tk.Frame):
    """ラベル付きのアウトライン入力欄"""

    def __init__(self, master, label, width=18, show=None, value=""):
        super().__init__(master, bg=master.cget("bg"))
        if label:
            tk.Label(self, text=label, bg=self.cget("bg"), fg=ON_SURFACE_VAR,
                     font=(FONT, 9)).pack(anchor="w", padx=px(2))
        self.box = tk.Frame(self, bg=SURFACE, highlightthickness=1,
                            highlightbackground=OUTLINE, highlightcolor=PRIMARY)
        self.box.pack(fill="x")
        self.var = tk.StringVar(value=value)
        self.entry = tk.Entry(self.box, textvariable=self.var, width=width, relief="flat",
                              bd=0, highlightthickness=0, bg=SURFACE, fg=ON_SURFACE, insertbackground=PRIMARY,
                              font=(FONT, 10), show=show or "")
        self.entry.pack(fill="x", padx=px(10), pady=px(7))
        self.entry.bind("<FocusIn>", lambda e: self.box.configure(highlightthickness=2, highlightbackground=PRIMARY))
        self.entry.bind("<FocusOut>", lambda e: self.box.configure(highlightthickness=1, highlightbackground=OUTLINE))

    def get(self) -> str:
        return self.var.get().strip()


class Snackbar:
    def __init__(self, root: tk.Tk):
        self.root, self.canvas, self.job = root, None, None

    def show(self, text: str, error=False, ms=3200):
        self.hide()
        font = tkfont.Font(family=FONT, size=10)
        maxw = min(px(640), self.root.winfo_width() - px(48))
        lines = []
        for para in text.splitlines() or [""]:
            cur = ""
            for ch in para:
                if font.measure(cur + ch) > maxw - px(32):
                    lines.append(cur)
                    cur = ch
                else:
                    cur += ch
            lines.append(cur)
        lines = lines[:4]
        tw = max(font.measure(ln) for ln in lines)
        lh = font.metrics("linespace")
        w, h = tw + px(32), lh * len(lines) + px(28)
        c = tk.Canvas(self.root, width=w, height=h, bg=BG, highlightthickness=0, bd=0)
        round_rect(c, 0, 0, w, h, px(8), fill=SNACK_BG, outline=SNACK_BG)
        if error:
            c.create_rectangle(0, px(8), px(4), h - px(8), fill="#F28B82", outline="")
        c.create_text(px(16), h / 2, text="\n".join(lines), fill=SNACK_FG, font=font, anchor="w")
        c.place(relx=0.5, rely=1.0, y=-px(56), anchor="s")
        c.bind("<Button-1>", lambda e: self.hide())
        self.canvas = c
        self.job = self.root.after(ms if not error else ms + 2500, self.hide)

    def hide(self):
        if self.job:
            self.root.after_cancel(self.job)
            self.job = None
        if self.canvas:
            self.canvas.destroy()
            self.canvas = None


class Dialog(tk.Toplevel):
    """マテリアル風のシンプルなダイアログ（確認 / テキスト入力）"""

    def __init__(self, root, title, message="", initial=None, ok="OK", danger=False):
        super().__init__(root)
        self.result = None
        self.withdraw()
        self.title(title)
        self.configure(bg=SURFACE)
        self.resizable(False, False)
        self.transient(root)
        pad = px(24)
        f = tk.Frame(self, bg=SURFACE)
        f.pack(fill="both", expand=True, padx=pad, pady=(pad, px(16)))
        tk.Label(f, text=title, bg=SURFACE, fg=ON_SURFACE, font=(FONT, 14),
                 anchor="w").pack(fill="x")
        if message:
            tk.Label(f, text=message, bg=SURFACE, fg=ON_SURFACE_VAR, font=(FONT, 10),
                     justify="left", anchor="w", wraplength=px(420)).pack(fill="x", pady=(px(12), 0))
        self.field = None
        if initial is not None:
            self.field = MField(f, "", width=40, value=initial)
            self.field.pack(fill="x", pady=(px(12), 0))
            self.field.entry.select_range(0, "end")
            base, ext = os.path.splitext(initial)
            if ext and base:
                self.field.entry.select_range(0, len(base))
        row = tk.Frame(f, bg=SURFACE)
        row.pack(fill="x", pady=(px(22), 0))
        okb = MButton(row, ok, self._ok, kind="filled")
        if danger:
            okb.colors = lambda: ((("#B3261E" if okb.hover else ERROR)), "white")
            okb.draw()
        okb.pack(side="right")
        MButton(row, "キャンセル", self.destroy, kind="text").pack(side="right", padx=(0, px(8)))
        self.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self.destroy())
        self.update_idletasks()
        x = root.winfo_rootx() + (root.winfo_width() - self.winfo_reqwidth()) // 2
        y = root.winfo_rooty() + (root.winfo_height() - self.winfo_reqheight()) // 3
        self.geometry(f"+{max(0, x)}+{max(0, y)}")
        self.deiconify()
        apply_titlebar(self, SURFACE)
        self.grab_set()
        (self.field.entry if self.field else okb).focus_set()
        self.wait_window()

    def _ok(self):
        self.result = self.field.get() if self.field else True
        self.destroy()


def ask_text(root, title, initial="") -> str | None:
    r = Dialog(root, title, initial=initial).result
    return r or None


def confirm(root, title, message, ok="OK", danger=False) -> bool:
    return bool(Dialog(root, title, message, ok=ok, danger=danger).result)


def apply_titlebar(win: tk.Misc, color: str):
    """Windows 11: タイトルバーの色をアプリに合わせる"""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        r, g, b = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
        val = ctypes.c_int(r | (g << 8) | (b << 16))
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(val), 4)   # caption color
        border = ctypes.c_int(r | (g << 8) | (b << 16))
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 34, ctypes.byref(border), 4)  # border color
    except Exception:
        pass


# ================================================================ アイコン画像
def make_icons(root) -> dict[str, tk.PhotoImage]:
    n = px(18)

    def img():
        return tk.PhotoImage(master=root, width=n, height=n)

    def rect(im, x1, y1, x2, y2, color):
        im.put(color, to=(int(x1 * n), int(y1 * n), max(int(x1 * n) + 1, int(x2 * n)),
                          max(int(y1 * n) + 1, int(y2 * n))))

    folder = img()
    rect(folder, 0.05, 0.18, 0.45, 0.32, "#E8A600")
    rect(folder, 0.05, 0.28, 0.95, 0.85, "#F9BC15")
    rect(folder, 0.05, 0.36, 0.95, 0.85, "#FBC94A")

    file = img()
    rect(file, 0.18, 0.06, 0.82, 0.94, "#80868B")
    rect(file, 0.24, 0.12, 0.76, 0.88, "#FFFFFF")
    for y in (0.36, 0.52, 0.68):
        rect(file, 0.32, y, 0.68, y + 0.06, "#AECBFA")

    drive = img()
    rect(drive, 0.06, 0.30, 0.94, 0.74, "#5F6368")
    rect(drive, 0.12, 0.36, 0.88, 0.68, "#9AA0A6")
    rect(drive, 0.70, 0.48, 0.80, 0.58, "#34A853")

    app = tk.PhotoImage(master=root, width=32, height=32)
    app.put(PRIMARY, to=(0, 0, 32, 32))
    app.put("#FFFFFF", to=(7, 9, 21, 12))
    app.put("#FFFFFF", to=(17, 6, 20, 15))
    app.put("#FFFFFF", to=(11, 20, 25, 23))
    app.put("#FFFFFF", to=(12, 17, 15, 26))
    return {"folder": folder, "file": file, "drive": drive, "app": app}


# ================================================================ ファイルペイン
class FilePane:
    def __init__(self, master, title, icons, app: "App", remote: bool):
        self.app, self.remote, self.icons = app, remote, icons
        self.card = RoundCard(master)
        b = self.card.body
        self.entries: list[Entry] = []
        self.sort_key, self.sort_rev = "name", False
        self.hover_row = ""

        head = tk.Frame(b, bg=SURFACE)
        head.pack(fill="x")
        tk.Label(head, text=title, bg=SURFACE, fg=ON_SURFACE,
                 font=(FONT, 13, "bold")).pack(side="left", padx=(px(4), px(8)))
        self.count = tk.Label(head, text="", bg=SURFACE, fg=ON_SURFACE_VAR, font=(FONT, 9))
        self.count.pack(side="left")
        self.buttons = {}
        for key, tip in (("delete", "削除 (Del)"), ("rename", "名前の変更 (F2)"),
                         ("newfolder", "新しいフォルダー"), ("refresh", "更新 (F5)"),
                         ("up", "上のフォルダーへ (Backspace)")):
            btn = MButton(head, kind="icon", icon=glyph(key), tooltip=tip,
                          command=lambda k=key: self.app.pane_action(self, k))
            btn.pack(side="right")
            self.buttons[key] = btn
        if not remote:
            hb = MButton(head, kind="icon", icon=glyph("home"), tooltip="ホーム / ドライブ一覧",
                         command=lambda: self.app.local_home())
            hb.pack(side="right")
            self.buttons["home"] = hb

        pbox = tk.Frame(b, bg=BG, highlightthickness=0)
        pbox.pack(fill="x", pady=(px(8), px(8)))
        self.path_var = tk.StringVar()
        self.path_entry = tk.Entry(pbox, textvariable=self.path_var, relief="flat", bd=0,
                                   highlightthickness=0, bg=BG, fg=ON_SURFACE, font=(FONT, 10),
                                   insertbackground=PRIMARY)
        self.path_entry.pack(fill="x", padx=px(12), pady=px(8))
        self.path_entry.bind("<Return>", lambda e: self.app.navigate(self, self.path_var.get().strip()))

        tf = tk.Frame(b, bg=SURFACE)
        tf.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(tf, style="M.Treeview", columns=("size", "mod"),
                                 show="tree headings", selectmode="extended")
        self.tree.heading("#0", text="名前", anchor="w", command=lambda: self.sort_by("name"))
        self.tree.heading("size", text="サイズ", anchor="e", command=lambda: self.sort_by("size"))
        self.tree.heading("mod", text="更新日時", anchor="w", command=lambda: self.sort_by("mod"))
        self.tree.column("#0", width=px(260), minwidth=px(120), stretch=True)
        self.tree.column("size", width=px(84), minwidth=px(60), anchor="e", stretch=False)
        self.tree.column("mod", width=px(128), minwidth=px(90), anchor="w", stretch=False)
        self.tree.tag_configure("hover", background=ROW_HOVER)
        sb = ttk.Scrollbar(tf, orient="vertical", style="M.Vertical.TScrollbar",
                           command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y", padx=(px(2), 0))

        self.empty = tk.Label(tf, text="", bg=SURFACE, fg=ON_SURFACE_VAR, font=(FONT, 10))

        t = self.tree
        t.bind("<Double-1>", self._on_double)
        t.bind("<Return>", self._on_double)
        t.bind("<BackSpace>", lambda e: self.app.pane_action(self, "up"))
        t.bind("<Delete>", lambda e: self.app.pane_action(self, "delete"))
        t.bind("<F2>", lambda e: self.app.pane_action(self, "rename"))
        t.bind("<F5>", lambda e: self.app.pane_action(self, "refresh"))
        t.bind("<Motion>", self._on_motion)
        t.bind("<Leave>", lambda e: self._set_hover(""))
        t.bind("<<TreeviewSelect>>", lambda e: self._set_hover(self.hover_row))
        t.bind("<Button-3>", self._on_menu)
        t.bind("<Control-a>", lambda e: (t.selection_set(t.get_children()), "break")[1])

        self.menu = tk.Menu(t, tearoff=0, bg=SURFACE, fg=ON_SURFACE, activebackground=PRIMARY_CONTAINER,
                            activeforeground=ON_SURFACE, bd=0, font=(FONT, 10))
        self.menu.add_command(label="開く", font=(FONT, 10, "bold"),
                              command=lambda: self.app.open_item(self))
        self.menu.add_command(label="ダウンロード" if remote else "アップロード",
                              command=lambda: self.app.transfer_from(self))
        if not remote:
            self.menu.add_command(label="エクスプローラーで開く",
                                  command=lambda: self.app.open_in_explorer())
        self.menu.add_separator()
        self.menu.add_command(label="名前の変更", accelerator="F2",
                              command=lambda: self.app.pane_action(self, "rename"))
        self.menu.add_command(label="削除", accelerator="Del",
                              command=lambda: self.app.pane_action(self, "delete"))
        self.menu.add_separator()
        self.menu.add_command(label="新しいフォルダー", command=lambda: self.app.pane_action(self, "newfolder"))
        self.menu.add_command(label="更新", accelerator="F5", command=lambda: self.app.pane_action(self, "refresh"))

    # --- 表示
    def set_entries(self, path: str, entries: list[Entry], placeholder: str = ""):
        self.path_var.set(path)
        self.entries = entries
        self._render()
        if placeholder and not entries:
            self.empty.configure(text=placeholder)
            self.empty.place(relx=0.5, rely=0.45, anchor="center")
        elif not entries:
            self.empty.configure(text="空のフォルダーです")
            self.empty.place(relx=0.5, rely=0.45, anchor="center")
        else:
            self.empty.place_forget()

    def _render(self):
        t = self.tree
        t.delete(*t.get_children())
        self.hover_row = ""
        key = {
            "name": lambda e: e.name.lower(),
            "size": lambda e: e.size,
            "mod": lambda e: e.modified.timestamp() if e.modified else 0,
        }[self.sort_key]
        dirs = sorted([e for e in self.entries if e.is_dir], key=key, reverse=self.sort_rev)
        files = sorted([e for e in self.entries if not e.is_dir], key=key, reverse=self.sort_rev)
        for i, e in enumerate(dirs + files):
            icon = self.icons["drive"] if (e.is_dir and len(e.name) <= 3 and e.name.endswith(":\\")) \
                else self.icons["folder"] if e.is_dir else self.icons["file"]
            t.insert("", "end", iid=str(i), text="  " + e.name, image=icon,
                     values=(e.size_text, e.modified_text))
        self._ordered = dirs + files
        nd, nf = len(dirs), len(files)
        self.count.configure(text=f"フォルダー {nd} ・ ファイル {nf}" if self.entries else "")
        arrows = {"name": "#0", "size": "size", "mod": "mod"}
        labels = {"#0": "名前", "size": "サイズ", "mod": "更新日時"}
        for col, lb in labels.items():
            mark = (" ▼" if self.sort_rev else " ▲") if arrows[self.sort_key] == col else ""
            t.heading(col, text=lb + mark)

    def sort_by(self, key):
        self.sort_rev = (not self.sort_rev) if self.sort_key == key else False
        self.sort_key = key
        self._render()

    def selected(self) -> list[Entry]:
        return [self._ordered[int(i)] for i in self.tree.selection()]

    def _set_hover(self, row):
        t = self.tree
        if self.hover_row and t.exists(self.hover_row):
            t.item(self.hover_row, tags=())
        self.hover_row = row
        if row and t.exists(row) and row not in t.selection():
            t.item(row, tags=("hover",))

    def _on_motion(self, e):
        row = self.tree.identify_row(e.y)
        if row != self.hover_row:
            self._set_hover(row)

    def _on_double(self, e=None):
        if e is not None and getattr(e, "type", None) == tk.EventType.ButtonPress:
            if self.tree.identify_region(e.x, e.y) == "heading":
                return
        sel = self.selected()
        if len(sel) != 1:
            return
        if sel[0].is_dir:
            self.app.open_dir(self, sel[0])
        else:
            self.app.transfer_from(self)

    def _on_menu(self, e):
        row = self.tree.identify_row(e.y)
        if not row:
            self.tree.selection_remove(self.tree.selection())   # 空白部分 → 今のフォルダーが対象
        elif row not in self.tree.selection():
            self.tree.selection_set(row)
        self.tree.focus_set()
        try:
            self.menu.tk_popup(e.x_root, e.y_root)
        finally:
            self.menu.grab_release()


# ================================================================ アプリ本体
class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.client = FtpClient()
        self.settings = load_settings()
        self.jobs: queue.Queue = queue.Queue()
        self.ui_q: queue.Queue = queue.Queue()
        self.busy = 0
        self.transfer_info = None  # (verb, name, done, total)
        self.transferring = False
        self.remote_path = "/"
        self.local_path = self._restore_local_path(self.settings.get("local_path"))

        self._setup_style()
        self.icons = make_icons(root)
        root.iconphoto(True, self.icons["app"])
        self.snack = Snackbar(root)
        self._build()
        threading.Thread(target=self._worker, daemon=True).start()
        root.after(40, self._poll)
        self.refresh_local()
        self._update_connected_ui()
        root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ---------------- スタイル
    def _setup_style(self):
        st = ttk.Style(self.root)
        st.theme_use("clam")
        st.layout("M.Treeview", [("M.Treeview.treearea", {"sticky": "nswe"})])
        st.configure("M.Treeview", background=SURFACE, fieldbackground=SURFACE, foreground=ON_SURFACE,
                     borderwidth=0, relief="flat", rowheight=px(30), font=(FONT, 10))
        st.map("M.Treeview", background=[("selected", PRIMARY_CONTAINER)],
               foreground=[("selected", ON_PRIMARY_CONTAINER)])
        st.configure("M.Treeview.Heading", background=SURFACE, foreground=ON_SURFACE_VAR,
                     relief="flat", borderwidth=0, font=(FONT, 9, "bold"), padding=(px(6), px(6)))
        st.map("M.Treeview.Heading", background=[("active", HOVER)])
        st.layout("M.Vertical.TScrollbar", [("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
            ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])
        st.configure("M.Vertical.TScrollbar", troughcolor=SURFACE, background="#C4C7C5",
                     bordercolor=SURFACE, lightcolor="#C4C7C5", darkcolor="#C4C7C5",
                     arrowsize=px(8), width=px(8), relief="flat", borderwidth=0)
        st.map("M.Vertical.TScrollbar", background=[("active", "#9AA0A6")],
               lightcolor=[("active", "#9AA0A6")], darkcolor=[("active", "#9AA0A6")])
        st.configure("M.TCombobox", fieldbackground=SURFACE, background=SURFACE, foreground=ON_SURFACE,
                     bordercolor=OUTLINE, lightcolor=SURFACE, darkcolor=SURFACE, arrowcolor=ON_SURFACE_VAR,
                     padding=(px(8), px(6)), relief="flat")
        st.map("M.TCombobox", bordercolor=[("focus", PRIMARY)], fieldbackground=[("readonly", SURFACE)],
               selectbackground=[("readonly", SURFACE)], selectforeground=[("readonly", ON_SURFACE)])
        self.root.option_add("*TCombobox*Listbox.font", (FONT, 10))
        self.root.option_add("*TCombobox*Listbox.selectBackground", PRIMARY_CONTAINER)
        self.root.option_add("*TCombobox*Listbox.selectForeground", ON_SURFACE)

    # ---------------- 画面
    def _build(self):
        r = self.root
        r.configure(bg=BG)
        g = px(16)

        # App bar
        bar = tk.Frame(r, bg=BG)
        bar.pack(fill="x", padx=g, pady=(px(10), px(4)))
        tk.Label(bar, text=APP_NAME, bg=BG, fg=ON_SURFACE, font=(FONT, 17, "bold")).pack(side="left")
        self.chip = tk.Canvas(bar, height=px(30), width=px(10), bg=BG, highlightthickness=0)
        self.chip.pack(side="right")

        # 接続カード
        cc = RoundCard(r)
        cc.pack(fill="x", padx=g, pady=(px(4), px(10)))
        cb = cc.body
        s = self.settings
        self.f_host = MField(cb, "ホスト", width=26, value=s.get("host", ""))
        self.f_port = MField(cb, "ポート", width=6, value=str(s.get("port", 21)))
        self.f_user = MField(cb, "ユーザー名", width=16, value=s.get("user", ""))
        self.f_pass = MField(cb, "パスワード", width=16, show="●",
                             value=unprotect_password(s.get("password", "")))
        for i, f in enumerate((self.f_host, self.f_port, self.f_user, self.f_pass)):
            f.grid(row=0, column=i, sticky="ew", padx=(0, px(10)))
            f.entry.bind("<Return>", lambda e: self.toggle_connect())
        cb.grid_columnconfigure(0, weight=3)
        cb.grid_columnconfigure(2, weight=2)
        cb.grid_columnconfigure(3, weight=2)

        opt = tk.Frame(cb, bg=SURFACE)
        opt.grid(row=0, column=4, sticky="s", padx=(px(4), px(12)))
        tk.Label(opt, text="文字コード", bg=SURFACE, fg=ON_SURFACE_VAR, font=(FONT, 9)).grid(row=0, column=0, sticky="w")
        self.enc = ttk.Combobox(opt, values=["UTF-8", "Shift_JIS"], width=9, state="readonly",
                                style="M.TCombobox", font=(FONT, 10))
        self.enc.set(s.get("encoding", "UTF-8"))
        self.enc.grid(row=1, column=0, padx=(0, px(14)))
        tk.Label(opt, text="FTPS (TLS)", bg=SURFACE, fg=ON_SURFACE_VAR, font=(FONT, 9)).grid(row=0, column=1, sticky="w")
        self.sw_tls = MSwitch(opt, value=bool(s.get("tls", False)))
        self.sw_tls.grid(row=1, column=1, sticky="w", pady=px(4), padx=(0, px(14)))
        tk.Label(opt, text="パスワード保存", bg=SURFACE, fg=ON_SURFACE_VAR, font=(FONT, 9)).grid(row=0, column=2, sticky="w")
        self.sw_savepw = MSwitch(opt, value=bool(s.get("save_password", True)), command=self._on_savepw)
        self.sw_savepw.grid(row=1, column=2, sticky="w", pady=px(4))

        self.btn_connect = MButton(cb, "接続", self.toggle_connect, kind="filled", min_width=96)
        self.btn_connect.grid(row=0, column=5, sticky="s", pady=(0, px(1)))
        cc.autosize()

        # 2ペイン
        main = tk.Frame(r, bg=BG)
        main.pack(fill="both", expand=True, padx=g)
        self.local = FilePane(main, "このPC", self.icons, self, remote=False)
        self.remote = FilePane(main, "サーバー", self.icons, self, remote=True)
        mid = tk.Frame(main, bg=BG)
        self.local.card.grid(row=0, column=0, sticky="nsew")
        mid.grid(row=0, column=1, sticky="ns", padx=px(10))
        self.remote.card.grid(row=0, column=2, sticky="nsew")
        main.grid_columnconfigure(0, weight=1, uniform="p")
        main.grid_columnconfigure(2, weight=1, uniform="p")
        main.grid_rowconfigure(0, weight=1)
        inner = tk.Frame(mid, bg=BG)
        inner.place(relx=0.5, rely=0.5, anchor="center")
        mid.configure(width=px(48))
        self.btn_up = MButton(inner, kind="icon", icon=glyph("right"), tooltip="アップロード",
                              command=lambda: self.transfer_from(self.local), bg=BG)
        self.btn_down = MButton(inner, kind="icon", icon=glyph("left"), tooltip="ダウンロード",
                                command=lambda: self.transfer_from(self.remote), bg=BG)
        for b in (self.btn_up, self.btn_down):
            b.colors = (lambda bb: lambda: (
                (PRIMARY_CONTAINER_HOVER if bb.hover else PRIMARY_CONTAINER) if bb.enabled else DISABLED_BG,
                ON_PRIMARY_CONTAINER if bb.enabled else DISABLED_FG))(b)
            b.draw()
        self.btn_up.pack(pady=px(6))
        self.btn_down.pack(pady=px(6))

        # ステータス
        stat = tk.Frame(r, bg=BG)
        stat.pack(fill="x", padx=g, pady=(px(8), px(10)))
        self.progress = LinearProgress(stat)
        self.progress.pack(fill="x", pady=(0, px(6)))
        row = tk.Frame(stat, bg=BG)
        row.pack(fill="x")
        self.status = tk.Label(row, text="準備完了", bg=BG, fg=ON_SURFACE_VAR, font=(FONT, 9), anchor="w")
        self.status.pack(side="left", fill="x", expand=True)
        self.btn_cancel = MButton(row, "キャンセル", self.cancel_transfer, kind="text", bg=BG)

        r.bind_all("<Control-l>", lambda e: (self.local.path_entry.focus_set(), self.local.path_entry.select_range(0, "end")))

    def _draw_chip(self):
        c = self.chip
        c.delete("all")
        if self.client.connected:
            text, dot, fill, fg = f"接続中  {self.f_host.get()}", SUCCESS, "#E6F4EA", "#0D652D"
            if self.sw_tls.value:
                text = "🔒 " + text
        else:
            text, dot, fill, fg = "未接続", "#9AA0A6", SURFACE, ON_SURFACE_VAR
        font = tkfont.Font(family=FONT, size=9, weight="bold")
        w = font.measure(text) + px(36)
        h = px(30)
        c.configure(width=w)
        round_rect(c, 1, 1, w - 1, h - 1, h / 2, fill=fill, outline=OUTLINE if not self.client.connected else fill)
        c.create_oval(px(12), h / 2 - px(4), px(20), h / 2 + px(4), fill=dot, outline=dot)
        c.create_text(px(26), h / 2, text=text, anchor="w", fill=fg, font=font)

    def _update_connected_ui(self):
        on = self.client.connected
        self.btn_connect.set_text("切断" if on else "接続", kind="tonal" if on else "filled")
        for k, b in self.remote.buttons.items():
            b.set_enabled(on)
        self.btn_up.set_enabled(on)
        self.btn_down.set_enabled(on)
        if not on:
            self.remote.set_entries("", [], placeholder="サーバーに接続すると、ここにファイルが表示されます")
        self._draw_chip()

    # ---------------- ワーカー
    def _worker(self):
        while True:
            fn, ok, err = self.jobs.get()
            try:
                res = fn()
                self.ui_q.put((ok, res))
            except BaseException as e:  # noqa
                self.ui_q.put((err, e))

    def run(self, fn, ok=None, err=None, busy_text=None):
        self.busy += 1
        if busy_text:
            self.status.configure(text=busy_text, fg=ON_SURFACE_VAR)

        def done(cb):
            def inner(v):
                self.busy -= 1
                if cb:
                    cb(v)
                elif isinstance(v, BaseException):
                    self.show_error(v)
                self._refresh_progress()
            return inner

        self.jobs.put((fn, done(ok), done(err or self.show_error)))
        self._refresh_progress()

    def _poll(self):
        while True:
            try:
                cb, v = self.ui_q.get_nowait()
            except queue.Empty:
                break
            try:
                cb(v)
            except Exception as e:  # UIコールバックの例外でポーリングを止めない
                self.show_error(e)
        if self.transferring and self.transfer_info:
            verb, name, done, total = self.transfer_info
            pct = done / total if total else 0
            self.progress.set(pct if total else None)
            size = f"{human_size(done)} / {human_size(total)}" if total else human_size(done)
            self.status.configure(text=f"{verb}: {name}   {pct * 100:.0f}%   {size}", fg=ON_SURFACE)
        self.root.after(60, self._poll)

    def _refresh_progress(self):
        if self.transferring:
            return
        self.progress.set(None if self.busy > 0 else -1)

    def show_error(self, e: BaseException):
        msg = str(e) or e.__class__.__name__
        if isinstance(e, ftplib.error_perm):
            msg = "サーバーが拒否しました: " + msg
        elif isinstance(e, (TimeoutError, ConnectionError, OSError)) and not isinstance(e, ftplib.Error):
            msg = "通信エラー: " + msg
        self.status.configure(text=msg.splitlines()[0][:200], fg=ERROR)
        self.snack.show(msg, error=True)
        if isinstance(e, (EOFError, ConnectionError)) or str(e).startswith("421"):
            self.client.ftp = None
            self._update_connected_ui()

    def ok(self, text: str):
        self.status.configure(text=text, fg=ON_SURFACE_VAR)
        self.snack.show(text)

    def _on_savepw(self, on: bool):
        self.settings["save_password"] = on
        if not on:
            self.settings.pop("password", None)
            self.snack.show("保存していたパスワードを削除しました")
        save_settings(self.settings)

    # ---------------- 接続
    def toggle_connect(self):
        if self.client.connected:
            self.client.cancel_flag = True
            self.run(self.client.close, ok=lambda _: (self._update_connected_ui(), self.ok("切断しました")))
            return
        host = self.f_host.get()
        if host.startswith(("ftp://", "ftps://")):
            if host.startswith("ftps://"):
                self.sw_tls.value = True
                self.sw_tls.draw()
            host = host.split("://", 1)[1].rstrip("/")
            self.f_host.var.set(host)
        if not host:
            self.snack.show("ホスト名を入力してください", error=True)
            self.f_host.entry.focus_set()
            return
        try:
            port = int(self.f_port.get() or 21)
        except ValueError:
            self.snack.show("ポート番号が正しくありません", error=True)
            return
        user, pw, tls = self.f_user.get(), self.f_pass.var.get(), self.sw_tls.value
        enc = "cp932" if self.enc.get() == "Shift_JIS" else "utf-8"
        self.settings.update(host=host, port=port, user=user, tls=tls, encoding=self.enc.get(),
                             save_password=self.sw_savepw.value)
        if self.sw_savepw.value and pw:
            try:
                self.settings["password"] = protect_password(pw)
            except OSError:
                self.settings.pop("password", None)
        else:
            self.settings.pop("password", None)
        save_settings(self.settings)
        self.btn_connect.set_enabled(False)

        def job(verify=True):
            self.client.cancel_flag = False
            self.client.connect(host, port, user, pw, tls=tls, encoding=enc, verify=verify)
            path = self.client.pwd() or "/"
            return path, self.client.listdir(path)

        def on_ok(res):
            self.btn_connect.set_enabled(True)
            path, entries = res
            self.remote_path = path
            self._update_connected_ui()
            self.remote.set_entries(path, entries)
            self.ok(f"{host} に接続しました")

        def on_err(e):
            self.btn_connect.set_enabled(True)
            self.client.ftp = None
            if isinstance(e, ssl.SSLCertVerificationError):
                if confirm(self.root, "証明書を確認できません",
                           f"{host} の証明書を検証できませんでした（自己署名証明書など）。\n"
                           "通信は暗号化されますが、接続先が本物かは保証されません。続行しますか？",
                           ok="続行"):
                    self.btn_connect.set_enabled(False)
                    self.run(lambda: job(False), on_ok, on_err, busy_text="接続しています…")
                return
            if isinstance(e, ftplib.error_perm) and str(e).startswith("530"):
                self.status.configure(text="ログインできませんでした", fg=ERROR)
                self.snack.show("ログインできませんでした。ユーザー名とパスワードを確認してください。", error=True)
                return
            self.show_error(e)
            self._update_connected_ui()

        self.run(job, on_ok, on_err, busy_text=f"{host} に接続しています…")

    # ---------------- 一覧
    def refresh_local(self, path: str | None = None):
        path = self.local_path if path is None else path
        try:
            entries = list_drives() if path == "" else list_local(path)
        except OSError as e:
            self.show_error(e)
            return
        self.local_path = path
        self.local.set_entries(path or "PC", entries)
        if self.settings.get("local_path") != path:   # 移動するたびに保存
            self.settings["local_path"] = path
            save_settings(self.settings)

    @staticmethod
    def _restore_local_path(saved: str | None) -> str:
        """前回のフォルダーを復元。消えていれば（USBを外した等）存在する親フォルダーまでさかのぼる"""
        home = os.path.expanduser("~")
        if saved is None:
            return home
        if saved == "":
            return "" if sys.platform == "win32" else home   # ドライブ一覧
        p = saved
        while p and not os.path.isdir(p):
            parent = os.path.dirname(p.rstrip("\\/"))
            if parent == p or not parent:
                return home
            p = parent
        return p or home

    def refresh_remote(self, path: str | None = None, quiet=True):
        if not self.client.connected:
            return
        path = self.remote_path if path is None else path

        def on_ok(entries):
            self.remote_path = path
            self.remote.set_entries(path, entries)
            if not quiet:
                self.status.configure(text="更新しました", fg=ON_SURFACE_VAR)

        self.run(lambda: self.client.listdir(path), on_ok)

    def local_home(self):
        self.refresh_local("" if sys.platform == "win32" and self.local_path == os.path.expanduser("~")
                           else os.path.expanduser("~"))

    def navigate(self, pane: FilePane, path: str):
        if pane is self.local:
            if path in ("", "PC"):
                self.refresh_local("")
            elif os.path.isdir(os.path.expandvars(path)):
                self.refresh_local(os.path.abspath(os.path.expandvars(path)))
            else:
                self.snack.show("フォルダーが見つかりません", error=True)
                pane.path_var.set(self.local_path or "PC")
        else:
            self.refresh_remote(path or "/")

    def open_dir(self, pane: FilePane, e: Entry):
        if pane is self.local:
            self.refresh_local(e.name if self.local_path == "" else os.path.join(self.local_path, e.name))
        else:
            self.refresh_remote(posixpath.join(self.remote_path, e.name))

    def go_up(self, pane: FilePane):
        if pane is self.local:
            if self.local_path == "":
                return
            parent = os.path.dirname(self.local_path.rstrip("\\/"))
            if sys.platform == "win32" and (parent == self.local_path or len(self.local_path) <= 3):
                self.refresh_local("")
            else:
                self.refresh_local(parent or "/")
        else:
            p = self.remote_path.rstrip("/")
            self.refresh_remote(posixpath.dirname(p) or "/")

    def open_in_explorer(self):
        """今表示している PC 側のフォルダーをエクスプローラーで開く（項目を右クリックした場合はそれを選択した状態で）"""
        folder = self.local_path
        sel = self.local.selected()
        try:
            if sys.platform == "win32":
                if folder == "":
                    target = os.path.join(sel[0].name) if len(sel) == 1 else None
                    subprocess.Popen(["explorer", target or "shell:MyComputerFolder"])
                elif len(sel) == 1:
                    subprocess.Popen(["explorer", "/select,", os.path.join(folder, sel[0].name)])
                else:
                    os.startfile(folder)
            else:
                subprocess.Popen(["xdg-open", folder or "/"])
        except OSError as e:
            self.show_error(e)

    # ---------------- ツールバー
    def pane_action(self, pane: FilePane, action: str):
        if pane is self.remote and not self.client.connected:
            return
        if action == "up":
            self.go_up(pane)
        elif action == "refresh":
            self.refresh_local() if pane is self.local else self.refresh_remote(quiet=False)
        elif action == "newfolder":
            self.new_folder(pane)
        elif action == "rename":
            self.rename(pane)
        elif action == "delete":
            self.delete(pane)

    def new_folder(self, pane: FilePane):
        if pane is self.local and self.local_path == "":
            return
        name = ask_text(self.root, "新しいフォルダー", "新しいフォルダー")
        if not name:
            return
        if pane is self.local:
            try:
                os.mkdir(os.path.join(self.local_path, name))
                self.refresh_local()
            except OSError as e:
                self.show_error(e)
        else:
            path = posixpath.join(self.remote_path, name)
            self.run(lambda: self.client.mkdir(path), lambda _: self.refresh_remote())

    def rename(self, pane: FilePane):
        sel = pane.selected()
        if len(sel) != 1 or (pane is self.local and self.local_path == ""):
            if len(sel) > 1:
                self.snack.show("名前の変更は1つずつ選んでください")
            return
        old = sel[0].name
        new = ask_text(self.root, "名前の変更", old)
        if not new or new == old:
            return
        if pane is self.local:
            try:
                os.rename(os.path.join(self.local_path, old), os.path.join(self.local_path, new))
                self.refresh_local()
            except OSError as e:
                self.show_error(e)
        else:
            a, b = posixpath.join(self.remote_path, old), posixpath.join(self.remote_path, new)
            self.run(lambda: self.client.rename(a, b), lambda _: self.refresh_remote())

    def delete(self, pane: FilePane):
        sel = pane.selected()
        if not sel or (pane is self.local and self.local_path == ""):
            return
        what = f"「{sel[0].name}」" if len(sel) == 1 else f"{len(sel)} 個の項目"
        if pane is self.local:
            if not confirm(self.root, "削除", f"{what}をごみ箱へ移動しますか？", ok="削除", danger=True):
                return
            try:
                recycle([os.path.join(self.local_path, e.name) for e in sel])
                self.ok(f"{what}をごみ箱へ移動しました")
            except OSError as e:
                self.show_error(e)
            self.refresh_local()
        else:
            has_dir = any(e.is_dir for e in sel)
            msg = f"サーバー上の{what}を削除しますか？\nこの操作は元に戻せません。"
            if has_dir:
                msg += "\nフォルダーは中身ごと削除されます。"
            if not confirm(self.root, "削除", msg, ok="削除", danger=True):
                return
            base = self.remote_path

            def job():
                self.client.cancel_flag = False
                for e in sel:
                    self.client.delete(posixpath.join(base, e.name), e.is_dir)

            def after(v):
                if isinstance(v, BaseException):
                    self.show_error(v)
                else:
                    self.ok(f"{what}を削除しました")
                self.refresh_remote()

            self.run(job, after, after, busy_text="削除しています…")

    # ---------------- 転送
    def transfer_from(self, pane: FilePane):
        if not self.client.connected:
            self.snack.show("先にサーバーへ接続してください")
            return
        if self.transferring:
            self.snack.show("転送中です。完了までお待ちください")
            return
        sel = pane.selected()
        if not sel:
            self.snack.show("転送するファイルを選んでください")
            return
        if pane is self.local:
            if self.local_path == "":
                return
            existing = {e.name for e in self.remote.entries}
            conflicts = [e.name for e in sel if e.name in existing]
            if conflicts and not self._confirm_overwrite(conflicts, "サーバー"):
                return
            src, dst = self.local_path, self.remote_path
            items = [(os.path.join(src, e.name), posixpath.join(dst, e.name), e) for e in sel]
            verb = "アップロード"

            def job():
                for l, r, _ in items:
                    self.client.upload(l, r, self._progress_cb(verb))
        else:
            if self.local_path == "":
                self.snack.show("保存先のフォルダーを左側で開いてください")
                return
            existing = {e.name for e in self.local.entries}
            conflicts = [e.name for e in sel if e.name in existing]
            if conflicts and not self._confirm_overwrite(conflicts, "このPC"):
                return
            src, dst = self.remote_path, self.local_path
            items = [(posixpath.join(src, e.name), os.path.join(dst, e.name), e) for e in sel]
            verb = "ダウンロード"

            def job():
                for r, l, e in items:
                    self.client.download(r, l, e.is_dir, self._progress_cb(verb), e.size)

        def wrapped():
            self.client.cancel_flag = False
            try:
                job()
            except Cancelled:
                self.client.cancel_flag = False
                self.client.reconnect(self.remote_path)
                return "cancel"
            return "ok"

        def finish(v):
            self.transferring = False
            self.transfer_info = None
            self.btn_cancel.pack_forget()
            self.progress.set(-1)
            if isinstance(v, BaseException):
                self.show_error(v)
            elif v == "cancel":
                self.ok("転送をキャンセルしました")
            else:
                n = len(items)
                self.ok(f"{verb}が完了しました（{n} 項目）")
            self.refresh_local()
            self.refresh_remote()

        self.transferring = True
        self.transfer_info = (verb, "準備中", 0, 0)
        self.btn_cancel.pack(side="right")
        self.run(wrapped, finish, finish)

    def open_item(self, pane: FilePane):
        """選択したファイルを関連付けられたアプリで開く（フォルダーなら中へ移動）"""
        sel = pane.selected()
        if len(sel) != 1:
            self.snack.show("開くファイルを1つ選んでください" if sel else "開くファイルを選んでください")
            return
        e = sel[0]
        if e.is_dir:
            self.open_dir(pane, e)
            return
        if pane is self.local:
            self._launch(os.path.join(self.local_path, e.name))
            return
        # サーバー側: 一時フォルダーにダウンロードしてから開く
        if not self.client.connected:
            return
        if self.transferring:
            self.snack.show("転送中です。完了までお待ちください")
            return
        import hashlib
        import tempfile
        key = hashlib.md5(f"{self.f_host.get()}{self.remote_path}".encode()).hexdigest()[:8]
        tmpdir = os.path.join(tempfile.gettempdir(), APP_NAME, key)
        os.makedirs(tmpdir, exist_ok=True)
        remote = posixpath.join(self.remote_path, e.name)
        local = os.path.join(tmpdir, e.name)

        def job():
            self.client.cancel_flag = False
            try:
                self.client.download(remote, local, False, self._progress_cb("開く準備"), e.size)
            except Cancelled:
                self.client.cancel_flag = False
                self.client.reconnect(self.remote_path)
                return None
            return local

        def finish(v):
            self.transferring = False
            self.transfer_info = None
            self.btn_cancel.pack_forget()
            self.progress.set(-1)
            if isinstance(v, BaseException):
                self.show_error(v)
            elif v is None:
                self.ok("キャンセルしました")
            else:
                self.status.configure(text=f"{e.name} を開きました（一時コピー）", fg=ON_SURFACE_VAR)
                self.snack.show(f"{e.name} を開きました。\nこれは一時コピーなので、編集してもサーバーには反映されません。")
                self._launch(v)

        self.transferring = True
        self.transfer_info = ("開く準備", e.name, 0, e.size)
        self.btn_cancel.pack(side="right")
        self.run(job, finish, finish)

    def _launch(self, path: str):
        try:
            if sys.platform == "win32":
                os.startfile(path)
            else:
                subprocess.Popen(["xdg-open", path])
        except OSError as e:
            self.show_error(e)

    def _progress_cb(self, verb):
        def cb(name, done, total):
            self.transfer_info = (verb, name, done, total)
        return cb

    def _confirm_overwrite(self, names, where):
        listed = "、".join(names[:3]) + (f" ほか {len(names) - 3} 件" if len(names) > 3 else "")
        return confirm(self.root, "上書きの確認", f"{where}に同じ名前の項目があります。\n{listed}\n\n上書きしますか？",
                       ok="上書き")

    def cancel_transfer(self):
        if self.transferring:
            self.client.cancel_flag = True
            self.status.configure(text="キャンセルしています…", fg=ON_SURFACE_VAR)

    # ---------------- 終了
    def on_close(self):
        self.settings["local_path"] = self.local_path
        save_settings(self.settings)
        self.client.cancel_flag = True
        threading.Thread(target=self.client.close, daemon=True).start()
        self.root.destroy()


def create_app() -> tuple[tk.Tk, "App"]:
    global S, ICON_FONT, FONT
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass
        try:  # タスクバーでPythonではなく独自アイコンとして扱う
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("SimpleFTP.App")
        except Exception:
            pass
    root = tk.Tk()
    root.withdraw()
    S = root.winfo_fpixels("1i") / 96.0
    fams = set(tkfont.families(root))
    for f in ("Segoe Fluent Icons", "Segoe MDL2 Assets"):
        if f in fams:
            ICON_FONT = f
            break
    # メイリオ優先（日本語版 Windows ではフォント名が「メイリオ」で返ることがあるので両方見る）
    for f in ("Meiryo", "メイリオ", "Meiryo UI", "Yu Gothic UI", "Segoe UI", "Noto Sans CJK JP"):
        if f in fams:
            FONT = f
            break
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
        try:
            tkfont.nametofont(name).configure(family=FONT, size=10)
        except tk.TclError:
            pass
    root.title(APP_NAME)
    root.geometry(f"{px(1180)}x{px(740)}")
    root.minsize(px(900), px(560))
    app = App(root)
    root.deiconify()
    apply_titlebar(root, BG)
    return root, app


def main():
    root, _ = create_app()
    root.mainloop()


if __name__ == "__main__":
    main()
