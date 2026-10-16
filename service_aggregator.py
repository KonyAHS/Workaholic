"""Workaholic (مرهق) — run, group and monitor your scripts and programs from one window."""
from __future__ import annotations

import argparse
import base64
import codecs
import functools
import itertools
import json
import os
import queue
import re
import shlex
import shutil
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
import zlib
import tkinter as tk
import tkinter.font as tkfont
import hashlib
import psutil
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from types import SimpleNamespace
from tkinter import filedialog, messagebox, ttk
from version import __version__

APP_NAME = "Workaholic"
APP_DIR = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
def config_path() -> Path:
    """Keep user settings independent of the executable's location."""
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / APP_NAME / "services_config.json"


CONFIG_FILE = config_path()
IS_WINDOWS = os.name == "nt"
INSTANCE_PORT = 47613          # localhost port used to keep a single instance running
STOP_GRACE_SECONDS = 3.0       # time a service gets to exit after a graceful stop signal
TICK_MS = 500                  # UI refresh / process polling interval
LOG_TAIL_BYTES = 128 * 1024    # how much existing log to load when showing a service's output
LOG_MAX_LINES = 5000
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
SCRIPT_TYPES = "*.py *.pyw *.exe *.bat *.cmd *.ps1"
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


class C:
    """Colour palette, taken from the app icon: forest green on warm ivory."""
    BG = "#f8f1e4"
    PANEL = "#fffaf1"
    PANEL_ALT = "#fbf4e8"
    INPUT = "#fffcf7"
    FOOTER = "#f1e6d2"
    BUTTON = "#f1e7d6"
    BUTTON_HOVER = "#e8dcc6"
    BUTTON_PRESS = "#ddcfb5"
    BORDER = "#e6dac6"
    BORDER_STRONG = "#cdbea4"
    SELECT = "#dbe7de"
    TEXT = "#22332e"
    MUTED = "#5e6c66"
    DIM = "#8b928a"
    ACCENT = "#32534b"
    ACCENT_HOVER = "#3d6359"
    ACCENT_PRESS = "#2a463f"
    ON_ACCENT = "#f9eedc"
    GREEN = "#2e8a57"
    AMBER = "#b7791f"
    RED = "#c0453b"
    BLUE = "#3b6e9e"
    GREY = "#aaa08f"
    # Sidebar, title bar and tooltips use the icon's green.
    SIDEBAR = "#2c4a43"
    NAV_HOVER = "#35554d"
    NAV_SELECTED = "#3f6158"
    SIDE_TEXT = "#f9eedc"
    SIDE_MUTED = "#bfcdc4"
    SIDE_DIM = "#8fa79d"
    SIDE_ACCENT = "#e3c99a"
    SIDE_GREEN = "#9ee0b3"
    SIDE_AMBER = "#f2c879"
    # Output panel: a dark green console.
    LOG_BG = "#243d37"
    LOG_TEXT = "#ece2cf"
    LOG_SELECT = "#44665c"
    LOG_THUMB = "#4a6a61"
    LOG_MARKER = "#a8d8b9"
    LOG_ERROR = "#ff9e90"
    LOG_WARN = "#f2c879"
    LOG_HINT = "#9fb5ab"


STATUS_COLORS = {
    "running": C.GREEN, "stopping": C.BLUE, "restarting": C.BLUE,
    "exited": C.AMBER, "stopped": C.GREY, "missing": C.RED,
}


# ---------- Helpers ----------
def norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def fmt_duration(seconds: float) -> str:
    s = int(seconds)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    if d: return f"{d}d {h}h"
    if h: return f"{h}h {m:02d}m"
    if m: return f"{m}m {s:02d}s"
    return f"{s}s"


def default_python() -> str:
    """Interpreter for .py services; avoids pythonw so services keep a valid stdout."""
    if getattr(sys, "frozen", False):
        return shutil.which("python.exe") or shutil.which("py.exe") or "python.exe"
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe" and exe.with_name("python.exe").exists():
        return str(exe.with_name("python.exe"))
    return str(exe)


def _has_console() -> bool:
    if not IS_WINDOWS: return True
    try:
        import ctypes
        return bool(ctypes.windll.kernel32.GetConsoleWindow())
    except Exception:
        return False


HAS_CONSOLE = _has_console()


def open_path(path: str):
    if IS_WINDOWS: os.startfile(path)
    elif sys.platform == "darwin": subprocess.Popen(["open", path])
    else: subprocess.Popen(["xdg-open", path])


def reveal_path(path: str):
    if IS_WINDOWS: subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    else: open_path(str(Path(path).parent))


def _send_graceful(proc: subprocess.Popen) -> bool:
    """Ask a process to exit. Returns False if there was no way to ask nicely."""
    try:
        if IS_WINDOWS:
            # CTRL_BREAK only reaches process groups attached to our console.
            if not HAS_CONSOLE: return False
            os.kill(proc.pid, signal.CTRL_BREAK_EVENT)
        else:
            os.killpg(proc.pid, signal.SIGTERM)
        return True
    except OSError:
        return False


def _kill_tree(proc: subprocess.Popen):
    """Force-kill a process and everything it spawned."""
    try:
        if IS_WINDOWS:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True,
                           timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        pass
    if proc.poll() is None:
        try: proc.kill()
        except Exception: pass


# ---------- Model ----------
def suggested_service_name(path: str) -> str:
    """Friendly defaults for recognizable helpers; explicit display names take priority."""
    file = Path(path)
    helpers = {
        "gsm_ffxiv_wrapper.py": "FFXIV Text to GameSentenceMiner",
        "avis_websocket_ffxiv_weapper.py": "FFXIV Voice (AivisSpeech)",
        "voicevox_jl_server_avis.py": "JL Voice Server (AivisSpeech)",
        "jl.exe": "JL Dictionary",
        "gamesentenceminer.exe": "GameSentenceMiner",
    }
    if file.name.lower() in helpers: return helpers[file.name.lower()]
    if file.name.lower() == "overlay.py" and file.parent.name.lower() == "llm prompt wrapper":
        return "Gemini Overlay"
    # Packaged engines and Electron apps often supply their actual name here.
    if file.name.lower() == "run.exe":
        try:
            manifest = json.loads((file.parent / "engine_manifest.json").read_text(encoding="utf-8"))
            name = manifest.get("name")
            if isinstance(name, str): return name
        except (OSError, ValueError, AttributeError): pass
    if file.name.lower() == "agent.exe":
        try:
            package = json.loads((file.parent / "resources" / "app" / "package.json").read_text(encoding="utf-8"))
            if package.get("name") == "agent" and "text hooker" in str(package.get("description", "")).lower():
                return "Agent Text Hooker"
        except (OSError, ValueError, AttributeError): pass
    return ""


def process_snapshot() -> list[dict]:
    """Inspect accessible processes; restricted processes must not break launching."""
    result = []
    for proc in psutil.process_iter(["pid", "name", "exe", "cmdline", "cwd", "create_time"], ad_value=None):
        if proc.pid != os.getpid():
            result.append(proc.info)
    return result


def trigger_process_running(target: str, processes: list[dict]) -> bool:
    """Match an executable name, or an exact path when a folder is specified."""
    target = target.strip().strip('"')
    if not target: return False
    if os.path.dirname(target):
        return any(info.get("exe") and norm(info["exe"]) == norm(target) for info in processes)
    return any(target.casefold() == (info.get("name") or Path(info.get("exe") or "").name).casefold()
               for info in processes)


def matching_process(path: str, processes: list[dict]) -> dict | None:
    target = norm(os.path.abspath(path))
    script = Path(path).suffix.lower() in (".py", ".pyw", ".ps1", ".bat", ".cmd")
    for info in processes:
        if not script:
            if info.get("exe") and norm(info["exe"]) == target:
                return info
            continue
        # Match the script argument, never just python.exe/powershell.exe.
        cmd = info.get("cmdline") or []
        runner = Path(info.get("exe") or (cmd[0] if cmd else "")).name.lower()
        if Path(path).suffix.lower() in (".py", ".pyw"):
            if not runner.startswith(("python", "pypy")): continue
            args = iter(cmd[1:])
            candidate = None
            for arg in args:
                if arg in ("-c", "-m"): break
                if arg in ("-W", "-X"):
                    next(args, None)
                    continue
                if arg.startswith("-"): continue
                candidate = arg
                break
            cmd = [cmd[0], candidate] if candidate else []
        elif runner not in ("powershell.exe", "pwsh.exe", "cmd.exe", "bash", "sh"):
            continue
        for arg in cmd[1:]:
            arg = arg.strip('"')
            if Path(arg).suffix.lower() != Path(path).suffix.lower(): continue
            if not os.path.isabs(arg):
                if not info.get("cwd"): continue
                arg = os.path.join(info["cwd"], arg)
            if norm(os.path.abspath(arg)) == target:
                return info
    return None


def discover_windows_apps() -> list[dict]:
    """Resolve Start menu shortcuts without requiring users to find executables."""
    script = r'''
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$shell = New-Object -ComObject WScript.Shell
$roots = @([Environment]::GetFolderPath('Programs'), [Environment]::GetFolderPath('CommonPrograms'))
$apps = @(foreach ($root in $roots) {
    Get-ChildItem -LiteralPath $root -Filter *.lnk -Recurse -ErrorAction SilentlyContinue | ForEach-Object {
        $link = $shell.CreateShortcut($_.FullName)
        $target = [Environment]::ExpandEnvironmentVariables($link.TargetPath)
        if ($target -match '\.(exe|py|pyw|bat|cmd|ps1)$' -and (Test-Path -LiteralPath $target -PathType Leaf)) {
            [PSCustomObject]@{name=$_.BaseName; path=$target; args=$link.Arguments; working_dir=$link.WorkingDirectory}
        }
    }
})
ConvertTo-Json -InputObject $apps -Compress
'''
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                            capture_output=True, encoding="utf-8", errors="replace", timeout=30,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "Couldn't read Start menu apps.")
    apps = json.loads(result.stdout.lstrip("\ufeff") or "[]")
    unique = {(norm(a["path"]), a["args"]): a for a in apps}
    return sorted(unique.values(), key=lambda a: a["name"].casefold())


def program_choices(windows: list[tuple[int, str]], processes: list[dict], background: bool) -> list[dict]:
    """Windowed programs first (in window order), then optionally every other program once."""
    by_pid = {info.get("pid"): info for info in processes}
    choices, seen = [], set()
    for pid, title in windows:
        info = by_pid.get(pid)
        name = info and (info.get("name") or Path(info.get("exe") or "").name)
        if not name: continue
        choices.append({"title": title, "name": name, "exe": info.get("exe") or ""})
        seen.add((info.get("exe") or name).casefold())
    if background:
        rest = {}
        for info in processes:
            name = info.get("name") or Path(info.get("exe") or "").name
            key = (info.get("exe") or name).casefold()
            if name and key not in seen and key not in rest:
                rest[key] = {"title": "", "name": name, "exe": info.get("exe") or ""}
        choices += sorted(rest.values(), key=lambda c: c["name"].casefold())
    return choices


# ---------- Icons ----------
WINDOWS_ICON_LIBRARIES = (("Windows", r"%SystemRoot%\System32\imageres.dll"),
                          ("Classic", r"%SystemRoot%\System32\shell32.dll"))


def icon_sources(path: str, interpreter: str = "", icon: str = "") -> list[str]:
    """Icon specs to try for a service, best first: its chosen icon, then the program that runs it."""
    ext = Path(path).suffix.lower()
    if ext in (".py", ".pyw"): runners = [interpreter, default_python(), "py.exe"]
    elif ext == ".ps1": runners = ["powershell.exe"]
    elif ext in (".bat", ".cmd"): runners = ["cmd.exe"]
    else: runners = [path]
    return ([icon] if icon else []) + [shutil.which(r) or r for r in runners if r]


def parse_icon_spec(spec: str) -> tuple[str, int]:
    """'C:\\file.dll,12' -> (path, 12). A bare path means its first icon."""
    path, sep, index = spec.rpartition(",")
    if sep and index.strip().isdigit():
        return os.path.expandvars(path.strip().strip('"')), int(index)
    return os.path.expandvars(spec.strip().strip('"')), 0


def png_bytes(w: int, h: int, rgba: bytes) -> bytes:
    stride = w * 4
    raw = b"".join(b"\x00" + bytes(rgba[y * stride:(y + 1) * stride]) for y in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def photo(master, w: int, h: int, rgba: bytes) -> tk.PhotoImage:
    """PhotoImage with a real alpha channel, so it blends into any row or background."""
    return tk.PhotoImage(master=master, data=base64.b64encode(png_bytes(w, h, rgba)), format="png")


@functools.cache
def _win32() -> SimpleNamespace:
    """Win32 functions for reading icons and listing windows, bound with explicit types."""
    import ctypes
    from ctypes import wintypes as w

    class ICONINFO(ctypes.Structure):
        _fields_ = [("fIcon", w.BOOL), ("xHotspot", w.DWORD), ("yHotspot", w.DWORD),
                    ("hbmMask", w.HBITMAP), ("hbmColor", w.HBITMAP)]

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", w.DWORD), ("biWidth", w.LONG), ("biHeight", w.LONG), ("biPlanes", w.WORD),
                    ("biBitCount", w.WORD), ("biCompression", w.DWORD), ("biSizeImage", w.DWORD),
                    ("biXPelsPerMeter", w.LONG), ("biYPelsPerMeter", w.LONG), ("biClrUsed", w.DWORD),
                    ("biClrImportant", w.DWORD)]

    user32, gdi32, dwmapi = ctypes.WinDLL("user32"), ctypes.WinDLL("gdi32"), ctypes.WinDLL("dwmapi")
    enum_proc = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)

    def bind(fn, restype, *argtypes):
        fn.restype, fn.argtypes = restype, list(argtypes)
        return fn

    return SimpleNamespace(
        ctypes=ctypes, w=w, ICONINFO=ICONINFO, BITMAPINFOHEADER=BITMAPINFOHEADER, WNDENUMPROC=enum_proc,
        extract_icons=bind(user32.PrivateExtractIconsW, w.UINT, w.LPCWSTR, ctypes.c_int, ctypes.c_int,
                           ctypes.c_int, ctypes.POINTER(w.HICON), ctypes.POINTER(w.UINT), w.UINT, w.UINT),
        get_icon_info=bind(user32.GetIconInfo, w.BOOL, w.HICON, ctypes.POINTER(ICONINFO)),
        destroy_icon=bind(user32.DestroyIcon, w.BOOL, w.HICON),
        get_dc=bind(user32.GetDC, w.HDC, w.HWND),
        release_dc=bind(user32.ReleaseDC, ctypes.c_int, w.HWND, w.HDC),
        get_dibits=bind(gdi32.GetDIBits, ctypes.c_int, w.HDC, w.HBITMAP, w.UINT, w.UINT, ctypes.c_void_p,
                        ctypes.POINTER(BITMAPINFOHEADER), w.UINT),
        delete_object=bind(gdi32.DeleteObject, w.BOOL, w.HGDIOBJ),
        enum_windows=bind(user32.EnumWindows, w.BOOL, enum_proc, w.LPARAM),
        is_visible=bind(user32.IsWindowVisible, w.BOOL, w.HWND),
        get_owner=bind(user32.GetWindow, w.HWND, w.HWND, w.UINT),
        get_ex_style=bind(user32.GetWindowLongW, w.LONG, w.HWND, ctypes.c_int),
        text_length=bind(user32.GetWindowTextLengthW, ctypes.c_int, w.HWND),
        get_text=bind(user32.GetWindowTextW, ctypes.c_int, w.HWND, w.LPWSTR, ctypes.c_int),
        get_class=bind(user32.GetClassNameW, ctypes.c_int, w.HWND, w.LPWSTR, ctypes.c_int),
        get_pid=bind(user32.GetWindowThreadProcessId, w.DWORD, w.HWND, ctypes.POINTER(w.DWORD)),
        dwm_attribute=bind(dwmapi.DwmGetWindowAttribute, ctypes.c_long, w.HWND, w.DWORD, ctypes.c_void_p, w.DWORD),
    )


def _icon_pixels(api: SimpleNamespace, hicon, size: int) -> bytearray | None:
    """RGBA pixels of an icon handle."""
    ctypes = api.ctypes
    info = api.ICONINFO()
    if not api.get_icon_info(hicon, ctypes.byref(info)): return None
    hdc = api.get_dc(None)
    try:
        if not info.hbmColor: return None   # monochrome icons are too rare to bother with
        header = api.BITMAPINFOHEADER(biSize=ctypes.sizeof(api.BITMAPINFOHEADER), biWidth=size, biHeight=-size,
                                      biPlanes=1, biBitCount=32)
        buf = ctypes.create_string_buffer(size * size * 4)
        if api.get_dibits(hdc, info.hbmColor, 0, size, buf, ctypes.byref(header), 0) != size: return None
        pixels = bytearray(buf.raw)
        if not any(pixels[3::4]):   # older icons have no alpha channel and use their mask instead
            mask = ctypes.create_string_buffer(size * size * 4)
            header.biBitCount, header.biCompression = 32, 0
            if api.get_dibits(hdc, info.hbmMask, 0, size, mask, ctypes.byref(header), 0) != size: return None
            pixels[3::4] = bytes(0 if m else 255 for m in mask.raw[0::4])
    finally:
        api.release_dc(None, hdc)
        for bitmap in (info.hbmColor, info.hbmMask):
            if bitmap: api.delete_object(bitmap)
    pixels[0::4], pixels[2::4] = pixels[2::4], pixels[0::4]   # BGRA -> RGBA
    return pixels


def icon_count(path: str) -> int:
    if not IS_WINDOWS or not Path(path).is_file(): return 0
    n = _win32().extract_icons(path, 0, 0, 0, None, None, 0, 0)
    return 0 if n == 0xFFFFFFFF else n


def load_icons(path: str, size: int, start: int = 0, count: int = 1) -> list[bytearray | None]:
    """RGBA pixels for icons in an .exe, .dll or .ico file, scaled by Windows to `size`."""
    if not IS_WINDOWS or count < 1 or not Path(path).is_file(): return []
    api = _win32()
    handles = (api.w.HICON * count)()
    ids = (api.w.UINT * count)()
    n = api.extract_icons(path, start, size, size, handles, ids, count, 0)
    if n == 0xFFFFFFFF: return []
    result = []
    for handle in handles[:n]:
        if not handle:
            result.append(None)
            continue
        try: result.append(_icon_pixels(api, handle, size))
        finally: api.destroy_icon(handle)
    return result


def visible_windows() -> list[tuple[int, str]]:
    """(pid, title) of top-level app windows — the same kind of list OBS shows for game capture."""
    if not IS_WINDOWS: return []
    api = _win32()
    ctypes = api.ctypes
    found = []

    def visit(hwnd, _):
        if not api.is_visible(hwnd) or api.get_owner(hwnd, 4): return True    # GW_OWNER: dialogs, popups
        if api.get_ex_style(hwnd, -20) & 0x80: return True                      # WS_EX_TOOLWINDOW
        cloaked = api.w.DWORD()
        if api.dwm_attribute(hwnd, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked)) == 0 and cloaked.value:
            return True                                                          # DWMWA_CLOAKED: hidden UWP
        length = api.text_length(hwnd)
        if not length: return True
        title = ctypes.create_unicode_buffer(length + 1)
        api.get_text(hwnd, title, length + 1)
        cls = ctypes.create_unicode_buffer(64)
        api.get_class(hwnd, cls, 64)
        if cls.value in ("Progman", "WorkerW", "Shell_TrayWnd"): return True
        pid = api.w.DWORD()
        api.get_pid(hwnd, ctypes.byref(pid))
        found.append((pid.value, title.value))
        return True

    api.enum_windows(api.WNDENUMPROC(visit), 0)
    return found


class Service:
    _ids = itertools.count(1)

    def __init__(self, path: str, name: str = "", args: str = "", interpreter: str = "",
                 auto_restart: bool = False, working_dir: str = "", icon: str = ""):
        self.id = f"svc{next(Service._ids)}"
        self.path = path
        self.name = name or suggested_service_name(path)
        self.args = args
        self.interpreter = interpreter
        self.auto_restart = auto_restart
        self.working_dir = working_dir
        self.icon = icon                     # "file,index" chosen by the user; empty = the program's own
        self.external: dict | None = None
        self.proc: subprocess.Popen | None = None
        self.start_time: float | None = None
        self.last_returncode: int | None = None
        self.restart_pending = False        # start again once the current stop completes
        self.restart_at: float | None = None  # scheduled auto-restart after a crash
        self.crash_count = 0
        self._stopping = False
        self._log_handle = None

    @property
    def display_name(self): return self.name or Path(self.path).name
    @property
    def log_path(self):
        if Path(self.path).suffix.lower() == ".exe":
            folder = Path(os.environ.get("LOCALAPPDATA", str(APP_DIR))) / APP_NAME / "logs"
            return folder / (Path(self.path).stem + "-" + hashlib.sha256(norm(self.path).encode()).hexdigest()[:10] + ".log")
        return Path(self.path).with_suffix(".log")
    @property
    def exists(self): return Path(self.path).is_file()
    @property
    def is_running(self): return bool(self.external) or (self.proc is not None and self.proc.poll() is None and not self._stopping)
    @property
    def is_stopping(self): return self._stopping
    @property
    def is_active(self): return self.is_running or self._stopping
    @property
    def pid(self): return self.external["pid"] if self.external else (self.proc.pid if self.proc is not None and self.proc.poll() is None else "")

    def detect_existing(self, processes: list[dict] | None = None):
        if self.proc is not None: return
        self.external = matching_process(self.path, processes if processes is not None else process_snapshot())
        self.start_time = self.external.get("create_time") if self.external else None

    @property
    def mtime(self):
        try: return time.strftime("%d %b %Y, %H:%M", time.localtime(os.path.getmtime(self.path)))
        except OSError: return "N/A"

    def status(self) -> tuple[str, str]:
        """(key, label) describing the current state."""
        if self._stopping: return ("stopping", "Restarting…" if self.restart_pending else "Stopping…")
        if self.is_running: return ("running", "Running elsewhere" if self.external else "Running")
        if not self.exists: return ("missing", "File missing")
        if self.restart_at:
            return ("restarting", f"Restarting in {max(0, int(self.restart_at - time.time() + 0.99))}s")
        if self.last_returncode is not None:
            if self.last_returncode == 0: return ("stopped", "Finished")
            return ("exited", f"Exited ({self.last_returncode})")
        return ("stopped", "Stopped")

    def command(self) -> list[str] | str:
        ext = Path(self.path).suffix.lower()
        if ext in (".py", ".pyw"):
            prefix = [self.interpreter or default_python(), self.path]
        elif ext == ".exe":
            if not IS_WINDOWS: raise RuntimeError(".exe services are only supported on Windows.")
            prefix = [self.path]
        elif ext in (".bat", ".cmd") and IS_WINDOWS:
            prefix = ["cmd.exe", "/c", self.path]
        elif ext == ".ps1":
            prefix = ["powershell" if IS_WINDOWS else "pwsh", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", self.path]
        else:
            prefix = [self.path]
        args = self.args.strip()
        if IS_WINDOWS:
            # Windows takes a single command line; pass user arguments through verbatim.
            return subprocess.list2cmdline(prefix) + (f" {args}" if args else "")
        return prefix + shlex.split(args)

    def start(self):
        if self.proc is not None and self.proc.poll() is None: return
        if self._stopping: return
        self.detect_existing()
        if self.external:
            self.restart_at = None
            return False
        if not self.exists: raise FileNotFoundError(f"File not found: {self.path}")
        cmd = self.command()
        self.restart_at = None
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        log = open(self.log_path, "a", encoding="utf-8", errors="replace")
        log.write(f"\n=== START {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
        log.flush()
        env = os.environ.copy()
        env.setdefault("PYTHONUNBUFFERED", "1")   # so output shows up live in the log
        env.setdefault("PYTHONIOENCODING", "utf-8")
        kwargs = dict(stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                      cwd=self.working_dir or str(Path(self.path).parent), env=env)
        if IS_WINDOWS:
            flags = subprocess.CREATE_NEW_PROCESS_GROUP
            if not HAS_CONSOLE: flags |= subprocess.CREATE_NO_WINDOW
            kwargs["creationflags"] = flags
        else:
            kwargs["start_new_session"] = True
        try:
            self.proc = subprocess.Popen(cmd, **kwargs)
        except Exception:
            log.close()
            raise
        self._log_handle = log
        self.start_time = time.time()
        self.last_returncode = None
        return True

    def stop(self, restart: bool = False):
        """Stop in the background: graceful signal, then kill the process tree."""
        self.restart_at = None
        if self.external: return  # Already-running apps belong to the user, not this launcher.
        if self._stopping:
            self.restart_pending = self.restart_pending or restart
            return
        proc = self.proc
        if proc is None: return
        if proc.poll() is not None:
            self._finish(proc, by_user=True)
            return
        self._stopping = True
        self.restart_pending = restart
        threading.Thread(target=self._stop_worker, args=(proc,), daemon=True).start()

    def _stop_worker(self, proc: subprocess.Popen):
        try:
            if _send_graceful(proc):
                try: proc.wait(STOP_GRACE_SECONDS)
                except subprocess.TimeoutExpired: pass
            if proc.poll() is None: _kill_tree(proc)
            try: proc.wait(5)
            except subprocess.TimeoutExpired: pass
        finally:
            self._finish(proc, by_user=True)
            self._stopping = False

    def poll(self) -> int | None:
        """Detect an exit we didn't ask for. Returns the exit code when it just happened."""
        if self._stopping or self.proc is None: return None
        rc = self.proc.poll()
        if rc is None: return None
        ran_for = time.time() - (self.start_time or time.time())
        self._finish(self.proc, by_user=False)
        if self.auto_restart and rc != 0:
            if ran_for > 60: self.crash_count = 0
            self.crash_count += 1
            self.restart_at = time.time() + min(60, 2 ** (self.crash_count - 1))
        return rc

    def _finish(self, proc: subprocess.Popen, by_user: bool):
        rc = proc.returncode
        self.last_returncode = None if by_user else rc
        self.proc = None
        self.start_time = None
        if self._log_handle:
            try:
                what = "STOPPED" if by_user else f"EXITED ({rc})"
                self._log_handle.write(f"=== {what} {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
                self._log_handle.close()
            except Exception:
                pass
            self._log_handle = None

    def to_dict(self):
        return {"path": self.path, "name": self.name, "args": self.args,
                "interpreter": self.interpreter, "auto_restart": self.auto_restart,
                "working_dir": self.working_dir, "icon": self.icon}


@dataclass
class Group:
    name: str
    services: list[str] = field(default_factory=list)  # paths, in start order
    autostart: bool = False
    start_delay: float = 1.0                           # seconds between starting each service
    trigger_process: str = ""                         # executable name or full path; empty disables


@dataclass
class Settings:
    start_minimized: bool = True    # when launched at sign-in
    autostart_delay: float = 1.0    # seconds after launch before autostart groups run
    confirm_exit: bool = True
    window_size: str = ""
    maximized: bool = False


# ---------- Platform integration ----------
def login_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --startup'
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    return f'"{pyw if pyw.exists() else exe}" "{Path(__file__).resolve()}" --startup'


def get_launch_at_login() -> str | None:
    if not IS_WINDOWS: return None
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            return winreg.QueryValueEx(key, APP_NAME)[0]
    except OSError:
        return None


def set_launch_at_login(enabled: bool):
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, login_command())
        else:
            try: winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError: pass


def enable_dpi_awareness():
    if not IS_WINDOWS: return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_NAME)
        try: ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception: ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def apply_titlebar(win: tk.Misc):
    """Colour the Windows 11 title bar in the icon's green, with ivory text."""
    if not IS_WINDOWS: return
    try:
        import ctypes
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        set_attr = ctypes.windll.dwmapi.DwmSetWindowAttribute
        on = ctypes.c_int(1)
        if set_attr(hwnd, 20, ctypes.byref(on), ctypes.sizeof(on)) != 0:  # DWMWA_USE_IMMERSIVE_DARK_MODE
            set_attr(hwnd, 19, ctypes.byref(on), ctypes.sizeof(on))
        for attr, color in ((34, C.SIDEBAR), (35, C.SIDEBAR), (36, C.SIDE_TEXT)):  # border, caption, text
            r, g, b = hex_rgb(color)
            value = ctypes.c_int(b << 16 | g << 8 | r)
            set_attr(hwnd, attr, ctypes.byref(value), ctypes.sizeof(value))
    except Exception:
        pass


def acquire_single_instance():
    """Listening socket if we're the first instance, False if an existing one was asked to show itself,
    None if we couldn't tell (run anyway)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if IS_WINDOWS:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    try:
        sock.bind(("127.0.0.1", INSTANCE_PORT))
        sock.listen(4)
        return sock
    except OSError:
        sock.close()
    try:
        with socket.create_connection(("127.0.0.1", INSTANCE_PORT), timeout=1) as conn:
            conn.sendall(b"workaholic:show")
            if conn.recv(32).startswith(b"workaholic:ok"):
                return False
    except OSError:
        pass
    return None


# ---------- Drawing ----------
def hex_rgb(color: str) -> tuple[int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def raster(master, w: int, h: int, layers, ss: int = 4) -> tk.PhotoImage:
    """Rasterise shapes into a PhotoImage with supersampled, alpha-blended edges."""
    layers = [(hex_rgb(color), inside) for color, inside in layers]
    n = ss * ss
    pixels = bytearray(w * h * 4)
    for y in range(h):
        for x in range(w):
            r = g = b = hits = 0
            for sy in range(ss):
                for sx in range(ss):
                    fx, fy = x + (sx + 0.5) / ss, y + (sy + 0.5) / ss
                    col = None
                    for c, inside in layers:
                        if inside(fx, fy): col = c
                    if col:
                        r += col[0]; g += col[1]; b += col[2]; hits += 1
            if hits:
                i = (y * w + x) * 4
                pixels[i:i + 4] = bytes((r // hits, g // hits, b // hits, 255 * hits // n))
    return photo(master, w, h, pixels)


def circle(cx, cy, r):
    return lambda x, y: (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def rrect(x0, y0, x1, y1, r):
    def inside(x, y):
        if x < x0 or x > x1 or y < y0 or y > y1: return False
        dx = max(x0 + r - x, 0, x - (x1 - r))
        dy = max(y0 + r - y, 0, y - (y1 - r))
        return dx * dx + dy * dy <= r * r
    return inside


def segment(ax, ay, bx, by, t):
    vx, vy = bx - ax, by - ay
    length = vx * vx + vy * vy

    def inside(x, y):
        u = max(0.0, min(1.0, ((x - ax) * vx + (y - ay) * vy) / length))
        dx, dy = ax + u * vx - x, ay + u * vy - y
        return dx * dx + dy * dy <= (t / 2) ** 2
    return inside


def make_logo(master, s: int, ss: int = 3) -> tk.PhotoImage:
    """Fallback for when the bundled icon can't be read: an ivory tile with green rows."""
    layers = [(C.ON_ACCENT, rrect(0, 0, s, s, s * 0.26))]
    for i in range(3):
        y = s * (0.3 + i * 0.2)
        layers.append((C.ACCENT, circle(s * 0.3, y, s * 0.065)))
        layers.append((C.ACCENT, rrect(s * 0.42, y - s * 0.045, s * 0.74, y + s * 0.045, s * 0.045)))
    return raster(master, s, s, layers, ss=ss)


def make_program_icon(master, s: int) -> tk.PhotoImage:
    """Generic icon for services whose own icon can't be read: a green tile with a prompt."""
    t = s * 0.1
    return raster(master, s, s, [(C.ACCENT, rrect(s * 0.06, s * 0.06, s * 0.94, s * 0.94, s * 0.22)),
                                 (C.ON_ACCENT, segment(s * 0.3, s * 0.34, s * 0.48, s * 0.5, t)),
                                 (C.ON_ACCENT, segment(s * 0.48, s * 0.5, s * 0.3, s * 0.66, t)),
                                 (C.ON_ACCENT, segment(s * 0.56, s * 0.68, s * 0.72, s * 0.68, t))], ss=3)


def make_checkbox(master, size: int, width: int, checked: bool) -> tk.PhotoImage:
    s = size
    if checked:
        layers = [(C.ACCENT, rrect(0.5, 0.5, s - 0.5, s - 0.5, s * 0.22)),
                  (C.ON_ACCENT, segment(s * 0.26, s * 0.52, s * 0.43, s * 0.69, s * 0.13)),
                  (C.ON_ACCENT, segment(s * 0.43, s * 0.69, s * 0.75, s * 0.32, s * 0.13))]
    else:
        layers = [(C.BORDER_STRONG, rrect(0.5, 0.5, s - 0.5, s - 0.5, s * 0.22)),
                  (C.INPUT, rrect(1.6, 1.6, s - 1.6, s - 1.6, s * 0.16))]
    return raster(master, width, s, layers)


# ---------- Small widgets ----------
class Tooltip:
    def __init__(self, widget: tk.Widget, text: str):
        self.widget, self.text = widget, text
        self.tip: tk.Toplevel | None = None
        self.job = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _e=None):
        self._cancel()
        self.job = self.widget.after(600, self._show)

    def _cancel(self):
        if self.job:
            self.widget.after_cancel(self.job)
            self.job = None

    def _show(self):
        self.job = None
        if self.tip or not self.widget.winfo_ismapped(): return
        tw = self.tip = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.attributes("-topmost", True)
        tw.configure(bg=C.NAV_SELECTED)
        tk.Label(tw, text=self.text, bg=C.SIDEBAR, fg=C.SIDE_TEXT, font="TkTooltipFont",
                 padx=8, pady=4).pack(padx=1, pady=1)
        x = self.widget.winfo_rootx()
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        tw.wm_geometry(f"+{x}+{y}")

    def _hide(self, _e=None):
        self._cancel()
        if self.tip:
            self.tip.destroy()
            self.tip = None


class NavRow(tk.Frame):
    """A clickable sidebar entry with icon, label and badge."""

    def __init__(self, parent, app: App, icon: str, text: str, on_click, on_menu=None, on_double=None,
                 icon_fg: str = C.SIDE_MUTED):
        super().__init__(parent, bg=C.SIDEBAR, cursor="hand2")
        self.app = app
        self._selected = self._hover = False
        self._text = None
        px = app.px
        self.bar = tk.Frame(self, width=px(3), bg=C.SIDEBAR)
        self.bar.pack(side="left", fill="y")
        self.icon = tk.Label(self, text=icon, fg=icon_fg, font=app.f_icon, width=2, bg=C.SIDEBAR)
        self.icon.pack(side="left", padx=(px(6), px(4)), pady=px(5))
        self.badge = tk.Label(self, fg=C.SIDE_DIM, font=app.f_small, bg=C.SIDEBAR)
        self.badge.pack(side="right", padx=(px(4), px(10)))
        self.extra = tk.Label(self, fg=C.SIDE_AMBER, font=app.f_small, bg=C.SIDEBAR)
        self.extra.pack(side="right")
        self.label = tk.Label(self, fg=C.SIDE_TEXT, font=app.f_base, anchor="w", bg=C.SIDEBAR)
        self.label.pack(side="left", fill="x", expand=True)
        for w in (self, self.bar, self.icon, self.label, self.badge, self.extra):
            w.bind("<Enter>", lambda e: self._set_hover(True))
            w.bind("<Leave>", lambda e: self._set_hover(False))
            w.bind("<Button-1>", lambda e: on_click())
            if on_menu:
                w.bind("<Button-3>", on_menu)
                if sys.platform == "darwin": w.bind("<Button-2>", on_menu)
            if on_double: w.bind("<Double-1>", lambda e: on_double())
        self.set_state(text=text)
        self._paint()

    def _set_hover(self, hover: bool):
        self._hover = hover
        self._paint()

    def _paint(self):
        bg = C.NAV_SELECTED if self._selected else C.NAV_HOVER if self._hover else C.SIDEBAR
        for w in (self, self.icon, self.label, self.badge, self.extra):
            w.configure(bg=bg)
        self.bar.configure(bg=C.SIDE_ACCENT if self._selected else bg)
        self.label.configure(font=self.app.f_bold if self._selected else self.app.f_base,
                             fg=C.SIDE_TEXT if self._selected else C.SIDE_MUTED)

    def set_state(self, selected=None, text=None, badge=None, badge_fg=None, extra=None):
        if selected is not None and selected != self._selected:
            self._selected = selected
            self._paint()
        if text is not None and text != self._text:
            self._text = text
            self.label.configure(text=self.app.ellipsize(text, self.app.px(128)))
        if badge is not None and badge != self.badge.cget("text"):
            self.badge.configure(text=badge)
        if badge_fg is not None and badge_fg != self.badge.cget("fg"):
            self.badge.configure(fg=badge_fg)
        if extra is not None and extra != self.extra.cget("text"):
            self.extra.configure(text=extra)


class SearchEntry(tk.Frame):
    def __init__(self, parent, app: App, placeholder: str, on_change):
        super().__init__(parent, bg=C.BG)
        self.var = tk.StringVar(self)
        self.entry = ttk.Entry(self, textvariable=self.var, width=30)
        self.entry.pack(fill="x")
        self.placeholder = tk.Label(self, text=placeholder, fg=C.DIM, bg=C.INPUT, font=app.f_base, cursor="xterm")
        self.placeholder.bind("<Button-1>", lambda e: self.entry.focus_set())
        self._x = app.px(10)
        self.var.trace_add("write", lambda *_: (self._sync(), on_change()))
        self.entry.bind("<FocusIn>", self._sync, add="+")
        self.entry.bind("<FocusOut>", self._sync, add="+")
        self.entry.bind("<Escape>", lambda e: self.var.set(""))
        self.after_idle(self._sync)

    def _sync(self, *_):
        try: focused = self.focus_get() is self.entry
        except (KeyError, tk.TclError): focused = False
        if self.var.get() or focused: self.placeholder.place_forget()
        else: self.placeholder.place(x=self._x, rely=0.5, anchor="w")

    def get(self) -> str: return self.var.get().strip()
    def clear(self): self.var.set("")

    def focus(self):
        self.entry.focus_set()
        self.entry.select_range(0, "end")


# ---------- Dialogs ----------
class Dialog(tk.Toplevel):
    def __init__(self, app: App, title: str, parent: tk.Misc | None = None):
        super().__init__(parent or app)
        self.withdraw()
        self.app = app
        self.result = None
        self.initial_focus: tk.Widget | None = None
        self.title(title)
        self.configure(bg=C.BG)
        self.transient(parent or app)
        self.resizable(False, False)
        px = app.px
        self.body = tk.Frame(self, bg=C.BG)
        self.body.pack(fill="both", expand=True, padx=px(26), pady=(px(22), px(18)))
        tk.Frame(self, bg=C.BORDER, height=1).pack(fill="x")
        footer = tk.Frame(self, bg=C.FOOTER)
        footer.pack(fill="x")
        self.footer = tk.Frame(footer, bg=C.FOOTER)
        self.footer.pack(fill="x", padx=px(26), pady=px(14))
        self.error = tk.Label(self.footer, text="", fg=C.RED, bg=C.FOOTER, font=app.f_small, anchor="w")
        self.error.pack(side="left", fill="x", expand=True)
        self.bind("<Escape>", lambda e: self.cancel())
        self.protocol("WM_DELETE_WINDOW", self.cancel)

    # layout helpers
    def heading(self, title: str, subtitle: str = ""):
        tk.Label(self.body, text=title, font=self.app.f_heading, fg=C.TEXT, bg=C.BG, anchor="w").pack(fill="x")
        if subtitle:
            tk.Label(self.body, text=subtitle, fg=C.MUTED, bg=C.BG, font=self.app.f_base, anchor="w",
                     justify="left", wraplength=self.app.px(470)).pack(fill="x", pady=(self.app.px(3), 0))

    def section(self, text: str, parent=None, top: int = 18):
        tk.Label(parent or self.body, text=text.upper(), font=self.app.f_section, fg=C.DIM, bg=C.BG,
                 anchor="w").pack(fill="x", pady=(self.app.px(top), self.app.px(6)))

    def field(self, label: str, hint: str = "", parent=None) -> tk.Frame:
        parent = parent or self.body
        px = self.app.px
        tk.Label(parent, text=label, font=self.app.f_small_bold, fg=C.MUTED, bg=C.BG, anchor="w").pack(
            fill="x", pady=(px(12), px(4)))
        row = tk.Frame(parent, bg=C.BG)
        row.pack(fill="x")
        if hint:
            tk.Label(parent, text=hint, font=self.app.f_small, fg=C.DIM, bg=C.BG, anchor="w", justify="left",
                     wraplength=px(470)).pack(fill="x", pady=(px(4), 0))
        return row

    def add_buttons(self, ok_text: str = "Save"):
        ttk.Button(self.footer, text=ok_text, style="Accent.TButton", command=self.ok).pack(side="right")
        ttk.Button(self.footer, text="Cancel", command=self.cancel).pack(side="right", padx=(0, self.app.px(8)))
        self.bind("<Return>", lambda e: self.ok())

    def show(self):
        self.update_idletasks()
        apply_titlebar(self)
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        owner = self.master
        if self.app.state() == "iconic":
            x, y = (self.winfo_screenwidth() - w) // 2, (self.winfo_screenheight() - h) // 3
        else:
            x = owner.winfo_rootx() + (owner.winfo_width() - w) // 2
            y = owner.winfo_rooty() + max(0, (owner.winfo_height() - h) // 3)
        self.geometry(f"+{max(0, x)}+{max(0, y)}")
        self.deiconify()
        try: previous = self.grab_current()   # the dialog this one was opened from
        except KeyError: previous = None
        self.grab_set()
        (self.initial_focus or self).focus_set()
        self.wait_window()
        if previous and previous.winfo_exists():
            previous.grab_set()
            previous.focus_set()
        return self.result

    def fail(self, message: str):
        self.error.configure(text=message)

    def validate(self):
        return True

    def ok(self):
        result = self.validate()
        if result is not None:
            self.result = result
            self.destroy()

    def cancel(self):
        self.destroy()


class GroupDialog(Dialog):
    def __init__(self, app: App, group: Group | None = None, preselect: list[Service] = ()):
        super().__init__(app, "Edit group" if group else "New group")
        self.group = group
        px = app.px
        self.heading("Edit group" if group else "Create a group",
                     "Groups start several services together — in the order you choose, "
                     "and optionally whenever Workaholic opens.")

        self.name_var = tk.StringVar(self, value=group.name if group else "")
        entry = ttk.Entry(self.field("Name"), textvariable=self.name_var, width=52)
        entry.pack(fill="x")
        self.initial_focus = entry

        index = app.service_index()
        members = [index.get(norm(p)) for p in group.services] if group else list(preselect)
        members = [s for s in members if s]
        self.order = members + [s for s in app.services if s not in members]
        self.checked = {s.id for s in members}

        row = self.field("Services", "Click the box to include a service. Checked services start from top to "
                                     "bottom — use the arrows to change the order.")
        box = tk.Frame(row, bg=C.INPUT, highlightthickness=1, highlightbackground=C.BORDER_STRONG)
        box.pack(side="left", fill="both", expand=True)
        self.tree = ttk.Treeview(box, style="List.Treeview", show="tree", selectmode="browse",
                                 height=min(8, max(4, len(self.order))))
        self.tree.column("#0", width=px(380))
        sb = ttk.Scrollbar(box, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True, padx=px(2), pady=px(2))
        self.tree.bind("<Button-1>", self._on_click)
        self.tree.bind("<Double-1>", lambda e: self._toggle(self.tree.identify_row(e.y)))
        self.tree.bind("<space>", lambda e: self._toggle(self.tree.focus()))
        side = tk.Frame(row, bg=C.BG)
        side.pack(side="left", fill="y", padx=(px(8), 0))
        for text, delta, tip in (("↑", -1, "Start earlier"), ("↓", 1, "Start later")):
            b = ttk.Button(side, text=text, style="Icon.TButton", command=lambda d=delta: self._move(d))
            b.pack(pady=(0, px(6)))
            Tooltip(b, tip)
        if not self.order:
            tk.Label(box, text="No services yet — add some first.", fg=C.DIM, bg=C.INPUT,
                     font=app.f_base).place(relx=0.5, rely=0.5, anchor="center")

        self.section("Startup")
        self.autostart_var = tk.BooleanVar(self, value=group.autostart if group else False)
        ttk.Checkbutton(self.body, text="Start this group automatically when Workaholic opens",
                        variable=self.autostart_var).pack(anchor="w")
        trigger_row = self.field("Start when this process runs",
                                 "Optional: pick a game or app that's running now, type a name such as "
                                 "ffxiv_dx11.exe, or browse for an executable. Workaholic must stay open. "
                                 "Closing the process leaves the group running.")
        self.trigger_var = tk.StringVar(self, value=group.trigger_process if group else "")
        ttk.Entry(trigger_row, textvariable=self.trigger_var).pack(side="left", fill="x", expand=True)
        ttk.Button(trigger_row, text="Pick running app…", command=self._pick_trigger).pack(side="left", padx=(px(8), 0))
        ttk.Button(trigger_row, text="Browse…", command=self._browse_trigger).pack(side="left", padx=(px(8), 0))
        delay_row = tk.Frame(self.body, bg=C.BG)
        delay_row.pack(fill="x", pady=(px(10), 0))
        tk.Label(delay_row, text="Wait", fg=C.TEXT, bg=C.BG, font=app.f_base).pack(side="left")
        self.delay_var = tk.StringVar(self, value=f"{group.start_delay if group else 1.0:g}")
        ttk.Spinbox(delay_row, from_=0, to=600, increment=0.5, width=6, textvariable=self.delay_var).pack(
            side="left", padx=px(8))
        tk.Label(delay_row, text="seconds between starting each service", fg=C.TEXT, bg=C.BG,
                 font=app.f_base).pack(side="left")

        self.add_buttons("Save" if group else "Create group")
        self._render()

    def _pick_trigger(self):
        target = ProcessPickerDialog(self.app, self).show()
        if target: self.trigger_var.set(target)

    def _browse_trigger(self):
        path = filedialog.askopenfilename(parent=self, title="Choose the process that starts this group",
                                          filetypes=[("Programs", "*.exe"), ("All files", "*.*")])
        if path: self.trigger_var.set(path)

    def _render(self, select: str | None = None):
        select = select or self.tree.focus()
        self.tree.delete(*self.tree.get_children())
        for s in self.order:
            img = self.app.img_list_on if s.id in self.checked else self.app.img_list_off
            folder = Path(s.path).parent.name
            self.tree.insert("", "end", iid=s.id, image=img, text=f"{s.display_name}   ·   {folder}")
        if select and self.tree.exists(select):
            self.tree.selection_set(select)
            self.tree.focus(select)
            self.tree.see(select)

    def _on_click(self, e):
        iid = self.tree.identify_row(e.y)
        if iid and e.x < self.app.px(30):
            self._toggle(iid)

    def _toggle(self, iid: str):
        if not iid: return
        self.checked.symmetric_difference_update({iid})
        self._render(iid)

    def _move(self, delta: int):
        iid = self.tree.focus()
        idx = next((i for i, s in enumerate(self.order) if s.id == iid), None)
        if idx is None or not 0 <= idx + delta < len(self.order): return
        self.order[idx], self.order[idx + delta] = self.order[idx + delta], self.order[idx]
        self._render(iid)

    def validate(self):
        name = self.name_var.get().strip()
        if not name:
            return self.fail("Give the group a name.")
        if any(g.name.lower() == name.lower() for g in self.app.groups if g is not self.group):
            return self.fail(f"A group named “{name}” already exists.")
        try:
            delay = float(self.delay_var.get())
            if not 0 <= delay <= 600: raise ValueError
        except ValueError:
            return self.fail("The delay must be a number between 0 and 600 seconds.")
        return Group(name=name, services=[s.path for s in self.order if s.id in self.checked],
                     autostart=self.autostart_var.get(), start_delay=delay,
                     trigger_process=self.trigger_var.get().strip().strip('"'))


class AddServiceDialog(Dialog):
    def __init__(self, app: App):
        super().__init__(app, "Add apps or services")
        self.heading("Add apps or services", "Search your Start menu apps, or browse for scripts and programs.")
        self.apps = []
        self.incoming = queue.Queue()
        self.query = tk.StringVar(self)
        entry = ttk.Entry(self.body, textvariable=self.query, width=64)
        entry.pack(fill="x", pady=(app.px(16), app.px(10)))
        self.initial_focus = entry
        self.query.trace_add("write", lambda *_: self._filter())
        self.tree = ttk.Treeview(self.body, columns=("location",), selectmode="extended", height=12)
        self.tree.heading("#0", text="App")
        self.tree.heading("location", text="Location")
        self.tree.column("#0", width=app.px(220))
        self.tree.column("location", width=app.px(320))
        scrollbar = ttk.Scrollbar(self.body, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<Double-1>", lambda e: self.ok())
        self.add_buttons("Add selected")
        ttk.Button(self.footer, text="Browse files…", command=self._browse).pack(side="right", padx=(0, app.px(8)))
        self.error.configure(text="Loading apps…", fg=C.MUTED)
        threading.Thread(target=self._load, daemon=True).start()
        self.after(100, self._receive)

    def _load(self):
        try: self.incoming.put(discover_windows_apps() if IS_WINDOWS else [])
        except Exception as e: self.incoming.put(e)

    def _receive(self):
        try: result = self.incoming.get_nowait()
        except queue.Empty:
            self.after(100, self._receive)
            return
        if isinstance(result, Exception):
            self.error.configure(text="Couldn't load apps. Use Browse files.", fg=C.RED)
            return
        self.apps = result
        self._filter()

    def _filter(self):
        self.tree.delete(*self.tree.get_children())
        query = self.query.get().strip().casefold()
        for i, app in enumerate(self.apps):
            if all(word in app["name"].casefold() for word in query.split()):
                self.tree.insert("", "end", iid=str(i), text=app["name"], values=(app["path"],))
        count = len(self.tree.get_children())
        self.error.configure(text=f"{count} apps" if count else "No apps found. Use Browse files.", fg=C.MUTED)

    def _browse(self):
        paths = filedialog.askopenfilenames(parent=self, title="Add services",
                    filetypes=[("Scripts and programs", SCRIPT_TYPES), ("All files", "*.*")])
        if paths:
            self.result = [{"path": p} for p in paths]
            self.destroy()

    def validate(self):
        selected = self.tree.selection()
        if not selected: return self.fail("Select an app, or use Browse files.")
        return [self.apps[int(i)] for i in selected]


class ServiceDialog(Dialog):
    def __init__(self, app: App, service: Service):
        super().__init__(app, "Edit service")
        self.service = service
        px = app.px
        self.heading("Edit service", f"Output: {service.log_path}")
        locked = service.is_active

        self.name_var = tk.StringVar(self, value=service.name)
        e = ttk.Entry(self.field("Display name", f"Leave empty to use the file name ({Path(service.path).name})."),
                      textvariable=self.name_var, width=56)
        e.pack(fill="x")
        self.initial_focus = e

        self.path_var = tk.StringVar(self, value=service.path)
        row = self.field("File", "Stop the service to change its file." if locked else "")
        pe = ttk.Entry(row, textvariable=self.path_var)
        pe.pack(side="left", fill="x", expand=True)
        pb = ttk.Button(row, text="Browse…", command=self._browse_path)
        pb.pack(side="left", padx=(px(8), 0))

        self.args_var = tk.StringVar(self, value=service.args)
        ttk.Entry(self.field("Arguments", "Passed to the program, e.g. --port 8080"),
                  textvariable=self.args_var).pack(fill="x")

        self.interp_var = tk.StringVar(self, value=service.interpreter)
        row = self.field("Python interpreter", f"Only used for .py files. Leave empty to use {default_python()}")
        ie = ttk.Entry(row, textvariable=self.interp_var)
        ie.pack(side="left", fill="x", expand=True)
        ib = ttk.Button(row, text="Browse…", command=self._browse_interp)
        ib.pack(side="left", padx=(px(8), 0))
        self.cwd_var = tk.StringVar(self, value=service.working_dir)
        ce = ttk.Entry(self.field("Working folder", "Leave empty to use the program's folder."),
                       textvariable=self.cwd_var)
        ce.pack(fill="x")

        self.icon = service.icon
        row = self.field("Icon")
        self.icon_preview = tk.Label(row, bg=C.BG)
        self.icon_preview.pack(side="left")
        self.icon_label = tk.Label(row, fg=C.MUTED, bg=C.BG, font=app.f_small, anchor="w")
        self.icon_label.pack(side="left", fill="x", expand=True, padx=(px(10), 0))
        self.icon_auto = ttk.Button(row, text="Use automatic", command=lambda: self._set_icon(""))
        self.icon_auto.pack(side="right")
        ttk.Button(row, text="Choose…", command=self._choose_icon).pack(side="right", padx=(0, px(8)))
        for var in (self.path_var, self.interp_var): var.trace_add("write", lambda *_: self._set_icon(self.icon))
        self._set_icon(self.icon)
        if locked:
            for w in (pe, pb, ie, ib, ce): w.state(["disabled"])

        self.section("Reliability")
        self.restart_var = tk.BooleanVar(self, value=service.auto_restart)
        ttk.Checkbutton(self.body, text="Restart automatically if it crashes",
                        variable=self.restart_var).pack(anchor="w")
        self.add_buttons()

    def _set_icon(self, spec: str):
        self.icon = spec
        path, interp = self.path_var.get().strip(), self.interp_var.get().strip()
        self.icon_preview.configure(image=self.app.icon_image(icon_sources(path, interp, spec), self.app.px(32)))
        if spec:
            file, index = parse_icon_spec(spec)
            text = f"{Path(file).name}, icon {index + 1}"
        elif Path(path).suffix.lower() in (".py", ".pyw"): text = "Automatic — Python's icon"
        else: text = "Automatic — the program's own icon"
        self.icon_label.configure(text=text)
        self.icon_auto.state(["!disabled"] if spec else ["disabled"])

    def _choose_icon(self):
        program = self.path_var.get().strip()
        if Path(program).suffix.lower() in (".py", ".pyw"):
            program = next(iter(icon_sources(program, self.interp_var.get().strip())), "")
        spec = IconPickerDialog(self.app, self, self.icon, program).show()
        if spec is not None: self._set_icon(spec)

    def _browse_path(self):
        p = filedialog.askopenfilename(parent=self, title="Choose file", initialfile=self.path_var.get(),
                                       filetypes=[("Scripts and programs", SCRIPT_TYPES), ("All files", "*.*")])
        if p: self.path_var.set(p)

    def _browse_interp(self):
        p = filedialog.askopenfilename(parent=self, title="Choose Python interpreter",
                                       filetypes=[("Python", "python*.exe python*"), ("All files", "*.*")])
        if p: self.interp_var.set(p)

    def validate(self):
        path = self.path_var.get().strip()
        if not path or not Path(path).is_file():
            return self.fail("That file doesn't exist.")
        if any(norm(s.path) == norm(path) for s in self.app.services if s is not self.service):
            return self.fail("Another service already uses that file.")
        interp = self.interp_var.get().strip()
        if interp and not Path(interp).is_file():
            return self.fail("The Python interpreter path doesn't exist.")
        cwd = self.cwd_var.get().strip()
        if cwd and not Path(cwd).is_dir(): return self.fail("The working folder doesn't exist.")
        return {"name": self.name_var.get().strip(), "path": path, "args": self.args_var.get().strip(),
                "interpreter": interp, "auto_restart": self.restart_var.get(), "working_dir": cwd,
                "icon": self.icon}


class IconPickerDialog(Dialog):
    COLUMNS, ROWS = 9, 5

    def __init__(self, app: App, parent: tk.Misc, current: str, program: str):
        super().__init__(app, "Choose icon", parent)
        px = app.px
        self.heading("Choose an icon", "Pick an icon for this service, or browse for an .ico, .exe or .dll file.")
        self.size, self.cell = px(32), px(52)
        self.images: list[tk.PhotoImage] = []
        self.cells: list[int] = []       # icon index shown in each grid cell
        self.file = ""
        self.selected: int | None = None

        tabs = tk.Frame(self.body, bg=C.BG)
        tabs.pack(fill="x", pady=(px(16), px(10)))
        sources = ([("This program", program)] if icon_count(program) else []) + list(WINDOWS_ICON_LIBRARIES)
        self.tabs: dict[str, ttk.Button] = {}
        for label, file in sources:
            b = ttk.Button(tabs, text=label, style="Tab.TButton", command=lambda f=file: self._load(f))
            b.pack(side="left", padx=(0, px(6)))
            self.tabs[file] = b
        ttk.Button(tabs, text="Browse…", style="Small.TButton", command=self._browse).pack(side="right")

        box = tk.Frame(self.body, bg=C.INPUT, highlightthickness=1, highlightbackground=C.BORDER_STRONG)
        box.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(box, bg=C.INPUT, highlightthickness=0, bd=0, width=self.COLUMNS * self.cell,
                                height=self.ROWS * self.cell, yscrollincrement=self.cell)
        sb = ttk.Scrollbar(box, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.hover = self.canvas.create_rectangle(0, 0, 0, 0, fill=C.BUTTON, outline="", state="hidden")
        self.mark = self.canvas.create_rectangle(0, 0, 0, 0, fill=C.SELECT, outline=C.ACCENT, width=2,
                                                 state="hidden")
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Double-1>", lambda e: self.ok())
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Leave>", lambda e: self.canvas.itemconfigure(self.hover, state="hidden"))
        self.canvas.bind("<MouseWheel>", lambda e: self.canvas.yview_scroll(-1 if e.delta > 0 else 1, "units"))

        self.add_buttons("Use icon")
        if current:
            file, index = parse_icon_spec(current)
            raw = current.rpartition(",")[0] or current
            self._load(raw if Path(file).is_file() or not sources else sources[0][1], select=index)
        elif sources:
            self._load(sources[0][1])

    def _load(self, file: str, select: int | None = None):
        self.configure(cursor="watch")
        self.update_idletasks()
        try:
            path = os.path.expandvars(file)
            pixels = load_icons(path, self.size, 0, icon_count(path))
        except Exception:
            pixels = []
        finally:
            self.configure(cursor="")
        self.file, self.selected = file, None
        self.canvas.delete("icon")
        self.images, self.cells = [], []
        for index, data in enumerate(pixels):
            if not data or not any(data[3::4]): continue    # skip empty slots
            row, col = divmod(len(self.cells), self.COLUMNS)
            img = photo(self, self.size, self.size, data)
            self.canvas.create_image((col + 0.5) * self.cell, (row + 0.5) * self.cell, image=img, tags="icon")
            self.images.append(img)
            self.cells.append(index)
        rows = max(self.ROWS, -(-len(self.cells) // self.COLUMNS))
        self.canvas.configure(scrollregion=(0, 0, self.COLUMNS * self.cell, rows * self.cell))
        self.canvas.yview_moveto(0)
        self.canvas.itemconfigure(self.mark, state="hidden")
        for f, b in self.tabs.items(): b.configure(style="TabOn.TButton" if f == file else "Tab.TButton")
        if not self.cells:
            return self.fail("That file has no icons.")
        self.fail("")
        self.error.configure(text=f"{len(self.cells)} icons", fg=C.MUTED)
        if select in self.cells: self._select(self.cells.index(select), scroll=True)

    def _cell_at(self, e) -> int | None:
        col, row = int(self.canvas.canvasx(e.x) // self.cell), int(self.canvas.canvasy(e.y) // self.cell)
        pos = row * self.COLUMNS + col
        return pos if 0 <= col < self.COLUMNS and 0 <= pos < len(self.cells) else None

    def _box(self, pos: int):
        row, col = divmod(pos, self.COLUMNS)
        pad = self.app.px(3)
        return (col * self.cell + pad, row * self.cell + pad, (col + 1) * self.cell - pad, (row + 1) * self.cell - pad)

    def _on_motion(self, e):
        pos = self._cell_at(e)
        if pos is None: return self.canvas.itemconfigure(self.hover, state="hidden")
        self.canvas.coords(self.hover, *self._box(pos))
        self.canvas.itemconfigure(self.hover, state="normal")
        self.canvas.tag_lower(self.hover)

    def _on_click(self, e):
        pos = self._cell_at(e)
        if pos is not None: self._select(pos)

    def _select(self, pos: int, scroll: bool = False):
        self.selected = self.cells[pos]
        self.canvas.coords(self.mark, *self._box(pos))
        self.canvas.itemconfigure(self.mark, state="normal")
        self.canvas.tag_raise(self.mark, self.hover)
        self.canvas.tag_raise("icon")
        if scroll:
            self.canvas.yview_moveto(max(0, pos // self.COLUMNS - 1) * self.cell
                                     / float(self.canvas.cget("scrollregion").split()[3]))

    def _browse(self):
        path = filedialog.askopenfilename(parent=self, title="Choose an icon file",
                                          filetypes=[("Icons and programs", "*.ico *.exe *.dll"), ("All files", "*.*")])
        if path: self._load(os.path.normpath(path))

    def validate(self):
        if self.selected is None: return self.fail("Click an icon to choose it.")
        return f"{self.file},{self.selected}"


class ProcessPickerDialog(Dialog):
    """Choose a running program by its window, like OBS's game capture list."""

    def __init__(self, app: App, parent: tk.Misc):
        super().__init__(app, "Pick a running app", parent)
        px = app.px
        self.heading("Pick a running app",
                     "Start the game or program first, then choose it here. Workaholic remembers its "
                     "executable and starts the group whenever it runs again.")
        self.choices: list[dict] = []
        self.incoming = queue.Queue()
        self.query = tk.StringVar(self)
        entry = ttk.Entry(self.body, textvariable=self.query, width=64)
        entry.pack(fill="x", pady=(px(16), px(10)))
        self.initial_focus = entry
        self.query.trace_add("write", lambda *_: self._filter())
        box = tk.Frame(self.body, bg=C.PANEL, highlightthickness=1, highlightbackground=C.BORDER_STRONG)
        box.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(box, columns=("program",), selectmode="browse", height=10)
        self.tree.heading("#0", text="WINDOW", anchor="w")
        self.tree.heading("program", text="PROGRAM", anchor="w")
        self.tree.column("#0", width=px(330))
        self.tree.column("program", width=px(190))
        sb = ttk.Scrollbar(box, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True, padx=(px(4), 0), pady=px(2))
        self.tree.bind("<Double-1>", lambda e: self.ok())

        self.background_var = tk.BooleanVar(self, value=False)
        ttk.Checkbutton(self.body, text="Include background programs without a window", variable=self.background_var,
                        command=self._reload).pack(anchor="w", pady=(px(10), 0))
        self.exact_var = tk.BooleanVar(self, value=False)
        ttk.Checkbutton(self.body, text="Only match this exact file location, not just the program name",
                        variable=self.exact_var).pack(anchor="w", pady=(px(4), 0))
        self.add_buttons("Use this app")
        ttk.Button(self.footer, text="Refresh", command=self._reload).pack(side="right", padx=(0, px(8)))
        self._reload()

    def _reload(self):
        self.error.configure(text="Looking for running apps…", fg=C.MUTED)
        background = self.background_var.get()
        threading.Thread(target=self._load, args=(background,), daemon=True).start()
        self.after(100, self._receive)

    def _load(self, background: bool):
        try: self.incoming.put(program_choices(visible_windows(), process_snapshot(), background))
        except Exception as e: self.incoming.put(e)

    def _receive(self):
        if not self.winfo_exists(): return
        try: result = self.incoming.get_nowait()
        except queue.Empty:
            self.after(100, self._receive)
            return
        if isinstance(result, Exception):
            self.error.configure(text="Couldn't list running apps. Use Browse instead.", fg=C.RED)
            return
        self.choices = result
        self._filter()

    def _filter(self):
        self.tree.delete(*self.tree.get_children())
        words = self.query.get().strip().casefold().split()
        size = self.app.px(20)
        for i, c in enumerate(self.choices):
            if all(w in f"{c['title']} {c['name']}".casefold() for w in words):
                self.tree.insert("", "end", iid=str(i), text=c["title"] or "(no window)", values=(c["name"],),
                                 image=self.app.spaced(self.app.icon_image([c["exe"]] if c["exe"] else [], size)))
        count = len(self.tree.get_children())
        self.error.configure(text=f"{count} apps" if count else "Nothing found. Is it running?", fg=C.MUTED)

    def validate(self):
        selected = self.tree.selection()
        if not selected: return self.fail("Select the app that should start this group.")
        c = self.choices[int(selected[0])]
        return c["exe"] if self.exact_var.get() and c["exe"] else c["name"]


class SettingsDialog(Dialog):
    def __init__(self, app: App):
        super().__init__(app, "Settings")
        px = app.px
        st = app.settings
        self.heading("Settings")

        self.section("Startup", top=10)
        self.login_var = tk.BooleanVar(self, value=bool(get_launch_at_login()))
        cb = ttk.Checkbutton(self.body, text="Launch Workaholic when I sign in", variable=self.login_var)
        cb.pack(anchor="w")
        if not IS_WINDOWS:
            cb.state(["disabled"])
            tk.Label(self.body, text="Available on Windows only.", fg=C.DIM, bg=C.BG,
                     font=app.f_small).pack(anchor="w", padx=px(26))
        self.minimized_var = tk.BooleanVar(self, value=st.start_minimized)
        ttk.Checkbutton(self.body, text="Start minimized when launched at sign-in",
                        variable=self.minimized_var).pack(anchor="w", pady=(px(8), 0))

        delay_row = tk.Frame(self.body, bg=C.BG)
        delay_row.pack(fill="x", pady=(px(12), 0))
        tk.Label(delay_row, text="Start autostart groups", fg=C.TEXT, bg=C.BG, font=app.f_base).pack(side="left")
        self.delay_var = tk.StringVar(self, value=f"{st.autostart_delay:g}")
        ttk.Spinbox(delay_row, from_=0, to=600, increment=1, width=6, textvariable=self.delay_var).pack(
            side="left", padx=px(8))
        tk.Label(delay_row, text="seconds after launch", fg=C.TEXT, bg=C.BG, font=app.f_base).pack(side="left")

        auto = [g.name for g in app.groups if g.autostart]
        summary = ("Autostart groups, in order: " + ", ".join(auto)) if auto else \
            "No groups start on launch yet. Turn on “Start on launch” for a group in the sidebar."
        tk.Label(self.body, text=summary, fg=C.DIM, bg=C.BG, font=app.f_small, justify="left", anchor="w",
                 wraplength=px(440)).pack(fill="x", pady=(px(8), 0))

        self.section("Closing")
        self.confirm_var = tk.BooleanVar(self, value=st.confirm_exit)
        ttk.Checkbutton(self.body, text="Ask before quitting while services are running",
                        variable=self.confirm_var).pack(anchor="w")

        self.section("Configuration")
        link = tk.Label(self.body, text=str(CONFIG_FILE), fg=C.ACCENT, bg=C.BG, font=app.f_small,
                        cursor="hand2", anchor="w", justify="left", wraplength=px(440))
        link.pack(fill="x")
        link.bind("<Button-1>", lambda e: reveal_path(str(CONFIG_FILE)) if CONFIG_FILE.exists()
                  else open_path(str(CONFIG_FILE.parent)))
        self.add_buttons()

    def validate(self):
        try:
            delay = float(self.delay_var.get())
            if not 0 <= delay <= 600: raise ValueError
        except ValueError:
            return self.fail("The delay must be a number between 0 and 600 seconds.")
        return {"launch_at_login": self.login_var.get(), "start_minimized": self.minimized_var.get(),
                "autostart_delay": delay, "confirm_exit": self.confirm_var.get()}


# ---------- Main window ----------
class App(tk.Tk):
    def __init__(self, opts: argparse.Namespace, instance_sock: socket.socket | None = None):
        super().__init__(className=APP_NAME)
        self.withdraw()
        self.opts = opts
        self.title(f"{APP_NAME} {__version__}")
        self.configure(bg=C.BG)
        self.scale = max(1.0, self.winfo_fpixels("1i") / 96.0)
        self.services: list[Service] = []
        self.groups: list[Group] = []
        self.settings = Settings()
        self.view: tuple[str, Group | None] = ("all", None)
        self._ui_queue: queue.Queue[str] = queue.Queue()
        self._instance_sock = instance_sock
        self._row_cache: dict[str, tuple] = {}
        self._menu_vars: list[tk.Variable] = []
        self._flash_job = None
        self._closing = False
        self._close_deadline = 0.0
        self._destroyed = False
        self._config_warning: str | None = None
        self._log_sid: str | None = None
        self._log_file: Path | None = None
        self._log_pos = 0
        self._log_decoder = None
        self._log_placeholder = False
        self._process_check_at = 0.0
        self._process_results = queue.Queue()
        self._process_scan_pending = False
        self._trigger_states: dict[int, tuple[str, bool]] = {}
        self._group_restart_tokens: dict[int, object] = {}
        self._icons: dict[tuple[str, int], tk.PhotoImage | None] = {}
        self._fallback_icons: dict[int, tk.PhotoImage] = {}
        self._service_icons: dict[tuple[str, str, str], tk.PhotoImage] = {}
        self._row_images: dict[tuple[str, str], tk.PhotoImage] = {}

        self._load_config()
        self._init_fonts()
        self._init_images()
        self._init_style()
        self._build()
        self._bind_keys()
        self._restore_window()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        if not getattr(opts, "smoke_test", False):
            self._refresh_login_entry()
        if instance_sock:
            threading.Thread(target=self._listen, args=(instance_sock,), daemon=True).start()

        self.render_sidebar()
        self._render_header()
        self._refresh()
        self._log_show(None)
        self.update_idletasks()
        apply_titlebar(self)
        if opts.minimized or (opts.startup and self.settings.start_minimized):
            self.iconify()
        else:
            self.deiconify()
            if self.settings.maximized: self._maximize()
        if self._config_warning:
            self.flash(self._config_warning, "error", sticky=True)
        self.after(TICK_MS, self._tick)
        if not opts.no_autostart and any(g.autostart for g in self.groups):
            delay = self.settings.autostart_delay
            if delay >= 1:
                self.flash(f"Starting autostart groups in {delay:g}s…", sticky=True)
            self.after(int(delay * 1000), self.run_autostart)

    # ---------- Setup ----------
    def px(self, n: float) -> int:
        return int(round(n * self.scale))

    def ellipsize(self, text: str, width: int, font=None) -> str:
        font = font or self.f_bold
        if font.measure(text) <= width: return text
        while text and font.measure(text + "…") > width:
            text = text[:-1]
        return text + "…"

    def _init_fonts(self):
        families = {f.lower(): f for f in tkfont.families(self)}

        def pick(*names, default):
            return next((families[n.lower()] for n in names if n.lower() in families), default)

        ui = pick("Segoe UI", "SF Pro Text", "Helvetica Neue", "Inter", "Ubuntu", "Cantarell", "Noto Sans",
                  "DejaVu Sans", default=tkfont.nametofont("TkDefaultFont").actual("family"))
        mono = pick("Cascadia Mono", "Cascadia Code", "JetBrains Mono", "Consolas", "SF Mono", "Menlo",
                    "DejaVu Sans Mono", default=tkfont.nametofont("TkFixedFont").actual("family"))
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont", "TkTooltipFont"):
            try: tkfont.nametofont(name).configure(family=ui, size=9 if name == "TkTooltipFont" else 10)
            except tk.TclError: pass
        F = tkfont.Font
        self.f_base = F(self, family=ui, size=10)
        self.f_bold = F(self, family=ui, size=10, weight="bold")
        self.f_small = F(self, family=ui, size=9)
        self.f_small_bold = F(self, family=ui, size=9, weight="bold")
        self.f_section = F(self, family=ui, size=8, weight="bold")
        self.f_heading = F(self, family=ui, size=13, weight="bold")
        self.f_title = F(self, family=ui, size=17, weight="bold")
        self.f_brand = F(self, family=ui, size=13, weight="bold")
        self.f_icon = F(self, family=ui, size=11)
        self.f_empty = F(self, family=ui, size=26)
        self.f_mono = F(self, family=mono, size=9)

    def _init_images(self):
        px = self.px
        h = px(12)
        self.dot_images = {key: raster(self, px(16), h, [(color, circle(px(6), h / 2, px(4.2)))])
                           for key, color in STATUS_COLORS.items()}
        box = px(16)
        self.img_cb_off = make_checkbox(self, box, box + px(8), False)
        self.img_cb_on = make_checkbox(self, box, box + px(8), True)
        self.img_list_off = make_checkbox(self, box, box + px(10), False)
        self.img_list_on = make_checkbox(self, box, box + px(10), True)
        icon = Path(getattr(sys, "_MEIPASS", APP_DIR)) / "assets" / "workaholic.ico"
        self.logo = self.icon_image([str(icon)], px(30), fallback=False) or make_logo(self, px(30))
        if IS_WINDOWS and icon.is_file():
            self.iconbitmap(default=str(icon))
        else:
            self.iconphoto(True, *(make_logo(self, n) for n in (32, 16)))

    def icon_image(self, sources: list[str], size: int, fallback: bool = True) -> tk.PhotoImage | None:
        """The first icon that loads from `sources` ("file" or "file,index"), cached."""
        for spec in sources:
            key = (spec, size)
            if key not in self._icons:
                try:
                    path, index = parse_icon_spec(spec)
                    pixels = load_icons(path, size, index)
                    self._icons[key] = photo(self, size, size, pixels[0]) if pixels and pixels[0] else None
                except Exception:
                    self._icons[key] = None
            if self._icons[key]: return self._icons[key]
        if not fallback: return None
        if size not in self._fallback_icons: self._fallback_icons[size] = make_program_icon(self, size)
        return self._fallback_icons[size]

    def spaced(self, icon: tk.PhotoImage) -> tk.PhotoImage:
        """The icon with a gap after it, so list text doesn't touch it."""
        img = self._row_images.get((str(icon), ""))
        if img is None:
            img = self._row_images[(str(icon), "")] = tk.PhotoImage(master=self, width=icon.width() + self.px(8),
                                                                    height=icon.height())
            img.tk.call(img, "copy", icon, "-to", 0, 0)
        return img

    def _row_image(self, s: Service, status: str) -> tk.PhotoImage:
        """Status dot and service icon side by side, for the Name column."""
        key = (s.path, s.interpreter, s.icon)
        icon = self._service_icons.get(key)
        if icon is None:
            icon = self._service_icons[key] = self.icon_image(icon_sources(*key), self.px(20))
        row_key = (str(icon), status)
        img = self._row_images.get(row_key)
        if img is None:
            dot = self.dot_images[status]
            w, h = dot.width() + icon.width() + self.px(8), max(dot.height(), icon.height())
            img = self._row_images[row_key] = tk.PhotoImage(master=self, width=w, height=h)
            img.tk.call(img, "copy", dot, "-to", 0, (h - dot.height()) // 2)
            img.tk.call(img, "copy", icon, "-to", dot.width(), (h - icon.height()) // 2)
        return img

    def _init_style(self):
        px = self.px
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure(".", background=C.BG, foreground=C.TEXT, fieldbackground=C.INPUT, bordercolor=C.BORDER,
                    lightcolor=C.BG, darkcolor=C.BG, troughcolor=C.BG, focuscolor=C.BORDER_STRONG,
                    selectbackground=C.SELECT, selectforeground=C.TEXT, insertcolor=C.TEXT,
                    arrowcolor=C.MUTED, font=self.f_base)

        def button(style, bg, hover, press, fg, border, pad=(px(14), px(6)), font=None):
            s.configure(style, background=bg, foreground=fg, bordercolor=border, lightcolor=bg, darkcolor=bg,
                        focusthickness=0, padding=pad, relief="flat", width=0, anchor="center",
                        font=font or self.f_base)
            fill = [("disabled", C.BG), ("pressed", press), ("active", hover)]
            s.map(style, background=fill, lightcolor=fill, darkcolor=fill,
                  foreground=[("disabled", C.DIM)],
                  bordercolor=[("disabled", C.BORDER), ("focus", C.ACCENT if style == "Accent.TButton"
                                                                 else C.BORDER_STRONG)])

        button("TButton", C.BUTTON, C.BUTTON_HOVER, C.BUTTON_PRESS, C.TEXT, C.BORDER)
        button("Accent.TButton", C.ACCENT, C.ACCENT_HOVER, C.ACCENT_PRESS, C.ON_ACCENT, C.ACCENT)
        button("Small.TButton", C.BUTTON, C.BUTTON_HOVER, C.BUTTON_PRESS, C.TEXT, C.BORDER,
               pad=(px(10), px(3)), font=self.f_small)
        button("Icon.TButton", C.BUTTON, C.BUTTON_HOVER, C.BUTTON_PRESS, C.TEXT, C.BORDER, pad=(px(8), px(4)))
        button("Tab.TButton", C.BG, C.BUTTON_HOVER, C.BUTTON_PRESS, C.MUTED, C.BORDER, pad=(px(12), px(4)),
               font=self.f_small)
        button("TabOn.TButton", C.SELECT, C.SELECT, C.SELECT, C.ACCENT, C.ACCENT, pad=(px(12), px(4)),
               font=self.f_small_bold)

        s.configure("TEntry", fieldbackground=C.INPUT, foreground=C.TEXT, bordercolor=C.BORDER_STRONG,
                    lightcolor=C.INPUT, darkcolor=C.INPUT, insertcolor=C.TEXT, padding=(px(8), px(5)))
        s.map("TEntry", bordercolor=[("focus", C.ACCENT)], lightcolor=[("focus", C.ACCENT)],
              fieldbackground=[("disabled", C.BG)], foreground=[("disabled", C.DIM)])
        s.configure("TSpinbox", fieldbackground=C.INPUT, foreground=C.TEXT, bordercolor=C.BORDER_STRONG,
                    lightcolor=C.INPUT, darkcolor=C.INPUT, background=C.BUTTON, arrowcolor=C.MUTED,
                    insertcolor=C.TEXT, padding=(px(6), px(4)), arrowsize=px(12))
        s.map("TSpinbox", bordercolor=[("focus", C.ACCENT)], lightcolor=[("focus", C.ACCENT)],
              background=[("active", C.BUTTON_HOVER)])

        s.element_create("Wk.Checkbutton.indicator", "image", self.img_cb_off,
                         ("selected", self.img_cb_on), sticky="w")
        s.layout("TCheckbutton", [("Checkbutton.padding", {"sticky": "nswe", "children": [
            ("Wk.Checkbutton.indicator", {"side": "left", "sticky": ""}),
            ("Checkbutton.focus", {"side": "left", "sticky": "w", "children": [
                ("Checkbutton.label", {"sticky": "nswe"})]})]})])
        s.configure("TCheckbutton", background=C.BG, foreground=C.TEXT, padding=px(2))
        s.map("TCheckbutton", background=[("active", C.BG)], foreground=[("disabled", C.DIM)])

        tree_layout = [("Treeview.treearea", {"sticky": "nswe"})]
        s.layout("Treeview", tree_layout)
        s.layout("List.Treeview", tree_layout)
        s.layout("Treeview.Item", [("Treeitem.padding", {"sticky": "nswe", "children": [
            ("Treeitem.image", {"side": "left", "sticky": ""}),
            ("Treeitem.text", {"side": "left", "sticky": ""})]})])
        s.configure("Treeview", background=C.PANEL, fieldbackground=C.PANEL, foreground=C.TEXT,
                    rowheight=px(32), font=self.f_base, borderwidth=0)
        s.map("Treeview", background=[("selected", C.SELECT)], foreground=[("selected", C.TEXT)])
        s.configure("List.Treeview", background=C.INPUT, fieldbackground=C.INPUT, rowheight=px(28))
        s.map("List.Treeview", background=[("selected", C.BUTTON_HOVER)], foreground=[("selected", C.TEXT)])
        s.configure("Treeview.Heading", background=C.PANEL, foreground=C.DIM, font=self.f_section,
                    relief="flat", bordercolor=C.PANEL, lightcolor=C.PANEL, darkcolor=C.PANEL,
                    padding=(px(4), px(8)))
        s.map("Treeview.Heading", background=[("active", C.PANEL)], foreground=[("active", C.MUTED)])

        s.layout("Vertical.TScrollbar", [("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
            ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])
        s.configure("Vertical.TScrollbar", background=C.BUTTON, troughcolor=C.PANEL, bordercolor=C.PANEL,
                    lightcolor=C.BUTTON, darkcolor=C.BUTTON, gripcount=0, arrowsize=px(10))
        s.map("Vertical.TScrollbar", background=[("active", C.BORDER_STRONG)],
              lightcolor=[("active", C.BORDER_STRONG)], darkcolor=[("active", C.BORDER_STRONG)])
        s.layout("Log.Vertical.TScrollbar", s.layout("Vertical.TScrollbar"))
        s.configure("Log.Vertical.TScrollbar", background=C.LOG_THUMB, troughcolor=C.LOG_BG, bordercolor=C.LOG_BG,
                    lightcolor=C.LOG_THUMB, darkcolor=C.LOG_THUMB)
        s.map("Log.Vertical.TScrollbar", background=[("active", C.LOG_SELECT)],
              lightcolor=[("active", C.LOG_SELECT)], darkcolor=[("active", C.LOG_SELECT)])

    def menu(self, parent=None) -> tk.Menu:
        return tk.Menu(parent or self, tearoff=0, bg=C.PANEL, fg=C.TEXT, activebackground=C.SELECT,
                       activeforeground=C.TEXT, disabledforeground=C.DIM, selectcolor=C.ACCENT, bd=0,
                       relief="flat", font=self.f_base)

    # ---------- Layout ----------
    def _build(self):
        px = self.px
        bar = tk.Frame(self, bg=C.FOOTER)
        bar.pack(side="bottom", fill="x")
        tk.Frame(bar, bg=C.BORDER, height=1).pack(side="top", fill="x")
        self.status_msg = tk.Label(bar, text="Ready", bg=C.FOOTER, fg=C.DIM, font=self.f_small, anchor="w")
        self.status_msg.pack(side="left", fill="x", expand=True, padx=px(14), pady=px(5))
        self.status_summary = tk.Label(bar, bg=C.FOOTER, fg=C.DIM, font=self.f_small)
        self.status_summary.pack(side="right", padx=px(14))

        body = tk.Frame(self, bg=C.BG)
        body.pack(fill="both", expand=True)
        self.sidebar = tk.Frame(body, bg=C.SIDEBAR, width=px(236))
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)
        main = tk.Frame(body, bg=C.BG)
        main.pack(side="left", fill="both", expand=True)
        self._build_sidebar()
        self._build_main(main)

    def _sidebar_section(self, text: str, action=None):
        px = self.px
        row = tk.Frame(self.sidebar, bg=C.SIDEBAR)
        row.pack(fill="x", padx=(px(22), px(12)), pady=(px(16), px(4)))
        tk.Label(row, text=text, font=self.f_section, fg=C.SIDE_DIM, bg=C.SIDEBAR).pack(side="left")
        if action:
            glyph, command, tip = action
            btn = tk.Label(row, text=glyph, font=self.f_heading, fg=C.SIDE_MUTED, bg=C.SIDEBAR, cursor="hand2",
                           padx=px(6))
            btn.pack(side="right")
            btn.bind("<Button-1>", lambda e: command())
            btn.bind("<Enter>", lambda e: btn.configure(fg=C.SIDE_TEXT, bg=C.NAV_SELECTED), add="+")
            btn.bind("<Leave>", lambda e: btn.configure(fg=C.SIDE_MUTED, bg=C.SIDEBAR), add="+")
            Tooltip(btn, tip)

    def _build_sidebar(self):
        px, sb = self.px, self.sidebar
        brand = tk.Frame(sb, bg=C.SIDEBAR)
        brand.pack(fill="x", padx=px(20), pady=(px(22), px(6)))
        tk.Label(brand, image=self.logo, bg=C.SIDEBAR).pack(side="left")
        tk.Label(brand, text=APP_NAME, font=self.f_brand, fg=C.SIDE_TEXT, bg=C.SIDEBAR).pack(side="left", padx=(px(10), 0))

        footer = tk.Frame(sb, bg=C.SIDEBAR)
        footer.pack(side="bottom", fill="x", pady=px(10))
        NavRow(footer, self, "⚙", "Settings", on_click=self.open_settings).pack(fill="x", padx=px(8))

        self._sidebar_section("LIBRARY")
        self.nav_all = NavRow(sb, self, "≡", "All services", on_click=lambda: self.set_view("all"))
        self.nav_all.pack(fill="x", padx=px(8), pady=1)
        self.nav_running = NavRow(sb, self, "●", "Running", icon_fg=C.SIDE_GREEN,
                                  on_click=lambda: self.set_view("running"))
        self.nav_running.pack(fill="x", padx=px(8), pady=1)

        self._sidebar_section("GROUPS", ("+", self.new_group, "New group (Ctrl+G)"))
        self.group_list = tk.Frame(sb, bg=C.SIDEBAR)
        self.group_list.pack(fill="x")
        self.group_rows: dict[int, NavRow] = {}

    def _build_main(self, main: tk.Frame):
        px = self.px
        header = tk.Frame(main, bg=C.BG)
        header.pack(fill="x", padx=px(28), pady=(px(22), px(16)))
        self.header_actions = tk.Frame(header, bg=C.BG)  # packed first so the title gets clipped, not the buttons
        self.header_actions.pack(side="right")
        titles = tk.Frame(header, bg=C.BG)
        titles.pack(side="left", fill="x", expand=True)
        self.title_lbl = tk.Label(titles, font=self.f_title, fg=C.TEXT, bg=C.BG, anchor="w")
        self.title_lbl.pack(fill="x")
        self.subtitle_lbl = tk.Label(titles, font=self.f_base, fg=C.MUTED, bg=C.BG, anchor="w")
        self.subtitle_lbl.pack(fill="x")

        toolbar = tk.Frame(main, bg=C.BG)
        toolbar.pack(fill="x", padx=px(28), pady=(0, px(12)))

        def tool(text, cmd, tip):
            b = ttk.Button(toolbar, text=text, command=cmd)
            b.pack(side="left", padx=(0, px(6)))
            Tooltip(b, tip)
            return b

        self.btn_start = tool("▶  Start", self.start_selected, "Start selected (Enter)")
        self.btn_stop = tool("■  Stop", self.stop_selected, "Stop selected (Enter)")
        self.btn_restart = tool("↻  Restart", self.restart_selected, "Restart selected (Ctrl+R)")
        tk.Frame(toolbar, bg=C.BORDER, width=1).pack(side="left", fill="y", padx=(px(6), px(12)), pady=px(4))
        self.btn_edit = tool("✎  Edit", self.edit_selected, "Edit service (F2)")
        self.btn_remove = tool("✕  Remove", self.remove_selected, "Remove from Workaholic (Del)")
        self.search = SearchEntry(toolbar, self, "Search services   Ctrl+F", self._refresh)
        self.search.pack(side="right")

        self.paned = tk.PanedWindow(main, orient="vertical", bg=C.BG, bd=0, sashwidth=px(12),
                                    sashrelief="flat", opaqueresize=True, sashcursor="sb_v_double_arrow")
        self.paned.pack(fill="both", expand=True, padx=px(28), pady=(0, px(18)))
        table, log = self._card(), self._card()
        self.paned.add(table, minsize=px(150), stretch="always")
        self.paned.add(log, minsize=px(110), height=px(240), stretch="never")
        self._build_table(table)
        self._build_log(log)

    def _card(self) -> tk.Frame:
        return tk.Frame(self.paned, bg=C.PANEL, highlightthickness=1, highlightbackground=C.BORDER,
                        highlightcolor=C.BORDER)

    def _build_table(self, card: tk.Frame):
        px = self.px
        cols = ("status", "uptime", "pid", "groups", "path")
        self.tree = ttk.Treeview(card, columns=cols, show="tree headings", selectmode="extended")
        heads = {"#0": "NAME", "status": "STATUS", "uptime": "UPTIME", "pid": "PID", "groups": "GROUPS", "path": "PATH"}
        widths = {"#0": 230, "status": 130, "uptime": 90, "pid": 70, "groups": 150, "path": 300}
        for col in ("#0",) + cols:
            self.tree.heading(col, text=heads[col], anchor="w")
            self.tree.column(col, width=px(widths[col]), minwidth=px(50), anchor="w", stretch=(col == "path"))
        sb = ttk.Scrollbar(card, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y", padx=(0, px(3)), pady=px(4))
        self.tree.pack(fill="both", expand=True, padx=(px(6), 0), pady=(px(2), px(4)))
        self.tree.tag_configure("odd", background=C.PANEL_ALT)
        self.tree.tag_configure("idle", foreground=C.MUTED)
        self.tree.tag_configure("missing", foreground=C.RED)

        self.tree.bind("<<TreeviewSelect>>", lambda e: self._on_select())
        self.tree.bind("<Double-1>", self._on_double)
        self.tree.bind("<Button-3>", self._tree_menu)
        if sys.platform == "darwin": self.tree.bind("<Button-2>", self._tree_menu)
        self.tree.bind("<Return>", lambda e: self.toggle_selected())
        self.tree.bind("<Delete>", lambda e: self.remove_selected())
        self.tree.bind("<F2>", lambda e: self.edit_selected())
        self.tree.bind("<Control-a>", lambda e: (self.tree.selection_set(self.tree.get_children()), "break")[1])

        self.empty = tk.Frame(card, bg=C.PANEL)
        self.empty_icon = tk.Label(self.empty, font=self.f_empty, fg=C.BORDER_STRONG, bg=C.PANEL)
        self.empty_icon.pack()
        self.empty_title = tk.Label(self.empty, font=self.f_heading, fg=C.TEXT, bg=C.PANEL)
        self.empty_title.pack(pady=(px(4), px(4)))
        self.empty_body = tk.Label(self.empty, font=self.f_base, fg=C.MUTED, bg=C.PANEL, justify="center",
                                   wraplength=px(420))
        self.empty_body.pack()
        self.empty_buttons = tk.Frame(self.empty, bg=C.PANEL)
        self.empty_buttons.pack(pady=(px(14), 0))
        self._empty_kind = None

    def _build_log(self, card: tk.Frame):
        px = self.px
        head = tk.Frame(card, bg=C.PANEL)
        head.pack(fill="x", padx=(px(16), px(10)), pady=(px(9), px(7)))
        self.log_buttons = []
        for text, cmd, tip in (("Clear", self._log_clear, "Empty this service's log file"),
                               ("Show in folder", lambda: self._log_target() and reveal_path(self._log_target().path),
                                "Show the service's file in Explorer"),
                               ("Open log", lambda: self._log_target() and self.open_log(self._log_target()),
                                "Open the log file in your editor")):
            b = ttk.Button(head, text=text, style="Small.TButton", command=cmd)
            b.pack(side="right", padx=(px(6), 0))
            Tooltip(b, tip)
            self.log_buttons.append(b)
        tk.Label(head, text="OUTPUT", font=self.f_section, fg=C.DIM, bg=C.PANEL).pack(side="left")
        self.log_title = tk.Label(head, font=self.f_bold, fg=C.TEXT, bg=C.PANEL)
        self.log_title.pack(side="left", padx=(px(10), 0))
        self.log_meta = tk.Label(head, font=self.f_small, fg=C.DIM, bg=C.PANEL, anchor="w")
        self.log_meta.pack(side="left", fill="x", expand=True, padx=(px(10), 0))

        body = tk.Frame(card, bg=C.LOG_BG)
        body.pack(fill="both", expand=True)
        sb = ttk.Scrollbar(body, orient="vertical", style="Log.Vertical.TScrollbar")
        sb.pack(side="right", fill="y")
        self.log_text = tk.Text(body, bg=C.LOG_BG, fg=C.LOG_TEXT, font=self.f_mono, bd=0, highlightthickness=0,
                                wrap="word", padx=px(14), pady=px(10), state="disabled", cursor="xterm",
                                selectbackground=C.LOG_SELECT, selectforeground=C.LOG_TEXT,
                                insertbackground=C.LOG_TEXT,
                                yscrollcommand=sb.set, spacing1=1)
        self.log_text.pack(side="left", fill="both", expand=True)
        sb.configure(command=self.log_text.yview)
        self.log_text.tag_configure("marker", foreground=C.LOG_MARKER)
        self.log_text.tag_configure("err", foreground=C.LOG_ERROR)
        self.log_text.tag_configure("warn", foreground=C.LOG_WARN)
        self.log_text.tag_configure("hint", foreground=C.LOG_HINT, font=self.f_base)

    def _bind_keys(self):
        self.bind("<Control-n>", lambda e: self.add_service())
        self.bind("<Control-g>", lambda e: self.new_group())
        self.bind("<Control-f>", lambda e: self.search.focus())
        self.bind("<Control-r>", lambda e: self.restart_selected())
        self.bind("<Control-comma>", lambda e: self.open_settings())

    def _restore_window(self):
        size = self.settings.window_size
        if not re.fullmatch(r"\d+x\d+", size or ""):
            size = f"{self.px(1180)}x{self.px(740)}"
        w, h = (int(v) for v in size.split("x"))
        w, h = min(w, self.winfo_screenwidth()), min(h, self.winfo_screenheight() - self.px(60))
        x, y = (self.winfo_screenwidth() - w) // 2, max(0, (self.winfo_screenheight() - h) // 2 - self.px(20))
        self.geometry(f"{w}x{h}+{x}+{y}")
        self.minsize(self.px(860), self.px(520))

    def _maximize(self):
        try: self.state("zoomed")
        except tk.TclError:
            try: self.attributes("-zoomed", True)
            except tk.TclError: pass

    # ---------- Lookups ----------
    def service_index(self) -> dict[str, Service]:
        return {norm(s.path): s for s in self.services}

    def group_services(self, g: Group, index: dict[str, Service] | None = None) -> list[Service]:
        index = index or self.service_index()
        out = []
        for p in g.services:
            s = index.get(norm(p))
            if s and s not in out: out.append(s)
        return out

    def selected_services(self) -> list[Service]:
        by_id = {s.id: s for s in self.services}
        return [by_id[i] for i in self.tree.selection() if i in by_id]

    def _visible_services(self) -> list[Service]:
        kind, g = self.view
        if kind == "group": items = self.group_services(g)
        elif kind == "running": items = [s for s in self.services if s.is_active]
        else: items = list(self.services)
        q = self.search.get().lower()
        if q: items = [s for s in items if q in s.display_name.lower() or q in s.path.lower()]
        return items

    # ---------- Rendering ----------
    def set_view(self, kind: str, group: Group | None = None):
        self.view = (kind, group)
        self._render_header()
        self._refresh()

    def render_sidebar(self):
        px = self.px
        for w in self.group_list.winfo_children(): w.destroy()
        self.group_rows = {}
        if not self.groups:
            tk.Label(self.group_list, text="Group services to start them\ntogether, or on launch.",
                     justify="left", fg=C.SIDE_DIM, bg=C.SIDEBAR, font=self.f_small).pack(
                anchor="w", padx=px(22), pady=(px(2), px(6)))
            link = tk.Label(self.group_list, text="+ New group", fg=C.SIDE_ACCENT, bg=C.SIDEBAR, font=self.f_small,
                            cursor="hand2")
            link.pack(anchor="w", padx=px(22))
            link.bind("<Button-1>", lambda e: self.new_group())
        for g in self.groups:
            row = NavRow(self.group_list, self, "#", g.name,
                         on_click=lambda g=g: self.set_view("group", g),
                         on_menu=lambda e, g=g: self._group_menu(e, g),
                         on_double=lambda g=g: self.edit_group(g))
            row.pack(fill="x", padx=px(8), pady=1)
            self.group_rows[id(g)] = row
        self._update_sidebar()

    def _update_sidebar(self):
        kind, cur = self.view
        running = sum(s.is_running for s in self.services)
        self.nav_all.set_state(selected=kind == "all", badge=str(len(self.services)))
        self.nav_running.set_state(selected=kind == "running", badge=str(running),
                                   badge_fg=C.SIDE_GREEN if running else C.SIDE_DIM)
        index = self.service_index()
        for g in self.groups:
            row = self.group_rows.get(id(g))
            if not row: continue
            members = self.group_services(g, index)
            r = sum(s.is_running for s in members)
            row.set_state(selected=kind == "group" and cur is g, text=g.name, badge=f"{r}/{len(members)}",
                          badge_fg=C.SIDE_GREEN if r else C.SIDE_DIM, extra="⚡" if g.autostart else "")

    def _render_header(self):
        px = self.px
        for w in self.header_actions.winfo_children(): w.destroy()
        kind, g = self.view

        def btn(text, cmd, style="TButton", tip=None):
            b = ttk.Button(self.header_actions, text=text, command=cmd, style=style)
            b.pack(side="left", padx=(px(8), 0))
            if tip: Tooltip(b, tip)

        if kind == "group":
            self._autostart_var = tk.BooleanVar(self, value=g.autostart)
            cb = ttk.Checkbutton(self.header_actions, text="Start on launch", variable=self._autostart_var,
                                 command=lambda: self.set_group_autostart(g, self._autostart_var.get()))
            cb.pack(side="left", padx=(0, px(8)))
            Tooltip(cb, "Start this group automatically when Workaholic opens")
            btn("✎  Edit", lambda: self.edit_group(g), tip="Choose services, order and startup options")
            btn("■  Stop", lambda: self.stop_group(g), tip="Stop every service in this group")
            btn("↻  Restart all", lambda: self.restart_group(g), tip="Restart this group's services in order")
            btn("▶  Start group", lambda: self.start_group(g), "Accent.TButton",
                f"Start in order, {g.start_delay:g}s apart")
        else:
            if kind == "all": btn("▶  Start all", self.start_all)
            btn("■  Stop all", self.stop_all)
        btn("+  Add app / service", self.add_service, "Accent.TButton", "Search apps or browse files (Ctrl+N)")
        self._update_header_text()

    def _update_header_text(self):
        kind, g = self.view
        if kind == "group":
            members = self.group_services(g)
            r = sum(s.is_running for s in members)
            title = g.name
            parts = [f"{r} of {len(members)} running" if members else "No services in this group"]
            if g.autostart: parts.append("starts on launch")
            if g.trigger_process: parts.append(f"starts with {Path(g.trigger_process).name}")
            if len(members) > 1: parts.append(f"{g.start_delay:g}s between services")
            sub = "  ·  ".join(parts)
        elif kind == "running":
            title = "Running"
            n = sum(s.is_active for s in self.services)
            sub = f"{n} service{'s' if n != 1 else ''} running" if n else "Nothing is running right now"
        else:
            title = "All services"
            r = sum(s.is_running for s in self.services)
            sub = f"{r} of {len(self.services)} running" if self.services else "Add your first script to get started"
        if self.title_lbl.cget("text") != title: self.title_lbl.configure(text=title)
        if self.subtitle_lbl.cget("text") != sub: self.subtitle_lbl.configure(text=sub)

    def _refresh(self):
        if self._destroyed: return
        visible = self._visible_services()
        ids = [s.id for s in visible]
        wanted = set(ids)
        for iid in self.tree.get_children():
            if iid not in wanted:
                self.tree.delete(iid)
                self._row_cache.pop(iid, None)
        index = self.service_index()
        member_of: dict[str, list[str]] = {}
        for g in self.groups:
            for s in self.group_services(g, index):
                member_of.setdefault(s.id, []).append(g.name)
        now = time.time()
        for i, s in enumerate(visible):
            key, label = s.status()
            uptime = fmt_duration(now - s.start_time) if s.is_running and s.start_time else "—"
            values = (label, uptime, s.pid or "—", ", ".join(member_of.get(s.id, [])) or "—",
                      os.path.normpath(s.path))
            tags = (("odd",) if i % 2 else ()) + (("missing",) if key == "missing" else
                                                  () if key in ("running", "stopping") else ("idle",))
            image = self._row_image(s, key)
            row = (s.display_name, str(image), values, tags)
            if not self.tree.exists(s.id):
                self.tree.insert("", i, iid=s.id, text=s.display_name, image=image, values=values, tags=tags)
            elif self._row_cache.get(s.id) != row:
                self.tree.item(s.id, text=s.display_name, image=image, values=values, tags=tags)
            self._row_cache[s.id] = row
        if list(self.tree.get_children()) != ids:
            for i, iid in enumerate(ids): self.tree.move(iid, "", i)
        self._update_empty(visible)
        self._update_toolbar()
        self._update_sidebar()
        self._update_header_text()
        r = sum(s.is_running for s in self.services)
        n = len(self.services)
        summary = f"{r} running  ·  {n} service{'s' if n != 1 else ''}  ·  {len(self.groups)} group{'s' if len(self.groups) != 1 else ''}"
        if self.status_summary.cget("text") != summary: self.status_summary.configure(text=summary)

    def _update_empty(self, visible: list[Service]):
        kind, g = self.view
        q = self.search.get()
        if visible: state = None
        elif q: state = ("search", q)
        elif kind == "group": state = ("group", id(g))
        elif kind == "running": state = ("running",)
        else: state = ("none",)
        if state == self._empty_kind: return
        self._empty_kind = state
        if state is None:
            self.empty.place_forget()
            return
        for w in self.empty_buttons.winfo_children(): w.destroy()

        def btn(text, cmd, style="TButton"):
            ttk.Button(self.empty_buttons, text=text, command=cmd, style=style).pack(side="left", padx=self.px(4))

        if state[0] == "search":
            content = ("⌕", "No matches", f"Nothing matches “{q}”.")
            btn("Clear search", self.search.clear)
        elif state[0] == "group":
            content = ("#", "This group is empty", f"Add services to “{g.name}” to start them together.")
            btn("Choose services…", lambda: self.edit_group(g))
            btn("+  Add files", self.add_service, "Accent.TButton")
        elif state[0] == "running":
            content = ("●", "Nothing is running", "Start a service or a group and it will show up here.")
        else:
            content = ("▶", "No services yet",
                       "Add a Python script, executable or batch file. Workaholic runs each one in its own "
                       "folder, keeps an eye on it and captures its output.")
            btn("+  Add service", self.add_service, "Accent.TButton")
        icon, title, body = content
        self.empty_icon.configure(text=icon)
        self.empty_title.configure(text=title)
        self.empty_body.configure(text=body)
        self.empty.place(relx=0.5, rely=0.45, anchor="center")

    def _update_toolbar(self):
        sel = self.selected_services()

        def enable(btn, on):
            btn.state(["!disabled"] if on else ["disabled"])

        enable(self.btn_start, any(not s.is_active for s in sel))
        enable(self.btn_stop, any(s.is_running and not s.external for s in sel))
        enable(self.btn_restart, any(not s.is_stopping and not s.external for s in sel))
        enable(self.btn_edit, len(sel) == 1)
        enable(self.btn_remove, bool(sel))

    def flash(self, message: str, level: str = "info", sticky: bool = False):
        colors = {"info": C.MUTED, "ok": C.GREEN, "warn": C.AMBER, "error": C.RED}
        self.status_msg.configure(text=message, fg=colors.get(level, C.MUTED))
        if self._flash_job: self.after_cancel(self._flash_job)
        self._flash_job = None if sticky else self.after(9000 if level == "error" else 5000, self._clear_flash)

    def _clear_flash(self):
        self._flash_job = None
        self.status_msg.configure(text="Ready", fg=C.DIM)

    # ---------- Events ----------
    def _on_select(self):
        self._update_toolbar()
        sel = self.selected_services()
        if sel: self._log_show(sel[0])

    def _on_double(self, e):
        if self.tree.identify_region(e.x, e.y) in ("cell", "tree"):
            self.start_selected()

    def _tree_menu(self, e):
        iid = self.tree.identify_row(e.y)
        if iid and iid not in self.tree.selection():
            self.tree.selection_set(iid)
            self.tree.focus(iid)
        elif not iid:
            self.tree.selection_set(())
        sel = self.selected_services()
        m = self.menu()
        self._menu_vars = []
        if not sel:
            m.add_command(label="Add service…", accelerator="Ctrl+N", command=self.add_service)
            m.add_command(label="New group…", accelerator="Ctrl+G", command=self.new_group)
        else:
            one = sel[0] if len(sel) == 1 else None
            on = lambda cond: "normal" if cond else "disabled"
            m.add_command(label="Start", command=self.start_selected, state=on(any(not s.is_active for s in sel)))
            m.add_command(label="Stop", command=self.stop_selected, state=on(any(s.is_running and not s.external for s in sel)))
            m.add_command(label="Restart", accelerator="Ctrl+R", command=self.restart_selected,
                          state=on(any(not s.external for s in sel)))
            m.add_separator()
            m.add_command(label="Edit…", accelerator="F2", command=self.edit_selected, state=on(one))
            m.add_command(label="Open log file", command=lambda: self.open_log(one), state=on(one))
            m.add_command(label="Show in folder", command=lambda: reveal_path(one.path), state=on(one))
            gm = self.menu(m)
            for g in self.groups:
                members = {norm(p) for p in g.services}
                var = tk.BooleanVar(m, value=all(norm(s.path) in members for s in sel))
                self._menu_vars.append(var)
                gm.add_checkbutton(label=g.name, variable=var,
                                   command=lambda g=g, v=var: self.set_membership(sel, g, v.get()))
            if self.groups: gm.add_separator()
            gm.add_command(label="New group from selection…", command=lambda: self.new_group(sel))
            m.add_cascade(label="Groups", menu=gm)
            m.add_separator()
            kind, g = self.view
            if kind == "group":
                m.add_command(label=f"Remove from “{g.name}”", command=lambda: self.set_membership(sel, g, False))
            m.add_command(label="Remove from Workaholic…", accelerator="Del", command=self.remove_selected)
        try: m.tk_popup(e.x_root, e.y_root)
        finally: m.grab_release()

    def _group_menu(self, e, g: Group):
        m = self.menu()
        idx = self.groups.index(g)
        var = tk.BooleanVar(m, value=g.autostart)
        self._menu_vars = [var]
        m.add_command(label="Start group", command=lambda: self.start_group(g))
        m.add_command(label="Stop group", command=lambda: self.stop_group(g))
        m.add_command(label="Restart all in group", command=lambda: self.restart_group(g))
        m.add_separator()
        m.add_checkbutton(label="Start on launch", variable=var,
                          command=lambda: self.set_group_autostart(g, var.get()))
        m.add_command(label="Edit…", command=lambda: self.edit_group(g))
        m.add_command(label="Move up", command=lambda: self.move_group(g, -1), state="normal" if idx else "disabled")
        m.add_command(label="Move down", command=lambda: self.move_group(g, 1),
                      state="normal" if idx < len(self.groups) - 1 else "disabled")
        m.add_separator()
        m.add_command(label="Delete group…", command=lambda: self.delete_group(g))
        try: m.tk_popup(e.x_root, e.y_root)
        finally: m.grab_release()

    # ---------- Service actions ----------
    def add_service(self):
        entries = AddServiceDialog(self).show()
        if not entries: return
        paths = [entry["path"] for entry in entries]
        index = self.service_index()
        added = []
        for entry in entries:
            p = entry["path"]
            if norm(p) in index: continue
            s = Service(**entry)
            self.services.append(s)
            index[norm(p)] = s
            added.append(s)
        kind, g = self.view
        in_group = kind == "group"
        if in_group:
            members = {norm(p) for p in g.services}
            g.services.extend(p for p in paths if norm(p) not in members)
        skipped = len(paths) - len(added)
        if not added and not in_group:
            self.flash("Those files are already in the list.", "warn")
            return
        self._save_config()
        self.render_sidebar()
        self._refresh()
        self.tree.selection_set([s.id for s in added if self.tree.exists(s.id)])
        msg = f"Added {len(added)} service{'s' if len(added) != 1 else ''}"
        if in_group: msg += f" to “{g.name}”"
        if skipped and not in_group: msg += f" ({skipped} already in the list)"
        self.flash(msg, "ok")

    def edit_selected(self):
        sel = self.selected_services()
        if len(sel) != 1: return
        s = sel[0]
        result = ServiceDialog(self, s).show()
        if not result: return
        old = norm(s.path)
        if norm(result["path"]) != old:
            for g in self.groups:
                g.services = [result["path"] if norm(p) == old else p for p in g.services]
        for k, v in result.items(): setattr(s, k, v)
        self._row_cache.pop(s.id, None)
        self._save_config()
        self._refresh()
        self._log_sid = None
        self._log_show(s)
        self.flash(f"Saved “{s.display_name}”", "ok")

    def remove_selected(self):
        sel = self.selected_services()
        if not sel: return
        what = f"“{sel[0].display_name}”" if len(sel) == 1 else f"{len(sel)} services"
        detail = "Files on disk are not deleted."
        if any(s.is_active and not s.external for s in sel): detail = "Services started here will be stopped. " + detail
        if not messagebox.askyesno("Remove", f"Remove {what} from Workaholic?", detail=detail,
                                   icon="warning", parent=self):
            return
        gone = {norm(s.path) for s in sel}
        for s in sel:
            s.stop()
            s.restart_pending = False
        self.services = [s for s in self.services if s not in sel]
        for g in self.groups:
            g.services = [p for p in g.services if norm(p) not in gone]
        if self._log_sid in {s.id for s in sel}: self._log_show(None)
        self._save_config()
        self.render_sidebar()
        self._refresh()
        self.flash(f"Removed {what}", "ok")

    def start_selected(self):
        sel = [s for s in self.selected_services() if not s.is_active]
        if sel: self._start_sequence([(s, 0) for s in sel], "Start", interactive=True)

    def stop_selected(self):
        sel = [s for s in self.selected_services() if (s.is_active or s.restart_at) and not s.external]
        for s in sel:
            s.restart_pending = False
            s.stop()
        if sel: self.flash(f"Stopping {self._names(sel)}…")
        self._refresh()

    def restart_selected(self):
        sel = self.selected_services()
        stopped = []
        for s in sel:
            if s.external: continue
            if s.is_running: s.stop(restart=True)
            elif not s.is_stopping: stopped.append(s)
        if stopped: self._start_sequence([(s, 0) for s in stopped], "Restart", interactive=True)
        owned = [s for s in sel if not s.external]
        if owned: self.flash(f"Restarting {self._names(owned)}…")
        elif sel: self.flash("Already running elsewhere. Restart it from its own window.", "warn")
        self._refresh()

    def toggle_selected(self):
        sel = self.selected_services()
        if sel and all(s.is_running for s in sel): self.stop_selected()
        else: self.start_selected()

    def start_all(self):
        self._start_sequence([(s, 0) for s in self.services], "Start all", interactive=True)

    def stop_all(self):
        self._group_restart_tokens.clear()
        active = [s for s in self.services if (s.is_active or s.restart_at) and not s.external]
        for s in active:
            s.restart_pending = False
            s.stop()
        self.flash(f"Stopping {len(active)} service{'s' if len(active) != 1 else ''}…" if active
                   else "Nothing is running.")
        self._refresh()

    def open_log(self, s: Service | None):
        if not s: return
        if not s.log_path.exists():
            self.flash(f"“{s.display_name}” hasn't written a log yet.", "warn")
            return
        open_path(str(s.log_path))

    @staticmethod
    def _names(services: list[Service]) -> str:
        return f"“{services[0].display_name}”" if len(services) == 1 else f"{len(services)} services"

    def _start_sequence(self, items: list[tuple[Service, float]], label: str, interactive: bool = False,
                        still_valid=None):
        """Start services one after another, waiting each item's delay before the next."""
        items = [(s, d) for s, d in items if not s.is_active]
        if not items:
            self.flash(f"{label}: everything is already running.")
            return
        errors: list[str] = []
        skipped = 0
        total = len(items)
        pos = 0

        def step():
            nonlocal pos, skipped
            if self._closing or self._destroyed or (still_valid and not still_valid()): return
            s, delay = items[pos]
            pos += 1
            if s in self.services and not s.is_active:
                try:
                    if s.start() is False: skipped += 1
                except Exception as e: errors.append(f"{s.display_name}: {e}")
            else: skipped += 1
            self._refresh()
            if pos < total:
                if delay > 0: self.flash(f"{label}: started {pos} of {total}…", sticky=True)
                self.after(int(delay * 1000), step)
                return
            ok = total - len(errors) - skipped
            if errors:
                self.flash(f"{label}: {len(errors)} of {total} failed — {errors[0]}", "error")
                if interactive:
                    messagebox.showerror("Couldn't start", "\n\n".join(errors), parent=self)
            else:
                self.flash(f"{label}: started {ok} service{'s' if ok != 1 else ''}" +
                           (f"; {skipped} already running" if skipped else ""),
                           "ok")
        step()

    # ---------- Groups ----------
    def new_group(self, preselect: list[Service] | None = None):
        if preselect is None: preselect = self.selected_services()
        g = GroupDialog(self, None, preselect).show()
        if not g: return
        self.groups.append(g)
        self._save_config()
        self.render_sidebar()
        self.set_view("group", g)
        self.flash(f"Created group “{g.name}”", "ok")

    def edit_group(self, g: Group):
        result = GroupDialog(self, g).show()
        if not result: return
        g.name, g.services, g.autostart, g.start_delay = (result.name, result.services, result.autostart,
                                                         result.start_delay)
        g.trigger_process = result.trigger_process
        self._save_config()
        self.render_sidebar()
        if self.view[1] is g: self._render_header()
        self._refresh()
        self.flash(f"Saved group “{g.name}”", "ok")

    def delete_group(self, g: Group):
        if not messagebox.askyesno("Delete group", f"Delete the group “{g.name}”?",
                                   detail="The services in it are kept.", icon="warning", parent=self):
            return
        self.groups.remove(g)
        self._group_restart_tokens.pop(id(g), None)
        self._trigger_states.pop(id(g), None)
        self._save_config()
        self.render_sidebar()
        if self.view[1] is g: self.set_view("all")
        else: self._refresh()
        self.flash(f"Deleted group “{g.name}”", "ok")

    def move_group(self, g: Group, delta: int):
        i = self.groups.index(g)
        j = i + delta
        if not 0 <= j < len(self.groups): return
        self.groups[i], self.groups[j] = self.groups[j], self.groups[i]
        self._save_config()
        self.render_sidebar()

    def set_group_autostart(self, g: Group, enabled: bool):
        g.autostart = enabled
        self._save_config()
        if self.view[1] is g: self._render_header()
        self._update_sidebar()
        self.flash(f"“{g.name}” will {'start' if enabled else 'no longer start'} when Workaholic opens", "ok")

    def set_membership(self, services: list[Service], g: Group, include: bool):
        members = {norm(p) for p in g.services}
        if include:
            g.services.extend(s.path for s in services if norm(s.path) not in members)
        else:
            drop = {norm(s.path) for s in services}
            g.services = [p for p in g.services if norm(p) not in drop]
        self._save_config()
        self._refresh()
        self.flash(f"{'Added' if include else 'Removed'} {self._names(services)} "
                   f"{'to' if include else 'from'} “{g.name}”", "ok")

    def start_group(self, g: Group):
        members = self.group_services(g)
        if not members:
            self.flash(f"“{g.name}” has no services yet.", "warn")
            return
        self._start_sequence([(s, g.start_delay) for s in members], f"“{g.name}”", interactive=True)

    def stop_group(self, g: Group):
        self._group_restart_tokens.pop(id(g), None)
        active = [s for s in self.group_services(g) if (s.is_active or s.restart_at) and not s.external]
        for s in active:
            s.restart_pending = False
            s.stop()
        self.flash(f"Stopping “{g.name}”…" if active else f"Nothing in “{g.name}” is running.")
        self._refresh()

    def restart_group(self, g: Group):
        members = [s for s in self.group_services(g) if not s.external]
        if not members:
            self.flash(f"No services in “{g.name}” can be restarted here.", "warn")
            return
        token = object()
        self._group_restart_tokens[id(g)] = token
        for s in members:
            s.restart_pending = False
            s.stop()
        self.flash(f"Restarting “{g.name}”…")
        self._refresh()

        def resume():
            if self._closing or self._destroyed or self._group_restart_tokens.get(id(g)) is not token:
                return
            if any(s.is_active for s in members):
                self.after(TICK_MS, resume)
                return
            current = self.group_services(g)
            self._start_sequence([(s, g.start_delay) for s in members if s in self.services and s in current],
                                 f"Restart “{g.name}”", interactive=True,
                                 still_valid=lambda: self._group_restart_tokens.get(id(g)) is token)
        resume()

    def _check_process_triggers(self, processes: list[dict]):
        if self._closing or self.opts.no_autostart: return
        items, seen, names = [], set(), []
        for g in self.groups:
            target = g.trigger_process
            running = trigger_process_running(target, processes)
            previous_target, was_running = self._trigger_states.get(id(g), ("", False))
            self._trigger_states[id(g)] = (target, running)
            if not running or (previous_target == target and was_running): continue
            names.append(g.name)
            for s in self.group_services(g):
                if s.id not in seen:
                    seen.add(s.id)
                    items.append((s, g.start_delay))
        if items: self._start_sequence(items, "Process startup: " + ", ".join(names))

    def run_autostart(self):
        seen, items, names = set(), [], []
        index = self.service_index()
        for g in self.groups:
            if not g.autostart: continue
            names.append(g.name)
            for s in self.group_services(g, index):
                if s.id not in seen:
                    seen.add(s.id)
                    items.append((s, g.start_delay))
        if items: self._start_sequence(items, "Autostart")
        else: self._clear_flash()

    # ---------- Settings ----------
    def open_settings(self):
        result = SettingsDialog(self).show()
        if not result: return
        if IS_WINDOWS and result["launch_at_login"] != bool(get_launch_at_login()):
            try:
                set_launch_at_login(result["launch_at_login"])
            except OSError as e:
                messagebox.showerror("Settings", f"Couldn't update the sign-in setting:\n{e}", parent=self)
        self.settings.start_minimized = result["start_minimized"]
        self.settings.autostart_delay = result["autostart_delay"]
        self.settings.confirm_exit = result["confirm_exit"]
        self._save_config()
        self.flash("Settings saved", "ok")

    def _refresh_login_entry(self):
        """Keep the sign-in entry pointing at this copy if the app was moved."""
        current = get_launch_at_login()
        if current and current != login_command():
            try: set_launch_at_login(True)
            except OSError: pass

    # ---------- Output panel ----------
    def _log_target(self) -> Service | None:
        return next((s for s in self.services if s.id == self._log_sid), None)

    def _log_show(self, s: Service | None):
        if s is None:
            self._log_sid = self._log_file = None
            self.log_title.configure(text="")
            self.log_meta.configure(text="")
            for b in self.log_buttons: b.state(["disabled"])
            self._log_hint("Select a service to see its output here.")
            return
        if self._log_sid == s.id and self._log_file == s.log_path: return
        self._log_sid, self._log_file = s.id, s.log_path
        self.log_title.configure(text=s.display_name)
        self.log_meta.configure(text=f"{Path(s.path).parent}   ·   modified {s.mtime}")
        for b in self.log_buttons: b.state(["!disabled"])
        self._log_reload()

    def _log_hint(self, text: str):
        t = self.log_text
        t.configure(state="normal")
        t.delete("1.0", "end")
        t.insert("end", text, ("hint",))
        t.configure(state="disabled")
        self._log_placeholder = True

    def _log_reload(self):
        self._log_decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._log_pos = 0
        try:
            size = self._log_file.stat().st_size
        except OSError:
            self._log_hint("No output yet — start the service and its output will stream in here.")
            return
        start = max(0, size - LOG_TAIL_BYTES)
        try:
            with open(self._log_file, "rb") as f:
                f.seek(start)
                data = f.read(size - start)
        except OSError as e:
            self._log_hint(f"Couldn't read the log: {e}")
            return
        self._log_pos = start + len(data)
        if start:
            data = data[data.find(b"\n") + 1:]
        t = self.log_text
        t.configure(state="normal")
        t.delete("1.0", "end")
        t.configure(state="disabled")
        self._log_placeholder = False
        if not data:
            self._log_hint("The log is empty.")
        self._log_append(self._log_decoder.decode(data))
        t.see("end")

    def _log_poll(self):
        if self._log_file is None: return
        try:
            size = self._log_file.stat().st_size
        except OSError:
            return
        if size < self._log_pos:
            self._log_reload()
            return
        if size == self._log_pos: return
        try:
            with open(self._log_file, "rb") as f:
                f.seek(self._log_pos)
                data = f.read(min(size - self._log_pos, 1 << 20))
        except OSError:
            return
        self._log_pos += len(data)
        if self._log_placeholder:
            self.log_text.configure(state="normal")
            self.log_text.delete("1.0", "end")
            self.log_text.configure(state="disabled")
            self._log_placeholder = False
        self._log_append(self._log_decoder.decode(data))

    def _log_append(self, text: str):
        if not text: return
        text = ANSI_RE.sub("", text).replace("\r", "")
        t = self.log_text
        at_bottom = t.yview()[1] >= 0.999
        t.configure(state="normal")
        for line in text.splitlines(keepends=True):
            low = line.lower()
            if line.startswith("=== "): tag = ("marker",)
            elif "traceback" in low or "error" in low or "exception" in low: tag = ("err",)
            elif "warn" in low: tag = ("warn",)
            else: tag = ()
            t.insert("end", line, tag)
        lines = int(t.index("end-1c").split(".")[0])
        if lines > LOG_MAX_LINES:
            t.delete("1.0", f"{lines - LOG_MAX_LINES}.0")
        t.configure(state="disabled")
        if at_bottom: t.see("end")

    def _log_clear(self):
        s = self._log_target()
        if not s or not s.log_path.exists(): return
        if not messagebox.askyesno("Clear log", f"Clear the log for “{s.display_name}”?",
                                   detail=str(s.log_path), parent=self):
            return
        try:
            open(s.log_path, "w").close()
        except OSError as e:
            messagebox.showerror("Clear log", str(e), parent=self)
            return
        self._log_reload()

    # ---------- Background loop ----------
    def _scan_processes(self):
        try: self._process_results.put(process_snapshot())
        except Exception: self._process_results.put(None)

    def _tick(self):
        if self._destroyed: return
        try:
            self._drain_queue()
            now = time.time()
            try:
                processes = self._process_results.get_nowait()
            except queue.Empty:
                pass
            else:
                self._process_scan_pending = False
                if processes is not None:
                    for service in self.services: service.detect_existing(processes)
                    self._check_process_triggers(processes)
            if now >= self._process_check_at and not self._process_scan_pending and not self._closing:
                self._process_scan_pending = True
                self._process_check_at = now + 2.0
                threading.Thread(target=self._scan_processes, daemon=True).start()
            for s in self.services:
                rc = s.poll()
                if rc is not None:
                    if rc == 0:
                        self.flash(f"“{s.display_name}” finished")
                    else:
                        extra = f" — restarting in {int(s.restart_at - now + 0.99)}s" if s.restart_at else ""
                        self.flash(f"“{s.display_name}” exited with code {rc}{extra}", "error")
                if self._closing: continue
                if s.restart_at and now >= s.restart_at and not s.is_active:
                    s.restart_at = None
                    try:
                        s.start()
                        self.flash(f"Restarted “{s.display_name}”", "ok")
                    except Exception as e:
                        self.flash(f"Couldn't restart “{s.display_name}”: {e}", "error")
                if s.restart_pending and not s.is_active:
                    s.restart_pending = False
                    try:
                        s.start()
                        self.flash(f"Restarted “{s.display_name}”", "ok")
                    except Exception as e:
                        self.flash(f"Couldn't restart “{s.display_name}”: {e}", "error")
            self._refresh()
            self._log_poll()
            if self._closing: self._check_close_done()
        except Exception as e:  # keep the loop alive no matter what
            print(f"[tick] {e!r}", file=sys.stderr)
        if not self._destroyed:
            self.after(TICK_MS, self._tick)

    def _drain_queue(self):
        while True:
            try: msg = self._ui_queue.get_nowait()
            except queue.Empty: return
            if msg == "show": self._show_window()

    def _listen(self, sock: socket.socket):
        while True:
            try: conn, _ = sock.accept()
            except OSError: return
            with conn:
                try:
                    conn.settimeout(1)
                    if conn.recv(32).startswith(b"workaholic:show"):
                        conn.sendall(b"workaholic:ok")
                        self._ui_queue.put("show")
                except OSError:
                    pass

    def _show_window(self):
        self.deiconify()
        if self.state() == "iconic": self.state("normal")
        self.lift()
        self.attributes("-topmost", True)
        self.after(200, lambda: self.attributes("-topmost", False))
        self.focus_force()

    # ---------- Persistence ----------
    def _load_config(self):
        source = CONFIG_FILE
        legacy = APP_DIR / "services_config.json"
        if not CONFIG_FILE.exists() and legacy.is_file() and not getattr(self.opts, "smoke_test", False):
            try:
                CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(legacy, CONFIG_FILE)
            except OSError as e:
                self._config_warning = f"Couldn't migrate settings from {legacy}: {e}"
                source = legacy  # Still load the user's services if copying fails.
        if not source.exists(): return
        try:
            data = json.loads(source.read_text(encoding="utf-8"))
            if not isinstance(data, dict): raise ValueError("unexpected format")
        except Exception as e:
            backup = source.with_name(source.stem + ".broken.json")
            try: shutil.copyfile(source, backup)
            except OSError: pass
            self._config_warning = f"Couldn't read {CONFIG_FILE.name} ({e}). A copy was saved as {backup.name}."
            return

        seen = set()
        for entry in data.get("services", []) or []:
            if isinstance(entry, str): entry = {"path": entry}
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str) or not entry["path"]: continue
            if norm(entry["path"]) in seen: continue
            seen.add(norm(entry["path"]))
            self.services.append(Service(entry["path"], name=str(entry.get("name") or ""),
                                         args=str(entry.get("args") or ""),
                                         interpreter=str(entry.get("interpreter") or ""),
                                         auto_restart=bool(entry.get("auto_restart", False)),
                                         working_dir=str(entry.get("working_dir") or ""),
                                         icon=str(entry.get("icon") or "")))

        raw_groups = data.get("groups", []) or []
        if isinstance(raw_groups, dict):  # v1: {"name": [paths]} + "autostart_groups": [names]
            legacy_auto = set(data.get("autostart_groups", []) or [])
            raw_groups = [{"name": k, "services": v, "autostart": k in legacy_auto} for k, v in raw_groups.items()]
        for entry in raw_groups:
            if not isinstance(entry, dict) or not entry.get("name"): continue
            try: delay = max(0.0, float(entry.get("start_delay", 1.0)))
            except (TypeError, ValueError): delay = 1.0
            self.groups.append(Group(name=str(entry["name"]),
                                     services=[p for p in entry.get("services", []) or [] if isinstance(p, str)],
                                     autostart=bool(entry.get("autostart", False)), start_delay=delay,
                                     trigger_process=str(entry.get("trigger_process") or "")))

        raw_settings = data.get("settings", {}) or {}
        for f in fields(Settings):
            if f.name in raw_settings:
                value, default = raw_settings[f.name], getattr(self.settings, f.name)
                if isinstance(default, float) and isinstance(value, (int, float)) and not isinstance(value, bool):
                    value = float(value)
                if type(value) is type(default):
                    setattr(self.settings, f.name, value)

    def _save_config(self):
        data = {
            "version": 2,
            "services": [s.to_dict() for s in self.services],
            "groups": [asdict(g) for g in self.groups],
            "settings": asdict(self.settings),
        }
        tmp = CONFIG_FILE.with_suffix(".json.tmp")
        try:
            CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, CONFIG_FILE)
        except OSError as e:
            messagebox.showerror("Couldn't save settings", str(e), parent=self)

    # ---------- Close ----------
    def on_close(self):
        if self._closing:  # second click while waiting: don't wait any longer
            self._close_deadline = 0
            return
        active = [s for s in self.services if s.is_active and not s.external]
        if active and self.settings.confirm_exit:
            if not messagebox.askyesno("Quit Workaholic", f"Stop {self._names(active)} and quit?",
                                       detail="Running services are stopped when Workaholic closes.",
                                       icon="warning", parent=self):
                return
        if self.state() == "normal":
            self.settings.window_size = f"{self.winfo_width()}x{self.winfo_height()}"
        self.settings.maximized = self.state() == "zoomed"
        self._save_config()
        self._closing = True
        self._close_deadline = time.time() + STOP_GRACE_SECONDS + 8
        for s in self.services:
            s.restart_pending = False
            s.stop()
        if active:
            self.flash(f"Stopping {self._names(active)}…", sticky=True)
        self._check_close_done()

    def _check_close_done(self):
        if any(s.is_active and not s.external for s in self.services) and time.time() < self._close_deadline: return
        for s in self.services:
            if s.proc is not None and s.proc.poll() is None:
                _kill_tree(s.proc)
        self.destroy()

    def destroy(self):
        self._destroyed = True
        if self._instance_sock:
            try: self._instance_sock.close()
            except OSError: pass
        super().destroy()


def main():
    parser = argparse.ArgumentParser(description="Run, group and monitor scripts and programs.")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    parser.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--startup", action="store_true",
                        help="launched at sign-in (honours the 'start minimized' setting)")
    parser.add_argument("--minimized", action="store_true", help="start minimized")
    parser.add_argument("--no-autostart", action="store_true", help="skip launch and process-triggered group startup this time")
    opts = parser.parse_args()

    if opts.smoke_test:
        # No user settings, sign-in changes, services, or singleton communication.
        import tempfile
        global CONFIG_FILE
        with tempfile.TemporaryDirectory(prefix="workaholic-smoke-") as folder:
            CONFIG_FILE = Path(folder) / "services_config.json"
            opts.no_autostart = True
            enable_dpi_awareness()
            app = App(opts)
            try:
                def callback_error(exc_type, value, traceback):
                    raise value.with_traceback(traceback)
                app.report_callback_exception = callback_error
                app.update()
                icon = Path(getattr(sys, "_MEIPASS", APP_DIR)) / "assets/workaholic.ico"
                if not app.logo or not icon.is_file():
                    raise RuntimeError("App icon did not load")
            finally:
                app.destroy()
        return

    instance = acquire_single_instance()
    if instance is False:  # an existing window was brought to the front
        return
    enable_dpi_awareness()
    App(opts, instance or None).mainloop()


if __name__ == "__main__":
    main()
