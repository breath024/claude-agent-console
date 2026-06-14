# -*- coding: utf-8 -*-
"""Claude 통합 에이전트 관리 콘솔 (오버레이형)

한 화면에서 여러 AI 콘솔(claude/ollama/…)을 만들고 전환·관리한다.
각 콘솔은 conhost 독립 창으로 띄우되, 관리창에 '종속(owner)'시켜 오른쪽
영역에 딱 겹쳐 띄운다. 콘솔이 진짜 독립 창이라 입력·포커스·커서가 전부
네이티브 → 매끄럽고, SetParent 임베드 시절의 포커스/크래시 문제가 없다.

기능:
- 위: 프로젝트 + 도구(AI) + 주제로 새 에이전트 / 이어서(claude --continue/--resume)
- 왼쪽: 에이전트 목록(상태·경과), 우클릭 메뉴, 종료/제거/정리
- 오른쪽: 정보바 + 오버레이 콘솔
- 단축키: Ctrl+N 주제, Ctrl+Tab/Shift+Tab 전환, Ctrl+1~9 선택, Ctrl+W 종료, F11 전체보기
- 폴더/도구 인앱 관리, 최근 주제, 복제, 드래그 분할, 세션 복구
"""
import os
import json
import time
import shutil
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
# heavy=True 는 GPU 무거운 로컬모델(동시 실행 시 VRAM 경고).
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
# 오너 설정용(64bit 안전하게 LongPtr)
_SetWindowLongPtr = getattr(u, "SetWindowLongPtrW", u.SetWindowLongW)
_SetWindowLongPtr.restype = ctypes.c_void_p
_SetWindowLongPtr.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]


def _dark_titlebar(hwnd):
    """Win10/11 제목표시줄을 다크로(있으면). 없으면 조용히 무시."""
    if not hwnd:
        return
    try:
        dwm = ctypes.windll.dwmapi
        val = ctypes.c_int(1)
        # DWMWA_USE_IMMERSIVE_DARK_MODE = 20 (구버전은 19)
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


def list_consoles():
    """현재 떠 있는 콘솔 창(ConsoleWindowClass) 핸들 집합. claude가 제목을 바꿔
    제목 매칭은 못 쓰므로 런치 전/후 차집합으로 새 창을 잡는다."""
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
    """cmd 첫 토큰(실행파일)이 PATH에 있는지. 빈 cmd(터미널)는 항상 OK."""
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
        self.agents = {}     # iid -> dict(pid,hwnd,topic,proj,path,tool,cmd,heavy,start_ts,alive)
        self.active = None
        self.custom_path = None
        self.projects = load_projects()
        self.tools = load_tools()
        self.cfg = load_config()
        self.recent = list(self.cfg.get("recent_topics", []))
        self.maximized = False
        self._busy = False        # 콘솔 한 개씩만 잡도록 직렬화(빠른 연타 시 창 뒤섞임 방지)
        self._pending = []        # 대기 중인 (path, topic, proj, tool) 큐
        self._mgr_hwnd = None     # 관리창 top-level hwnd(콘솔 오너로 씀)

        root.title("Claude 통합 에이전트 관리 콘솔")
        root.geometry(self.cfg.get("geometry", "1180x720"))
        root.minsize(900, 520)
        root.configure(bg=DARK_BG)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

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
        부모(child)가 아니라 오너라서 입력/포커스/커서는 전부 네이티브로 동작하고,
        관리창 위에 떠 있고 같이 최소화되며 작업표시줄엔 따로 안 뜬다."""
        if not u.IsWindow(hwnd):
            return
        style = u.GetWindowLongW(hwnd, GWL_STYLE)
        style = (style & ~WS_OVERLAPPEDWINDOW & ~WS_CAPTION & ~WS_THICKFRAME
                 & ~WS_BORDER & ~WS_SYSMENU & ~WS_MINIMIZEBOX & ~WS_MAXIMIZEBOX)
        style = style | WS_POPUP | WS_VISIBLE
        u.SetWindowLongW(hwnd, GWL_STYLE, style)
        if self._mgr_hwnd:
            try:
                _SetWindowLongPtr(hwnd, GWLP_HWNDPARENT, ctypes.c_void_p(self._mgr_hwnd))
            except Exception:
                pass
        u.SetWindowPos(hwnd, 0, 0, 0, 0, 0,
                       SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED | SWP_NOACTIVATE)
        self._place(hwnd)

    def _place(self, hwnd):
        """콘솔을 오른쪽 호스트 영역(화면 좌표)에 딱 맞춘다."""
        if not u.IsWindow(hwnd):
            return
        try:
            x = self.host.winfo_rootx()
            y = self.host.winfo_rooty()
            w = max(self.host.winfo_width(), 50)
            h = max(self.host.winfo_height(), 50)
        except Exception:
            return
        u.MoveWindow(hwnd, x, y, w, h, True)

    def _strip_frame(self, hwnd):
        """conhost가 포커스 받을 때 되붙이는 테두리(→ ↔ 리사이즈 커서)를 제거.
        이미 깨끗하면 아무것도 안 해서 깜빡임 없음."""
        if not u.IsWindow(hwnd):
            return
        style = u.GetWindowLongW(hwnd, GWL_STYLE)
        want = (style & ~WS_OVERLAPPEDWINDOW & ~WS_CAPTION & ~WS_THICKFRAME
                & ~WS_BORDER & ~WS_SYSMENU & ~WS_MINIMIZEBOX & ~WS_MAXIMIZEBOX) | WS_POPUP | WS_VISIBLE
        if want != style:
            u.SetWindowLongW(hwnd, GWL_STYLE, want)
            u.SetWindowPos(hwnd, 0, 0, 0, 0, 0,
                           SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED | SWP_NOACTIVATE)
            self._place(hwnd)

    def _place_active(self, _e=None):
        a = self.agents.get(self.active)
        if a and u.IsWindow(a["hwnd"]):
            self._place(a["hwnd"])

    def _fit_active(self):
        self._place_active()

    def _tick(self):
        """안전한 Tk 컨텍스트의 가벼운 주기 루프: 활성 콘솔 테두리 점검·제거."""
        a = self.agents.get(self.active)
        if a and u.IsWindow(a["hwnd"]):
            self._strip_frame(a["hwnd"])
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
        ttk.Button(top, text="폴더 관리", width=9,
                   command=self.manage_projects).pack(side="left", padx=(0, 14))

        ttk.Label(top, text="도구").pack(side="left")
        tnames = [t["name"] for t in self.tools]
        last_tool = self.cfg.get("last_tool")
        self.tool_var = tk.StringVar(value=last_tool if last_tool in tnames
                                     else (tnames[0] if tnames else ""))
        self.tool_cb = ttk.Combobox(top, textvariable=self.tool_var, state="readonly",
                                    width=18, values=tnames)
        self.tool_cb.pack(side="left", padx=(6, 4))
        ttk.Button(top, text="도구 관리", width=9,
                   command=self.manage_tools).pack(side="left", padx=(0, 14))

        ttk.Label(top, text="주제").pack(side="left")
        self.topic_var = tk.StringVar()
        e = ttk.Combobox(top, textvariable=self.topic_var, width=22, values=self.recent)
        e.pack(side="left", padx=(6, 14))
        e.bind("<Return>", lambda ev: self.launch())
        self.topic_entry = e

        ttk.Button(top, text="＋ 새 에이전트", style="Accent.TButton",
                   command=self.launch).pack(side="left")
        rb = ttk.Menubutton(top, text="이어서 ▾")
        rm = tk.Menu(rb, tearoff=0)
        _style_menu(rm)
        rm.add_command(label="이어서 (최근 대화)", command=lambda: self.launch(resume="continue"))
        rm.add_command(label="골라서 이어서…", command=lambda: self.launch(resume="resume"))
        rb["menu"] = rm
        rb.pack(side="left", padx=(6, 0))
        ttk.Label(top, text="Ctrl+N · Ctrl+Tab · Ctrl+1~9 · F11",
                  foreground=DARK_SUBTLE).pack(side="right")

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

        # ---- 왼쪽: 목록 ---- (드래그로 폭 조절)
        left = ttk.Frame(body, padding=(12, 0, 8, 12))
        body.add(left, weight=0)
        self.left_panel = left
        ttk.Label(left, text="에이전트", foreground=DARK_SUBTLE,
                  font=("Segoe UI", 9)).pack(anchor="w", pady=(0, 6))

        self.tree = ttk.Treeview(left, columns=("name", "up"), show="headings",
                                 height=20, selectmode="browse")
        self.tree.heading("name", text="에이전트")
        self.tree.heading("up", text="경과")
        self.tree.column("name", width=190, anchor="w")
        self.tree.column("up", width=64, anchor="e")
        self.tree.tag_configure("run", foreground=RUN_FG)
        self.tree.tag_configure("dead", foreground=DEAD_FG)
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda ev: self.show_selected())
        self.tree.bind("<Button-3>", self._popup_menu)
        self.tree.bind("<Double-1>", lambda ev: self.rename(self._sel(quiet=True)))

        b = ttk.Frame(left)
        b.pack(fill="x", pady=(10, 4))
        ttk.Button(b, text="종료", width=7, command=self.kill_selected).pack(side="left")
        ttk.Button(b, text="제거", width=7, command=self.remove_selected).pack(side="left", padx=4)
        ttk.Button(b, text="정리", width=7, command=self.clear_dead).pack(side="left")
        self.status_lbl = ttk.Label(left, text="준비됨", foreground=DARK_SUBTLE, wraplength=230)
        self.status_lbl.pack(anchor="w", pady=(8, 0))

        # ---- 오른쪽: 정보바 + 콘솔 ----
        right = ttk.Frame(body)
        body.add(right, weight=1)
        self.right_panel = right

        info = ttk.Frame(right, padding=(12, 8))
        info.pack(fill="x")
        self.info_lbl = ttk.Label(info, text="에이전트를 선택하면 정보가 여기 표시됩니다.",
                                  foreground=DARK_SUBTLE)
        self.info_lbl.pack(side="left")
        ttk.Button(info, text="⛶ 전체보기", width=10,
                   command=self.toggle_max).pack(side="right", padx=(0, 8))
        ttk.Button(info, text="종료", width=6,
                   command=lambda: self.kill_selected(self.active)).pack(side="right")
        ttk.Button(info, text="분리", width=6,
                   command=lambda: self.detach_selected(self.active)).pack(side="right", padx=4)
        ttk.Button(info, text="폴더", width=6,
                   command=lambda: self.open_folder(self.active)).pack(side="right")
        ttk.Button(info, text="재시작", width=7,
                   command=lambda: self.restart(self.active)).pack(side="right", padx=4)

        self.host = tk.Frame(right, bg=CONSOLE_BG)
        self.host.pack(fill="both", expand=True, padx=(0, 2), pady=(0, 2))
        self.host.bind("<Configure>", lambda e: self._place_active())
        self.root.bind("<Configure>", self._place_active, add="+")
        self._placeholder = tk.Label(
            self.host, bg=CONSOLE_BG, fg=DARK_SUBTLE, justify="center",
            font=("Segoe UI", 11),
            text="여기에 선택한 에이전트 콘솔이 표시됩니다.\n\n"
                 "위에서 프로젝트·도구·주제를 정하고  [＋ 새 에이전트]  를 눌러봐.")
        self._placeholder.place(relx=0.5, rely=0.5, anchor="center")

    def _build_menu(self):
        m = tk.Menu(self.root, tearoff=0)
        _style_menu(m)
        m.add_command(label="보기(앞으로)", command=lambda: self.show_selected())
        m.add_command(label="이름 변경…", command=lambda: self.rename(self._sel()))
        m.add_command(label="폴더 열기", command=lambda: self.open_folder(self._sel()))
        m.add_separator()
        m.add_command(label="복제(같은 폴더·주제로)", command=lambda: self.duplicate(self._sel()))
        m.add_command(label="재시작", command=lambda: self.restart(self._sel()))
        m.add_command(label="분리(팝아웃)", command=lambda: self.detach_selected(self._sel()))
        m.add_separator()
        m.add_command(label="종료", command=lambda: self.kill_selected(self._sel()))
        m.add_command(label="목록에서 제거", command=lambda: self.remove_selected(self._sel()))
        self.menu = m

    def _popup_menu(self, e):
        iid = self.tree.identify_row(e.y)
        if iid:
            self.tree.selection_set(iid)
            try:
                self.menu.tk_popup(e.x_root, e.y_root)
            finally:
                self.menu.grab_release()

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
        """폴더(프로젝트)를 인앱에서 추가/이름변경/삭제/순서이동."""
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
        """AI 도구(실행 명령)를 인앱에서 추가/편집/삭제/순서이동. tools.json 에 저장."""
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
        """지난 종료 때 살아있던 에이전트들을 물어보고 한 번에 재오픈."""
        sess = [s for s in (self.cfg.get("session") or []) if os.path.isdir(s.get("path", ""))]
        if not sess:
            return
        if not messagebox.askyesno("세션 복구", f"지난번 열려 있던 에이전트 {len(sess)}개를 다시 열까?"):
            return
        for s in sess:
            tool = {"name": s.get("tool", "Claude"), "cmd": s.get("cmd", ""),
                    "heavy": s.get("heavy", False)}
            self._spawn(s.get("path"), s.get("topic", "세션"), s.get("proj", ""), tool)

    def toggle_max(self):
        """콘솔만 크게: 상단바·목록 숨김 ↔ 복귀 (F11/버튼)."""
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
        self.root.after_idle(self._place_active)

    def _show_placeholder(self):
        if self._placeholder is not None:
            try:
                self._placeholder.place(relx=0.5, rely=0.5, anchor="center")
                self._placeholder.lift()
            except Exception:
                pass

    def _hide_placeholder(self):
        if self._placeholder is not None:
            try:
                self._placeholder.place_forget()
            except Exception:
                pass

    # ================= 콘솔 생성(비동기) =================
    def _start_console(self, path, topic, cmd, on_ready, on_fail=None):
        """콘솔을 띄우고 새 창이 잡힐 때까지 after 로 폴링(메인 스레드 안 막음).
        cmd: 콘솔 안에서 실행할 명령(claude / ollama run ... / 빈칸이면 터미널)."""
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
        self.status_lbl.config(text=f"여는 중… {topic}")
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

    def _spawn(self, path, topic, proj_name, tool):
        """콘솔 생성 요청을 큐에 넣고 한 개씩 처리(직렬화)."""
        self._pending.append((path, topic, proj_name, tool))
        self._pump()

    def _pump(self):
        if self._busy or not self._pending:
            return
        path, topic, proj_name, tool = self._pending.pop(0)
        self._busy = True
        self.counter += 1

        def ready(pid, hwnd):
            iid = self.tree.insert("", "end", values=(f"● {topic}", "00:00"), tags=("run",))
            self.agents[iid] = dict(pid=pid, hwnd=hwnd, topic=topic, proj=proj_name,
                                    path=path, tool=tool["name"], cmd=tool.get("cmd", ""),
                                    heavy=bool(tool.get("heavy")), start_ts=time.time(), alive=True)
            self._hide_placeholder()
            self.tree.selection_set(iid)
            self.show_selected()
            self.status_lbl.config(text=f"열림: {topic} · {tool['name']}")
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
        """resume=None 새로 / 'continue' 최근 대화 / 'resume' 골라서 이어가기(claude 계열만)."""
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
        topic = mark + (self.topic_var.get().strip() or tool["name"])
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
        self._spawn(a["path"], a["topic"], a["proj"], tool)
        self.status_lbl.config(text=f"복제: {a['topic']}")

    # ================= 표시/전환 =================
    def show_selected(self):
        iid = self._sel(quiet=True)
        if not iid:
            return
        a = self.agents.get(iid)
        if not a:
            return
        self.active = iid
        if u.IsWindow(a["hwnd"]):
            self._hide_placeholder()
            self._attach(a["hwnd"])
            u.ShowWindow(a["hwnd"], SW_SHOW)
            self._place(a["hwnd"])
            try:
                u.SetForegroundWindow(a["hwnd"])   # 네이티브 포커스
            except Exception:
                pass
        else:
            self._show_placeholder()
        for j, b in self.agents.items():
            if j != iid and u.IsWindow(b["hwnd"]):
                u.ShowWindow(b["hwnd"], SW_HIDE)
        self._update_info()

    def _on_activate(self, _e=None):
        """관리창이 다시 활성화되면 활성 콘솔을 제자리에 맞춘다(따라오기)."""
        self.root.after_idle(self._place_active)

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
        self.status_lbl.config(text=f"종료 요청: {a['topic']}")

    def remove_selected(self, iid=None):
        iid = iid or self._sel()
        if not iid:
            return
        a = self.agents.pop(iid, None)
        if a and u.IsWindow(a["hwnd"]):
            kill_tree(a["pid"])
        self.tree.delete(iid)
        if self.active == iid:
            self.active = None
            self._show_placeholder()
            self._update_info()

    def clear_dead(self):
        gone = [iid for iid, a in self.agents.items() if not u.IsWindow(a["hwnd"])]
        for iid in gone:
            self.agents.pop(iid, None)
            self.tree.delete(iid)
        self.status_lbl.config(text=f"종료된 에이전트 {len(gone)}개 정리됨")

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
            self.tree.item(iid, values=(f"● {a['topic']}", "00:00"), tags=("run",))
            if self.active == iid or self.tree.selection() == (iid,):
                self.tree.selection_set(iid)
                self.show_selected()
            self.status_lbl.config(text=f"재시작: {a['topic']}")

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
            dot = "●" if a["alive"] else "○"
            self.tree.item(iid, values=(f"{dot} {a['topic']}", self.tree.set(iid, "up")))
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
        """콘솔을 오너 해제 + 일반 창으로 복원(독립 창으로 빼냄)."""
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
        self.status_lbl.config(text=f"분리됨: {a['topic']} (독립 창)")

    # ================= 단축키 helper =================
    def _children(self):
        return list(self.tree.get_children(""))

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
                self.status_lbl.config(text="목록에서 에이전트를 먼저 선택해줘.")
            return None
        return s[0]

    # ================= 폴링 =================
    def _refresh_loop(self):
        run = 0
        for iid, a in list(self.agents.items()):
            prev = a.get("alive", True)
            alive = bool(u.IsWindow(a["hwnd"]))
            if alive:
                run += 1
            dot = "●" if alive else "○"
            up = fmt_uptime(a["start_ts"]) if alive else "—"
            self.tree.item(iid, values=(f"{dot} {a['topic']}", up),
                           tags=("run" if alive else "dead",))
            a["alive"] = alive
            if iid == self.active and prev and not alive:
                self._show_placeholder()   # 활성 콘솔이 방금 죽음 → 안내 복귀
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
            "session": [{"topic": a["topic"], "path": a["path"], "proj": a["proj"],
                         "tool": a.get("tool", "Claude"), "cmd": a.get("cmd", ""),
                         "heavy": a.get("heavy", False)} for a in live],
        })
        if live:
            if messagebox.askyesno(
                "닫기",
                f"실행 중 에이전트 {len(live)}개가 있어.\n"
                "예: 독립 창으로 분리하고 관리창만 닫기\n"
                "아니오: 관리창 계속 사용",
            ):
                for a in live:
                    try:
                        self._detach_window(a["hwnd"])
                    except Exception:
                        pass
                self.root.destroy()
            return
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
