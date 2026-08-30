# -*- coding: utf-8 -*-
"""Claude 통합 에이전트 관리 콘솔 (오버레이형)

한 화면에서 여러 AI 콘솔(claude/ollama/…)을 만들고 전환·관리한다.
각 콘솔은 conhost 독립 창으로 띄우되, 관리창에 '종속(owner)'시켜 오른쪽
영역에 딱 겹쳐 띄운다. 콘솔이 진짜 독립 창이라 입력·포커스·커서가 전부
네이티브 → 매끄럽고, SetParent 임베드 시절의 포커스/크래시 문제가 없다.

기능:
- 위: 프로젝트 + 도구(AI) + 주제로 새 에이전트 / 이어서(claude --continue/--resume) / 옵션
- 왼쪽: 에이전트 목록(단축번호·상태·경과, 드래그 순서변경), 우클릭 메뉴, 종료/제거/정리
- 오른쪽: 정보바(분할 1/2/4) + 오버레이 콘솔(타일 보기)
- 단축키: Ctrl+N 주제, Ctrl+Tab/Shift+Tab 전환, Ctrl+1~9 선택, Ctrl+W 종료, F11 전체보기
- 폴더/도구 인앱 관리, 최근 주제, 복제, 드래그 분할, 세션 복구, 토스트 알림, 콘솔 글꼴 개선
"""
import os
import json
import time
import shutil
import winreg
import subprocess
import ctypes
from ctypes import wintypes
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog

# ---- Win32 상수 ----
CREATE_NEW_CONSOLE = 0x00000010
CREATE_NO_WINDOW = 0x08000000
GWL_STYLE = -16
GWLP_HWNDPARENT = -8         # top-level의 '오너' 설정(부모 아님 → 입력 네이티브 유지)
GA_ROOT = 2
WS_VISIBLE = 0x10000000
WS_POPUP = 0x80000000
WS_CAPTION = 0x00C00000
WS_THICKFRAME = 0x00040000
WS_BORDER = 0x00800000
WS_SYSMENU = 0x00080000
WS_MINIMIZEBOX = 0x00020000
WS_MAXIMIZEBOX = 0x00010000
WS_OVERLAPPEDWINDOW = 0x00CF0000
SW_HIDE = 0
SW_SHOW = 5
SW_RESTORE = 9
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020

# ---- 다크 팔레트(sv-ttk와 어울리게) ----
DARK_BG = "#1c1c1c"
DARK_FG = "#e6e6e6"
DARK_SEL = "#2f5fbf"
DARK_SUBTLE = "#9aa0a6"
CONSOLE_BG = "#0c0c0c"
RUN_FG = "#3fb950"
DEAD_FG = "#7d8590"
TOAST_BG = "#2b2b2b"
DIVIDER = "#5a5a5a"      # 타일 사이 구분선 색(검은 콘솔 위에서 보이게)
GAP = 6                  # 구분선 두께 = 잡고 끌 수 있는 폭
CIRCLED = "①②③④⑤⑥⑦⑧⑨"

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECTS_FILE = os.path.join(HERE, "projects.json")
TOOLS_FILE = os.path.join(HERE, "tools.json")
CONFIG_FILE = os.path.join(HERE, "console_config.json")
CUSTOM_LABEL = "📁 직접 경로 선택…"

DEFAULT_PROJECTS = [
    {"name": "홈",            "path": r"C:\Users\USER"},
    {"name": "내 프로젝트",        "path": r"C:\Users\USER\Desktop\창업\myproject"},
    {"name": "주식봇",        "path": r"C:\Users\USER\Desktop\창업\주식봇"},
    {"name": "코인봇",        "path": r"C:\Users\USER\Desktop\창업\코인봇"},
    {"name": "중국어 공부",   "path": r"C:\Users\USER\Desktop\중국어 공부"},
    {"name": "창업 폴더 전체", "path": r"C:\Users\USER\Desktop\창업"},
]

# 콘솔 안에서 실행할 'AI 도구'. cmd 빈 문자열이면 그냥 터미널.
DEFAULT_TOOLS = [
    {"name": "Claude",              "cmd": "claude"},
    {"name": "Ollama · qwen2.5:14b", "cmd": "ollama run qwen2.5:14b"},
    {"name": "Ollama · gemma3:27b",  "cmd": "ollama run gemma3:27b",  "heavy": True},
    {"name": "Ollama · qwen2.5:32b", "cmd": "ollama run qwen2.5:32b", "heavy": True},
    {"name": "터미널(cmd)",          "cmd": ""},
]

u = ctypes.windll.user32
u.SetProcessDPIAware()
_EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

u.GetAncestor.restype = wintypes.HWND
u.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
u.SetForegroundWindow.argtypes = [wintypes.HWND]
u.SetForegroundWindow.restype = wintypes.BOOL
_SetWindowLongPtr = getattr(u, "SetWindowLongPtrW", u.SetWindowLongW)
_SetWindowLongPtr.restype = ctypes.c_void_p
_SetWindowLongPtr.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]


def _dark_titlebar(hwnd):
    if not hwnd:
        return
    try:
        dwm = ctypes.windll.dwmapi
        val = ctypes.c_int(1)
        if dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(val), ctypes.sizeof(val)) != 0:
            dwm.DwmSetWindowAttribute(hwnd, 19, ctypes.byref(val), ctypes.sizeof(val))
    except Exception:
        pass


def _dark_titlebar_for(widget):
    try:
        widget.update_idletasks()
        _dark_titlebar(u.GetAncestor(widget.winfo_id(), GA_ROOT))
    except Exception:
        pass


def _style_menu(m):
    try:
        m.configure(bg=DARK_BG, fg=DARK_FG, activebackground=DARK_SEL,
                    activeforeground="white", bd=0, relief="flat")
    except Exception:
        pass


def make_listbox(parent):
    return tk.Listbox(parent, bg=DARK_BG, fg=DARK_FG, selectbackground=DARK_SEL,
                      selectforeground="white", highlightthickness=0, bd=0,
                      activestyle="none", relief="flat", font=("Segoe UI", 10))


class Tip:
    """위젯에 마우스를 올리면 잠깐 뒤 설명 풍선을 띄운다(친화성)."""
    def __init__(self, widget, text):
        self.w = widget
        self.text = text
        self.tip = None
        self._a = None
        widget.bind("<Enter>", self._enter, add="+")
        widget.bind("<Leave>", self._leave, add="+")
        widget.bind("<ButtonPress>", self._leave, add="+")

    def _enter(self, _e=None):
        self._cancel()
        try:
            self._a = self.w.after(450, self._show)
        except Exception:
            pass

    def _show(self):
        if self.tip or not self.text:
            return
        try:
            x = self.w.winfo_rootx() + self.w.winfo_width() // 2
            y = self.w.winfo_rooty() + self.w.winfo_height() + 6
            self.tip = tk.Toplevel(self.w)
            self.tip.overrideredirect(True)
            self.tip.attributes("-topmost", True)
            tk.Label(self.tip, text=self.text, bg="#3a3a3a", fg="white",
                     font=("Segoe UI", 9), padx=8, pady=4, justify="left").pack()
            self.tip.update_idletasks()
            self.tip.geometry(f"+{x - self.tip.winfo_reqwidth() // 2}+{y}")
        except Exception:
            self.tip = None

    def _leave(self, _e=None):
        self._cancel()
        if self.tip:
            try:
                self.tip.destroy()
            except Exception:
                pass
            self.tip = None

    def _cancel(self):
        if self._a:
            try:
                self.w.after_cancel(self._a)
            except Exception:
                pass
            self._a = None


# ---- 콘솔 글꼴 개선(HKCU\Console 기본값, 종료 시 원복) ----
def apply_console_prefs():
    """새 콘솔이 상속할 기본 글꼴을 보기 좋게(Consolas 18) + QuickEdit 끔.
    기존 값을 저장해서 종료 때 되돌린다. 실패하면 조용히 무시(no-op)."""
    try:
        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, "Console")
    except Exception:
        return None
    saved = {}
    # QuickEdit=0(끔). 켜두면 콘솔 안을 클릭/드래그하는 순간 '선택 모드'로
    # 들어가 출력이 통째로 멈춘다(아무 키나 누르면 풀림). 타일로 띄워 클릭 전환이
    # 잦은 이 앱에선 그게 '중간중간 멈춤'의 원인이라 반드시 꺼야 한다.
    new = {"FaceName": (winreg.REG_SZ, "Consolas"),
           "FontFamily": (winreg.REG_DWORD, 0x36),
           "FontWeight": (winreg.REG_DWORD, 400),
           "FontSize": (winreg.REG_DWORD, 18 << 16),
           "QuickEdit": (winreg.REG_DWORD, 0)}
    try:
        for name, (typ, val) in new.items():
            try:
                old = winreg.QueryValueEx(key, name)
            except OSError:
                old = None
            saved[name] = old
            winreg.SetValueEx(key, name, 0, typ, val)
    except Exception:
        pass
    finally:
        try:
            winreg.CloseKey(key)
        except Exception:
            pass
    return saved


def restore_console_prefs(saved):
    if not saved:
        return
    try:
        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, "Console")
    except Exception:
        return
    for name, old in saved.items():
        try:
            if old is None:
                try:
                    winreg.DeleteValue(key, name)
                except OSError:
                    pass
            else:
                val, typ = old
                winreg.SetValueEx(key, name, 0, typ, val)
        except Exception:
            pass
    try:
        winreg.CloseKey(key)
    except Exception:
        pass


def list_consoles():
    """현재 떠 있는 콘솔 창(ConsoleWindowClass) 핸들 집합. 런치 전/후 차집합으로 새 창을 잡는다."""
    s = set()

    def cb(h, _l):
        cn = ctypes.create_unicode_buffer(64)
        u.GetClassNameW(h, cn, 64)
        if cn.value == "ConsoleWindowClass":
            s.add(h)
        return True

    u.EnumWindows(_EnumProc(cb), 0)
    return s


def kill_tree(pid):
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                   creationflags=CREATE_NO_WINDOW, capture_output=True)


def fmt_uptime(ts):
    s = int(max(0, time.time() - ts))
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


def load_projects():
    if not os.path.exists(PROJECTS_FILE):
        save_projects(DEFAULT_PROJECTS)
        return list(DEFAULT_PROJECTS)
    try:
        with open(PROJECTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        out = [d for d in data if "name" in d and "path" in d]
        return out or list(DEFAULT_PROJECTS)
    except Exception:
        return list(DEFAULT_PROJECTS)


def save_projects(projects):
    try:
        with open(PROJECTS_FILE, "w", encoding="utf-8") as f:
            json.dump(projects, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def load_tools():
    if not os.path.exists(TOOLS_FILE):
        save_tools(DEFAULT_TOOLS)
        return [dict(t) for t in DEFAULT_TOOLS]
    try:
        with open(TOOLS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        out = [d for d in data if "name" in d and "cmd" in d]
        return out or [dict(t) for t in DEFAULT_TOOLS]
    except Exception:
        return [dict(t) for t in DEFAULT_TOOLS]


def save_tools(tools):
    try:
        with open(TOOLS_FILE, "w", encoding="utf-8") as f:
            json.dump(tools, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def tool_exe_ok(cmd):
    cmd = (cmd or "").strip()
    if not cmd:
        return True
    return shutil.which(cmd.split()[0]) is not None


def load_config():
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(cfg):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


class App:
    def __init__(self, root):
        self.root = root
        self.counter = 0
        self.agents = {}
        self.active = None
        self.custom_path = None
        self.projects = load_projects()
        self.tools = load_tools()
        self.cfg = load_config()
        self.recent = list(self.cfg.get("recent_topics", []))
        self.maximized = False
        self.layout = int(self.cfg.get("layout", 1)) or 1   # 1 / 2 / 4 분할
        self.split_x = float(self.cfg.get("split_x", 0.5))   # 좌우 분할 비율
        self.split_y = float(self.cfg.get("split_y", 0.5))   # 상하 분할 비율(4칸)
        self._drag_div = None                                # 'v'/'h' 구분선 드래그 중
        self._suppress_fg = False                            # 우클릭 메뉴 중 콘솔 포커스 탈취 방지
        self.slots = []                                      # 타일 슬롯: index=칸, 값=iid 또는 None
        self.folders = {}                                    # 프로젝트명 → 폴더 트리노드 iid
        self._cell_labels = {}                               # 빈 칸 안내 라벨 풀
        self._busy = False
        self._pending = []
        self._mgr_hwnd = None
        self._drag_iid = None
        self._toast_win = None
        self._toast_after = None
        self._console_saved = None
        # 옵션(설정 영속)
        self.opt_topmost = bool(self.cfg.get("topmost", False))
        self.opt_restore = bool(self.cfg.get("restore_session", True))
        self.opt_console_font = bool(self.cfg.get("nice_console", True))

        root.title("Claude 통합 에이전트 관리 콘솔")
        root.geometry(self.cfg.get("geometry", "1180x720"))
        root.minsize(900, 520)
        root.configure(bg=DARK_BG)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        if self.opt_topmost:
            try:
                root.attributes("-topmost", True)
            except Exception:
                pass
        if self.opt_console_font:
            self._console_saved = apply_console_prefs()

        self._build_top()
        self._build_body()
        self._build_menu()
        self._bind_keys()
        self.root.after(80, self._resolve_mgr_hwnd)
        self.root.after(300, self._refresh_loop)
        self.root.after(350, self._tick)
        self.root.after(450, self._restore_sash)
        self.root.after(600, self._restore_session)

    # ================= 콘솔 오버레이(핵심) =================
    def _resolve_mgr_hwnd(self):
        try:
            self._mgr_hwnd = u.GetAncestor(self.host.winfo_id(), GA_ROOT)
        except Exception:
            self._mgr_hwnd = None
        _dark_titlebar(self._mgr_hwnd)

    def _attach(self, hwnd):
        """콘솔(top-level)을 테두리 없는 팝업으로 만들고 관리창에 종속(owner)시킨다.
        부모(child)가 아니라 오너라서 입력/포커스/커서는 전부 네이티브로 동작한다.
        멱등: 이미 맞으면 스타일/FRAMECHANGED 안 건드림(드래그 중 깜빡임 방지)."""
        if not u.IsWindow(hwnd):
            return
        style = u.GetWindowLongW(hwnd, GWL_STYLE)
        want = (style & ~WS_OVERLAPPEDWINDOW & ~WS_CAPTION & ~WS_THICKFRAME
                & ~WS_BORDER & ~WS_SYSMENU & ~WS_MINIMIZEBOX & ~WS_MAXIMIZEBOX) | WS_POPUP | WS_VISIBLE
        if want != style:
            u.SetWindowLongW(hwnd, GWL_STYLE, want)
            u.SetWindowPos(hwnd, 0, 0, 0, 0, 0,
                           SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED | SWP_NOACTIVATE)
        if self._mgr_hwnd:
            try:
                _SetWindowLongPtr(hwnd, GWLP_HWNDPARENT, ctypes.c_void_p(self._mgr_hwnd))
            except Exception:
                pass

    def _strip_frame(self, hwnd):
        """conhost가 포커스 받을 때 되붙이는 테두리(↔ 리사이즈 커서)를 제거.
        실제로 바꿨으면 True(→ 재배치 필요)."""
        if not u.IsWindow(hwnd):
            return False
        style = u.GetWindowLongW(hwnd, GWL_STYLE)
        want = (style & ~WS_OVERLAPPEDWINDOW & ~WS_CAPTION & ~WS_THICKFRAME
                & ~WS_BORDER & ~WS_SYSMENU & ~WS_MINIMIZEBOX & ~WS_MAXIMIZEBOX) | WS_POPUP | WS_VISIBLE
        if want != style:
            u.SetWindowLongW(hwnd, GWL_STYLE, want)
            u.SetWindowPos(hwnd, 0, 0, 0, 0, 0,
                           SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED | SWP_NOACTIVATE)
            return True
        return False

    def _host_rect(self):
        try:
            return (self.host.winfo_rootx(), self.host.winfo_rooty(),
                    max(self.host.winfo_width(), 50), max(self.host.winfo_height(), 50))
        except Exception:
            return None

    def _cells(self, n):
        """n칸용 host-로컬 사각형(1=풀, 2=좌우, 4=2x2). 분할 비율 적용, 사이 GAP."""
        w = max(self.host.winfo_width(), 50)
        h = max(self.host.winfo_height(), 50)
        if n <= 1:
            return [(0, 0, w, h)]
        g = GAP
        cw = int((w - g) * self.split_x)
        if n == 2:
            return [(0, 0, cw, h), (cw + g, 0, w - cw - g, h)]
        ch = int((h - g) * self.split_y)
        return [(0, 0, cw, ch), (cw + g, 0, w - cw - g, ch),
                (0, ch + g, cw, h - ch - g), (cw + g, ch + g, w - cw - g, h - ch - g)]

    def _clamp_ratio(self, v):
        return max(0.15, min(0.85, v))

    def _div_positions(self):
        """현재 구분선의 host-로컬 중심 픽셀 (vx, hy). 단일 모드면 None."""
        if self.layout <= 1:
            return (None, None)
        w = max(self.host.winfo_width(), 50)
        h = max(self.host.winfo_height(), 50)
        vx = int((w - GAP) * self.split_x) + GAP // 2
        if self.layout == 2:
            return (vx, None)
        hy = int((h - GAP) * self.split_y) + GAP // 2
        return (vx, hy)

    def _host_motion(self, e):
        if self._drag_div:
            return
        vx, hy = self._div_positions()
        cur = ""
        if vx is not None and abs(e.x - vx) <= 6:
            cur = "sb_h_double_arrow"
        elif hy is not None and abs(e.y - hy) <= 6:
            cur = "sb_v_double_arrow"
        try:
            self.host.config(cursor=cur)
        except Exception:
            pass

    def _host_press(self, e):
        vx, hy = self._div_positions()
        if vx is not None and abs(e.x - vx) <= 6:
            self._drag_div = "v"
        elif hy is not None and abs(e.y - hy) <= 6:
            self._drag_div = "h"
        else:
            self._drag_div = None

    def _host_drag(self, e):
        if not self._drag_div:
            return
        w = max(self.host.winfo_width(), 50)
        h = max(self.host.winfo_height(), 50)
        if self._drag_div == "v":
            self.split_x = self._clamp_ratio((e.x - GAP / 2) / max(w - GAP, 1))
        else:
            self.split_y = self._clamp_ratio((e.y - GAP / 2) / max(h - GAP, 1))
        self._relayout()

    def _host_release(self, _e=None):
        self._drag_div = None

    def _visible_iids(self):
        """현재 화면에 실제로 보이는 콘솔 iid. 단일=활성, 타일=슬롯에 든 살아있는 것."""
        if self.layout <= 1:
            alive = [iid for iid in self._children()
                     if self.agents.get(iid) and u.IsWindow(self.agents[iid]["hwnd"])]
            return [self.active] if self.active in alive else alive[:1]
        return [iid for iid in self.slots
                if iid and self.agents.get(iid) and u.IsWindow(self.agents[iid]["hwnd"])]

    def _relayout(self):
        for i, iid in enumerate(self.slots):   # 죽은 슬롯 비움
            if iid and not (self.agents.get(iid) and u.IsWindow(self.agents[iid]["hwnd"])):
                self.slots[i] = None
        if self.layout <= 1:
            self._relayout_single()
        else:
            self._relayout_tiled()

    def _relayout_single(self):
        self._hide_cell_labels()
        a = self.agents.get(self.active)
        shown = None
        if a and u.IsWindow(a["hwnd"]):
            self._hide_placeholder()
            self._attach(a["hwnd"])
            rx, ry = self.host.winfo_rootx(), self.host.winfo_rooty()
            w = max(self.host.winfo_width(), 50)
            h = max(self.host.winfo_height(), 50)
            u.ShowWindow(a["hwnd"], SW_SHOW)
            u.MoveWindow(a["hwnd"], rx, ry, w, h, True)
            shown = self.active
        else:
            self._show_placeholder()
        for iid, b in self.agents.items():
            if iid != shown and u.IsWindow(b["hwnd"]):
                u.ShowWindow(b["hwnd"], SW_HIDE)

    def _relayout_tiled(self):
        self._hide_placeholder()
        cells = self._cells(self.layout)
        rx, ry = self.host.winfo_rootx(), self.host.winfo_rooty()
        shown = set()
        for i, (lx, ly, lw, lh) in enumerate(cells):
            iid = self.slots[i] if i < len(self.slots) else None
            a = self.agents.get(iid) if iid else None
            if a and u.IsWindow(a["hwnd"]):
                self._hide_cell_label(i)
                self._attach(a["hwnd"])
                u.ShowWindow(a["hwnd"], SW_SHOW)
                u.MoveWindow(a["hwnd"], rx + lx, ry + ly, lw, lh, True)
                shown.add(iid)
            else:
                self._show_cell_label(i, lx, ly, lw, lh)
        for j in list(self._cell_labels):       # 안 쓰는 칸 라벨 정리
            if j >= len(cells):
                self._hide_cell_label(j)
        for iid, b in self.agents.items():
            if iid not in shown and u.IsWindow(b["hwnd"]):
                u.ShowWindow(b["hwnd"], SW_HIDE)

    def _cell_label(self, i):
        lbl = self._cell_labels.get(i)
        if lbl is None or not lbl.winfo_exists():
            lbl = tk.Label(self.host, bg=CONSOLE_BG, fg=DARK_SUBTLE, font=("Segoe UI", 10),
                           text=f"{i + 1}번 칸 · 비어 있음\n\n클릭해서 고르거나\n목록에서 끌어다 놓기")
            lbl.bind("<Button-1>", lambda e, i=i: self._pick_for_slot(i, e.x_root, e.y_root))
            self._cell_labels[i] = lbl
        return lbl

    def _assign_slot(self, i, iid):
        """슬롯 i에 에이전트 iid 배치(다른 칸에 있던 같은 건 비워 중복 방지)."""
        while len(self.slots) < self.layout:
            self.slots.append(None)
        self.slots = [None if s == iid else s for s in self.slots]
        self.slots[i] = iid
        self._relayout()

    def _pick_for_slot(self, i, x_root, y_root):
        """빈 칸 클릭 → 그 칸에 넣을 에이전트를 바로 고르는 메뉴."""
        agents = [(iid, self.agents[iid]) for iid in self._children()
                  if self.agents.get(iid) and u.IsWindow(self.agents[iid]["hwnd"])]
        if not agents:
            self._toast("먼저 ‘＋ 새 에이전트’로 콘솔을 만들어줘")
            return
        m = tk.Menu(self.root, tearoff=0)
        _style_menu(m)
        m.add_command(label=f"— {i + 1}번 칸에 넣기 —", state="disabled")
        for iid, a in agents:
            m.add_command(label=f"{a['topic']} · {a['tool']}",
                          command=lambda iid=iid, i=i: self._assign_slot(i, iid))
        m.add_separator()
        m.add_command(label="타일 구성 전체…", command=self.configure_tiles)
        try:
            m.tk_popup(x_root, y_root)
        finally:
            m.grab_release()

    def _drag_release(self, e):
        """목록에서 끌어다 빈 칸 위에 놓으면 그 칸에 배치(드래그-드롭)."""
        iid = self._drag_iid
        self._drag_iid = None
        if not iid or self.layout <= 1 or iid not in self.agents:
            return
        px, py = e.x_root, e.y_root
        for i, lbl in self._cell_labels.items():
            try:
                if not lbl.winfo_ismapped():
                    continue
                x0, y0 = lbl.winfo_rootx(), lbl.winfo_rooty()
                if x0 <= px <= x0 + lbl.winfo_width() and y0 <= py <= y0 + lbl.winfo_height():
                    self._assign_slot(i, iid)
                    self._toast(f"{self.agents[iid]['topic']} → {i + 1}번 칸")
                    break
            except Exception:
                pass

    def _show_cell_label(self, i, x, y, w, h):
        try:
            lbl = self._cell_label(i)
            lbl.place(x=x, y=y, width=w, height=h)
            lbl.lift()
        except Exception:
            pass

    def _hide_cell_label(self, i):
        lbl = self._cell_labels.get(i)
        if lbl is not None:
            try:
                lbl.place_forget()
            except Exception:
                pass

    def _hide_cell_labels(self):
        for i in list(self._cell_labels):
            self._hide_cell_label(i)

    def _fit_active(self):
        self._relayout()

    def _tick(self):
        changed = False
        for iid in self._visible_iids():
            a = self.agents.get(iid)
            if a and u.IsWindow(a["hwnd"]):
                if self._strip_frame(a["hwnd"]):
                    changed = True
        if changed:
            self._relayout()
        self.root.after(250, self._tick)

    # ================= UI =================
    def _build_top(self):
        top = ttk.Frame(self.root, padding=(14, 12))
        top.pack(fill="x")
        self.topbar = top

        ttk.Label(top, text="프로젝트").pack(side="left")
        names = [p["name"] for p in self.projects]
        last = self.cfg.get("last_project")
        self.proj_var = tk.StringVar(value=last if last in names else (names[0] if names else ""))
        self.proj_cb = ttk.Combobox(top, textvariable=self.proj_var, state="readonly",
                                    width=20, values=names + [CUSTOM_LABEL])
        self.proj_cb.pack(side="left", padx=(6, 4))
        self.proj_cb.bind("<<ComboboxSelected>>", self._on_proj_change)
        self._tip(ttk.Button(top, text="폴더 관리", width=9, command=self.manage_projects),
                  "자주 쓰는 프로젝트 폴더 추가/이름변경/삭제/순서").pack(side="left", padx=(0, 14))

        ttk.Label(top, text="도구").pack(side="left")
        tnames = [t["name"] for t in self.tools]
        last_tool = self.cfg.get("last_tool")
        self.tool_var = tk.StringVar(value=last_tool if last_tool in tnames
                                     else (tnames[0] if tnames else ""))
        self.tool_cb = ttk.Combobox(top, textvariable=self.tool_var, state="readonly",
                                    width=18, values=tnames)
        self.tool_cb.pack(side="left", padx=(6, 4))
        self._tip(ttk.Button(top, text="도구 관리", width=9, command=self.manage_tools),
                  "AI 도구(claude·ollama 등) 명령 추가/편집").pack(side="left", padx=(0, 14))

        ttk.Label(top, text="주제").pack(side="left")
        self.topic_var = tk.StringVar()
        e = ttk.Combobox(top, textvariable=self.topic_var, width=20, values=self.recent)
        e.pack(side="left", padx=(6, 12))
        e.bind("<Return>", lambda ev: self.launch())
        self.topic_entry = e

        nb = ttk.Button(top, text="＋ 새 에이전트", style="Accent.TButton", command=self.launch)
        nb.pack(side="left")
        self._tip(nb, "고른 프로젝트 폴더에서 도구(AI)를 새로 띄움  ·  Enter")
        rb = ttk.Menubutton(top, text="이어서 ▾")
        rm = tk.Menu(rb, tearoff=0)
        _style_menu(rm)
        rm.add_command(label="이어서 (최근 대화)", command=lambda: self.launch(resume="continue"))
        rm.add_command(label="골라서 이어서…", command=lambda: self.launch(resume="resume"))
        rb["menu"] = rm
        rb.pack(side="left", padx=(6, 0))
        self._tip(rb, "이 폴더에서 하던 Claude 대화를 이어서 시작\n(--continue: 최근 / --resume: 골라서)")

        # 옵션 메뉴
        self.v_topmost = tk.BooleanVar(value=self.opt_topmost)
        self.v_restore = tk.BooleanVar(value=self.opt_restore)
        self.v_console = tk.BooleanVar(value=self.opt_console_font)
        ob = ttk.Menubutton(top, text="옵션 ▾")
        om = tk.Menu(ob, tearoff=0)
        _style_menu(om)
        om.add_checkbutton(label="항상 위에 표시", variable=self.v_topmost, command=self._opt_topmost)
        om.add_checkbutton(label="시작 시 지난 세션 복구", variable=self.v_restore, command=self._opt_restore)
        om.add_checkbutton(label="콘솔 글꼴 개선(Consolas)", variable=self.v_console, command=self._opt_console)
        ob["menu"] = om
        ob.pack(side="right")
        self._tip(ob, "항상 위 / 세션 자동복구 / 콘솔 글꼴 같은 설정")

    def _tip(self, widget, text):
        Tip(widget, text)
        return widget

    def _reclaim_tk(self, widget=None):
        if widget is not None:
            try:
                widget.focus_set()
            except Exception:
                pass

    def _build_body(self):
        body = ttk.PanedWindow(self.root, orient="horizontal")
        body.pack(fill="both", expand=True, padx=2, pady=(0, 2))
        self.body = body

        left = ttk.Frame(body, padding=(12, 0, 8, 12))
        body.add(left, weight=0)
        self.left_panel = left
        ttk.Label(left, text="에이전트", foreground=DARK_SUBTLE,
                  font=("Segoe UI", 9)).pack(anchor="w", pady=(0, 6))

        self.tree = ttk.Treeview(left, columns=("up",), show="tree headings",
                                 height=20, selectmode="browse")
        self.tree.heading("#0", text="에이전트")
        self.tree.heading("up", text="경과")
        self.tree.column("#0", width=212, anchor="w")
        self.tree.column("up", width=58, anchor="e", stretch=False)
        self.tree.tag_configure("run", foreground=RUN_FG)
        self.tree.tag_configure("dead", foreground=DEAD_FG)
        self.tree.tag_configure("folder", font=("Segoe UI", 10, "bold"))
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda ev: self.show_selected())
        self.tree.bind("<Button-3>", self._popup_menu)
        self.tree.bind("<Double-1>", self._on_double)
        self.tree.bind("<ButtonPress-1>", self._drag_start)
        self.tree.bind("<B1-Motion>", self._drag_motion)
        self.tree.bind("<ButtonRelease-1>", self._drag_release)

        b = ttk.Frame(left)
        b.pack(fill="x", pady=(10, 4))
        self._tip(ttk.Button(b, text="종료", width=7, command=self.kill_selected),
                  "선택한 에이전트 종료 (콘솔만 닫고 목록엔 남김)").pack(side="left")
        self._tip(ttk.Button(b, text="제거", width=7, command=self.remove_selected),
                  "종료 + 목록에서도 삭제").pack(side="left", padx=4)
        self._tip(ttk.Button(b, text="정리", width=7, command=self.clear_dead),
                  "이미 종료된 항목들 목록에서 일괄 삭제").pack(side="left")
        self.status_lbl = ttk.Label(left, text="준비됨", foreground=DARK_SUBTLE, wraplength=240)
        self.status_lbl.pack(anchor="w", pady=(8, 0))

        right = ttk.Frame(body)
        body.add(right, weight=1)
        self.right_panel = right

        info = ttk.Frame(right, padding=(12, 8))
        info.pack(fill="x")
        self.info_lbl = ttk.Label(info, text="에이전트를 선택하면 정보가 여기 표시됩니다.",
                                  foreground=DARK_SUBTLE)
        self.info_lbl.pack(side="left")

        self._tip(ttk.Button(info, text="⛶", width=3, command=self.toggle_max),
                  "전체보기: 목록·상단 숨기고 콘솔만 크게 (F11)").pack(side="right", padx=(0, 8))
        # 분할(타일) 선택
        sb = ttk.Menubutton(info)
        sm = tk.Menu(sb, tearoff=0)
        _style_menu(sm)
        self.v_layout = tk.IntVar(value=self.layout)
        for n, lbl in ((1, "1칸 (단일)"), (2, "2칸 (좌우)"), (4, "4칸 (격자)")):
            sm.add_radiobutton(label=lbl, value=n, variable=self.v_layout,
                               command=lambda n=n: self.set_layout(n))
        sm.add_separator()
        sm.add_command(label="타일 구성…", command=self.configure_tiles)
        sb["menu"] = sm
        sb.pack(side="right", padx=(0, 8))
        self.split_btn = sb
        self._update_layout_label()           # 현재 분할 상태를 버튼에 표시(자동 갱신)
        self._tip(sb, "콘솔을 1·2·4칸으로 나눠 동시에 보기\n빈 칸 클릭/끌어놓기로 채움")

        self._tip(ttk.Button(info, text="종료", width=6,
                  command=lambda: self.kill_selected(self.active)),
                  "이 에이전트(콘솔) 종료").pack(side="right")
        self._tip(ttk.Button(info, text="분리", width=6,
                  command=lambda: self.detach_selected(self.active)),
                  "이 콘솔을 독립 창으로 빼냄").pack(side="right", padx=4)
        self._tip(ttk.Button(info, text="폴더", width=6,
                  command=lambda: self.open_folder(self.active)),
                  "이 에이전트의 작업 폴더 열기").pack(side="right")
        self._tip(ttk.Button(info, text="재시작", width=7,
                  command=lambda: self.restart(self.active)),
                  "이 에이전트를 같은 설정으로 다시 시작").pack(side="right", padx=4)

        self.host = tk.Frame(right, bg=DIVIDER)   # 타일 사이 GAP에 이 색이 비쳐 구분선이 됨
        self.host.pack(fill="both", expand=True, padx=(0, 2), pady=(0, 2))
        self.host.bind("<Configure>", lambda e: self._relayout())
        self.host.bind("<Motion>", self._host_motion)
        self.host.bind("<ButtonPress-1>", self._host_press)
        self.host.bind("<B1-Motion>", self._host_drag)
        self.host.bind("<ButtonRelease-1>", self._host_release)
        self.root.bind("<Configure>", lambda e: self._relayout(), add="+")
        # placeholder가 host를 꽉 채워서 빈/단일 상태는 검정 유지(구분선 색 안 비침)
        self._placeholder = tk.Label(
            self.host, bg=CONSOLE_BG, fg=DARK_SUBTLE, justify="center", font=("Segoe UI", 11),
            text="시작하기\n\n"
                 "①  위에서 프로젝트 폴더 고르기\n"
                 "②  도구(AI) 고르기 — Claude / Ollama …\n"
                 "③  [＋ 새 에이전트] 누르기  (또는 주제 입력 후 Enter)\n\n"
                 "여러 개 동시에 보려면  ‘분할 ▾’  ·  버튼에 마우스를 올리면 설명이 떠")
        self._placeholder.place(x=0, y=0, relwidth=1, relheight=1)

    def _build_menu(self):
        # 메뉴는 우클릭 시점에 그룹 목록까지 반영해 즉석에서 만든다(_agent_menu/_folder_menu)
        pass

    def _group_labels(self):
        """현재 존재하는 그룹 이름들(트리 표시 순서)."""
        out = []
        for k, fid in self.folders.items():
            if self.tree.exists(fid):
                out.append(k)
        return out

    def _agent_menu(self, iid):
        m = tk.Menu(self.root, tearoff=0)
        _style_menu(m)
        m.add_command(label="보기(앞으로)", command=lambda: self.show_selected())
        m.add_command(label="이름 변경…", command=lambda: self.rename(iid))
        m.add_command(label="폴더 열기", command=lambda: self.open_folder(iid))
        gm = tk.Menu(m, tearoff=0)
        _style_menu(gm)
        cur = self.agents.get(iid, {}).get("group")
        for label in self._group_labels():
            gm.add_command(label=("● " if label == cur else "    ") + label,
                           command=lambda l=label, iid=iid: self.move_to_group(iid, l))
        if self._group_labels():
            gm.add_separator()
        gm.add_command(label="＋ 새 그룹…", command=lambda iid=iid: self.move_to_new_group(iid))
        m.add_cascade(label="그룹 이동", menu=gm)
        m.add_separator()
        m.add_command(label="복제(같은 폴더·주제로)", command=lambda: self.duplicate(iid))
        m.add_command(label="재시작", command=lambda: self.restart(iid))
        m.add_command(label="분리(팝아웃)", command=lambda: self.detach_selected(iid))
        m.add_separator()
        m.add_command(label="종료", command=lambda: self.kill_selected(iid))
        m.add_command(label="목록에서 제거", command=lambda: self.remove_selected(iid))
        return m

    def _folder_menu(self, fid):
        m = tk.Menu(self.root, tearoff=0)
        _style_menu(m)
        m.add_command(label="✎ 그룹 이름 변경…", command=lambda: self.rename_folder(fid))
        m.add_separator()
        m.add_command(label="펼치기", command=lambda: self.tree.item(fid, open=True))
        m.add_command(label="접기", command=lambda: self.tree.item(fid, open=False))
        return m

    def _popup_menu(self, e):
        row = self.tree.identify_row(e.y)
        if not row:
            return
        self._suppress_fg = True   # 선택→show_selected가 콘솔로 포커스 뺏어 메뉴가 닫히던 버그 방지
        self.tree.selection_set(row)
        m = self._agent_menu(row) if row in self.agents else self._folder_menu(row)
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()
            self._suppress_fg = False

    # ================= 그룹(폴더) 이름/이동 =================
    def rename_folder(self, fid=None):
        fid = fid or self._sel()
        if not fid:
            return
        cur = next((k for k, v in self.folders.items() if v == fid), None)
        if cur is None:
            return
        new = simpledialog.askstring("그룹 이름 변경", "새 그룹 이름:",
                                     initialvalue=cur, parent=self.root)
        if not new or not new.strip() or new.strip() == cur:
            return
        new = new.strip()
        if new in self.folders and self.folders[new] != fid:   # 같은 이름 → 합치기
            target = self.folders[new]
            for iid in list(self.tree.get_children(fid)):
                self.tree.move(iid, target, "end")
                if iid in self.agents:
                    self.agents[iid]["group"] = new
            self.tree.delete(fid)
        else:
            self.tree.item(fid, text=f"📁 {new}")
            self.folders[new] = fid
            for iid in self.tree.get_children(fid):
                if iid in self.agents:
                    self.agents[iid]["group"] = new
        self.folders.pop(cur, None)
        self._toast(f"그룹 이름: {cur} → {new}")
        self._update_info()

    def move_to_group(self, iid, group):
        if iid not in self.agents:
            return
        group = (group or "기타").strip() or "기타"
        fid = self._folder_for(group)
        self.tree.move(iid, fid, "end")
        self.agents[iid]["group"] = group
        self._prune_folders()
        self.tree.selection_set(iid)
        self.tree.see(iid)
        self._toast(f"{self.agents[iid]['topic']} → 그룹 ‘{group}’")

    def move_to_new_group(self, iid):
        name = simpledialog.askstring("새 그룹", "새 그룹 이름:", parent=self.root)
        if name and name.strip():
            self.move_to_group(iid, name.strip())

    def _on_double(self, e):
        """더블클릭: 에이전트=이름변경, 그룹=이름변경(없으면 펼치기/접기 기본동작)."""
        row = self.tree.identify_row(e.y)
        if not row:
            return
        if row in self.agents:
            self.rename(row)
            return "break"
        self.rename_folder(row)
        return "break"

    def _bind_keys(self):
        r = self.root
        r.bind("<Activate>", self._on_activate)
        r.bind("<Control-n>", lambda e: (self._reclaim_tk(self.topic_entry), "break")[1])
        r.bind("<Control-N>", lambda e: (self._reclaim_tk(self.topic_entry), "break")[1])
        r.bind("<Control-Tab>", lambda e: self._cycle(1))
        r.bind("<Control-Shift-Tab>", lambda e: self._cycle(-1))
        r.bind("<Control-w>", lambda e: self.kill_selected())
        r.bind("<F11>", lambda e: self.toggle_max())
        for i in range(1, 10):
            r.bind(f"<Control-Key-{i}>", lambda e, i=i: self._select_index(i - 1))

    # ================= 드래그 순서변경 =================
    def _drag_start(self, e):
        self._drag_iid = self.tree.identify_row(e.y)

    def _drag_motion(self, e):
        if not self._drag_iid:
            return
        tgt = self.tree.identify_row(e.y)
        if not tgt or tgt == self._drag_iid:
            return
        pa = self.tree.parent(self._drag_iid)      # 같은 폴더 안에서만 순서변경
        if pa and self.tree.parent(tgt) == pa:
            try:
                self.tree.move(self._drag_iid, pa, self.tree.index(tgt))
            except Exception:
                pass

    # ================= 옵션 =================
    def _opt_topmost(self):
        self.opt_topmost = bool(self.v_topmost.get())
        try:
            self.root.attributes("-topmost", self.opt_topmost)
        except Exception:
            pass
        self._toast("항상 위: " + ("켜짐" if self.opt_topmost else "꺼짐"))

    def _opt_restore(self):
        self.opt_restore = bool(self.v_restore.get())
        self._toast("세션 자동복구: " + ("켜짐" if self.opt_restore else "꺼짐"))

    def _opt_console(self):
        self.opt_console_font = bool(self.v_console.get())
        if self.opt_console_font:
            if not self._console_saved:
                self._console_saved = apply_console_prefs()
            self._toast("콘솔 글꼴 개선 켜짐 (새로 여는 콘솔부터 적용)")
        else:
            restore_console_prefs(self._console_saved)
            self._console_saved = None
            self._toast("콘솔 글꼴 원래대로 (새로 여는 콘솔부터)")

    # ================= 토스트 =================
    def _toast(self, msg):
        self.status_lbl.config(text=msg)
        try:
            if self._toast_win is None or not tk.Toplevel.winfo_exists(self._toast_win):
                self._toast_win = tk.Toplevel(self.root)
                self._toast_win.overrideredirect(True)
                self._toast_win.attributes("-topmost", True)
                try:
                    self._toast_win.attributes("-alpha", 0.95)
                except Exception:
                    pass
                self._toast_win.configure(bg=TOAST_BG)
                self._toast_lbl = tk.Label(self._toast_win, bg=TOAST_BG, fg="white",
                                           font=("Segoe UI", 10), padx=16, pady=9)
                self._toast_lbl.pack()
            self._toast_lbl.config(text=msg)
            self._toast_win.update_idletasks()
            tw = self._toast_lbl.winfo_reqwidth() + 32
            th = self._toast_lbl.winfo_reqheight() + 18
            rx = self.root.winfo_rootx(); ry = self.root.winfo_rooty()
            rw = self.root.winfo_width(); rh = self.root.winfo_height()
            x = rx + (rw - tw) // 2
            y = ry + rh - th - 28
            self._toast_win.geometry(f"{tw}x{th}+{x}+{y}")
            self._toast_win.deiconify()
            self._toast_win.lift()
            if self._toast_after:
                self.root.after_cancel(self._toast_after)
            self._toast_after = self.root.after(2600, self._hide_toast)
        except Exception:
            pass

    def _hide_toast(self):
        try:
            if self._toast_win is not None:
                self._toast_win.withdraw()
        except Exception:
            pass

    # ================= 프로젝트/도구 선택 =================
    def _on_proj_change(self, _e=None):
        if self.proj_var.get() == CUSTOM_LABEL:
            d = filedialog.askdirectory(title="에이전트를 시작할 폴더 선택")
            if d:
                self.custom_path = d.replace("/", "\\")
                self.proj_cb.set(f"📁 {self.custom_path}")
            else:
                names = [p["name"] for p in self.projects]
                self.proj_var.set(names[0] if names else "")

    def _selected_path(self):
        name = self.proj_var.get()
        if name.startswith("📁 "):
            return self.custom_path
        for p in self.projects:
            if p["name"] == name:
                return p["path"]
        return None

    def _refresh_proj_combo(self):
        names = [p["name"] for p in self.projects]
        self.proj_cb["values"] = names + [CUSTOM_LABEL]
        if self.proj_var.get() not in names and not self.proj_var.get().startswith("📁"):
            self.proj_var.set(names[0] if names else "")

    def _refresh_tool_combo(self):
        names = [t["name"] for t in self.tools]
        self.tool_cb["values"] = names
        if self.tool_var.get() not in names:
            self.tool_var.set(names[0] if names else "")

    def _selected_tool(self):
        name = self.tool_var.get()
        for t in self.tools:
            if t["name"] == name:
                return t
        return self.tools[0] if self.tools else None

    def manage_projects(self):
        dlg = tk.Toplevel(self.root)
        dlg.title("폴더 관리")
        dlg.geometry("520x360")
        dlg.configure(bg=DARK_BG)
        dlg.transient(self.root)
        dlg.grab_set()
        _dark_titlebar_for(dlg)

        lb = make_listbox(dlg)
        lb.pack(side="left", fill="both", expand=True, padx=(12, 6), pady=12)
        path_lbl = ttk.Label(dlg, text="", foreground=DARK_SUBTLE, wraplength=180, justify="left")

        def cur():
            s = lb.curselection()
            return s[0] if s else None

        def show_path(_e=None):
            i = cur()
            path_lbl.config(text=self.projects[i]["path"] if i is not None else "")

        def redraw(sel=None):
            lb.delete(0, "end")
            for p in self.projects:
                lb.insert("end", p["name"])
            if self.projects:
                i = sel if sel is not None else 0
                i = max(0, min(i, len(self.projects) - 1))
                lb.selection_set(i)
                lb.see(i)
            show_path()

        lb.bind("<<ListboxSelect>>", show_path)

        def commit(sel=None):
            save_projects(self.projects)
            self._refresh_proj_combo()
            redraw(sel)

        def add():
            d = filedialog.askdirectory(title="추가할 폴더 선택", parent=dlg)
            if not d:
                return
            d = d.replace("/", "\\")
            default = os.path.basename(d.rstrip("\\")) or d
            name = simpledialog.askstring("폴더 이름", "표시할 이름:", initialvalue=default, parent=dlg)
            if not name or not name.strip():
                return
            self.projects.append({"name": name.strip(), "path": d})
            commit(len(self.projects) - 1)

        def rename():
            i = cur()
            if i is None:
                return
            new = simpledialog.askstring("이름 변경", "새 이름:",
                                         initialvalue=self.projects[i]["name"], parent=dlg)
            if new and new.strip():
                self.projects[i]["name"] = new.strip()
                commit(i)

        def delete():
            i = cur()
            if i is None:
                return
            if messagebox.askyesno("삭제", f"'{self.projects[i]['name']}' 폴더를 목록에서 뺄까?", parent=dlg):
                self.projects.pop(i)
                commit(min(i, len(self.projects) - 1))

        def move(step):
            i = cur()
            if i is None:
                return
            j = i + step
            if 0 <= j < len(self.projects):
                self.projects[i], self.projects[j] = self.projects[j], self.projects[i]
                commit(j)

        btns = ttk.Frame(dlg)
        btns.pack(side="left", fill="y", padx=(0, 12), pady=12)
        ttk.Button(btns, text="＋ 추가", width=12, command=add).pack(pady=2)
        ttk.Button(btns, text="이름 변경", width=12, command=rename).pack(pady=2)
        ttk.Button(btns, text="삭제", width=12, command=delete).pack(pady=2)
        ttk.Button(btns, text="▲ 위로", width=12, command=lambda: move(-1)).pack(pady=2)
        ttk.Button(btns, text="▼ 아래로", width=12, command=lambda: move(1)).pack(pady=2)
        path_lbl.pack(in_=btns, pady=(10, 6))
        ttk.Button(btns, text="닫기", width=12, command=dlg.destroy).pack(side="bottom")
        redraw()

    def manage_tools(self):
        dlg = tk.Toplevel(self.root)
        dlg.title("도구(AI) 관리")
        dlg.geometry("580x400")
        dlg.configure(bg=DARK_BG)
        dlg.transient(self.root)
        dlg.grab_set()
        _dark_titlebar_for(dlg)

        lb = make_listbox(dlg)
        lb.pack(side="left", fill="both", expand=True, padx=(12, 6), pady=12)
        info = ttk.Label(dlg, text="", foreground=DARK_SUBTLE, wraplength=210, justify="left")

        def label(t):
            tag = "  ⚠무거움" if t.get("heavy") else ""
            return f"{t['name']}  —  {t['cmd'] or '(터미널)'}{tag}"

        def cur():
            s = lb.curselection()
            return s[0] if s else None

        def show_info(_e=None):
            i = cur()
            if i is None:
                info.config(text="")
                return
            t = self.tools[i]
            ok = "✓ PATH에서 찾음" if tool_exe_ok(t["cmd"]) else "✗ 실행파일 못 찾음"
            info.config(text=f"명령:\n{t['cmd'] or '(없음 · 터미널)'}\n\n{ok}")

        def redraw(sel=None):
            lb.delete(0, "end")
            for t in self.tools:
                lb.insert("end", label(t))
            if self.tools:
                i = 0 if sel is None else max(0, min(sel, len(self.tools) - 1))
                lb.selection_set(i)
                lb.see(i)
            show_info()

        lb.bind("<<ListboxSelect>>", show_info)

        def commit(sel=None):
            save_tools(self.tools)
            self._refresh_tool_combo()
            redraw(sel)

        def edit_fields(init=None):
            init = init or {"name": "", "cmd": "", "heavy": False}
            name = simpledialog.askstring("도구 이름", "표시 이름:",
                                          initialvalue=init["name"], parent=dlg)
            if not name or not name.strip():
                return None
            cmd = simpledialog.askstring(
                "실행 명령",
                "콘솔에서 실행할 명령\n예) claude / ollama run qwen2.5:32b / 비우면 터미널:",
                initialvalue=init["cmd"], parent=dlg)
            if cmd is None:
                return None
            heavy = messagebox.askyesno("무거운 모델?",
                                        "GPU 많이 먹는 로컬모델이야?\n(동시 실행 시 VRAM 경고)", parent=dlg)
            return {"name": name.strip(), "cmd": cmd.strip(), "heavy": heavy}

        def add():
            t = edit_fields()
            if t:
                self.tools.append(t)
                commit(len(self.tools) - 1)

        def edit():
            i = cur()
            if i is None:
                return
            t = edit_fields(self.tools[i])
            if t:
                self.tools[i] = t
                commit(i)

        def delete():
            i = cur()
            if i is None:
                return
            if messagebox.askyesno("삭제", f"'{self.tools[i]['name']}' 도구를 뺄까?", parent=dlg):
                self.tools.pop(i)
                commit(min(i, len(self.tools) - 1))

        def move(step):
            i = cur()
            if i is None:
                return
            j = i + step
            if 0 <= j < len(self.tools):
                self.tools[i], self.tools[j] = self.tools[j], self.tools[i]
                commit(j)

        btns = ttk.Frame(dlg)
        btns.pack(side="left", fill="y", padx=(0, 12), pady=12)
        ttk.Button(btns, text="＋ 추가", width=12, command=add).pack(pady=2)
        ttk.Button(btns, text="편집", width=12, command=edit).pack(pady=2)
        ttk.Button(btns, text="삭제", width=12, command=delete).pack(pady=2)
        ttk.Button(btns, text="▲ 위로", width=12, command=lambda: move(-1)).pack(pady=2)
        ttk.Button(btns, text="▼ 아래로", width=12, command=lambda: move(1)).pack(pady=2)
        info.pack(in_=btns, pady=(10, 6))
        ttk.Button(btns, text="닫기", width=12, command=dlg.destroy).pack(side="bottom")
        redraw()

    def _restore_sash(self):
        pos = self.cfg.get("sash")
        if pos:
            try:
                self.body.sashpos(0, int(pos))
            except Exception:
                pass

    def _restore_session(self):
        if not self.opt_restore:
            return
        sess = [s for s in (self.cfg.get("session") or []) if os.path.isdir(s.get("path", ""))]
        if not sess:
            return
        if not messagebox.askyesno("세션 복구", f"지난번 열려 있던 에이전트 {len(sess)}개를 다시 열까?"):
            return
        for s in sess:
            tool = {"name": s.get("tool", "Claude"), "cmd": s.get("cmd", ""),
                    "heavy": s.get("heavy", False)}
            self._spawn(s.get("path"), s.get("topic", "세션"), s.get("proj", ""),
                        tool, s.get("group"))

    def _layout_name(self, n):
        return {1: "단일", 2: "2분할", 4: "4분할"}.get(n, f"{n}칸")

    def _update_layout_label(self):
        try:
            self.split_btn.config(text=f"화면: {self._layout_name(self.layout)} ▾")
        except Exception:
            pass

    def set_layout(self, n):
        self.layout = n
        self.v_layout.set(n)
        self._update_layout_label()
        if n > 1:
            slots = (self.slots + [None] * n)[:n]
            placed = set(x for x in slots if x)
            pool = [iid for iid in self._children()
                    if self.agents.get(iid) and u.IsWindow(self.agents[iid]["hwnd"])
                    and iid not in placed]
            for i in range(n):           # 빈 칸은 아직 안 들어간 에이전트로 자동 채움
                if slots[i] is None and pool:
                    slots[i] = pool.pop(0)
            self.slots = slots
        self._relayout()
        self._toast({1: "단일 보기",
                     2: "2칸 분할 · ‘타일 구성’ 또는 빈 칸 클릭으로 지정",
                     4: "4칸 분할 · ‘타일 구성’ 또는 빈 칸 클릭으로 지정"}.get(n, ""))

    def configure_tiles(self):
        """각 칸에 어떤 에이전트를 넣을지 드롭다운으로 직접 지정."""
        if self.layout <= 1:
            self._toast("‘분할 ▾’에서 2칸/4칸을 먼저 골라줘")
            return
        agents = [(iid, self.agents[iid]) for iid in self._children()
                  if self.agents.get(iid) and u.IsWindow(self.agents[iid]["hwnd"])]
        disp_to_iid = {"(비움)": None}
        opts = ["(비움)"]
        seen = {}
        for iid, a in agents:
            base = f"{a['topic']} · {a['tool']}"
            seen[base] = seen.get(base, 0) + 1
            d = base if seen[base] == 1 else f"{base} #{seen[base]}"
            disp_to_iid[d] = iid
            opts.append(d)
        iid_to_disp = {v: kk for kk, v in disp_to_iid.items()}

        dlg = tk.Toplevel(self.root)
        dlg.title("타일 구성")
        dlg.configure(bg=DARK_BG)
        dlg.transient(self.root)
        dlg.grab_set()
        _dark_titlebar_for(dlg)
        ttk.Label(dlg, text=f"{self.layout}칸 — 각 칸에 보여줄 에이전트를 골라줘",
                  foreground=DARK_SUBTLE).pack(anchor="w", padx=14, pady=(14, 8))
        vars_ = []
        for i in range(self.layout):
            row = ttk.Frame(dlg)
            row.pack(fill="x", padx=14, pady=4)
            ttk.Label(row, text=f"{i + 1}번 칸", width=7).pack(side="left")
            cur_iid = self.slots[i] if i < len(self.slots) else None
            var = tk.StringVar(value=iid_to_disp.get(cur_iid, "(비움)"))
            ttk.Combobox(row, textvariable=var, state="readonly", values=opts,
                         width=34).pack(side="left", fill="x", expand=True)
            vars_.append(var)

        def apply():
            new, used = [], set()
            for var in vars_:
                iid = disp_to_iid.get(var.get())
                if iid in used:          # 같은 에이전트 중복 배치 방지
                    iid = None
                if iid:
                    used.add(iid)
                new.append(iid)
            self.slots = new
            self._relayout()
            dlg.destroy()

        bar = ttk.Frame(dlg)
        bar.pack(fill="x", padx=14, pady=(12, 14))
        ttk.Button(bar, text="적용", style="Accent.TButton", command=apply).pack(side="right")
        ttk.Button(bar, text="취소", command=dlg.destroy).pack(side="right", padx=6)

    def toggle_max(self):
        if not self.maximized:
            try:
                self._saved_sash = self.body.sashpos(0)
            except Exception:
                self._saved_sash = None
            self.topbar.pack_forget()
            self.body.forget(self.left_panel)
            self.maximized = True
        else:
            self.topbar.pack_forget()
            self.topbar.pack(fill="x", before=self.body)
            self.body.insert(0, self.left_panel, weight=0)
            self.maximized = False
            if getattr(self, "_saved_sash", None):
                self.root.after(10, lambda: self.body.sashpos(0, self._saved_sash))
        self.root.after_idle(self._relayout)

    def _show_placeholder(self):
        if self._placeholder is not None:
            try:
                self._placeholder.place(x=0, y=0, relwidth=1, relheight=1)
                self._placeholder.lift()
            except Exception:
                pass

    def _hide_placeholder(self):
        if self._placeholder is not None:
            try:
                self._placeholder.place_forget()
            except Exception:
                pass

    # ================= 콘솔 생성 =================
    def _start_console(self, path, topic, cmd, on_ready, on_fail=None):
        safe = topic.replace("&", " ").replace('"', " ").replace("%", " ")
        cmd = (cmd or "").strip()
        inner = f"title {safe} & {cmd}" if cmd else f"title {safe}"
        before = list_consoles()
        try:
            p = subprocess.Popen(["conhost.exe", "cmd.exe", "/k", inner], cwd=path,
                                 creationflags=CREATE_NEW_CONSOLE)
        except Exception as ex:
            messagebox.showerror("실행 실패", str(ex))
            if on_fail:
                on_fail()
            return
        self._toast(f"여는 중… {topic}")
        self._poll_console(before, p, on_ready, on_fail, 0)

    def _poll_console(self, before, p, on_ready, on_fail, tries):
        new = list_consoles() - before
        if new:
            hwnd = new.pop()
            self._attach(hwnd)
            on_ready(p.pid, hwnd)
            return
        if tries >= 80:
            messagebox.showerror("콘솔 실패", "콘솔 창을 찾지 못했어.")
            try:
                kill_tree(p.pid)
            except Exception:
                pass
            if on_fail:
                on_fail()
            return
        self.root.after(100, lambda: self._poll_console(before, p, on_ready, on_fail, tries + 1))

    def _spawn(self, path, topic, proj_name, tool, group=None):
        self._pending.append((path, topic, proj_name, tool, group))
        self._pump()

    def _pump(self):
        if self._busy or not self._pending:
            return
        path, topic, proj_name, tool, group = self._pending.pop(0)
        grp = (group or proj_name or "기타")
        self._busy = True
        self.counter += 1

        def ready(pid, hwnd):
            fid = self._folder_for(grp)
            iid = self.tree.insert(fid, "end", text=f"● {topic}", values=("00:00",), tags=("run",))
            self.agents[iid] = dict(pid=pid, hwnd=hwnd, topic=topic, proj=proj_name, group=grp,
                                    path=path, tool=tool["name"], cmd=tool.get("cmd", ""),
                                    heavy=bool(tool.get("heavy")), start_ts=time.time(), alive=True)
            self._hide_placeholder()
            if self.layout > 1:        # 타일 모드면 빈 칸에 자동으로 꽂아줌
                while len(self.slots) < self.layout:
                    self.slots.append(None)
                for i in range(self.layout):
                    if self.slots[i] is None:
                        self.slots[i] = iid
                        break
            self.tree.selection_set(iid)
            self.show_selected()
            self._toast(f"열림: {topic} · {tool['name']}")
            self._busy = False
            self.root.after(200, self._pump)

        def fail():
            self._busy = False
            self.root.after(200, self._pump)

        self._start_console(path, topic, tool.get("cmd", ""), ready, fail)

    def _remember_topic(self, topic):
        topic = (topic or "").strip()
        if not topic:
            return
        self.recent = [topic] + [t for t in self.recent if t != topic]
        self.recent = self.recent[:12]
        try:
            self.topic_entry["values"] = self.recent
        except Exception:
            pass

    def _heavy_ok(self, tool):
        if not tool.get("heavy"):
            return True
        running = [a for a in self.agents.values()
                   if a.get("heavy") and u.IsWindow(a["hwnd"])]
        if not running:
            return True
        return messagebox.askyesno(
            "무거운 모델 경고",
            f"이미 무거운 로컬모델 {len(running)}개가 돌고 있어.\n"
            f"'{tool['name']}' 까지 띄우면 VRAM이 부족해 느려지거나 죽을 수 있어.\n계속할까?")

    def _is_claude_tool(self, cmd):
        return (cmd or "").strip().lower().startswith("claude")

    def launch(self, resume=None):
        path = self._selected_path()
        if not path or not os.path.isdir(path):
            messagebox.showerror("폴더 없음", f"폴더를 먼저 골라줘.\n{path}")
            return
        tool = self._selected_tool()
        if not tool:
            messagebox.showerror("도구 없음", "도구(AI)를 먼저 골라줘.")
            return
        if not tool_exe_ok(tool.get("cmd", "")):
            exe = tool["cmd"].split()[0]
            messagebox.showerror(
                "실행파일 없음",
                f"'{exe}' 를 PATH에서 못 찾았어.\n설치/경로를 확인해줘.\n도구: {tool['name']}")
            return
        if not self._heavy_ok(tool):
            return

        base = tool.get("cmd", "")
        cmd, mark = base, ""
        if resume:
            if self._is_claude_tool(base):
                cmd = base + (" --continue" if resume == "continue" else " --resume")
                mark = "↻ "
            else:
                messagebox.showinfo(
                    "이어가기 미지원",
                    f"'{tool['name']}' 는 세션 이어가기를 지원하지 않아.\n새로 시작할게.")

        spawn_tool = dict(tool)
        spawn_tool["cmd"] = cmd
        # 주제를 비워두면 창이 다 똑같아 보임 → 도구명+번호로라도 구분되게
        topic = mark + (self.topic_var.get().strip() or f"{tool['name']} {len(self.agents) + 1}")
        proj_name = self.proj_var.get()
        self._spawn(path, topic, proj_name, spawn_tool)
        self._remember_topic(self.topic_var.get())
        self.topic_var.set("")

    def duplicate(self, iid=None):
        iid = iid or self._sel()
        if not iid:
            return
        a = self.agents.get(iid)
        if not a:
            return
        if not os.path.isdir(a["path"]):
            messagebox.showerror("폴더 없음", f"원본 폴더가 사라졌어.\n{a['path']}")
            return
        tool = {"name": a.get("tool", "Claude"), "cmd": a.get("cmd", ""),
                "heavy": a.get("heavy", False)}
        if not self._heavy_ok(tool):
            return
        self._spawn(a["path"], a["topic"], a["proj"], tool, a.get("group"))

    # ================= 표시/전환 =================
    def show_selected(self):
        iid = self._sel(quiet=True)
        if not iid or not self.agents.get(iid):
            return
        self.active = iid
        self._relayout()
        a = self.agents.get(iid)
        # 타일 모드에선 '그 칸에 실제로 보이는' 콘솔만 앞으로(숨은 콘솔 깨우지 않음)
        on_screen = (self.layout <= 1) or (iid in self._visible_iids())
        if a and u.IsWindow(a["hwnd"]) and not self._suppress_fg and on_screen:
            try:
                u.SetForegroundWindow(a["hwnd"])
            except Exception:
                pass
        self._update_info()

    def _on_activate(self, _e=None):
        self.root.after_idle(self._relayout)

    def _update_info(self):
        a = self.agents.get(self.active)
        if not a:
            self.info_lbl.config(text="에이전트를 선택하면 정보가 여기 표시됩니다.")
            return
        state = "실행중" if a["alive"] else "종료됨"
        tool = a.get("tool", "")
        self.info_lbl.config(
            text=f"▶ {a['topic']}   ·   {tool}   ·   {a['proj']}   ·   "
                 f"⏱ {fmt_uptime(a['start_ts'])}   ·   {state}")

    # ================= 동작 =================
    def kill_selected(self, iid=None):
        iid = iid or self._sel()
        if not iid:
            return
        a = self.agents.get(iid)
        if not a:
            return
        if not messagebox.askyesno("종료", f"'{a['topic']}' 에이전트를 종료할까?"):
            return
        kill_tree(a["pid"])
        self._toast(f"종료 요청: {a['topic']}")

    def remove_selected(self, iid=None):
        iid = iid or self._sel()
        if not iid or iid not in self.agents:
            return
        a = self.agents.pop(iid, None)
        if a and u.IsWindow(a["hwnd"]):
            kill_tree(a["pid"])
        self.tree.delete(iid)
        self.slots = [None if s == iid else s for s in self.slots]
        if self.active == iid:
            self.active = None
        self._prune_folders()
        self._relayout()
        self._update_info()

    def clear_dead(self):
        gone = [iid for iid, a in self.agents.items() if not u.IsWindow(a["hwnd"])]
        for iid in gone:
            self.agents.pop(iid, None)
            self.tree.delete(iid)
        self._prune_folders()
        self._toast(f"종료된 에이전트 {len(gone)}개 정리됨")

    def restart(self, iid=None):
        iid = iid or self._sel()
        if not iid:
            return
        a = self.agents.get(iid)
        if not a:
            return
        if u.IsWindow(a["hwnd"]):
            kill_tree(a["pid"])

        def ready(pid, hwnd):
            a["pid"], a["hwnd"], a["start_ts"], a["alive"] = pid, hwnd, time.time(), True
            self.tree.item(iid, text=f"● {a['topic']}", values=("00:00",), tags=("run",))
            if self.active == iid or self.tree.selection() == (iid,):
                self.tree.selection_set(iid)
                self.show_selected()
            self._toast(f"재시작: {a['topic']}")

        self._start_console(a["path"], a["topic"], a.get("cmd", ""), ready)

    def rename(self, iid=None):
        iid = iid or self._sel()
        if not iid:
            return
        a = self.agents.get(iid)
        if not a:
            return
        new = simpledialog.askstring("이름 변경", "새 주제 이름:", initialvalue=a["topic"], parent=self.root)
        if new and new.strip():
            a["topic"] = new.strip()
            if self.active == iid:
                self._update_info()

    def open_folder(self, iid=None):
        iid = iid or self._sel()
        if not iid:
            return
        a = self.agents.get(iid)
        if a:
            try:
                os.startfile(a["path"])
            except Exception as ex:
                messagebox.showerror("폴더 열기 실패", str(ex))

    def _detach_window(self, hwnd):
        try:
            _SetWindowLongPtr(hwnd, GWLP_HWNDPARENT, ctypes.c_void_p(0))
        except Exception:
            pass
        u.SetWindowLongW(hwnd, GWL_STYLE, WS_OVERLAPPEDWINDOW | WS_VISIBLE)
        u.SetWindowPos(hwnd, 0, 0, 0, 0, 0,
                       SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED | SWP_NOACTIVATE)
        u.MoveWindow(hwnd, 120, 120, 960, 600, True)
        u.ShowWindow(hwnd, SW_RESTORE)

    def detach_selected(self, iid=None):
        iid = iid or self._sel()
        if not iid:
            return
        a = self.agents.get(iid)
        if not a or not u.IsWindow(a["hwnd"]):
            return
        self._detach_window(a["hwnd"])
        self._toast(f"분리됨: {a['topic']} (독립 창)")

    # ================= 단축키 helper =================
    def _children(self):
        # 폴더 안의 에이전트(잎 노드)들을 화면 순서대로 평탄화
        out = []
        for fid in self.tree.get_children(""):
            out.extend(self.tree.get_children(fid))
        return out

    def _folder_for(self, proj):
        """프로젝트명에 해당하는 폴더 노드를 찾거나 새로 만든다."""
        key = proj or "기타"
        fid = self.folders.get(key)
        if fid and self.tree.exists(fid):
            return fid
        label = key
        if label.startswith("📁 "):
            label = os.path.basename(label[2:].rstrip("\\")) or label[2:]
        fid = self.tree.insert("", "end", text=f"📁 {label}", open=True,
                               tags=("folder",), values=("",))
        self.folders[key] = fid
        return fid

    def _prune_folders(self):
        """빈 폴더 노드 제거."""
        for key, fid in list(self.folders.items()):
            if not self.tree.exists(fid):
                self.folders.pop(key, None)
            elif not self.tree.get_children(fid):
                self.tree.delete(fid)
                self.folders.pop(key, None)

    def _cycle(self, step):
        ch = self._children()
        if not ch:
            return "break"
        cur = self._sel(quiet=True)
        idx = ch.index(cur) if cur in ch else -step
        nxt = ch[(idx + step) % len(ch)]
        self.tree.selection_set(nxt)
        self.tree.see(nxt)
        self.show_selected()
        return "break"

    def _select_index(self, i):
        ch = self._children()
        if 0 <= i < len(ch):
            self.tree.selection_set(ch[i])
            self.tree.see(ch[i])
            self.show_selected()
        return "break"

    def _sel(self, quiet=False):
        s = self.tree.selection()
        if not s:
            if not quiet:
                self._toast("목록에서 에이전트를 먼저 선택해줘.")
            return None
        return s[0]

    # ================= 폴링 =================
    def _refresh_loop(self):
        run = 0
        died_names = []
        idx = 0
        for fid in self.tree.get_children(""):
            frun = ftot = 0
            for iid in self.tree.get_children(fid):
                a = self.agents.get(iid)
                if not a:
                    continue
                ftot += 1
                prev = a.get("alive", True)
                alive = bool(u.IsWindow(a["hwnd"]))
                if alive:
                    run += 1
                    frun += 1
                badge = CIRCLED[idx] if idx < 9 else "  "
                dot = "●" if alive else "○"
                up = fmt_uptime(a["start_ts"]) if alive else "—"
                self.tree.item(iid, text=f"{badge} {dot} {a['topic']}", values=(up,),
                               tags=("run" if alive else "dead",))
                a["alive"] = alive
                if prev and not alive:
                    died_names.append(a["topic"])
                idx += 1
            self.tree.item(fid, values=(f"{frun}/{ftot}",))
        if died_names:
            self._relayout()
            self._toast("종료됨: " + ", ".join(died_names[:3]))
        total = len(self.agents)
        self.root.title(f"Claude 통합 에이전트 관리 콘솔 — 실행중 {run}/{total}")
        if self.active:
            self._update_info()
        self.root.after(1000, self._refresh_loop)

    # ================= 종료 =================
    def on_close(self):
        live = [a for a in self.agents.values() if u.IsWindow(a["hwnd"])]
        try:
            sash = self.body.sashpos(0)
        except Exception:
            sash = self.cfg.get("sash")
        save_config({
            "geometry": self.root.winfo_geometry(),
            "last_project": self.proj_var.get() if not self.proj_var.get().startswith("📁") else None,
            "last_tool": self.tool_var.get(),
            "recent_topics": self.recent,
            "sash": sash,
            "layout": self.layout,
            "split_x": self.split_x,
            "split_y": self.split_y,
            "topmost": self.opt_topmost,
            "restore_session": self.opt_restore,
            "nice_console": self.opt_console_font,
            "session": [{"topic": a["topic"], "path": a["path"], "proj": a["proj"],
                         "group": a.get("group", a.get("proj", "")),
                         "tool": a.get("tool", "Claude"), "cmd": a.get("cmd", ""),
                         "heavy": a.get("heavy", False)} for a in live],
        })
        restore_console_prefs(self._console_saved)
        if live:
            if not messagebox.askyesno(
                "닫기",
                f"실행 중인 에이전트 {len(live)}개도 모두 종료하고 닫을까?"):
                return
            for a in live:
                try:
                    kill_tree(a["pid"])
                except Exception:
                    pass
        self.root.destroy()


ERROR_LOG = os.path.join(HERE, "agent_console_error.log")

if __name__ == "__main__":
    import faulthandler
    import traceback
    from datetime import datetime
    try:
        _logf = open(ERROR_LOG, "a", encoding="utf-8")
        faulthandler.enable(_logf)
    except Exception:
        _logf = None

    root = tk.Tk()
    try:
        import sv_ttk
        sv_ttk.set_theme("dark")
    except Exception:
        try:
            ttk.Style().theme_use("vista")
        except Exception:
            pass
    try:
        style = ttk.Style()
        style.configure("Treeview", rowheight=30, font=("Segoe UI", 10))
        style.configure("Treeview.Heading", font=("Segoe UI", 9))
    except Exception:
        pass

    def _report_exc(exc, val, tb):
        text = "".join(traceback.format_exception(exc, val, tb))
        if _logf:
            try:
                _logf.write(f"\n=== {datetime.now():%Y-%m-%d %H:%M:%S} ===\n{text}\n")
                _logf.flush()
            except Exception:
                pass
        try:
            messagebox.showerror("오류(기록됨)", f"{val}\n\n자세한 내용: {ERROR_LOG}")
        except Exception:
            pass

    root.report_callback_exception = _report_exc
    App(root)
    root.mainloop()
